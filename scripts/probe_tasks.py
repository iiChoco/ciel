"""Durable tasks keep their place without granting themselves new authority.

Structured owner questions, atomic resource waits, whole-batch retries, tool/current
and Chart/rendered revisions, and reserved tool bindings survive reopening.
Temporary stores exercise request identity, owner scope, exact criteria,
transitions, stale results, task allowances, private files, competing owners,
and unsupported/corrupt databases. Held read locks exercise both a successful
wait and an exhausted timeout; rolled-back contention leaves the store usable,
while a failed rollback still stops execution. Long polling, clean waits,
interruption, and reopening keep separate durable allowances. Retargeted heads
preserve request identity and scope, fence stale callbacks, and require matching
fresh evidence for every criterion. Cancelled callers, cancelled shutdown,
and failed writes cannot be confused with rolled-back or completed work. Real subprocess deaths pin rollback before
commit, recovery before dispatch, retryable reads, uncertain external writes,
and completion notices surviving independently of delivery. The runner's side
pins eligibility in age order, a model-call allowance spent before the call,
abandonment that spends the attempt and requeues with backoff, waits for
reconciliation, or fails the task, and feature records with a namespace:
validated write-sets, expected revisions, a per-namespace allowance, write-sets
riding a checkpoint, adapter-owned migration at open, unsupported and unknown
namespaces preserved untouched, a failed migration rolled back, and a
version-two store lifted to three with every task in place. No actual tool,
model, mic, network, or user's runtime state is used.

    uv run --no-sync python scripts/probe_tasks.py
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ciel.config import TasksConfig, load_config
from ciel.tasks import (Criterion, Evidence, Namespace, Origin, RecordSet, RecordWrite, Scope, Specification, Step, Task, TaskBusy,
                        TaskConflict, TaskLimit, TaskStore, TaskStoreError)

CHECKS: list[str] = []
OWNER = 'fixture-owner'
SPEC = Specification('The selected checks pass', Scope(('inspect','edit'),('fixture:pr',)),
                     (Criterion('checks','fixture:pr','passed','head-1'),), project='fixture-project')
READ = Step('read','inspect','fixture:pr',(('head','head-1'),))
WRITE = Step('mutation','edit','fixture:pr',(('head','head-1'),))


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def refused(name: str, call: object, kind: type[Exception] = TaskConflict) -> None:
    try:
        await call
    except kind:
        check(name,True)
    else:
        check(name,False)


def config(directory: Path, **kwargs: object) -> TasksConfig:
    return replace(TasksConfig(),directory=directory,**kwargs)


async def create(store: TaskStore, name: str = 'request-1', step: Step = READ) -> Task:
    return await store.create(Origin(OWNER,name,'voice'),SPEC,step,now=100)


async def observed(store: TaskStore, task: Task, value: str | None = 'passed', revision: str = 'head-1') -> Task:
    attempt = await store.claim(OWNER,task.id,task.revision,now=101)
    attempt = await store.mark_dispatched(OWNER,attempt,now=102)
    return await store.observe(OWNER,attempt,(Evidence('checks','fixture:pr',value,'fixture-adapter',103,revision),),now=104)


async def probe_records(root: Path) -> None:
    directory = root/'records'
    async with TaskStore(config(directory)) as store:
        task = await create(store)
        check('a mandate retains outcome, scope, criteria, origin, and next step', task.specification == SPEC and task.next_step == READ and task.origin.request_id == 'request-1' and task.status == 'queued')
        check('the same originating request creates one responsibility', (await create(store)).id == task.id and len(await store.list(OWNER)) == 1)
        await refused('a reused request identity cannot change the outcome',store.create(task.origin,replace(SPEC,outcome='different'),READ,now=100))
        await refused('another owner cannot inspect a task',store.get('someone-else',task.id))
        check('another owner sees no task or notification details', not await store.list('someone-else') and not await store.notices('someone-else'))
        for origin in (replace(task.origin,request_id='public',private=False),replace(task.origin,request_id='unattended',attended=False)):
            await refused('public or unattended input cannot create a mandate',store.create(origin,SPEC,READ,now=100))
        await refused('a step cannot widen the task resource scope',store.create(replace(task.origin,request_id='wide'),SPEC,replace(READ,target='other'),now=100))
        await refused('a queued task cannot declare itself done',store.complete(OWNER,task.id,task.revision,now=101))
        attempt = await store.claim(OWNER,task.id,task.revision,now=101)
        await refused('the same revision cannot be claimed twice',store.claim(OWNER,task.id,task.revision,now=101))
        await refused('observations cannot precede dispatch intent',store.observe(OWNER,attempt,(),now=102))
        running = await store.get(OWNER,task.id)
        paused = await store.pause(OWNER,task.id,running.revision,now=102)
        await refused('a paused attempt cannot dispatch late',store.mark_dispatched(OWNER,attempt,now=103))
        queued = await store.resume(OWNER,task.id,paused.revision,now=103)
        attempt2 = await store.claim(OWNER,task.id,queued.revision,now=104)
        attempt2 = await store.mark_dispatched(OWNER,attempt2,now=105)
        await refused('a previous attempt cannot attach its result to new work',store.observe(OWNER,attempt,(),now=106))
        verifying = await store.observe(OWNER,attempt2,(Evidence('checks','fixture:pr','passed','fixture-adapter',106,'head-1'),),now=107)
        done = await store.complete(OWNER,task.id,verifying.revision,now=108)
        notices = await store.notices(OWNER)
        check('done and its evidence-backed completion notice commit together', done.status == 'done' and len([n for n in notices if n.status == 'done']) == 1 and notices[-1].revision == done.revision)
        await refused('completion cannot be repeated with an old revision',store.complete(OWNER,task.id,verifying.revision,now=109))
        await refused('cancellation cannot resurrect a completed task',store.cancel(OWNER,task.id,done.revision,now=109))
        history = await store.history(OWNER,task.id)
        check('every task revision has one ordered history row', [e.revision for e in history] == list(range(1,done.revision+1)))
        check('only an interrupted attempt spends the retry allowance',done.attempts == 1 and done.polls == 2)
    async with TaskStore(config(directory)) as store:
        recovered = await store.get(OWNER,task.id)
        check('completed work and its notice survive reopening', recovered == done and len([n for n in await store.notices(OWNER) if n.status=='done']) == 1)
        check('request dedupe survives completion and reopening',(await create(store)).id == task.id)
    check('the task directory and its files are owner-only', stat.S_IMODE(directory.stat().st_mode)==0o700 and all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in directory.iterdir()))


async def probe_evidence(root: Path) -> None:
    async with TaskStore(config(root/'evidence')) as store:
        for value, rev, label in ((None,'head-1','unknown'),('failed','head-1','unsatisfied'),('passed','head-old','old-head')):
            task = await observed(store,await create(store,label),value,rev)
            await refused(f'{label} evidence cannot satisfy completion',store.complete(OWNER,task.id,task.revision,now=105))
        task = await observed(store,await create(store,'stale'))
        await refused('expired observations cannot complete a task',store.complete(OWNER,task.id,task.revision,now=1000))
        task = await create(store,'missing')
        attempt = await store.claim(OWNER,task.id,task.revision,now=101)
        attempt = await store.mark_dispatched(OWNER,attempt,now=102)
        await refused('future observations cannot claim freshness',store.observe(OWNER,attempt,(Evidence('checks','fixture:pr','passed','adapter',500,'head-1'),),now=103),ValueError)
        await refused('an observation for another target cannot satisfy this task',store.observe(OWNER,attempt,(Evidence('checks','other','passed','adapter',103,'head-1'),),now=103),ValueError)
        item = Evidence('checks','fixture:pr','passed','adapter',103,'head-1')
        await refused('duplicate criterion results roll back the whole observation batch',store.observe(OWNER,attempt,(item,item),now=103),ValueError)
        task = await store.observe(OWNER,attempt,(),now=104)
        await refused('empty evidence is not vacuous success',store.complete(OWNER,task.id,task.revision,now=105))
        task = await store.checkpoint(OWNER,task.id,task.revision,READ,eligible_at=200,now=105)
        await refused('a scheduled checkpoint is not runnable early',store.claim(OWNER,task.id,task.revision,now=199))
        task = await store.wait(OWNER,task.id,task.revision,'owner','Which PR should I monitor?',now=106)
        check('an owner question is durable data rather than a live Future',task.status == 'waiting' and task.wait_reason == 'owner' and 'Which PR' in task.detail)
    async with TaskStore(config(root/'evidence')) as store:
        waiting = await store.get(OWNER,task.id)
        check('an owner question survives restart unchanged', waiting == task)
        paused = await store.pause(OWNER,task.id,task.revision,now=107)
        await refused('a late answer cannot resume a superseded question',store.resume(OWNER,task.id,task.revision,now=108))
        cancelled = await store.cancel(OWNER,task.id,paused.revision,now=109)
        await refused('cancelled responsibility cannot resume',store.resume(OWNER,task.id,cancelled.revision,now=110))


async def probe_recovery(root: Path) -> None:
    directory = root/'recovery'
    async with TaskStore(config(directory)) as store:
        read = await create(store,'read')
        pending = await create(store,'prepared-mutation',WRITE)
        mutation = await create(store,'dispatched-mutation',WRITE)
        verifying = await observed(store,await create(store,'verifying'))
        a = await store.claim(OWNER,read.id,read.revision,now=101)
        a = await store.mark_dispatched(OWNER,a,now=102)
        await store.claim(OWNER,pending.id,pending.revision,now=101)
        m = await store.claim(OWNER,mutation.id,mutation.revision,now=101)
        m = await store.mark_dispatched(OWNER,m,now=102)
    store = TaskStore(config(directory))
    recovered = await store.start(now=110)
    try:
        check('recovery finds only unfinished running attempts',len(recovered)==3)
        check('a dispatched read becomes runnable after restart',(await store.get(OWNER,read.id)).status=='queued')
        check('a mutation that never dispatched remains safe to retry',(await store.get(OWNER,pending.id)).status=='queued')
        unknown = await store.get(OWNER,mutation.id)
        check('a possibly dispatched mutation waits for reconciliation',unknown.status=='waiting' and unknown.wait_reason=='reconciliation' and (await store.attempts(OWNER,mutation.id))[-1].phase=='unknown')
        await refused('resume cannot turn uncertainty into another mutation',store.resume(OWNER,mutation.id,unknown.revision,now=111))
        paused = await store.pause(OWNER,mutation.id,unknown.revision,now=112)
        await refused('pause and resume cannot launder an uncertain action',store.resume(OWNER,mutation.id,paused.revision,now=113))
        cancelled = await store.cancel(OWNER,mutation.id,paused.revision,now=114)
        check('cancellation keeps the uncertainty visible',cancelled.status=='cancelled' and cancelled.wait_reason=='reconciliation')
        await refused('a late pre-restart result cannot modify current state',store.observe(OWNER,m,(),now=115))
        check('recorded observations resume verification without redispatch',(await store.get(OWNER,verifying.id))==verifying)
        notices = await store.notices(OWNER)
        attempts = (await store.get(OWNER, read.id)).attempts
        await store.close()
        store = TaskStore(config(directory))
        check('repeated startup does not repeat recovery notices',not await store.start(now=120) and await store.notices(OWNER) == notices)
        check('reopening charges an interrupted attempt only once', (await store.get(OWNER, read.id)).attempts == attempts == 1)
    finally:
        await store.close()


async def probe_limits_and_storage(root: Path) -> None:
    directory = root/'limits'
    async with TaskStore(config(directory,max_active=1,max_attempts=1)) as store:
        task = await create(store)
        await refused('active task admission has a bound',create(store,'extra'),TaskLimit)
        attempt = await store.claim(OWNER,task.id,task.revision,now=101)
        task = await store.fail(OWNER,task.id,attempt.task_revision,'attempt interrupted',now=102)
        await refused('resuming cannot renew an exhausted attempt allowance',store.resume(OWNER,task.id,task.revision,now=103),TaskLimit)
    async with TaskStore(config(directory,max_attempts=99)) as store:
        check('changing defaults does not refill a persisted task budget',(await store.get(OWNER,task.id)).max_attempts==1)
    async with TaskStore(config(root/'owner')) as first:
        second = TaskStore(config(root/'owner'))
        await refused('a second runtime cannot own the same task store',second.start(),TaskStoreError)
        await second.close()
        check('a rejected competing owner does not harm the first', (await create(first)).status=='queued')
    for variant in ('ahead','behind','foreign','corrupt','empty','missing-table','bad-json','bad-polls','bad-attempts','changed-mandate'):
        directory = root/variant
        if variant in ('ahead','behind','foreign','missing-table','bad-json','bad-polls','bad-attempts','changed-mandate'):
            async with TaskStore(config(directory)) as store:
                await create(store)
            with sqlite3.connect(directory/'tasks.sqlite3') as db:
                if variant=='ahead':
                    db.execute('PRAGMA user_version=99')
                elif variant=='behind':
                    db.execute('PRAGMA user_version=1')
                elif variant=='foreign':
                    db.execute('PRAGMA application_id=42')
                elif variant=='missing-table':
                    db.execute('DROP TABLE outbox')
                elif variant == 'bad-polls':
                    db.execute('UPDATE tasks SET polls=1')
                elif variant == 'bad-attempts':
                    db.execute('UPDATE tasks SET attempts=1, polls=1')
                elif variant == 'changed-mandate':
                    raw = json.loads(db.execute('SELECT specification_json FROM tasks').fetchone()[0])
                    raw['criteria'][0]['expected'] = 'anything'
                    db.execute('UPDATE tasks SET specification_json=?', (json.dumps(raw),))
                else:
                    db.execute("UPDATE tasks SET request_json='broken'")
        else:
            directory.mkdir(mode=0o700)
            (directory/'tasks.sqlite3').write_bytes(b'not a database' if variant=='corrupt' else b'')
        before = (directory/'tasks.sqlite3').read_bytes()
        broken = TaskStore(config(directory))
        await refused(f'{variant} storage is refused without reset',broken.start(),TaskStoreError)
        await broken.close()
        check(f'{variant} database contents remain intact',(directory/'tasks.sqlite3').read_bytes()==before)
    directory = root/'symlink'
    directory.mkdir(mode=0o700)
    victim = root/'untouched'
    victim.write_text('private fixture')
    (directory/'tasks.sqlite3').symlink_to(victim)
    bad = TaskStore(config(directory))
    await refused('a database symlink cannot redirect task writes',bad.start(),TaskStoreError)
    await bad.close()
    check('a rejected symlink target is unchanged',victim.read_text()=='private fixture')


async def probe_lifecycle(root: Path) -> None:
    directory = root/'cancelled-call'
    store = TaskStore(config(directory))
    await store.start()
    entered, release = threading.Event(), threading.Event()
    original = store._event
    def delayed_event(task: Task, before: str | None) -> None:
        original(task,before)
        entered.set()
        if not release.wait(5):
            raise RuntimeError('probe did not release its worker')
    store._event = delayed_event
    caller = asyncio.create_task(create(store))
    check('SQLite work leaves the event loop available',await asyncio.to_thread(entered.wait,2) and not caller.done())
    caller.cancel()
    try:
        await caller
    except asyncio.CancelledError:
        check('caller cancellation remains cancellation',True)
    closing = asyncio.create_task(store.close())
    await asyncio.sleep(0)
    closing.cancel()
    try:
        await closing
    except asyncio.CancelledError:
        pass
    release.set()
    await store.close()
    async with TaskStore(config(directory)) as reopened:
        tasks = await reopened.list(OWNER)
        check('cancelled shutdown still releases the worker and ownership',len(tasks)==1)
        check('a cancelled caller rereads its committed request instead of duplicating it',(await create(reopened)).id==tasks[0].id)

    directory = root/'write-failure'
    async with TaskStore(config(directory)) as broken:
        def failed_event(task: Task, before: str | None) -> None:
            raise sqlite3.OperationalError('simulated disk failure')
        broken._event = failed_event
        await refused('a storage failure refuses the operation',create(broken),TaskStoreError)
        await refused('a storage failure prevents subsequent execution through this handle',broken.list(OWNER),TaskStoreError)
    async with TaskStore(config(directory)) as reopened:
        check('a failed write leaves neither a task nor a notification',not await reopened.list(OWNER) and not await reopened.notices(OWNER))

    async with TaskStore(config(root/'mutation-controls')) as store:
        task = await create(store,step=WRITE)
        waiting = await store.wait(OWNER,task.id,task.revision,'owner','Confirm the concrete edit',now=101)
        check('a not-yet-dispatched mutation can wait for its owner',waiting.wait_reason=='owner')
        task = await store.resume(OWNER,task.id,waiting.revision,now=102)
        attempt = await store.claim(OWNER,task.id,task.revision,now=103)
        attempt = await store.mark_dispatched(OWNER,attempt,now=104)
        item = Evidence('checks','fixture:pr','passed','read-back',105,'head-1')
        task = await store.observe(OWNER,attempt,(item,),now=106)
        check('observations retain source, time, and target revision',await store.observations(OWNER,attempt.id)==(item,))
        await refused('another owner cannot read task evidence',store.observations('other',attempt.id))
        await refused('a checkpoint cannot blindly chain mutations',store.checkpoint(OWNER,task.id,task.revision,WRITE,eligible_at=107,now=107))
        task = await store.pause(OWNER,task.id,task.revision,now=107)
        await refused('pausing an unverified mutation cannot make it retryable',store.resume(OWNER,task.id,task.revision,now=108))

    async with TaskStore(config(root/'record-bound',max_record_chars=100)) as store:
        await refused('oversized task records cannot enter the store',create(store),TaskLimit)
    directory = root/'not-private'
    directory.mkdir(mode=0o755)
    opened = TaskStore(config(directory))
    await refused('a shared directory is refused instead of having its permissions changed',opened.start(),TaskStoreError)
    await opened.close()
    check('a refused shared directory is left alone',stat.S_IMODE(directory.stat().st_mode)==0o755 and not list(directory.iterdir()))


def kill() -> None:
    os.kill(os.getpid(),signal.SIGKILL)


async def child(directory: Path, mode: str) -> None:
    async with TaskStore(config(directory)) as store:
        if mode == 'locked':
            return
        task = await create(store,step=WRITE if mode=='external' else READ)
        if mode == 'before-dispatch':
            await store.claim(OWNER,task.id,task.revision,now=101)
            kill()
        if mode == 'external':
            attempt = await store.claim(OWNER,task.id,task.revision,now=101)
            await store.mark_dispatched(OWNER,attempt,now=102)
            (directory/'external-count').write_text('1')
            kill()
        task = await observed(store,task)
        if mode=='before-commit':
            original = store._event
            def torn_event(changed: Task, before: str | None) -> None:
                original(changed,before)
                if changed.status=='done':
                    kill()
            store._event = torn_event
        await store.complete(OWNER,task.id,task.revision,now=105)
        kill()


async def probe_crashes(root: Path) -> None:
    for mode in ('before-dispatch','external','before-commit','after-commit'):
        directory = root/mode
        result = await asyncio.to_thread(subprocess.run,[sys.executable,__file__,'--child',str(directory),mode],capture_output=True,text=True,timeout=10)
        check(f'{mode} fixture actually dies without clean shutdown',result.returncode==-signal.SIGKILL)
        async with TaskStore(config(directory)) as store:
            task = (await store.list(OWNER))[0]
            if mode=='before-dispatch':
                check('process death releases ownership and preserves a safe retry',task.status=='queued' and task.attempts==1)
            elif mode=='external':
                check('external success before receipt stays uncertain',task.status=='waiting' and task.wait_reason=='reconciliation')
                await refused('recovery cannot redispatch an uncertain external effect',store.claim(OWNER,task.id,task.revision),TaskConflict)
                check('recovery does not repeat the synthetic external effect',(directory/'external-count').read_text()=='1')
            elif mode=='before-commit':
                check('a torn completion rolls back task state and notification together',task.status=='verifying' and not await store.notices(OWNER))
                task = await store.complete(OWNER,task.id,task.revision,now=106)
                check('verification after rollback commits exactly one completion notice',task.status=='done' and len(await store.notices(OWNER))==1)
            else:
                check('completion survives a kill before notification delivery',task.status=='done' and len(await store.notices(OWNER))==1)
                check('reading a durable notice does not rerun completed work',(await store.notices(OWNER))==(await store.notices(OWNER)) and task.attempts==0 and task.polls==1)
    async with TaskStore(config(root/'cross-process')) as store:
        result = await asyncio.to_thread(subprocess.run,[sys.executable,__file__,'--child',str(root/'cross-process'),'locked'],capture_output=True,text=True,timeout=10)
        check('the ownership lock also excludes a separate process',result.returncode!=0 and 'another runtime owns' in result.stderr)


async def probe_contention(root: Path) -> None:
    directory = root / 'contention'
    async with TaskStore(config(directory, busy_timeout_s=0.5)) as store:
        task = await observed(store, await create(store))
        reader = sqlite3.connect(directory / 'tasks.sqlite3', isolation_level=None)
        try:
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM tasks').fetchone()
            entered = threading.Event()
            original = store._event
            def reached_commit(changed: Task, before: str | None) -> None:
                original(changed, before)
                entered.set()
            store._event = reached_commit
            pending = asyncio.create_task(create(store, 'brief-reader'))
            check('a reader can hold commit while the event loop remains available',
                  await asyncio.to_thread(entered.wait, 2) and not pending.done())
            reader.execute('ROLLBACK')
            created = await pending
            check('a read lock released within the timeout lets the write commit', created.status == 'queued')
            store._event = original

            history = await store.history(OWNER, task.id)
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM tasks').fetchone()
            await refused('a held read lock refuses completion after its timeout',
                          store.complete(OWNER, task.id, task.revision, now=105), TaskBusy)
            reader.execute('ROLLBACK')
            check('timed-out completion rolls back the state, evidence phase, history, and notice',
                  await store.get(OWNER, task.id) == task and await store.history(OWNER, task.id) == history
                  and not await store.notices(OWNER) and (await store.attempts(OWNER, task.id))[-1].phase == 'observed')
            done = await store.complete(OWNER, task.id, task.revision, now=106)
            check('the same handle can complete after the reader leaves', done.status == 'done' and len(await store.notices(OWNER)) == 1)

            reader.execute('BEGIN IMMEDIATE')
            await refused('a competing write lock refuses begin without poisoning the store', create(store, 'writer'), TaskBusy)
            reader.execute('ROLLBACK')
            created = await create(store, 'writer')
            check('a refused begin can be retried through the same handle', created.status == 'queued' and len(await store.list(OWNER)) == 3)
        finally:
            reader.close()

    class BrokenRollback(sqlite3.Connection):
        def execute(self, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
            if sql == 'ROLLBACK':
                raise sqlite3.OperationalError('simulated rollback failure')
            return super().execute(sql, parameters)

    connect = sqlite3.connect
    with patch('ciel.tasks.sqlite3.connect', side_effect=lambda *a, **kw: connect(*a, **kw, factory=BrokenRollback)):
        async with TaskStore(config(root / 'broken-rollback')) as store:
            await create(store)
            await refused('a failed rollback makes even a refused request a storage failure',
                          store.create(Origin(OWNER, 'request-1', 'voice'), replace(SPEC, outcome='changed'), READ), TaskStoreError)
            await refused('an uncertain rollback prevents further work on that handle', store.list(OWNER), TaskStoreError)


async def probe_polling(root: Path) -> None:
    directory = root / 'long-poll'
    store = TaskStore(config(directory, max_attempts=2, max_polls=41))
    await store.start()
    try:
        task = await create(store)
        attempt = await store.claim(OWNER, task.id, task.revision, now=101)
        task = await store.pause(OWNER, task.id, attempt.task_revision, now=102)
        check('an interrupted round spends one retry and one polling round', task.attempts == 1 and task.polls == 1)
        task = await store.resume(OWNER, task.id, task.revision, now=103)
        for i in range(40):
            if i == 20:
                await store.close()
                store = TaskStore(config(directory, max_attempts=99, max_polls=999))
                await store.start(now=200 + i * 300)
                task = await store.get(OWNER, task.id)
                check('reopening preserves both used allowances and their original limits',
                      task.attempts == 1 and task.polls == 21 and task.max_attempts == 2 and task.max_polls == 41)
            stamp = 200 + i * 300
            attempt = await store.claim(OWNER, task.id, task.revision, now=stamp)
            attempt = await store.mark_dispatched(OWNER, attempt, now=stamp + 1)
            task = await store.observe(OWNER, attempt,
                (Evidence('checks', 'fixture:pr', 'pending', 'fixture-adapter', stamp + 2, 'head-1'),), now=stamp + 3)
            task = await store.checkpoint(OWNER, task.id, task.revision, READ, eligible_at=stamp + 300, now=stamp + 4)
        check('forty clean five-minute polls do not spend or refill the retry allowance',
              task.attempts == 1 and task.polls == 41 and task.status == 'queued')
        check('clean checkpoints remain visible in attempt history',
              sum(a.phase == 'checkpointed' for a in await store.attempts(OWNER, task.id)) == 40)
        await refused('polling still stops at its separate durable bound', store.claim(OWNER, task.id, task.revision, now=13000), TaskLimit)
        task = await store.pause(OWNER, task.id, task.revision, now=13001)
        await refused('resume cannot refill an exhausted polling allowance', store.resume(OWNER, task.id, task.revision, now=13002), TaskLimit)
    finally:
        await store.close()
    async with TaskStore(config(directory, max_polls=999)) as store:
        task = await store.get(OWNER, task.id)
        await refused('reopening cannot refill an exhausted polling allowance', store.resume(OWNER, task.id, task.revision, now=14000), TaskLimit)

    directory = root / 'clean-wait'
    async with TaskStore(config(directory, max_attempts=1)) as store:
        task = await observed(store, await create(store), 'pending')
        task = await store.wait(OWNER, task.id, task.revision, 'external', 'Checks are pending', now=105)
        check('a wait after a read is a clean checkpoint', task.attempts == 0 and (await store.attempts(OWNER, task.id))[-1].phase == 'checkpointed')
        task = await store.resume(OWNER, task.id, task.revision, now=106)
        attempt = await store.claim(OWNER, task.id, task.revision, now=107)
    async with TaskStore(config(directory)) as store:
        task = await store.get(OWNER, task.id)
        check('recovery charges only the round that never checkpointed', task.attempts == 1 and task.polls == 2)
        await refused('a recovered interruption can exhaust the retry allowance', store.claim(OWNER, task.id, task.revision, now=14000), TaskLimit)


async def probe_retarget(root: Path) -> None:
    directory = root / 'retarget'
    async with TaskStore(config(directory)) as store:
        task = await create(store)
        await refused('a queued task cannot invent a new head', store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=101))
        task = await observed(store, task, revision='head-2')
        await refused('new-head evidence still needs an explicit retarget', store.complete(OWNER, task.id, task.revision, now=105))
        await refused('another owner cannot retarget a mandate', store.retarget('other', task.id, task.revision, 'fixture:pr', 'head-2', now=105))
        await refused('retargeting cannot widen the target scope', store.retarget(OWNER, task.id, task.revision, 'other', 'head-2', now=105))
        await refused('retargeting cannot name an unobserved head', store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-3', now=105))
        await refused('retargeting cannot rely on an expired observation', store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=1000))
        before = task
        history_before = await store.history(OWNER, task.id)
        event = store._event
        def refused_retarget(changed: Task, previous: str | None) -> None:
            event(changed, previous)
            raise TaskConflict('fixture refuses after the retarget history write')
        with patch.object(store, '_event', refused_retarget):
            await refused('a retarget refused after its history write rolls back', store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=105))
        check('a refused retarget leaves the specification, task revision, and history intact',
              await store.get(OWNER, task.id) == before and await store.history(OWNER, task.id) == history_before)
        task = await store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=105)
        history = await store.history(OWNER, task.id)
        check('retargeting records the old and new head in one revision',
              task.revision == before.revision + 1 and json.loads(history[-1].detail) ==
              {'retarget': 'fixture:pr', 'from': {'checks': 'head-1'}, 'to': 'head-2'})
        check('retargeting keeps the owner, scope, desired result, and current evidence',
              task.origin == before.origin and task.specification == replace(SPEC, criteria=(replace(SPEC.criteria[0], target_revision='head-2'),))
              and task.current_attempt == before.current_attempt and task.polls == before.polls)
        await refused('a pre-retarget callback cannot complete with its old revision', store.complete(OWNER, task.id, before.revision, now=106))
        await refused('a stale retarget cannot replace the current head', store.retarget(OWNER, task.id, before.revision, 'fixture:pr', 'head-3', now=106))
        check('the original request still deduplicates after a retarget', await create(store) == task)
        await refused('retargeting cannot rewrite the original request identity',
                      store.create(task.origin, task.specification, READ, now=106))
    async with TaskStore(config(directory)) as store:
        check('the retarget and original request survive reopening', await create(store) == task)
        done = await store.complete(OWNER, task.id, task.revision, now=107)
        check('passing evidence on the second head completes with one durable notice', done.status == 'done' and len(await store.notices(OWNER)) == 1)
        await refused('a completed mandate cannot be retargeted', store.retarget(OWNER, task.id, done.revision, 'fixture:pr', 'head-3', now=108))

    async with TaskStore(config(root / 'retarget-all-criteria')) as store:
        spec = replace(SPEC, criteria=(*SPEC.criteria, Criterion('review', 'fixture:pr', 'approved', 'head-1')))
        task = await store.create(Origin(OWNER, 'all-criteria', 'voice'), spec, READ, now=100)
        attempt = await store.claim(OWNER, task.id, task.revision, now=101)
        attempt = await store.mark_dispatched(OWNER, attempt, now=102)
        task = await store.observe(OWNER, attempt, (
            Evidence('checks', 'fixture:pr', 'passed', 'fixture-adapter', 103, 'head-2'),
            Evidence('review', 'fixture:pr', 'approved', 'fixture-adapter', 103, 'head-1')), now=104)
        task = await store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=105)
        check('all criteria for a changed target move together', all(c.target_revision == 'head-2' for c in task.specification.criteria))
        await refused('old-head evidence cannot be laundered by a retarget', store.complete(OWNER, task.id, task.revision, now=106))
        task = await store.checkpoint(OWNER, task.id, task.revision, replace(READ, arguments=(('head', 'head-2'),)), eligible_at=107, now=107)
        attempt = await store.claim(OWNER, task.id, task.revision, now=108)
        attempt = await store.mark_dispatched(OWNER, attempt, now=109)
        task = await store.observe(OWNER, attempt, (
            Evidence('checks', 'fixture:pr', 'passed', 'fixture-adapter', 110, 'head-2'),
            Evidence('review', 'fixture:pr', 'approved', 'fixture-adapter', 110, 'head-2')), now=111)
        done = await store.complete(OWNER, task.id, task.revision, now=112)
        check('fresh evidence for every retargeted criterion can complete', done.status == 'done' and done.attempts == 0 and done.polls == 2)
        task = await observed(store, await create(store, 'mutation', WRITE), revision='head-2')
        await refused('a mutation observation cannot retarget a mandate', store.retarget(OWNER, task.id, task.revision, 'fixture:pr', 'head-2', now=105))


async def probe_owner_controls(root: Path) -> None:
    cfg = config(root / 'owner-controls')
    origin = Origin(OWNER, 'batch', 'discord', ingress_ids=('dm:1', 'dm:2'))
    async with TaskStore(cfg) as store:
        task = await store.create(origin, SPEC, READ, resource_wait=True, now=100)
        check('an owner DM saves directly into a resource wait', task.status == 'waiting' and task.wait_reason == 'resource' and task.revision == 2)
        check('creation and its wait retain both history rows', len(await store.history(OWNER, task.id)) == 2)
        check('an exact batch retry returns its existing task', (await store.create(origin, SPEC, READ, resource_wait=True)).id == task.id)
        retry = replace(origin, request_id='subset', ingress_ids=('dm:2',))
        check('a consumed message retried alone finds its original batch', (await store.create(retry, SPEC, READ, resource_wait=True)).id == task.id)
        await refused('a partly consumed batch asks for the new part alone', store.create(replace(origin, request_id='overlap', ingress_ids=('dm:2', 'dm:3')), SPEC, READ, resource_wait=True))
        await refused('a consumed message cannot silently change its mandate', store.create(retry, replace(SPEC, outcome='different'), READ, resource_wait=True))
        paused = await store.owner_control(OWNER, task.id, 'pause')
        await refused('a Chart control carries the revision it rendered', store.owner_control(OWNER, task.id, 'resume', revision=task.revision))
        resumed = await store.owner_control(OWNER, task.id, 'resume')
        check('a spoken resume uses the current record and remains resource waiting', resumed.revision == paused.revision + 1 and resumed.wait_reason == 'resource' and resumed.status == 'waiting')
        again = await store.owner_control(OWNER, task.id, 'resume')
        check('resuming an unavailable resource never queues execution', again.wait_reason == 'resource' and again.polls == 0)
        task = await store.ask_owner(OWNER, task.id, again.revision, 'Which check?', ('checks', 'review'), now=101)
        detail = await store.owner_view(OWNER, task.id)
        question = detail['question']['id']
        await refused('a question cannot be bypassed with plain resume', store.owner_control(OWNER, task.id, 'resume'))
        await refused('an ambiguous answer leaves the question waiting', store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='yes'))
        await refused('a question cannot widen scope through its proposed next step', store.ask_owner(OWNER, task.id, task.revision, 'More?', ('yes',), step=replace(READ, target='elsewhere')))
    async with TaskStore(cfg) as store:
        check('a question and its identity survive reopening', (await store.owner_view(OWNER, task.id))['question']['id'] == question)
        answered = await store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='checks')
        check('answering without a runner leaves the task waiting on execution', answered.status == 'waiting' and answered.wait_reason == 'resource')
        check('answering preserves scope and every durable allowance', answered.specification == SPEC and answered.polls == 0 and answered.max_polls == task.max_polls and answered.max_attempts == task.max_attempts)
        await refused('an answer is applied only once', store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='checks'))
        task = await store.ask_owner(OWNER, task.id, answered.revision, 'Continue?', ('continue',))
        question = (await store.owner_view(OWNER, task.id))['question']['id']
        await store.owner_control(OWNER, task.id, 'cancel')
        await refused('cancellation rejects an old owner answer', store.owner_control(OWNER, task.id, 'answer', question_id=question, answer='continue'))
        check('another owner gets an empty private list', (await store.owner_view('another'))['tasks'] == [])
        await refused('another owner cannot control a task', store.owner_control('another', task.id, 'resume'))
        task = await create(store, 'bindings')
        attempt = await store.claim(OWNER, task.id, task.revision)
        await store.bind_call(OWNER, attempt, 'generation', 'tool-1')
        await store.bind_call(OWNER, attempt, 'generation', 'tool-2')
        await store.bind_call(OWNER, attempt, 'generation', 'tool-1')
        check('one attempt can bind multiple distinct tool calls idempotently', True)
        attempt = await store.mark_dispatched(OWNER, attempt)
        task = await store.observe(OWNER, attempt, (Evidence('checks', 'fixture:pr', 'passed', 'fixture', __import__('time').time(), 'head-1'),))
        task = await store.complete(OWNER, task.id, task.revision)
        check('completion keeps its evidence available to the owner view', len((await store.owner_view(OWNER, task.id))['evidence']) == 1)

        another = await create(store, 'other-binding')
        other_attempt = await store.claim(OWNER, another.id, another.revision)
        await refused('a tool binding cannot be reassigned to another attempt', store.bind_call(OWNER, other_attempt, 'generation', 'tool-1'))
    async with TaskStore(cfg) as store:
        check('ingress associations and tool bindings validate after reopening', (await store.create(retry, SPEC, READ)).id != another.id)
    async with TaskStore(config(root / 'empty-v2')):
        pass
    async with TaskStore(config(root / 'empty-v2')) as store:
        check('an empty version-two bindings table validates', not await store.list(OWNER))


def validate_record(payload: dict) -> None:
    if not isinstance(payload.get('n'), int):
        raise ValueError('n')


async def probe_runner_side(root: Path) -> None:
    print('\nthe runner\'s side of the store')
    cfg = config(root / 'runner-side', max_attempts=2, max_model_calls=2)
    async with TaskStore(cfg) as store:
        task = await create(store)
        later = await store.create(Origin(OWNER, 'later', 'voice'), SPEC, READ, now=101)
        check('eligibility lists queued tasks whose time has come, oldest first',
              [t.id for t in await store.eligible(OWNER, now=101)] == [task.id, later.id]
              and [t.id for t in await store.eligible(OWNER, now=100)] == [task.id]
              and not await store.eligible(OWNER, now=99) and not await store.eligible('someone-else', now=101))
        attempt = await store.claim(OWNER, task.id, task.revision, now=101)
        check('a running task is not eligible', task.id not in [t.id for t in await store.eligible(OWNER, now=200)])
        counted = await store.note_model_call(OWNER, attempt, now=101)
        counted = await store.note_model_call(OWNER, attempt, now=101)
        await refused('a model-call allowance is spent before the call and runs out', store.note_model_call(OWNER, attempt, now=101), TaskLimit)
        check('model calls are counted on the task', counted.model_calls == 2 and counted.max_model_calls == 2)
        abandoned = await store.abandon(OWNER, attempt, 'the read failed', eligible_at=150, now=102)
        check('an abandoned read is spent and requeued with backoff',
              abandoned.status == 'queued' and abandoned.attempts == 1 and abandoned.polls == 1 and abandoned.eligible_at == 150
              and abandoned.detail == 'the read failed' and (await store.attempts(OWNER, task.id))[0].phase == 'interrupted')
        await refused('an abandoned attempt cannot be abandoned twice', store.abandon(OWNER, attempt, 'again', eligible_at=150, now=103))
        check('backoff keeps the task out of eligibility until its time', task.id not in [t.id for t in await store.eligible(OWNER, now=149)]
              and task.id in [t.id for t in await store.eligible(OWNER, now=150)])
        attempt = await store.claim(OWNER, task.id, abandoned.revision, now=150)
        attempt = await store.mark_dispatched(OWNER, attempt, now=151)
        observed = await store.observe(OWNER, attempt, (Evidence('checks', 'fixture:pr', 'pending', 'fixture', 152, 'head-1'),), now=152)
        failed = await store.abandon(OWNER, attempt, 'the outcome was refused', eligible_at=200, now=153)
        check('an observed attempt can still be given up, and an exhausted allowance fails the task visibly',
              observed.status == 'verifying' and failed.status == 'failed' and failed.attempts == 2
              and 'attempt allowance exhausted' in failed.detail)
        write = await create(store, 'write', WRITE)
        attempt = await store.claim(OWNER, write.id, write.revision, now=160)
        attempt = await store.mark_dispatched(OWNER, attempt, now=161)
        uncertain = await store.abandon(OWNER, attempt, 'lost the response', eligible_at=200, now=162)
        check('a sent mutation given up on waits for reconciliation, never a retry',
              uncertain.status == 'waiting' and uncertain.wait_reason == 'reconciliation'
              and (await store.attempts(OWNER, write.id))[0].phase == 'unknown')
    async with TaskStore(cfg) as store:
        check('model calls and spent attempts survive reopening',
              (await store.get(OWNER, task.id)).model_calls == 2 and (await store.get(OWNER, task.id)).attempts == 2)


async def probe_feature_records(root: Path) -> None:
    print('\nfeature records have a namespace, not a column')
    cfg = config(root / 'feature-records', max_feature_records=2)
    spec = Namespace('fixture', 1, validate_record)
    store = TaskStore(cfg)
    store.register(spec)
    try:
        store.register(spec)
    except ValueError:
        check('a namespace is registered once', True)
    else:
        check('a namespace is registered once', False)
    async with store:
        try:
            store.register(Namespace('late', 1, validate_record))
        except TaskStoreError:
            check('namespaces are registered before the store opens', True)
        else:
            check('namespaces are registered before the store opens', False)
        await store.write_records(OWNER, RecordSet('fixture', (RecordWrite('a', {'n': 1}, 0), RecordWrite('b', {'n': 2}))))
        rows = await store.records(OWNER, 'fixture')
        check('records are written with revision one and read back in key order',
              [(r.key, r.revision, r.payload) for r in rows] == [('a', 1, {'n': 1}), ('b', 1, {'n': 2})])
        await refused('a write-set the validator rejects is refused whole', store.write_records(OWNER, RecordSet('fixture', (RecordWrite('a', {'n': 5}), RecordWrite('c', {'n': 'x'})))), ValueError)
        check('a refused write-set changed nothing', (await store.records(OWNER, 'fixture', ('a',)))[0].payload == {'n': 1})
        await refused('an expected revision that no longer matches refuses the write', store.write_records(OWNER, RecordSet('fixture', (RecordWrite('a', {'n': 5}, 2),))))
        await refused('a record expected to be new cannot overwrite one that exists', store.write_records(OWNER, RecordSet('fixture', (RecordWrite('a', {'n': 5}, 0),))))
        await refused('a namespace fills up at its allowance', store.write_records(OWNER, RecordSet('fixture', (RecordWrite('c', {'n': 3}),))), TaskLimit)
        await store.write_records(OWNER, RecordSet('fixture', (RecordWrite('a', {'n': 9}, 1), RecordWrite('b', None, 1))))
        rows = await store.records(OWNER, 'fixture')
        check('an update bumps the revision and a delete removes the record', [(r.key, r.revision, r.payload) for r in rows] == [('a', 2, {'n': 9})])
        check('another owner sees no records', not await store.records('someone-else', 'fixture'))
        await refused('an unregistered namespace is unsupported', store.records(OWNER, 'unknown'), TaskStoreError)
        task = await create(store)
        seen = await observed(store, task, 'pending')
        saved = await store.checkpoint(OWNER, task.id, seen.revision, READ, eligible_at=200, now=105,
                                       records=RecordSet('fixture', (RecordWrite('cursor', {'n': 1}),)))
        check('a write-set rides the checkpoint it belongs to', saved.status == 'queued'
              and any(r.key == 'cursor' for r in await store.records(OWNER, 'fixture')))
        await refused('a checkpoint whose write-set is refused is not a checkpoint',
                      store.checkpoint(OWNER, task.id, saved.revision, READ, eligible_at=200, now=106,
                                       records=RecordSet('fixture', (RecordWrite('cursor', {'n': 2}, 7),))))
        check('the refused checkpoint changed neither the task nor the records',
              (await store.get(OWNER, task.id)).revision == saved.revision
              and (await store.records(OWNER, 'fixture', ('cursor',)))[0].revision == 1)

    lifted = TaskStore(cfg)
    lifted.register(Namespace('fixture', 2, lambda p: validate_record({'n': p['count']}), lambda v, p: {'count': p['n']}))
    async with lifted as store:
        rows = await store.records(OWNER, 'fixture')
        check('an older namespace is lifted by the adapter\'s own migration at open',
              store.namespace_supported('fixture') and [(r.key, r.revision, r.payload) for r in rows] == [('a', 3, {'count': 9}), ('cursor', 2, {'count': 1})])
    newer = TaskStore(cfg)
    newer.register(Namespace('fixture', 1, validate_record))
    async with newer as store:
        check('a namespace newer than the runtime is unsupported and untouched', not store.namespace_supported('fixture'))
        await refused('unsupported records cannot be read or written', store.records(OWNER, 'fixture'), TaskStoreError)
        await refused('unsupported records cannot be read or written', store.write_records(OWNER, RecordSet('fixture', (RecordWrite('z', {'n': 1}),))), TaskStoreError)
    unknown = TaskStore(cfg)
    async with unknown as store:
        check('an unregistered namespace is preserved and reported unsupported', not store.namespace_supported('fixture'))
    def bad_migrate(version: int, payload: dict) -> dict:
        raise RuntimeError('cannot lift')
    broken = TaskStore(cfg)
    broken.register(Namespace('fixture', 3, validate_record, bad_migrate))
    async with broken as store:
        check('a failing migration marks the namespace unsupported and the store still opens', not store.namespace_supported('fixture'))
    again = TaskStore(cfg)
    again.register(Namespace('fixture', 2, lambda p: validate_record({'n': p['count']})))
    async with again as store:
        rows = await store.records(OWNER, 'fixture')
        check('the failed migration rolled back to the records as they were',
              [(r.key, r.revision, r.payload) for r in rows] == [('a', 3, {'count': 9}), ('cursor', 2, {'count': 1})])


async def probe_migration(root: Path) -> None:
    print('\nversion two is lifted, not reset')
    cfg = config(root / 'schema-migration')
    async with TaskStore(cfg) as store:
        task = await create(store)
        seen = await observed(store, task, 'pending')
        await store.checkpoint(OWNER, task.id, seen.revision, READ, eligible_at=200, now=105)
    check('this SQLite can build a version-two fixture', sqlite3.sqlite_version_info >= (3, 35))
    db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
    db.executescript('''
        DROP TABLE feature_records; DROP TABLE feature_namespaces;
        ALTER TABLE tasks DROP COLUMN max_model_calls; ALTER TABLE tasks DROP COLUMN model_calls;
        PRAGMA user_version=2;
    ''')
    db.close()
    async with TaskStore(config(root / 'schema-migration', max_model_calls=5)) as store:
        lifted = await store.get(OWNER, task.id)
        db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
        version = db.execute('PRAGMA user_version').fetchone()[0]
        db.close()
        check('a version-two store opens as version three with every task in place and the new allowance at its configured value',
              version == 3 and lifted.status == 'queued' and lifted.next_step == READ and lifted.polls == 1
              and lifted.model_calls == 0 and lifted.max_model_calls == 5)
    db = sqlite3.connect(cfg.directory / 'tasks.sqlite3')
    db.execute('PRAGMA user_version=4')
    db.close()
    try:
        async with TaskStore(cfg):
            pass
    except TaskStoreError:
        check('a store newer than the runtime is still refused, not downgraded', True)
    else:
        check('a store newer than the runtime is still refused, not downgraded', False)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-task-probe-') as tmp:
        root = Path(tmp)
        config_file = root/'config.toml'
        config_file.write_text('[tasks]\nmax_active=7\nmax_attempts=3\nmax_polls=40\nbusy_timeout_s=0.25\nevidence_max_age_s=60\n')
        with patch.object(Path,'home',return_value=root):
            cfg=load_config(config_file)
        check('task configuration loads without starting a runtime',cfg.tasks.max_active==7 and cfg.tasks.max_attempts==3 and cfg.tasks.max_polls==40 and cfg.tasks.busy_timeout_s==0.25 and not cfg.tasks.enabled)
        with patch.object(Path, 'home', return_value=root), patch.dict(os.environ, {'CIEL_TASKS_MAX_POLLS': '90', 'CIEL_TASKS_BUSY_TIMEOUT_S': '0.75'}):
            overrides = load_config(config_file)
        check('polling and lock timeout environment overrides use the configuration boundary',
              overrides.tasks.max_polls == 90 and overrides.tasks.busy_timeout_s == 0.75)
        await probe_records(root)
        await probe_evidence(root)
        await probe_recovery(root)
        await probe_limits_and_storage(root)
        await probe_crashes(root)
        await probe_lifecycle(root)
        await probe_contention(root)
        await probe_polling(root)
        await probe_retarget(root)
        await probe_owner_controls(root)
        await probe_runner_side(root)
        await probe_feature_records(root)
        await probe_migration(root)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':
        asyncio.run(child(Path(sys.argv[2]),sys.argv[3]))
    else:
        asyncio.run(main())
