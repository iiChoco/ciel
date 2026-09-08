"""Reproductions for the task-store review of 2026-09-07. Temporary state only.

    uv run --no-sync python reports/2026-09-07-task-store-review-repro.py
    uv run --no-sync python reports/2026-09-07-task-store-review-repro.py --accept

The default retains the review's diagnostic scenarios. Acceptance mode asserts
that contention recovers, twenty polls fit, and an explicit retarget lets the
second head complete while completion without retargeting remains refused.
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from ciel.config import TasksConfig
from ciel.tasks import Criterion, Evidence, Origin, Scope, Specification, Step, TaskLimit, TaskStore, TaskStoreError

SPEC = Specification('checks pass', Scope(('inspect',), ('pr',)), (Criterion('c', 'pr', 'passed', 'head-1'),))
READ = Step('read', 'inspect', 'pr', ())


async def main(*, accept: bool = False) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 1. A concurrent reader — a status tool, a human with sqlite3 — poisons the runtime's store.
        cfg = replace(TasksConfig(), directory=root / 'busy')
        async with TaskStore(cfg) as store:
            await store.create(Origin('me', 'r1', 'voice'), SPEC, READ, now=100)
            reader = sqlite3.connect(root / 'busy' / 'tasks.sqlite3', isolation_level=None)
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM tasks').fetchone()  # holds a shared lock
            write_refused = False
            try:
                await store.create(Origin('me', 'r2', 'voice'), SPEC, READ, now=101)
                print('1. write succeeded despite a concurrent reader')
            except TaskStoreError as exc:
                write_refused = True
                print('1. a concurrent reader made the write fail:', exc)
            reader.execute('COMMIT'); reader.close()
            try:
                await store.list('me')
                print('   ...and the store recovered on its own')
            except TaskStoreError as exc:
                print('   ...and the store stays poisoned after the reader left:', exc)
            if accept:
                assert write_refused and len(await store.list('me')) == 1
                task = await store.create(Origin('me', 'r2', 'voice'), SPEC, READ, now=102)
                assert task.status == 'queued' and len(await store.list('me')) == 2
                print('   PASS: the refused write rolled back and the same store accepts new work')

        # 2. Every poll is an attempt: the default allowance ends a watch after eight looks.
        cfg = replace(TasksConfig(), directory=root / 'polls')
        async with TaskStore(cfg) as store:
            task = await store.create(Origin('me', 'r1', 'voice'), SPEC, READ, now=100)
            polls = 0
            try:
                for i in range(20):
                    t = 200 + i * 300
                    attempt = await store.claim('me', task.id, task.revision, now=t)
                    attempt = await store.mark_dispatched('me', attempt, now=t + 1)
                    task = await store.observe('me', attempt, (Evidence('c', 'pr', 'pending', 'adapter', t + 2, 'head-1'),), now=t + 3)
                    task = await store.checkpoint('me', task.id, task.revision, READ, eligible_at=t + 300, now=t + 4)
                    polls += 1
            except TaskLimit as exc:
                print(f'2. the watch ended after {polls} polls with: {exc}')
            else:
                print('2. twenty polls fit inside the allowance')
            if accept:
                assert polls == 20 and task.attempts == 0 and task.polls == 20
                print('   PASS: clean polls leave the retry allowance intact')

        # 3. A criterion's target revision is fixed at creation: a new head can never be verified.
        cfg = replace(TasksConfig(), directory=root / 'head')
        async with TaskStore(cfg) as store:
            task = await store.create(Origin('me', 'r1', 'voice'), SPEC, READ, now=100)
            attempt = await store.claim('me', task.id, task.revision, now=101)
            attempt = await store.mark_dispatched('me', attempt, now=102)
            task = await store.observe('me', attempt, (Evidence('c', 'pr', 'passed', 'adapter', 103, 'head-2'),), now=104)
            completion_refused = False
            try:
                await store.complete('me', task.id, task.revision, now=105)
                print('3. completion on the new head was accepted')
            except Exception as exc:  # noqa: BLE001
                completion_refused = True
                print('3. completion without retargeting was refused:' if accept else
                      '3. checks passed on head-2 but the task can only ever complete on head-1:', exc)
            methods = [m for m in dir(store) if not m.startswith('_')]
            print('   public methods that could re-target it:', [m for m in methods if 'criter' in m or 'target' in m or 'retarget' in m] or 'none')

            if accept:
                assert completion_refused
                task = await store.retarget('me', task.id, task.revision, 'pr', 'head-2', now=106)
                task = await store.complete('me', task.id, task.revision, now=107)
                assert task.status == 'done' and len(await store.notices('me')) == 1
                assert (await store.create(Origin('me', 'r1', 'voice'), SPEC, READ, now=108)).id == task.id
                print('   PASS: the retargeted head completes with one notice and the original request identity')
        if accept:
            print('all three review acceptance scenarios passed')


asyncio.run(main(accept='--accept' in sys.argv[1:]))
