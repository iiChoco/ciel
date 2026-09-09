"""The owner can save and steer a responsibility without starting an executor.

**One controller, two doors.** Conversation tools and admitted Chart sockets
share owner checks, store transitions, and journal recording. Identity arrives
from their trusted ingress, never from the model or a socket's role string.

**Saved means waiting.** Only an explicit PR-check watch can be created here.
Its repository, PR, and selected checks are parsed offline into a bounded read
mandate. Nothing contacts GitHub or queues an executor. Owner answers choose a
runtime-defined option; they never rewrite scope or refill allowances.

**History survives the caller.** Applied controls journal on the store worker
after commit, even if the caller disconnects. The optional best-effort journal
cannot turn a committed transition into an apparent rollback.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict
from typing import Any

from ciel.config import TasksConfig
from ciel.journal import ActionJournal
from ciel.task_context import TaskBinding
from ciel.tasks import Criterion, Namespace, Scope, Specification, Step, Task, TaskConflict, TaskStore, TaskStoreError

log = logging.getLogger(__name__)


class TaskController:
    def __init__(self, config: TasksConfig, journal: ActionJournal | None = None,
                 namespaces: tuple[Namespace, ...] = ()) -> None:
        self.config = config
        self.journal = journal
        self.namespaces = namespaces
        """The adapters' record namespaces, registered before the store opens."""
        self.store: TaskStore | None = None
        self.unavailable = 'Tasks are disabled.' if not config.enabled else 'Task storage is starting.'

    async def start(self) -> None:
        if not self.config.enabled or self.store is not None:
            return
        store = None
        try:
            if not isinstance(self.config.owner, str) or not self.config.owner.strip():
                raise ValueError('task owner must be a stable nonempty principal')
            if type(self.config.max_pending_controls) is not int or self.config.max_pending_controls < 1:
                raise ValueError('pending task controls must have a positive bound')
            store = TaskStore(self.config)
            for namespace in self.namespaces:
                store.register(namespace)
            await store.start()
        except asyncio.CancelledError:
            if store is not None:
                await store.close()
            self.unavailable = 'Task storage startup was cancelled.'
            raise
        except Exception:
            if store is not None:
                await store.close()
            self.unavailable = 'Task storage could not open; check its directory and schema version.'
            log.warning('task storage could not open', exc_info=True)
        else:
            self.store = store
            self.unavailable = ''

    async def close(self) -> None:
        store, self.store = self.store, None
        self.unavailable = 'Task storage is closed.'
        if store is not None:
            await store.close()

    def _store(self, binding: TaskBinding | None) -> TaskStore:
        if binding is None or not binding.origin.private or not binding.origin.attended or binding.origin.owner != self.config.owner:
            raise TaskConflict('Tasks are unavailable this turn; ask again in a live private owner turn.')
        if self.store is None:
            raise TaskStoreError(self.unavailable)
        return self.store

    async def view(self, binding: TaskBinding | None, task_id: str | None = None) -> dict[str, Any]:
        store = self._store(binding)
        assert binding is not None
        return await store.owner_view(binding.origin.owner, task_id, fence=binding.fence)

    def _record(self, operation: str, task: Task) -> None:
        if self.journal is not None:
            try:
                self.journal.record(tool=f'task_{operation}', args={'task_id': task.id, 'revision': task.revision},
                                    response=task.status, note='Explicit private owner control; no execution dispatched.')
            except Exception:
                log.warning('could not journal a committed task control', exc_info=True)

    async def apply(self, binding: TaskBinding | None, operation: str, args: dict[str, Any], *, revision: int | None = None) -> dict[str, Any]:
        store = self._store(binding)
        assert binding is not None
        if operation == 'create':
            repository = args.get('repository')
            pr = args.get('pr')
            checks = args.get('checks')
            if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
                raise ValueError('Name one repository as owner/repository.')
            if type(pr) is not int or pr < 1:
                raise ValueError('Name one positive pull request number.')
            if not isinstance(checks, list) or not 1 <= len(checks) <= 64 or any(not isinstance(c, str) or not c.strip() or len(c) > 256 for c in checks):
                raise ValueError('Name the exact checks to watch; unspecified checks need clarification.')
            checks = sorted(set(c.strip() for c in checks))
            target = f'https://github.com/{repository.lower()}/pull/{pr}'
            spec = Specification(f'Selected checks pass on {target}', Scope(('github.pr_checks',), (target,)),
                                 tuple(Criterion(c, target, 'success') for c in checks))
            task = await store.create(binding.origin, spec, Step('read', 'github.pr_checks', target),
                                      resource_wait=True, fence=binding.fence, record=lambda t: self._record(operation, t))
        else:
            task_id = args.get('task_id')
            if not isinstance(task_id, str) or not task_id:
                raise ValueError('A task ID is required.')
            task = await store.owner_control(binding.origin.owner, task_id, operation, revision=revision,
                                             question_id=args.get('question_id'), answer=args.get('answer'),
                                             fence=binding.fence, record=lambda t: self._record(operation, t))
        return {'task': asdict(task)}
