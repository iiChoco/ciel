"""An interrupted action can be understood.

A synthetic writing adapter over a file-backed fake remote drives the runner
through mutations in a private temporary store. Pins: a mutation under a
grant is planned, journaled, authorized, sent once, and verified by a read
before it completes, its intent resolved by the provider and its journal
entry correlated; a lost response leaves an outcome nobody knows, and
reconciliation finds the effect and never resends it; a target that moved
between the plan and the send is an unsent attempt, planned again under a
fresh precondition; a send past its timeout reconciles as not applied and
is sent again, once; a reconciliation that cannot tell is counted, and
after the configured reads the owner is asked, whose word resolves it
either way; a task the owner cancelled while uncertain records the effect
recovery found without reviving; a grant revoked after the send still lets
the effect be reconciled read-only and refuses the next send; a runtime
with no journal sends nothing; a passed deadline sends nothing; a human
task with no standing authority asks the owner for this exact payload, an
approve answer dispatches it once and a cancel ends it, and a changed
payload asks again; an owner's edit after the effect is preserved, never
overwritten by a retry; a process killed after the send and one killed
before it both recover to exactly one effect; and intents validate after
reopening. No model, mic, network, or runtime state is used.

    uv run --no-sync python scripts/probe_task_dispatch.py
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ciel.config import JournalConfig, TasksConfig
from ciel.journal import ActionJournal
from ciel.task_runner import MutationResult, Outcome, Plan, PreconditionFailed, Preparation, Reconciliation, StepContext, TaskRunner
from ciel.tasks import (Criterion, Evidence, GrantLimits, HumanOrigin, Namespace, Origin, Scope, Specification, Step, Task, TaskConflict,
                        TaskStore)

CHECKS: list[str] = []
OWNER = 'fixture-owner'
TARGET = 'remote:thing'
NAMESPACE = Namespace('writer', 1, lambda payload: None)
SPEC = Specification('The thing reads done', Scope(('edit', 'inspect'), (TARGET,)), (Criterion('value', TARGET, 'done', 'v2'),))
EDIT = Step('mutation', 'edit', TARGET, (('value', 'done'),))
LOOK = Step('read', 'inspect', TARGET)
TURN = HumanOrigin(OWNER, 'activation', 'web', ingress_ids=('web:1',))


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class Remote:
    """The fake service: a file, so a killed process leaves its effect behind."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {'value': 'pending', 'version': 'v1', 'applied': [], 'effects': 0}
        return json.loads(self.path.read_text())

    def write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data))


class Writer:
    """Plans, sends, and reconciles against the fake remote; its modes are the failures."""

    namespace = NAMESPACE
    operations = frozenset({'edit', 'inspect'})

    def __init__(self, remote: Remote, mode: str = 'normal') -> None:
        self.remote = remote
        self.mode = mode
        self.plans = 0
        self.sends = 0
        self.reconciles = 0
        self.payload_value = 'done'

    def prepare(self, task: Task, records: tuple) -> Preparation:
        return Preparation()

    def _evidence(self, now: float) -> tuple[Evidence, ...]:
        r = self.remote.read()
        return (Evidence('value', TARGET, r['value'], 'writer', now, r['version']),)

    async def read(self, ctx: StepContext) -> Outcome:
        return Outcome(evidence=self._evidence(ctx.now), delay_s=5.0)

    async def plan(self, ctx: StepContext) -> Plan:
        self.plans += 1
        r = self.remote.read()
        return Plan({'value': self.payload_value}, {'version': r['version']}, {'key': ctx.attempt.id}, LOOK)

    async def mutate(self, ctx: StepContext, intent: Any, plan: Plan) -> MutationResult:
        self.sends += 1
        if self.mode == 'moved':
            r = self.remote.read()
            r['version'] = 'v1b'
            self.remote.write(r)
            self.mode = 'normal'
        r = self.remote.read()
        if r['version'] != plan.preconditions['version']:
            raise PreconditionFailed(f"version {r['version']} is not {plan.preconditions['version']}")
        if self.mode == 'kill-before-send':
            os.kill(os.getpid(), signal.SIGKILL)
        if self.mode == 'timeout':
            await asyncio.sleep(0.5)
        if intent.action_id not in r['applied']:
            r['applied'].append(intent.action_id)
            r['value'] = plan.payload['value']
            r['effects'] += 1
            r['version'] = 'v2'
            self.remote.write(r)
        if self.mode == 'kill-after-send':
            os.kill(os.getpid(), signal.SIGKILL)
        if self.mode in ('lose-response', 'blind'):
            raise RuntimeError('the response was lost')
        return MutationResult(evidence=self._evidence(ctx.now))

    async def reconcile(self, ctx: StepContext, intent: Any) -> Reconciliation:
        self.reconciles += 1
        if self.mode == 'blind':
            return Reconciliation('unknown', detail='the service cannot say')
        r = self.remote.read()
        if intent.action_id in r['applied']:
            return Reconciliation('applied', evidence=self._evidence(ctx.now))
        return Reconciliation('not_applied')


class Fixture:
    def __init__(self, root: Path, name: str, mode: str = 'normal', journal: bool = True, **overrides: Any) -> None:
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = replace(TasksConfig(), directory=self.root / 'tasks', owner=OWNER, retry_backoff_s=1.0, mutation_timeout_s=0.2,
                              step_timeout_s=5.0, **overrides)
        self.remote = Remote(self.root / 'remote.json')
        self.adapter = Writer(self.remote, mode)
        self.journal = ActionJournal(JournalConfig(dir=self.root / 'journal')) if journal else None
        self.store: TaskStore | None = None
        self.clock = 1000.0
        """The probe's clock: the runner stamps nothing later than the step it was given."""
        self.runner = TaskRunner(self.config, lambda: self.store, (self.adapter,), journal=self.journal, clock=lambda: self.clock)

    async def open(self) -> TaskStore:
        store = TaskStore(self.config)
        store.register(NAMESPACE)
        await store.start()
        self.store = store
        return store

    async def close(self) -> None:
        if self.store is not None:
            await self.store.close()
            self.store = None

    async def granted(self, name: str, now: float = 1000.0) -> tuple[Task, Any, Any]:
        """A derived child under a fresh grant covering edit and inspect on the target."""
        assert self.store is not None
        draft = await self.store.save_grant_draft(OWNER, 'hub', NAMESPACE.name, 'Things read done', Scope(('edit', 'inspect'), (TARGET,)),
                                                  GrantLimits(8, 3600.0, 8, 86400.0), now=now)
        grant, mandate = await self.store.activate_grant(TURN, draft.id, draft.revision, draft.digest, f'journal:{name}', now=now)
        child = await self.store.derive_task(OWNER, mandate.id, mandate.revision, NAMESPACE.name, name, 'src-1', SPEC, EDIT, now=now)
        return child, grant, mandate

    async def run_until(self, task_id: str, statuses: tuple[str, ...], limit: int = 12, start: float = 1001.0) -> list[str]:
        """Steps, each at a later clock, until the task reaches one of the statuses."""
        assert self.store is not None
        results = []
        now = start
        for _ in range(limit):
            # The store stamps a saved step's eligibility on the wall clock;
            # the probe's clock steps up to it rather than pretending otherwise.
            current = await self.store.get(OWNER, task_id)
            now = max(now, current.eligible_at + 1)
            self.clock = now
            report = await self.runner.step(now=now)
            results.append(report.result if report is not None else 'none')
            if (await self.store.get(OWNER, task_id)).status in statuses:
                break
            now += 10.0
        return results


async def probe_grant_path(root: Path) -> None:
    print('a mutation under a grant, sent once and verified by a read')
    f = Fixture(root, 'grant')
    store = await f.open()
    child, grant, mandate = await f.granted('evt-1')
    report = await f.runner.step(now=1001)
    after = await store.get(OWNER, child.id)
    intents = await store.intents(OWNER, child.id)
    check('the mutation is planned, journaled, authorized under the grant, sent, and the task goes on to its verifying read',
          report is not None and report.result == 'dispatched' and after.status == 'queued' and after.next_step == LOOK
          and len(intents) == 1 and intents[0].authority.kind == 'grant' and intents[0].authority.grant_revision == grant.revision
          and intents[0].resolution == 'applied' and intents[0].resolved_by == 'provider')
    assert f.journal is not None
    entry = f.journal.recent(1)[0]
    check('the intent names the journal entry written before the send, and the entry names the digests',
          entry['ref'] == intents[0].journal_ref and entry['args']['payload_digest'] == intents[0].payload_digest and 'before the send' in entry['note'])
    report = await f.runner.step(now=after.eligible_at + 1)
    done = await store.get(OWNER, child.id)
    check('the verifying read completes the task; the effect happened exactly once',
          report is not None and report.result == 'done' and done.status == 'done' and f.remote.read()['effects'] == 1 and f.adapter.sends == 1)
    await f.close()


async def probe_uncertain(root: Path) -> None:
    print('\nan outcome nobody knows is reconciled, never resent')
    f = Fixture(root, 'lost', mode='lose-response')
    store = await f.open()
    child, grant, mandate = await f.granted('evt-1')
    report = await f.runner.step(now=1001)
    waiting = await store.get(OWNER, child.id)
    intent = (await store.intents(OWNER, child.id))[0]
    check('a lost response leaves the task waiting for reconciliation with its intent unresolved',
          report is not None and report.result == 'abandoned' and waiting.status == 'waiting' and waiting.wait_reason == 'reconciliation'
          and intent.resolution is None and f.remote.read()['effects'] == 1)
    f.runner.refresh(1002)
    await asyncio.sleep(0.05)
    check('the runner is ready for the reconciliation, not a new step', f.runner.ready and f.runner.aged(time.time() + 301, 300))
    f.adapter.mode = 'normal'
    report = await f.runner.step(now=1002)
    after = await store.get(OWNER, child.id)
    intent = (await store.intents(OWNER, child.id))[0]
    check('reconciliation finds the effect, resolves the intent as applied by the adapter, and queues the verifying read',
          report is not None and report.result == 'reconciled' and after.status == 'queued' and after.next_step == LOOK
          and intent.resolution == 'applied' and intent.resolved_by == 'adapter' and f.adapter.sends == 1)
    report = await f.runner.step(now=after.eligible_at + 1)
    check('the read completes it with the one effect', report is not None and report.result == 'done' and f.remote.read()['effects'] == 1)

    f2 = Fixture(root, 'moved', mode='moved')
    store = await f2.open()
    child, _, _ = await f2.granted('evt-1')
    report = await f2.runner.step(now=1001)
    after = await store.get(OWNER, child.id)
    intent = (await store.intents(OWNER, child.id))[0]
    check('a target that moved between the plan and the send is an unsent attempt: nothing happened, the intent is closed, the mutation is planned again',
          report is not None and report.result == 'abandoned' and after.status == 'queued' and after.next_step == EDIT and after.wait_reason is None
          and intent.resolution == 'not_applied' and intent.resolved_by == 'runner' and f2.remote.read()['effects'] == 0)
    results = await f2.run_until(child.id, ('done',), start=after.eligible_at + 1)
    check('the second plan carries the new precondition and the send lands once', results[:2] == ['dispatched', 'done'] and f2.remote.read()['effects'] == 1
          and len(await store.intents(OWNER, child.id)) == 2)
    await f2.close()

    f3 = Fixture(root, 'timeout', mode='timeout')
    store = await f3.open()
    child, _, _ = await f3.granted('evt-1')
    report = await f3.runner.step(now=1001)
    waiting = await store.get(OWNER, child.id)
    check('a send past its timeout is an outcome nobody knows', report is not None and report.result == 'abandoned' and waiting.wait_reason == 'reconciliation')
    f3.adapter.mode = 'normal'
    results = await f3.run_until(child.id, ('done',), start=1002)
    intents = await store.intents(OWNER, child.id)
    check('reconciliation finds the timed-out send never landed, and it is sent again, once, under a fresh intent',
          results[0] == 'reconciled' and results[-1] == 'done' and f3.remote.read()['effects'] == 1 and f3.adapter.sends == 2
          and [i.resolution for i in intents] == ['not_applied', 'applied'])
    await f3.close()
    await f.close()


async def probe_owner_word(root: Path) -> None:
    print('\nwhen nothing can tell, the owner is asked')
    f = Fixture(root, 'blind', mode='blind', max_reconcile_reads=2)
    store = await f.open()
    child, _, _ = await f.granted('evt-1')
    await f.runner.step(now=1001)
    first = await f.runner.step(now=1002)
    intent = (await store.intents(OWNER, child.id))[0]
    check('a reconciliation that cannot tell is counted and the wait kept', first is not None and first.result == 'waiting' and intent.reconcile_reads == 1)
    second = await f.runner.step(now=1003)
    view = await store.owner_view(OWNER, child.id)
    check('after the configured reads the owner is asked, in two exact words',
          second is not None and second.result == 'asked' and view['question']['choices'] == ['it happened', 'it did not happen']
          and view['task']['wait_reason'] == 'reconciliation')
    check('nothing new is dispatched while the question waits', (await f.runner.step(now=1004)) is None or f.adapter.sends == 1)
    await store.owner_control(OWNER, child.id, 'answer', question_id=view['question']['id'], answer='it happened', execution=True, now=1005)
    after = await store.get(OWNER, child.id)
    intent = (await store.intents(OWNER, child.id))[0]
    check('the owner\'s word resolves the intent as applied and queues the verifying read',
          after.status == 'queued' and after.next_step == LOOK and intent.resolution == 'applied' and intent.resolved_by == 'owner')
    f.adapter.mode = 'normal'
    report = await f.runner.step(now=after.eligible_at + 1)
    check('the read then completes it', report is not None and report.result == 'done' and f.remote.read()['effects'] == 1)

    f2 = Fixture(root, 'blind-no', mode='blind', max_reconcile_reads=1)
    store = await f2.open()
    child, _, _ = await f2.granted('evt-1')
    await f2.runner.step(now=1001)
    asked = await f2.runner.step(now=1002)
    question = (await store.owner_view(OWNER, child.id))['question']['id']
    await store.owner_control(OWNER, child.id, 'answer', question_id=question, answer='it did not happen', execution=True, now=1003)
    after = await store.get(OWNER, child.id)
    check('the owner saying it did not happen reopens the mutation for a fresh plan', asked is not None and asked.result == 'asked'
          and after.status == 'queued' and after.next_step == EDIT and (await store.intents(OWNER, child.id))[0].resolution == 'not_applied')
    await f2.close()

    f3 = Fixture(root, 'cancelled', mode='lose-response')
    store = await f3.open()
    child, _, _ = await f3.granted('evt-1')
    await f3.runner.step(now=1001)
    cancelled = await store.cancel(OWNER, child.id, (await store.get(OWNER, child.id)).revision, now=1002)
    f3.adapter.mode = 'normal'
    report = await f3.runner.step(now=1003)
    after = await store.get(OWNER, child.id)
    intent = (await store.intents(OWNER, child.id))[0]
    check('a task cancelled while uncertain still has its effect reconciled onto the record, and stays cancelled',
          cancelled.status == 'cancelled' and report is not None and report.result == 'reconciled' and after.status == 'cancelled'
          and intent.resolution == 'applied' and 'reconciled while cancelled' in after.detail)
    check('the cancelled task is not picked again', await f3.runner.step(now=1004) is None)
    await f3.close()
    await f.close()


async def probe_authority_edges(root: Path) -> None:
    print('\nauthority is checked at the send, every time')
    f = Fixture(root, 'revoked', mode='lose-response')
    store = await f.open()
    child, grant, mandate = await f.granted('evt-1')
    await f.runner.step(now=1001)
    await store.revoke_grant(OWNER, grant.id, now=1002)
    f.adapter.mode = 'normal'
    report = await f.runner.step(now=1003)
    check('a grant revoked after the send still lets the effect be reconciled, read-only',
          report is not None and report.result == 'reconciled' and (await store.get(OWNER, child.id)).next_step == LOOK)
    await f.close()
    f = Fixture(root, 'revoked-unsent', mode='timeout')
    store = await f.open()
    child, grant, mandate = await f.granted('evt-1')
    await f.runner.step(now=1001)
    await store.revoke_grant(OWNER, grant.id, now=1002)
    f.adapter.mode = 'normal'
    report = await f.runner.step(now=1003)
    reopened = await store.get(OWNER, child.id)
    check('a send that never landed reconciles as not applied even after the revocation, and the mutation is planned again',
          report is not None and report.result == 'reconciled' and reopened.status == 'queued' and reopened.next_step == EDIT)
    report = await f.runner.step(now=reopened.eligible_at + 1)
    parked = await store.get(OWNER, child.id)
    check('under the revoked grant the next send is refused before anything is sent, and the task waits on the world',
          report is not None and report.result == 'waiting' and parked.status == 'waiting' and parked.wait_reason == 'external'
          and 'no longer covers' in parked.detail and f.remote.read()['effects'] == 0 and len(await store.intents(OWNER, child.id)) == 1)
    await f.close()

    f2 = Fixture(root, 'no-journal', journal=False)
    store = await f2.open()
    child, _, _ = await f2.granted('evt-1')
    report = await f2.runner.step(now=1001)
    check('a runtime with no journal sends nothing', report is not None and report.result == 'abandoned' and f2.remote.read()['effects'] == 0
          and f2.adapter.sends == 0 and not await store.intents(OWNER, child.id))
    await f2.close()

    f3 = Fixture(root, 'deadline', dispatch_deadline_s=0.0)
    store = await f3.open()
    child, _, _ = await f3.granted('evt-1')
    report = await f3.runner.step(now=1001)
    intent = (await store.intents(OWNER, child.id))[0]
    check('a passed deadline sends nothing and closes the intent as not applied', report is not None and report.result == 'abandoned'
          and f3.adapter.sends == 0 and intent.resolution == 'not_applied' and 'deadline' in (await store.get(OWNER, child.id)).detail)
    await f3.close()


async def probe_owner_approval(root: Path) -> None:
    print('\na human task with no standing authority asks for this exact action')
    f = Fixture(root, 'approval')
    store = await f.open()
    task = await store.create(Origin(OWNER, 'request-1', 'typed'), SPEC, EDIT, now=1000)
    report = await f.runner.step(now=1001)
    view = await store.owner_view(OWNER, task.id)
    check('the runner plans the payload and asks the owner to approve it, sending nothing',
          report is not None and report.result == 'asked' and view['question']['choices'] == ['approve', 'cancel'] and view['task']['wait_reason'] == 'owner'
          and f.adapter.sends == 0 and view['task']['attempts'] == 0)
    question = view['question']['id']
    f.adapter.payload_value = 'other'
    await store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='approve', execution=True, now=1002)
    report = await f.runner.step(now=1003)
    view = await store.owner_view(OWNER, task.id)
    check('an approval covers only the payload it was asked for; a changed payload asks again',
          report is not None and report.result == 'asked' and view['question']['id'] != question and f.adapter.sends == 0)
    f.adapter.payload_value = 'done'
    await store.owner_control(OWNER, task.id, 'answer', question_id=view['question']['id'], answer='cancel', execution=True, now=1004)
    check('cancel ends the task', (await store.get(OWNER, task.id)).status == 'cancelled')
    task = await store.create(Origin(OWNER, 'request-2', 'typed'), SPEC, EDIT, now=1010)
    await f.runner.step(now=1011)
    question = (await store.owner_view(OWNER, task.id))['question']['id']
    await store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='approve', execution=True, now=1012)
    results = await f.run_until(task.id, ('done',), start=1013)
    intents = await store.intents(OWNER, task.id)
    check('approve dispatches it once under that approval and the read completes it',
          results[:2] == ['dispatched', 'done'] and len(intents) == 1 and intents[0].authority.kind == 'approval'
          and intents[0].authority.approval_ref == f'question:{question}' and f.remote.read()['effects'] == 1)
    await f.close()


async def probe_owner_edit(root: Path) -> None:
    print('\nan owner\'s edit is preserved')
    f = Fixture(root, 'owner-edit')
    store = await f.open()
    child, _, _ = await f.granted('evt-1')
    await f.runner.step(now=1001)
    r = f.remote.read()
    r.update(value='mine', version='v3')
    f.remote.write(r)
    results = await f.run_until(child.id, ('done',), limit=3, start=1002)
    after = await store.get(OWNER, child.id)
    check('a value the owner changed after the effect is read, not overwritten: no completion, no second send',
          results == ['checkpointed', 'checkpointed', 'checkpointed'] and after.status == 'queued' and after.next_step == LOOK
          and f.remote.read()['value'] == 'mine' and f.remote.read()['effects'] == 1 and f.adapter.sends == 1)
    await f.close()


async def child(root: Path, mode: str) -> None:
    f = Fixture(root, mode, mode=mode)
    await f.open()
    await f.granted('evt-1')
    await f.runner.step(now=1001)


async def probe_kills(root: Path) -> None:
    print('\na killed process recovers to exactly one effect')
    for mode in ('kill-after-send', 'kill-before-send'):
        result = await asyncio.to_thread(subprocess.run, [sys.executable, __file__, '--child', str(root), mode], capture_output=True, text=True, timeout=20)
        check(f'{mode}: the fixture actually dies without clean shutdown', result.returncode == -signal.SIGKILL)
        f = Fixture(root, mode)
        store = await f.open()
        task = (await store.list(OWNER))[0]
        intent = (await store.intents(OWNER, task.id))[0]
        check(f'{mode}: recovery marks the outcome unknown with the intent on the record',
              task.status == 'waiting' and task.wait_reason == 'reconciliation' and intent.resolution is None)
        results = await f.run_until(task.id, ('done',), start=1002)
        check(f'{mode}: reconciliation reads the remote and the task finishes with exactly one effect',
              results[0] == 'reconciled' and results[-1] == 'done' and f.remote.read()['effects'] == 1
              and f.adapter.sends == (0 if mode == 'kill-after-send' else 1))
        await f.close()
        async with TaskStore(replace(f.config)) as reopened:
            reopened_task = await reopened.get(OWNER, task.id)
        check(f'{mode}: intents and their resolutions validate after reopening', reopened_task.status == 'done')


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-dispatch-probe-') as tmp:
        root = Path(tmp)
        await probe_grant_path(root)
        await probe_uncertain(root)
        await probe_owner_word(root)
        await probe_authority_edges(root)
        await probe_owner_approval(root)
        await probe_owner_edit(root)
        await probe_kills(root)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--child':
        asyncio.run(child(Path(sys.argv[2]), sys.argv[3]))
    else:
        asyncio.run(main())
