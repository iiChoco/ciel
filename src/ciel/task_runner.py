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

**A mutation is written down before it is sent, and never sent twice on a
guess.** The adapter plans the payload under a claimed attempt; the runner
digests it, journals the intent, and asks the store for authority — the
grant at its activated revision, or the owner's approval of this exact
payload — and only then dispatches, once, under a timeout. A failed
precondition or a passed deadline is an unsent attempt, planned again. A
timeout, an exception, or the owner's voice after the send is an outcome
nobody knows: the task waits for reconciliation, where the adapter is asked
what happened, and after enough reads that cannot tell, the owner is.

**Giving up is recorded, never retried blindly.** A timeout, an adapter's
exception, a cancellation: each abandons the attempt through the store,
which charges the allowance and requeues, waits for reconciliation, or
fails the task as its rules say. The runner decides nothing about outcomes
it did not observe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Literal, Protocol

from ciel.brain.extract import ExtractionBackend, ExtractionError, ExtractionLimits, Lease, extract_json
from ciel.config import TasksConfig
from ciel.proactive.events import EventQueue, ProactiveEvent
from ciel.tasks import (Attempt, Notice, Specification, DerivedOrigin, Evidence, FeatureRecord, GrantSetup, Intent, Namespace, NoAuthority, RecordSet, RecordWrite, Step,
                        Task, TaskConflict, TaskLimit, TaskStore, TaskStoreError, WaitReason)

if TYPE_CHECKING:
    from ciel.journal import ActionJournal

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
class Derivation:
    """Work a read proposes under a standing mandate: the store admits it
    only inside the mandate's grant, deduplicated on its event and source
    revision. The adapter names it; the runner asks; the store decides."""

    mandate_id: str
    event_key: str
    source_revision: str
    specification: Specification
    step: Step


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
    derive: tuple[Derivation, ...] = ()
    """Children to derive once the outcome is committed; refusals are logged, never fatal."""


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


@dataclass(frozen=True, slots=True)
class Plan:
    """What a mutation would send, planned under a claimed attempt and read
    back from the target before anything is authorized."""

    payload: dict[str, Any]
    preconditions: dict[str, Any]
    """What the adapter observed the target to be; the send must fail if it moved."""
    recipe: dict[str, Any]
    """The adapter's own instructions for reconciling: an idempotency key, a lookup."""
    verify: Step
    """The read that verifies the effect afterwards; a mutation never completes on its own word."""


@dataclass(frozen=True, slots=True)
class MutationResult:
    evidence: tuple[Evidence, ...] = ()
    records: RecordSet | None = None


@dataclass(frozen=True, slots=True)
class Reconciliation:
    verdict: Literal['applied', 'not_applied', 'unknown']
    evidence: tuple[Evidence, ...] = ()
    detail: str = ''


class PreconditionFailed(Exception):
    """The target moved between the plan and the send; nothing was sent."""


class TaskAdapter(Protocol):
    namespace: Namespace | None
    operations: frozenset[str]

    def prepare(self, task: Task, records: tuple[FeatureRecord, ...]) -> Preparation: ...
    async def read(self, context: StepContext) -> Outcome: ...


class WritingAdapter(TaskAdapter, Protocol):
    """An adapter that can be granted mutations declares how it plans, sends,
    and reconciles them; one without these never has a mutation dispatched."""

    async def plan(self, context: StepContext) -> Plan: ...
    async def mutate(self, context: StepContext, intent: Intent, plan: Plan) -> MutationResult: ...
    async def reconcile(self, context: StepContext, intent: Intent) -> Reconciliation: ...


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


Result = Literal['done', 'checkpointed', 'waiting', 'asked', 'abandoned', 'refused', 'stale', 'dispatched', 'reconciled']


@dataclass(frozen=True, slots=True)
class StepReport:
    task_id: str
    result: Result
    detail: str = ''


class TaskRunner:
    def __init__(self, config: TasksConfig, store: Callable[[], TaskStore | None], adapters: Iterable[TaskAdapter] = (), *,
                 lease: Lease | None = None, backend: ExtractionBackend | None = None,
                 clock: Callable[[], float] = time.time, journal: "ActionJournal | None" = None) -> None:
        self._config = config
        self._store = store
        self._lease = lease
        self._backend = backend
        self._clock = clock
        self._journal = journal
        """Inverse; a mutation whose intent cannot be journaled is not sent."""
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

    @property
    def setups(self) -> tuple[GrantSetup, ...]:
        """What each adapter offers the owner to approve; an adapter with no
        standing work to offer simply has none."""
        return tuple(setup for setup in (getattr(a, 'setup', None) for a in self._adapters) if setup is not None)

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
            uncertain = await store.reconcilable(self._config.owner, limit=1)
        except (TaskStoreError, TaskConflict, ValueError):
            log.debug('task eligibility could not be read', exc_info=True)
            self._ready, self._oldest = False, None
            return
        stamps = [t.eligible_at for t in tasks] + [t.updated_at for t, _, _ in uncertain]
        self._ready = bool(stamps)
        self._oldest = min(stamps) if stamps else None

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
            uncertain = await store.reconcilable(self._config.owner, limit=1)
            tasks = () if uncertain else await store.eligible(self._config.owner, now=stamp, limit=1)
        except TaskStoreError:
            log.warning('task store unavailable; no step taken', exc_info=True)
            return None
        if uncertain:
            # An outcome nobody knows comes before any new step: the world may
            # already hold an effect the runner must not add to.
            report = await self._reconcile(store, *uncertain[0], stamp)
        elif tasks:
            report = await self._run(store, tasks[0], stamp)
        else:
            return None
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
        writer = all(hasattr(adapter, name) for name in ('plan', 'mutate', 'reconcile'))
        if step.kind == 'mutation' and not writer:
            return await self._wait(store, task, 'resource', f'the adapter for {step.operation} cannot dispatch a mutation', now)
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
        if step.kind == 'mutation':
            return await self._dispatch(store, task, adapter, records, now)  # type: ignore[arg-type]
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
        report = await self._settle(store, task, attempt, outcome, now)
        if outcome.derive and report.result in ('checkpointed', 'waiting', 'done') and namespace is not None:
            derived = await self._derive(store, namespace.name, outcome.derive, now)
            report = StepReport(report.task_id, report.result, f'{report.detail}; derived {derived} of {len(outcome.derive)}')
        return report

    async def _derive(self, store: TaskStore, namespace: str, derivations: tuple[Derivation, ...], now: float) -> int:
        """Ask the store for each child the read proposed. The store holds the
        grant, the dedupe, and the allowances; here a refusal is one warning."""
        count = 0
        for item in derivations:
            try:
                await store.derive_task(self._config.owner, item.mandate_id, None, namespace, item.event_key, item.source_revision,
                                        item.specification, item.step, now=self._after(now))
                count += 1
            except (TaskConflict, TaskLimit, ValueError, TaskStoreError) as exc:
                log.warning('a derived task was refused: %s', exc)
        return count

    async def _dispatch(self, store: TaskStore, task: Task, adapter: WritingAdapter, records: tuple[FeatureRecord, ...], now: float) -> StepReport:
        """One mutation: plan, journal, authorize, send once, verify by a read."""
        owner = self._config.owner
        step = task.next_step
        try:
            attempt = await store.claim(owner, task.id, task.revision, now=now)
        except (TaskConflict, TaskLimit) as exc:
            return StepReport(task.id, 'stale', str(exc))
        context = StepContext(task, attempt, records, now, self._extractor(store, attempt), self._backend is not None and self._lease is not None)
        try:
            plan = await asyncio.wait_for(adapter.plan(context), self._config.step_timeout_s)
            payload_digest, precondition_digest = _digest(plan.payload), _digest(plan.preconditions)
        except asyncio.CancelledError:
            await self._abandon(store, task, attempt, 'interrupted by the owner while planning', now)
            raise
        except Exception as exc:  # noqa: BLE001 - nothing was sent; the plan failed
            log.warning('adapter could not plan task %s', task.id, exc_info=True)
            return await self._abandon(store, task, attempt, f'the plan failed: {type(exc).__name__}', now)
        ref = None
        if self._journal is not None:
            ref = self._journal.record(tool=f'task_{step.operation}',
                                       args={'task_id': task.id, 'attempt_id': attempt.id, 'target': step.target,
                                             'payload_digest': payload_digest, 'precondition_digest': precondition_digest},
                                       note='Dispatch intent recorded before the send; the store holds the authority and the outcome.')
        if ref is None:
            return await self._abandon(store, task, attempt, 'the journal could not record the intent; nothing was sent', now)
        authorized_at = self._clock()
        try:
            intent = await store.authorize(owner, attempt, step.operation, step.target, payload_digest, precondition_digest,
                                           {**plan.recipe, 'verify': {'kind': plan.verify.kind, 'operation': plan.verify.operation,
                                                                       'target': plan.verify.target, 'arguments': list(plan.verify.arguments)}},
                                           ref, now=now)
        except NoAuthority as exc:
            if isinstance(task.origin, DerivedOrigin) or task.origin.approval_ref is not None:
                try:
                    await store.park(owner, attempt, 'external', str(exc), now=now)
                except TaskConflict:
                    return StepReport(task.id, 'stale', 'the attempt was no longer current')
                return StepReport(task.id, 'waiting', str(exc))
            prompt = f'{task.specification.outcome}: {step.operation} on {step.target} — approve?'
            try:
                await store.ask_approval(owner, attempt, prompt, payload_digest, now=now)
            except TaskConflict:
                return StepReport(task.id, 'stale', 'the attempt was no longer current')
            return StepReport(task.id, 'asked', prompt)
        except (TaskConflict, TaskLimit, ValueError) as exc:
            return StepReport(task.id, 'stale', str(exc))
        # Authorization moved the task's revision; the attempt the store now
        # holds names it, and every later write must name the same one.
        attempt = next(a for a in await store.attempts(owner, task.id) if a.id == attempt.id)
        if self._clock() - authorized_at >= self._config.dispatch_deadline_s:
            # Elapsed on the runner's own clock: the store's deadline stamp is
            # the same bound written down, for anyone reading the record.
            return await self._abandon(store, task, attempt, 'the dispatch deadline passed before the send', now, sent=False)
        try:
            result = await asyncio.wait_for(adapter.mutate(context, intent, plan), self._config.mutation_timeout_s)
        except PreconditionFailed as exc:
            return await self._abandon(store, task, attempt, f'the target moved before the send: {exc}', now, sent=False)
        except asyncio.CancelledError:
            await self._abandon(store, task, attempt, 'interrupted by the owner after the send', now)
            raise
        except asyncio.TimeoutError:
            return await self._abandon(store, task, attempt, 'the send timed out; its outcome is unknown', now)
        except Exception:  # noqa: BLE001 - the adapter's failure is logged without its payload
            log.warning('adapter mutation failed for task %s', task.id, exc_info=True)
            return await self._abandon(store, task, attempt, 'the send failed; its outcome is unknown', now)
        stamp = self._after(now)
        try:
            current = await store.observe(owner, attempt, result.evidence, now=stamp)
            await store.checkpoint(owner, current.id, current.revision, plan.verify, eligible_at=stamp, now=stamp, records=result.records)
            return StepReport(task.id, 'dispatched', f'sent; verifying by {plan.verify.operation}')
        except (TaskConflict, ValueError, TaskLimit) as exc:
            log.warning('mutation outcome refused for task %s: %s', task.id, exc)
            return await self._abandon(store, task, attempt, 'the send returned an outcome the store refused; its effect is unknown', now)

    async def _reconcile(self, store: TaskStore, task: Task, attempt: Attempt, intent: Intent, now: float) -> StepReport:
        """Ask the adapter what became of an effect nobody saw land; never resend."""
        owner = self._config.owner
        adapter = self._by_operation.get(intent.operation)
        if adapter is None or not hasattr(adapter, 'reconcile'):
            return StepReport(task.id, 'waiting', f'no adapter can reconcile {intent.operation}')
        namespace = adapter.namespace
        try:
            records = await store.records(owner, namespace.name) if namespace is not None and store.namespace_supported(namespace.name) else ()
        except TaskStoreError:
            records = ()
        context = StepContext(task, attempt, records, now, self._extractor(store, attempt), False)
        try:
            outcome = await asyncio.wait_for(adapter.reconcile(context, intent), self._config.step_timeout_s)  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed read tells nothing; it is counted, not acted on
            log.warning('reconciliation read failed for task %s', task.id, exc_info=True)
            outcome = Reconciliation('unknown', detail=f'the reconciliation read failed: {type(exc).__name__}')
        stamp = self._after(now)
        try:
            if outcome.verdict in ('applied', 'not_applied'):
                verify = intent.recipe.get('verify')
                next_step = Step(verify['kind'], verify['operation'], verify['target'], tuple(tuple(p) for p in verify['arguments'])) \
                    if outcome.verdict == 'applied' and isinstance(verify, dict) else None
                after = await store.resolve(owner, attempt, outcome.verdict, outcome.evidence, by='adapter', now=stamp, next_step=next_step)
                return StepReport(task.id, 'reconciled', f'{outcome.verdict} ({after.status})')
            noted = await store.note_reconcile(owner, attempt, outcome.detail or 'the read could not tell', now=stamp)
            if noted.reconcile_reads >= self._config.max_reconcile_reads:
                prompt = (f'{task.specification.outcome}: I sent {intent.operation} on {intent.target} but never saw whether it landed, '
                          f'and {noted.reconcile_reads} checks could not tell. Did it happen?')
                await store.ask_reconciliation(owner, attempt, prompt, now=stamp)
                return StepReport(task.id, 'asked', prompt)
            return StepReport(task.id, 'waiting', outcome.detail or 'the read could not tell')
        except (TaskConflict, TaskLimit, ValueError) as exc:
            return StepReport(task.id, 'stale', str(exc))

    def _after(self, now: float) -> float:
        """A stamp no earlier than the step's own clock: a caller may run the
        runner ahead of wall time, and the store will not take evidence
        observed after the moment it is recorded."""
        return max(now, self._clock())

    async def _abandon(self, store: TaskStore, task: Task, attempt: Attempt, detail: str, now: float, *, sent: bool = True) -> StepReport:
        stamp = self._after(now)
        try:
            after = await store.abandon(self._config.owner, attempt, detail, eligible_at=stamp + self._config.retry_backoff_s, now=stamp, sent=sent)
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


class TaskNotifier:
    """What the store owes the owner, handed to Vigil one event at a time.

    The store decides what is owed; Vigil decides when and where it is
    said, with its presence, quiet hours, and budgets; this class only
    carries the one to the other and writes down what came back. An event
    is pushed once per owed notice per delivery attempt, so a failed
    delivery is offered again after its retry time and a delivered one is
    never offered twice. The notice switch is read from the store on every
    poll: muted means nothing is pushed, and nothing else changes.
    """

    def __init__(self, config: TasksConfig, store: Callable[[], TaskStore | None], events: Callable[[], EventQueue | None], *,
                 clock: Callable[[], float] = time.time) -> None:
        self._config = config
        self._store = store
        self._events = events
        """Vigil's queue, looked up on every poll: it is built after the
        controller and absent when Vigil is off."""
        self._clock = clock
        self._poll: asyncio.Task[None] | None = None
        self._next_poll = 0.0
        self.pushed: list[str] = []
        """Notice ids handed to Vigil, newest last, for the owner view and probes."""

    def poll(self, now: float) -> None:
        """At most every few seconds, off the loop's critical path."""
        if self._events() is None or self._store() is None:
            return
        if self._poll is not None and not self._poll.done():
            return
        if now < self._next_poll:
            return
        self._next_poll = now + _NOTIFY_POLL_S
        self._poll = asyncio.create_task(self.poll_now(now))

    async def poll_now(self, now: float | None = None) -> int:
        """Offer every owed notice to Vigil; returns how many were new to it."""
        store, events = self._store(), self._events()
        if store is None or events is None:
            return 0
        stamp = self._clock() if now is None else now
        try:
            if not await store.notify_enabled(self._config.owner):
                return 0
            owed = await store.undelivered(self._config.owner, now=stamp)
        except (TaskStoreError, TaskConflict, ValueError):
            log.debug('owed notices could not be read', exc_info=True)
            return 0
        pushed = 0
        for notice, attempts in owed:
            event = ProactiveEvent(
                id=events.next_id(), source='task', importance=3 if notice.status == 'failed' else 2,
                created_at=stamp, expires_at=None, summary=_notice_summary(notice),
                dedupe_key=f'task:{notice.id}:{attempts + 1}',
                payload={'notice_id': notice.id, 'task_id': notice.task_id},
            )
            if events.push(event):
                self.pushed.append(notice.id)
                del self.pushed[:-32]
                pushed += 1
        return pushed + await self._ask(store, events, stamp)

    async def _ask(self, store: TaskStore, events: EventQueue, stamp: float) -> int:
        """A feature's questions go to Vigil as news — importance one, held
        for the next conversation, never spoken into a room — at most
        ``max_held_questions`` waiting at once; each is marked asked on its
        record the moment Vigil takes it, so it is put once."""
        try:
            owed = await store.owed_questions(self._config.owner)
        except (TaskStoreError, ValueError):
            log.debug('owed questions could not be read', exc_info=True)
            return 0
        pushed = 0
        for record in owed:
            # Its own source, so the cap counts questions and not the
            # task notices that share the queue.
            if events.count_source('question') >= self._config.max_held_questions:
                break
            event = ProactiveEvent(
                id=events.next_id(), source='question', importance=1, created_at=stamp, expires_at=None,
                summary=str(record.payload['question']), dedupe_key=f'ask:{record.namespace}:{record.key}',
                payload={'question': record.key, 'namespace': record.namespace},
            )
            if not events.push(event):
                continue
            try:
                await store.write_records(self._config.owner, RecordSet(record.namespace, (
                    RecordWrite(record.key, {**record.payload, 'asked_at': stamp}, record.revision),)))
            except (TaskStoreError, TaskConflict, ValueError):
                log.warning('a question was handed to Vigil but could not be marked asked', exc_info=True)
            self.pushed.append(record.key)
            del self.pushed[:-32]
            pushed += 1
        return pushed

    async def delivered(self, event: ProactiveEvent, destination: str) -> None:
        """Vigil says the lane took it; the attempt is on the record."""
        await self._record(event, destination, 'sent', '')

    async def failed(self, event: ProactiveEvent, destination: str, detail: str) -> None:
        await self._record(event, destination, 'failed', detail)

    async def _record(self, event: ProactiveEvent, destination: str, outcome: Literal['sent', 'failed'], detail: str) -> None:
        store = self._store()
        notice_id = event.payload.get('notice_id')
        if store is None or event.source != 'task' or not notice_id:
            return
        try:
            await store.record_delivery(self._config.owner, notice_id, destination, outcome, detail or outcome,
                                        now=self._clock(), retry_after_s=self._config.notice_retry_s)
        except (TaskStoreError, TaskConflict, ValueError):
            log.warning('a task notice delivery could not be recorded', exc_info=True)

    async def close(self) -> None:
        if self._poll is not None and not self._poll.done():
            self._poll.cancel()
            try:
                await self._poll
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - closing
                pass


_NOTIFY_POLL_S = 5.0
"""Between looks at what is owed; a notice noticed within five seconds reads as prompt."""


def _notice_summary(notice: Notice) -> str:
    """One spoken-English line: the outcome the owner asked for, and where it stands."""
    outcome = notice.outcome.rstrip('.')
    if notice.status == 'done':
        return f'Done: {outcome}.'
    if notice.status == 'failed':
        return f'I could not finish this: {outcome}. {notice.detail.rstrip(".")}.'
    return f'A task needs you: {outcome}. {notice.detail.rstrip(".")}.'


__all__ = ['TaskNotifier', 'MutationResult', 'Outcome', 'Plan', 'PreconditionFailed', 'Preparation', 'Reconciliation', 'StepContext', 'StepReport',
           'TaskAdapter', 'TaskRunner', 'WritingAdapter']
