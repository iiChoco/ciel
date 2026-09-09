"""A task gets another turn, and only the turn the store allows it.

A synthetic adapter over a fake world drives the runner through one bounded
step at a time in a private temporary store. Pins: a read is observed, its
records committed, and its next step saved in one transition; the next step
sees those records; a matching world completes the task with a durable notice;
a restart resumes from the saved step; a step that outlives the owner's
cancellation writes nothing; a mutation step and an unserved operation wait
visibly instead of running; an adapter that raises, or a read past its
timeout, spends the attempt and requeues with backoff; the owner's voice
cancels an extraction holding the model turn, spends the attempt, and frees
the lease, while a plain read is left to finish; an exhausted attempt
allowance fails the task where the owner can see why; a model-call allowance
is spent before the call and never refunded; a question and an external wait
land as the owner would see them; an outcome the store refuses is abandoned,
not left running; readiness and aging are read without blocking; an
unsupported namespace keeps its task waiting and its records intact; and a
runtime with no extraction backend refuses rather than falls back. No model,
mic, network, or runtime state is used.

    uv run --no-sync python scripts/probe_task_runner.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator

from ciel.brain.extract import ExtractionLimits
from ciel.config import TasksConfig
from ciel.task_runner import Outcome, Preparation, StepContext, TaskRunner
from ciel.tasks import (Criterion, Evidence, FeatureRecord, Namespace, Origin, RecordSet, RecordWrite, Scope, Specification,
                        Step, Task, TaskStore)

CHECKS: list[str] = []
OWNER = 'fixture-owner'
TARGET = 'synthetic:thing'
SPEC = Specification('The thing reads done', Scope(('inspect', 'edit', 'extract', 'other'), (TARGET,)),
                     (Criterion('value', TARGET, 'done', 'rev-1'),))
READ = Step('read', 'inspect', TARGET)
WRITE = Step('mutation', 'edit', TARGET)
EXTRACT = Step('read', 'extract', TARGET)
OTHER = Step('read', 'other', TARGET)
SCHEMA = {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'string'}}, 'additionalProperties': False}


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class World:
    def __init__(self) -> None:
        self.value = 'pending'
        self.revision = 'rev-1'


class SyntheticAdapter:
    """Reads the fake world; keeps a cursor in its own records."""

    namespace = Namespace('synthetic', 1, lambda payload: None if isinstance(payload.get('seen'), int) else (_ for _ in ()).throw(ValueError('seen')))
    operations = frozenset({'inspect', 'edit', 'extract'})

    def __init__(self, world: World) -> None:
        self.world = world
        self.reads: list[tuple[str, tuple[str, ...]]] = []
        self.prepare_wait: tuple[str, str] | None = None
        self.fail: Exception | None = None
        self.sleep_s = 0.0
        self.outcome: Outcome | None = None
        self.expected_revision: int | None = None

    def prepare(self, task: Task, records: tuple[FeatureRecord, ...]) -> Preparation:
        return Preparation(wait=self.prepare_wait)  # type: ignore[arg-type]

    async def read(self, ctx: StepContext) -> Outcome:
        self.reads.append((ctx.task.id, tuple(r.key for r in ctx.records)))
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        if self.fail is not None:
            raise self.fail
        if ctx.task.next_step.operation == 'extract':
            data = await ctx.extract('Say what you see.', 'the payload', SCHEMA)
            value = data['value']
        else:
            value = self.world.value
        if self.outcome is not None:
            return self.outcome
        evidence = (Evidence('value', TARGET, value, 'synthetic', ctx.now, self.world.revision),)
        records = RecordSet('synthetic', (RecordWrite('cursor', {'seen': len(self.reads)}, self.expected_revision),))
        return Outcome(evidence=evidence, delay_s=5.0, records=records)


class FakeBackend:
    def __init__(self, result: dict[str, Any], delay_s: float = 0.0) -> None:
        self.result, self.delay_s, self.calls = result, delay_s, 0

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *, limits: ExtractionLimits) -> dict[str, Any]:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return dict(self.result)


class Fixture:
    def __init__(self, root: Path, name: str, **overrides: Any) -> None:
        self.config = replace(TasksConfig(), directory=root / name, owner=OWNER, retry_backoff_s=30.0, **overrides)
        self.world = World()
        self.adapter = SyntheticAdapter(self.world)
        self.store: TaskStore | None = None
        self.lock = asyncio.Lock()
        self.backend = FakeBackend({'value': 'extracted'})
        self.runner = TaskRunner(self.config, lambda: self.store, (self.adapter,), lease=self.lease, backend=self.backend)

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        async with self.lock:
            yield

    async def open(self, namespace: Namespace | None = None) -> TaskStore:
        store = TaskStore(self.config)
        store.register(namespace or self.adapter.namespace)
        await store.start()
        self.store = store
        return store

    async def close(self) -> None:
        if self.store is not None:
            await self.store.close()
            self.store = None

    async def task(self, name: str, step: Step = READ) -> Task:
        assert self.store is not None
        return await self.store.create(Origin(OWNER, name, 'typed'), SPEC, step)


async def probe_steps(root: Path) -> None:
    print('one step at a time')
    f = Fixture(root, 'steps')
    store = await f.open()
    task = await f.task('read-1')
    before = time.time()
    report = await f.runner.step()
    after = await store.get(OWNER, task.id)
    records = await store.records(OWNER, 'synthetic')
    check('an eligible read is observed, its records committed, and its next step saved in one transition',
          report is not None and report.result == 'checkpointed' and after.status == 'queued'
          and after.eligible_at >= before + 5.0 and [(r.key, r.revision, r.payload) for r in records] == [('cursor', 1, {'seen': 1})])
    check('a task that is not yet eligible gets no step', await f.runner.step() is None)
    report = await f.runner.step(now=after.eligible_at + 1)
    check('the next step sees the records the last one wrote', f.adapter.reads[-1][1] == ('cursor',) and report is not None and report.result == 'checkpointed')
    f.world.value = 'done'
    after = await store.get(OWNER, task.id)
    report = await f.runner.step(now=after.eligible_at + 1)
    done = await store.get(OWNER, task.id)
    check('when the world matches the criteria the step completes the task with a durable notice',
          report is not None and report.result == 'done' and done.status == 'done'
          and any(n.task_id == task.id and n.status == 'done' for n in await store.notices(OWNER)))
    check('the runner keeps a bounded log of what it did', [r.result for r in f.runner.reports] == ['checkpointed', 'checkpointed', 'done'])

    mutation = await f.task('write-1', WRITE)
    report = await f.runner.step()
    waiting = await store.get(OWNER, mutation.id)
    check('a mutation step waits for the dispatch phase that does not exist yet',
          report is not None and report.result == 'refused' and waiting.status == 'waiting' and waiting.wait_reason == 'resource'
          and 'mutation dispatch' in waiting.detail)
    unserved = await f.task('other-1', OTHER)
    report = await f.runner.step()
    waiting = await store.get(OWNER, unserved.id)
    check('an operation no adapter serves waits visibly', report is not None and report.result == 'refused'
          and waiting.status == 'waiting' and 'no adapter serves other' in waiting.detail)
    f.adapter.prepare_wait = ('resource', 'the world is not connected')
    prepared = await f.task('prepare-1')
    report = await f.runner.step()
    waiting = await store.get(OWNER, prepared.id)
    check('an adapter that says wait before reading gets a wait and no attempt',
          report is not None and report.result == 'refused' and waiting.status == 'waiting' and waiting.polls == 0)
    f.adapter.prepare_wait = None
    await f.close()


async def probe_restart(root: Path) -> None:
    print('\na restart resumes')
    f = Fixture(root, 'restart')
    store = await f.open()
    task = await f.task('read-1')
    await f.runner.step()
    saved = await store.get(OWNER, task.id)
    await f.close()
    store = await f.open()
    reopened = await store.get(OWNER, task.id)
    check('the saved step, its eligibility, and its records survive reopening',
          reopened.next_step == saved.next_step and reopened.eligible_at == saved.eligible_at
          and (await store.records(OWNER, 'synthetic'))[0].payload == {'seen': 1})
    report = await f.runner.step(now=reopened.eligible_at + 1)
    check('the runner resumes from the saved step, not the beginning', report is not None and report.result == 'checkpointed'
          and f.adapter.reads[-1][1] == ('cursor',) and (await store.records(OWNER, 'synthetic'))[0].payload == {'seen': 2})
    await f.close()


async def probe_fencing(root: Path) -> None:
    print('\nthe store fences every late result')
    f = Fixture(root, 'fencing')
    store = await f.open()
    task = await f.task('read-1')
    f.adapter.sleep_s = 0.2
    step = asyncio.create_task(f.runner.step())
    await asyncio.sleep(0.05)
    running = await store.get(OWNER, task.id)
    cancelled = await store.cancel(OWNER, task.id, running.revision)
    report = await step
    final = await store.get(OWNER, task.id)
    check('a step that outlives the owner\'s cancellation writes nothing',
          running.status == 'running' and cancelled.status == 'cancelled' and report is not None and report.result == 'stale'
          and final.status == 'cancelled' and final.revision == cancelled.revision
          and not await store.observations(OWNER, running.current_attempt or ''))
    f.adapter.sleep_s = 0.0
    await f.close()


async def probe_giving_up(root: Path) -> None:
    print('\ngiving up is recorded')
    f = Fixture(root, 'giving-up', max_attempts=2, step_timeout_s=0.1)
    store = await f.open()
    task = await f.task('read-1')
    f.adapter.fail = RuntimeError('the world is on fire')
    before = time.time()
    report = await f.runner.step()
    after = await store.get(OWNER, task.id)
    check('an adapter that raises spends the attempt and requeues with backoff',
          report is not None and report.result == 'abandoned' and after.status == 'queued' and after.attempts == 1 and after.polls == 1
          and after.eligible_at >= before + 30.0 - 0.001 and after.detail == 'the read failed'
          and (await store.attempts(OWNER, task.id))[-1].phase == 'interrupted')
    f.adapter.fail = None
    f.adapter.sleep_s = 0.5
    report = await f.runner.step(now=after.eligible_at + 1)
    failed = await store.get(OWNER, task.id)
    check('a read that outlives its timeout is abandoned, and an exhausted attempt allowance fails the task visibly',
          report is not None and report.result == 'abandoned' and failed.status == 'failed' and failed.attempts == 2
          and 'timed out' in failed.detail and 'attempt allowance exhausted' in failed.detail)
    f.adapter.sleep_s = 0.0
    f.adapter.expected_revision = 99
    stale = await f.task('stale-records')
    report = await f.runner.step()
    after = await store.get(OWNER, stale.id)
    check('an outcome the store refuses is abandoned, not left running',
          report is not None and report.result == 'abandoned' and after.status == 'queued' and after.attempts == 1
          and after.current_attempt is None)
    f.adapter.expected_revision = None
    await f.close()


async def probe_human_input(root: Path) -> None:
    print('\nhuman input wins')
    f = Fixture(root, 'human', max_model_calls=1)
    store = await f.open()
    task = await f.task('extract-1', EXTRACT)
    f.backend.delay_s = 1.0
    f.runner.refresh(time.time())
    await asyncio.sleep(0.05)
    check('readiness is read without blocking the loop', f.runner.ready and f.runner.aged(time.time() + 300, 300.0)
          and not f.runner.aged(time.time(), 300.0))
    started = f.runner.start_step(time.time())
    for _ in range(50):
        await asyncio.sleep(0.01)
        if f.runner._leased:
            break
    check('a started step holding the model turn holds the lease', started and f.runner._leased and f.lock.locked() and not f.runner.ready)
    f.runner.interrupt()
    results = await asyncio.gather(f.runner._step, return_exceptions=True)  # type: ignore[arg-type]
    after = await store.get(OWNER, task.id)
    check('the owner\'s voice cancels an extraction, spends the attempt, and frees the lease',
          isinstance(results[0], asyncio.CancelledError) and not f.lock.locked() and after.status == 'queued'
          and after.attempts == 1 and after.model_calls == 1 and after.detail == 'interrupted by the owner')
    f.backend.delay_s = 0.0
    report = await f.runner.step(now=after.eligible_at + 1)
    after = await store.get(OWNER, task.id)
    check('a model-call allowance is spent before the call and never refunded',
          report is not None and report.result == 'abandoned' and 'model-call allowance exhausted' in after.detail
          and after.model_calls == 1 and f.backend.calls == 1)

    plain = await f.task('read-1')
    f.adapter.sleep_s = 0.2
    step = asyncio.create_task(f.runner.step())
    await asyncio.sleep(0.05)
    f.runner.interrupt()
    report = await step
    check('a plain read holding no lease is left to finish when the owner speaks',
          report is not None and report.result == 'checkpointed' and (await store.get(OWNER, plain.id)).attempts == 0)
    f.adapter.sleep_s = 0.0
    check('start_step claims nothing when nothing is eligible', not f.runner.start_step(time.time()))
    await f.close()


async def probe_outcomes(root: Path) -> None:
    print('\nwhat the owner sees')
    f = Fixture(root, 'outcomes')
    store = await f.open()
    task = await f.task('ask-1')
    f.adapter.outcome = Outcome(question=('Which thing?', ('this', 'that')))
    report = await f.runner.step()
    view = await store.owner_view(OWNER, task.id)
    check('a question from the adapter waits on the owner with exact choices',
          report is not None and report.result == 'asked' and view['task']['status'] == 'waiting' and view['task']['wait_reason'] == 'owner'
          and view['question']['choices'] == ['this', 'that'])
    other = await f.task('wait-1')
    f.adapter.outcome = Outcome(wait=('external', 'the checks are still running'),
                                records=RecordSet('synthetic', (RecordWrite('note', {'seen': 7}),)))
    report = await f.runner.step()
    waiting = await store.get(OWNER, other.id)
    check('an external wait keeps the step\'s records', report is not None and report.result == 'waiting'
          and waiting.status == 'waiting' and waiting.wait_reason == 'external'
          and any(r.key == 'note' and r.payload == {'seen': 7} for r in await store.records(OWNER, 'synthetic')))
    f.adapter.outcome = None
    await f.close()


async def probe_namespaces(root: Path) -> None:
    print('\nrecords the runtime cannot read')
    f = Fixture(root, 'namespaces')
    store = await f.open()
    task = await f.task('read-1')
    await f.runner.step()
    await f.close()
    store = await f.open(Namespace('synthetic', 2, lambda payload: None))
    saved = await store.get(OWNER, task.id)
    report = await f.runner.step(now=saved.eligible_at + 1)
    after = await store.get(OWNER, task.id)
    check('an unsupported namespace keeps its task waiting and its records intact',
          not store.namespace_supported('synthetic') and report is not None and report.result == 'refused'
          and after.status == 'waiting' and 'unsupported' in after.detail)
    await f.close()
    store = await f.open()
    check('the same records read again once the runtime understands them',
          store.namespace_supported('synthetic') and (await store.records(OWNER, 'synthetic'))[0].payload == {'seen': 1})
    await f.close()

    bare = Fixture(root, 'no-backend')
    bare.runner = TaskRunner(bare.config, lambda: bare.store, (bare.adapter,))
    store = await bare.open()
    task = await bare.task('extract-1', EXTRACT)
    report = await bare.runner.step()
    after = await store.get(OWNER, task.id)
    check('a runtime with no extraction backend refuses rather than falls back',
          report is not None and report.result == 'abandoned' and 'not configured' in after.detail and after.model_calls == 0)
    await bare.close()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-runner-probe-') as tmp:
        root = Path(tmp)
        await probe_steps(root)
        await probe_restart(root)
        await probe_fencing(root)
        await probe_giving_up(root)
        await probe_human_input(root)
        await probe_outcomes(root)
        await probe_namespaces(root)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    asyncio.run(main())
