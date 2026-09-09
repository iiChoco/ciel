"""A task gets another turn.

The store remembers what the owner asked for; the controller lets the owner
steer it; nothing, until now, ever moved one forward. This is the executor
the durable-tasks plan deferred: when the ladder hands it the slot, it takes
the oldest task whose time has come and gives it exactly one bounded step —
prepare, claim, dispatch, observe, then complete, checkpoint, or wait — and
hands the slot back.

**Prepare is pure; the adapter mutates nothing here.** An adapter serves a
set of operations and, if it keeps records, one feature namespace. Its
``prepare`` looks at the task and its own records and says proceed or wait;
its ``read`` observes the target and returns evidence and what should
happen next. A mutation step is not dispatched by this runner at all: it
waits, visibly, for the authorized dispatch phase that arrives with the
foundation's third milestone. A task whose operation no adapter serves, or
whose namespace this runtime cannot read, waits the same way.

**The store fences every late result.** Every write the runner makes names
the attempt it holds; the store refuses one for an attempt that is no
longer current. A step that outlives the owner's cancellation, a restart, or
a second runner writes nothing.

**Human input wins.** The ladder never picks a task step unless the room is
quiet and every human lane is empty. If a step is holding the model turn —
an extraction — when the owner speaks, the pipeline interrupts it: the step
is cancelled, the attempt is spent, the task is requeued with backoff. A
plain read is left to finish; it holds nothing anyone is waiting for.

**Giving up is recorded, never retried blindly.** A timeout, an adapter's
exception, a cancellation: each abandons the attempt through the store,
which charges the allowance and requeues, waits for reconciliation, or
fails the task as its rules say. The runner decides nothing about outcomes
it did not observe.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Literal, Protocol

from ciel.brain.extract import ExtractionBackend, ExtractionError, ExtractionLimits, Lease, extract_json
from ciel.config import TasksConfig
from ciel.tasks import (Attempt, Evidence, FeatureRecord, Namespace, RecordSet, Step, Task, TaskConflict, TaskLimit,
                        TaskStore, TaskStoreError, WaitReason)

log = logging.getLogger(__name__)

_REFRESH_S = 1.0
"""How often the runner asks the store whether anything is eligible when
nothing has changed; a committed step refreshes at once."""


@dataclass(frozen=True, slots=True)
class Preparation:
    """What the adapter concluded before touching anything."""

    wait: tuple[WaitReason, str] | None = None
    """A reason and an explanation, and the task waits instead of running."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one read found and what should happen next."""

    evidence: tuple[Evidence, ...] = ()
    next_step: Step | None = None
    """None keeps the current step."""
    delay_s: float = 0.0
    """How long until the next step is eligible, when the task is checkpointed."""
    wait: tuple[WaitReason, str] | None = None
    """External or resource: the task waits with this explanation."""
    question: tuple[str, tuple[str, ...]] | None = None
    """A prompt and exact choices: the task waits on the owner."""
    retarget: tuple[str, str] | None = None
    """A target and the head revision the read actually saw."""
    records: RecordSet | None = None
    """The adapter's own records, committed with the transition."""


@dataclass(frozen=True, slots=True)
class StepContext:
    task: Task
    attempt: Attempt
    records: tuple[FeatureRecord, ...]
    now: float
    extract: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
    """One isolated model call: system prompt, payload, schema. Spends a
    model call before it is made; raises ExtractionError or TaskLimit."""
    extraction_available: bool


class TaskAdapter(Protocol):
    namespace: Namespace | None
    operations: frozenset[str]

    def prepare(self, task: Task, records: tuple[FeatureRecord, ...]) -> Preparation: ...
    async def read(self, context: StepContext) -> Outcome: ...


Result = Literal['done', 'checkpointed', 'waiting', 'asked', 'abandoned', 'refused', 'stale']


@dataclass(frozen=True, slots=True)
class StepReport:
    task_id: str
    result: Result
    detail: str = ''


class TaskRunner:
    def __init__(self, config: TasksConfig, store: Callable[[], TaskStore | None], adapters: Iterable[TaskAdapter] = (), *,
                 lease: Lease | None = None, backend: ExtractionBackend | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._store = store
        self._lease = lease
        self._backend = backend
        self._clock = clock
        self._by_operation: dict[str, TaskAdapter] = {}
        self._adapters: list[TaskAdapter] = []
        for adapter in adapters:
            self.add(adapter)
        self._step: asyncio.Task[StepReport | None] | None = None
        self._leased = False
        self._ready = False
        self._oldest: float | None = None
        self._refresh: asyncio.Task[None] | None = None
        self._next_refresh = 0.0
        self.reports: list[StepReport] = []
        """The last few step reports, newest last, for the owner view and probes."""

    def add(self, adapter: TaskAdapter) -> None:
        for operation in adapter.operations:
            if operation in self._by_operation:
                raise ValueError(f'operation {operation!r} already has an adapter')
        for operation in adapter.operations:
            self._by_operation[operation] = adapter
        self._adapters.append(adapter)

    @property
    def namespaces(self) -> tuple[Namespace, ...]:
        return tuple(a.namespace for a in self._adapters if a.namespace is not None)

    # ── what the ladder reads ─────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready and (self._step is None or self._step.done())

    def aged(self, now: float, aging_s: float) -> bool:
        return self._oldest is not None and now - self._oldest >= aging_s

    def refresh(self, now: float) -> None:
        """Ask the store, at most once a second, whether anything is eligible.
        Never blocks the loop; the answer lands on the next snapshot."""
        if self._store() is None:
            self._ready, self._oldest = False, None
            return
        if self._refresh is not None and not self._refresh.done():
            return
        if now < self._next_refresh:
            return
        self._next_refresh = now + _REFRESH_S
        self._refresh = asyncio.create_task(self._refresh_now(now))

    async def _refresh_now(self, now: float) -> None:
        store = self._store()
        if store is None:
            self._ready, self._oldest = False, None
            return
        try:
            tasks = await store.eligible(self._config.owner, now=now, limit=1)
        except (TaskStoreError, TaskConflict, ValueError):
            log.debug('task eligibility could not be read', exc_info=True)
            self._ready, self._oldest = False, None
            return
        self._ready = bool(tasks)
        self._oldest = tasks[0].eligible_at if tasks else None

    # ── what the ladder enacts ────────────────────────────────────────────────

    def start_step(self, now: float) -> bool:
        """Enact a TASK pick: one step, in the background. False when there
        was nothing to do after all, so the round is not claimed."""
        if not self.ready:
            return False
        self._ready = False
        self._step = asyncio.create_task(self.step(now))
        return True

    def interrupt(self) -> None:
        """Human input arrived: a step holding the model turn yields it."""
        if self._step is not None and not self._step.done() and self._leased:
            self._step.cancel()

    async def close(self) -> None:
        for task in (self._step, self._refresh):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 - closing, nothing to do with it
                    pass

    async def step(self, now: float | None = None) -> StepReport | None:
        """One bounded step for the oldest eligible task; None when none was."""
        store = self._store()
        if store is None:
            return None
        stamp = self._clock() if now is None else now
        try:
            tasks = await store.eligible(self._config.owner, now=stamp, limit=1)
        except TaskStoreError:
            log.warning('task store unavailable; no step taken', exc_info=True)
            return None
        if not tasks:
            return None
        report = await self._run(store, tasks[0], stamp)
        self.reports.append(report)
        del self.reports[:-32]
        self._next_refresh = 0.0
        return report

    async def _wait(self, store: TaskStore, task: Task, reason: WaitReason, detail: str, now: float) -> StepReport:
        try:
            await store.wait(self._config.owner, task.id, task.revision, reason, detail, now=now)
        except TaskConflict:
            return StepReport(task.id, 'stale', 'the task changed under the runner')
        return StepReport(task.id, 'refused' if reason == 'resource' else 'waiting', detail)

    async def _run(self, store: TaskStore, task: Task, now: float) -> StepReport:
        owner = self._config.owner
        step = task.next_step
        adapter = self._by_operation.get(step.operation)
        if adapter is None:
            return await self._wait(store, task, 'resource', f'no adapter serves {step.operation}', now)
        if step.kind == 'mutation':
            return await self._wait(store, task, 'resource', 'mutation dispatch is not available yet', now)
        namespace = adapter.namespace
        if namespace is not None and not store.namespace_supported(namespace.name):
            return await self._wait(store, task, 'resource', f'records for {namespace.name} are unsupported by this runtime', now)
        try:
            records = await store.records(owner, namespace.name) if namespace is not None else ()
            preparation = adapter.prepare(task, records)
        except Exception:  # noqa: BLE001 - the adapter's failure is logged without its payload
            log.warning('adapter could not prepare task %s', task.id, exc_info=True)
            return await self._wait(store, task, 'resource', 'the adapter could not prepare this step', now)
        if preparation.wait is not None:
            return await self._wait(store, task, preparation.wait[0], preparation.wait[1], now)
        try:
            attempt = await store.claim(owner, task.id, task.revision, now=now)
            attempt = await store.mark_dispatched(owner, attempt, now=now)
        except (TaskConflict, TaskLimit) as exc:
            return StepReport(task.id, 'stale', str(exc))
        context = StepContext(task, attempt, records, now, self._extractor(store, attempt), self._backend is not None and self._lease is not None)
        try:
            outcome = await asyncio.wait_for(adapter.read(context), self._config.step_timeout_s)
        except asyncio.CancelledError:
            await self._abandon(store, task, attempt, 'interrupted by the owner', now)
            raise
        except asyncio.TimeoutError:
            return await self._abandon(store, task, attempt, 'the read timed out', now)
        except (ExtractionError, TaskLimit) as exc:
            return await self._abandon(store, task, attempt, str(exc), now)
        except Exception:  # noqa: BLE001 - the adapter's failure is logged without its payload
            log.warning('adapter read failed for task %s', task.id, exc_info=True)
            return await self._abandon(store, task, attempt, 'the read failed', now)
        return await self._settle(store, task, attempt, outcome, now)

    def _after(self, now: float) -> float:
        """A stamp no earlier than the step's own clock: a caller may run the
        runner ahead of wall time, and the store will not take evidence
        observed after the moment it is recorded."""
        return max(now, self._clock())

    async def _abandon(self, store: TaskStore, task: Task, attempt: Attempt, detail: str, now: float) -> StepReport:
        stamp = self._after(now)
        try:
            after = await store.abandon(self._config.owner, attempt, detail, eligible_at=stamp + self._config.retry_backoff_s, now=stamp)
        except TaskConflict:
            return StepReport(task.id, 'stale', 'the attempt was no longer current')
        return StepReport(task.id, 'abandoned', f'{detail} ({after.status})')

    async def _settle(self, store: TaskStore, task: Task, attempt: Attempt, outcome: Outcome, now: float) -> StepReport:
        owner = self._config.owner
        stamp = self._after(now)
        try:
            current = await store.observe(owner, attempt, outcome.evidence, now=stamp)
            if outcome.retarget is not None:
                current = await store.retarget(owner, current.id, current.revision, outcome.retarget[0], outcome.retarget[1], now=stamp)
            try:
                await store.complete(owner, current.id, current.revision, now=stamp, records=outcome.records)
                return StepReport(task.id, 'done', 'completion criteria verified')
            except TaskConflict:
                pass  # the criteria are not met yet; the step is still a clean one
            if outcome.question is not None:
                current = await store.checkpoint(owner, current.id, current.revision, outcome.next_step or current.next_step,
                                                 eligible_at=stamp, now=stamp, records=outcome.records)
                await store.ask_owner(owner, current.id, current.revision, outcome.question[0], outcome.question[1], now=stamp)
                return StepReport(task.id, 'asked', outcome.question[0])
            if outcome.wait is not None:
                if outcome.wait[0] == 'owner':
                    raise ValueError('an owner wait carries a question')
                await store.wait(owner, current.id, current.revision, outcome.wait[0], outcome.wait[1], now=stamp,
                                 step=outcome.next_step, records=outcome.records)
                return StepReport(task.id, 'waiting', outcome.wait[1])
            await store.checkpoint(owner, current.id, current.revision, outcome.next_step or current.next_step,
                                   eligible_at=stamp + max(0.0, outcome.delay_s), now=stamp, records=outcome.records)
            return StepReport(task.id, 'checkpointed', 'next step saved')
        except (TaskConflict, ValueError, TaskLimit) as exc:
            # The store refused the outcome: evidence off-target, a step outside
            # scope, a record revision that moved, a full namespace — or the
            # task itself changed under the runner. Abandoning tells them
            # apart: an attempt still ours is spent and requeued so the task
            # does not sit running forever; one that is not is simply stale.
            log.warning('adapter outcome refused for task %s: %s', task.id, exc)
            return await self._abandon(store, task, attempt, 'the read returned an outcome the store refused', now)

    def _extractor(self, store: TaskStore, attempt: Attempt) -> Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]:
        async def extract(system_prompt: str, payload: str, schema: dict[str, Any]) -> dict[str, Any]:
            if self._backend is None or self._lease is None:
                raise ExtractionError('extraction is not configured on this runtime')
            await store.note_model_call(self._config.owner, attempt)
            limits = ExtractionLimits(self._config.extraction_max_chars, self._config.extraction_timeout_s,
                                      self._config.extraction_max_budget_usd)
            self._leased = True
            try:
                return await extract_json(self._backend, self._lease, system_prompt, payload, schema, limits)
            finally:
                self._leased = False
        return extract


__all__ = ['Outcome', 'Preparation', 'StepContext', 'StepReport', 'TaskAdapter', 'TaskRunner']
