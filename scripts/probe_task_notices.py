"""A result is owed until the owner has seen it.

A private temporary store, a fake Vigil queue, and no model, mic, network,
or runtime state. Pins: a completion's notice is owed after the store is
reopened, so a crash before notification loses nothing; a question the task
is waiting on is owed while it is current and not once answered; a
cancellation and a resource wait are not owed; the notifier offers each owed
notice to Vigil exactly once per delivery attempt and never a delivered one
again; a delivery recorded as sent stops the offers, a failed one is offered
again only after its retry time; the owner's look at the task on a private
lane is the receipt for every notice it owed, and looking twice is free;
the notice switch is persisted, silences the notifier, changes no task's
state, and lets the runner keep stepping; a public lane can neither look nor
switch; the event Vigil gets carries the outcome in spoken English and only
identifiers in its payload; the detail view shows authority, unresolved
effects, and notices with their deliveries; a version-six store is lifted
with every notice it held still owed; and everything validates after
reopening.

    uv run --no-sync python scripts/probe_task_notices.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ciel.config import JournalConfig, TasksConfig
from ciel.journal import ActionJournal
from ciel.proactive.events import ProactiveEvent
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.task_runner import Outcome, Preparation, StepContext, TaskNotifier, TaskRunner
from ciel.tasks import Criterion, Evidence, Namespace, Origin, Scope, Specification, Step, Task, TaskConflict, TaskStore

CHECKS: list[str] = []
OWNER = 'fixture-owner'
TARGET = 'fixture:thing'
SPEC = Specification('The thing reads done', Scope(('inspect',), (TARGET,)), (Criterion('value', TARGET, 'done', 'rev-1'),))
READ = Step('read', 'inspect', TARGET)


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def refused(name: str, call: object, kind: type[Exception] = TaskConflict) -> None:
    try:
        await call
    except kind:
        check(name, True)
    else:
        check(name, False)


def config(directory: Path, **kwargs: object) -> TasksConfig:
    return replace(TasksConfig(), directory=directory, owner=OWNER, **kwargs)


class FakeQueue:
    """Vigil's queue as the notifier sees it: ids, a dedupe map, and a list."""

    def __init__(self) -> None:
        self.events: list[ProactiveEvent] = []
        self.keys: set[str] = set()
        self._n = 0

    def next_id(self) -> str:
        self._n += 1
        return f'e{self._n}'

    def push(self, event: ProactiveEvent) -> bool:
        if event.dedupe_key in self.keys:
            return False
        self.keys.add(event.dedupe_key)
        self.events.append(event)
        return True


async def finished(store: TaskStore, name: str, now: float) -> Task:
    task = await store.create(Origin(OWNER, name, 'voice'), SPEC, READ, now=now)
    attempt = await store.claim(OWNER, task.id, task.revision, now=now + 1)
    attempt = await store.mark_dispatched(OWNER, attempt, now=now + 1)
    seen = await store.observe(OWNER, attempt, (Evidence('value', TARGET, 'done', 'fixture', now + 2, 'rev-1'),), now=now + 2)
    return await store.complete(OWNER, seen.id, seen.revision, now=now + 3)


async def probe_owed(root: Path) -> None:
    print('what is owed')
    cfg = config(root / 'owed')
    async with TaskStore(cfg) as store:
        done = await finished(store, 'done-1', 100)
        asked = await store.create(Origin(OWNER, 'asked-1', 'voice'), SPEC, READ, now=110)
        asked = await store.ask_owner(OWNER, asked.id, asked.revision, 'Which one?', ('a', 'b'), now=111)
        cancelled = await store.create(Origin(OWNER, 'cancelled-1', 'voice'), SPEC, READ, now=120)
        await store.cancel(OWNER, cancelled.id, cancelled.revision, now=121)
        parked = await store.create(Origin(OWNER, 'parked-1', 'voice'), SPEC, READ, resource_wait=True, now=130)
    async with TaskStore(cfg) as store:
        owed = await store.undelivered(OWNER, now=200)
        check('after reopening, the completion and the open question are owed; the cancellation and the resource wait are not',
              [(n.task_id, n.status) for n, _ in owed] == [(done.id, 'done'), (asked.id, 'waiting')] and all(a == 0 for _, a in owed))
        question = (await store.owner_view(OWNER, asked.id))['question']['id']
        await store.owner_control(OWNER, asked.id, 'answer', question_id=question, answer='a', now=201)
        check('an answered question is no longer owed', [n.task_id for n, _ in await store.undelivered(OWNER, now=202)] == [done.id])
        notice = owed[0][0]
        failed = await store.record_delivery(OWNER, notice.id, 'message', 'failed', 'no lane', now=210, retry_after_s=100)
        check('a failed delivery keeps the notice owed, after its retry time',
              failed.attempt == 1 and failed.retry_at == 310 and not await store.undelivered(OWNER, now=250)
              and [n.id for n, a in await store.undelivered(OWNER, now=311)] == [notice.id] and (await store.undelivered(OWNER, now=311))[0][1] == 1)
        sent = await store.record_delivery(OWNER, notice.id, 'spoken', 'sent', now=320)
        check('a sent delivery ends the offers but is not yet a receipt',
              sent.attempt == 2 and not await store.undelivered(OWNER, now=400) and not any(d.outcome == 'acknowledged' for d in await store.deliveries(OWNER, notice.id)))
        received = await store.acknowledge(OWNER, done.id, 'web', now=330)
        again = await store.acknowledge(OWNER, done.id, 'web', now=331)
        check('the owner\'s look is the receipt, once', received == 1 and again == 0
              and (await store.deliveries(OWNER, notice.id))[-1].outcome == 'acknowledged')
        await refused('another owner cannot record a delivery on this notice', store.record_delivery('another', notice.id, 'spoken', 'sent'))
        check('the switch defaults to on and persists off', await store.notify_enabled(OWNER) and not await store.set_notify(OWNER, False, now=340))
    async with TaskStore(cfg) as store:
        check('deliveries and the switch survive reopening', not await store.notify_enabled(OWNER) and len(await store.deliveries(OWNER, notice.id)) == 3)
        view = await store.owner_view(OWNER)
        check('the list view says the switch is off and how many notices are owed', view['notify'] is False and view['owed'] == 0)
        detail = await store.owner_view(OWNER, done.id)
        check('the detail view carries authority, unresolved effects, and notices with their deliveries',
              detail['authority'] == {'kind': 'owner', 'approved_actions': 0} and detail['unresolved'] == []
              and detail['notices'][0]['status'] == 'done' and [d['outcome'] for d in detail['notices'][0]['deliveries']] == ['failed', 'sent', 'acknowledged'])


class QuietAdapter:
    namespace = None
    operations = frozenset({'inspect'})

    def prepare(self, task: Task, records: tuple) -> Preparation:
        return Preparation()

    async def read(self, ctx: StepContext) -> Outcome:
        return Outcome(evidence=(Evidence('value', TARGET, 'done', 'fixture', ctx.now, 'rev-1'),))


async def probe_notifier(root: Path) -> None:
    print('\nthe notifier and Vigil')
    controller = TaskController(config(root / 'notifier', enabled=True), ActionJournal(JournalConfig(dir=root / 'journal')))
    await controller.start()
    store = controller.store
    assert store is not None
    queue = FakeQueue()
    notifier = TaskNotifier(controller.config, lambda: controller.store, lambda: queue, clock=lambda: 1000.0)
    done = await finished(store, 'done-1', 100)
    check('a notice is offered to Vigil once, in spoken English, with only identifiers in its payload',
          await notifier.poll_now(1000) == 1 and await notifier.poll_now(1001) == 0
          and queue.events[0].source == 'task' and queue.events[0].summary == 'Done: The thing reads done.'
          and queue.events[0].payload == {'notice_id': f'{done.id}:{done.revision}', 'task_id': done.id} and queue.events[0].importance == 2)
    await notifier.delivered(queue.events[0], 'spoken')
    check('the spoken delivery is on the record and the notice is not offered again',
          (await store.deliveries(OWNER, queue.events[0].payload['notice_id']))[0].outcome == 'sent' and await notifier.poll_now(1002) == 0)
    failed_task = await store.create(Origin(OWNER, 'fail-1', 'voice'), SPEC, READ, now=200)
    await store.fail(OWNER, failed_task.id, failed_task.revision, 'the world went away', now=201)
    await notifier.poll_now(1003)
    failure = queue.events[-1]
    check('a failure is offered as urgent and says why', failure.importance == 3 and 'could not finish' in failure.summary and 'went away' in failure.summary)
    await notifier.failed(failure, 'message', 'no lane')
    check('a failed delivery is offered again only after the retry time',
          await notifier.poll_now(1004) == 0 and await notifier.poll_now(1000 + controller.config.notice_retry_s + 1) == 1
          and queue.events[-1].dedupe_key.endswith(':2'))
    binding = TaskBinding(Origin(OWNER, 'chart-turn', 'web', ingress_ids=('web:1',)), 1, 1)
    detail = await controller.view(binding, failed_task.id)
    check('an attended private look through the controller is the receipt', detail['received'] == 1 and (await controller.view(binding, failed_task.id))['received'] == 0
          and await notifier.poll_now(1000 + 2 * controller.config.notice_retry_s) == 0)
    public = TaskBinding(Origin(OWNER, 'public-turn', 'discord', private=False, ingress_ids=('dm:9',)), 1, 1)
    await refused('a public lane cannot look', controller.view(public, failed_task.id))
    await refused('a public lane cannot touch the switch', controller.apply(public, 'notices_mute', {}))
    muted = await controller.apply(binding, 'notices_mute', {})
    runner = TaskRunner(controller.config, lambda: controller.store, (QuietAdapter(),), clock=lambda: 301.0)
    task = await store.create(Origin(OWNER, 'run-1', 'voice'), SPEC, READ, now=300)
    report = await runner.step(now=301)
    check('muted, the runner still steps and completes; nothing is offered to Vigil; no task changed state',
          muted == {'notify': False} and report is not None and report.result == 'done' and await notifier.poll_now(2000) == 0
          and (await store.get(OWNER, task.id)).status == 'done')
    check('the list view distinguishes the switch from any pause', (await controller.view(binding))['notify'] is False
          and all(t['status'] != 'paused' for t in (await controller.view(binding))['tasks']))
    unmuted = await controller.apply(binding, 'notices_unmute', {})
    check('unmuting offers what was owed while muted', unmuted == {'notify': True} and await notifier.poll_now(2001) == 1
          and queue.events[-1].payload['task_id'] == task.id)
    entries = ActionJournal(JournalConfig(dir=root / 'journal')).recent(3)
    check('the switch is journaled as the notice switch, not a pause', any(e['tool'] == 'task_notices_mute' and 'execution is unaffected' in e['note'] for e in entries))
    await controller.close()
    check('a notifier with no queue offers nothing and does not fail',
          await TaskNotifier(controller.config, lambda: None, lambda: None).poll_now(1) == 0)


async def probe_migration(root: Path) -> None:
    print('\nversion six is lifted, not reset')
    cfg = config(root / 'schema')
    async with TaskStore(cfg) as store:
        done = await finished(store, 'done-1', 100)
    db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
    db.executescript('DROP TABLE deliveries; DROP TABLE settings; PRAGMA user_version=6;')
    db.commit()
    db.close()
    async with TaskStore(cfg) as store:
        db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        db.close()
        check('a version-six store opens as version seven with every notice it held still owed',
              version == 7 and [n.task_id for n, _ in await store.undelivered(OWNER, now=200)] == [done.id] and await store.notify_enabled(OWNER))


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-notices-probe-') as tmp:
        root = Path(tmp)
        await probe_owed(root)
        await probe_notifier(root)
        await probe_migration(root)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    asyncio.run(main())
