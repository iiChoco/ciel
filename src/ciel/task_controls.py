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

**Derived work comes through the registered adapter, never a door.** The
runtime-only ``derive`` takes the adapter's own ``Namespace`` object, so the
origin it records names a namespace application code registered, not a
string a model or a socket supplied. Mandate and grant controls share the
owner admission of every other control.

**A grant is the broker's yes, and nothing else.** The Chart form saves a
draft from an adapter's setup: the owner narrows operations and targets, the
host, accounts, outcome, and limits are the adapter's, shown and not chosen.
Approve binds the revision and digest the owner is looking at, asks the
question through the pipeline's broker on that one private session, and only
a yes activates; the store rechecks the draft at activation, so an edit while
the question is open leaves a draft, never a grant. No broker call is alive
while a form is being filled in.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from ciel.config import TasksConfig
from ciel.journal import ActionJournal
from ciel.task_context import TaskBinding
from ciel.tasks import (Criterion, FeatureRecord, GrantDraft, GrantSetup, Mandate, Namespace, Scope, Specification, StandingGrant, Step,
                        Task, TaskConflict, TaskStore, TaskStoreError)

Asker = Callable[[str, Callable[[str], Awaitable[None]]], Awaitable[bool]]
"""The broker's question through one channel: (question, send) -> yes."""

log = logging.getLogger(__name__)


class TaskController:
    def __init__(self, config: TasksConfig, journal: ActionJournal | None = None,
                 namespaces: tuple[Namespace, ...] = (), setups: tuple[GrantSetup, ...] = ()) -> None:
        self.config = config
        self.journal = journal
        self.namespaces = namespaces
        """The adapters' record namespaces, registered before the store opens."""
        self.setups = setups
        """What the adapters offer the owner to approve; the Chart form's fields."""
        self._asker: Asker | None = None
        self._requests: dict[str, Callable[[dict[str, Any]], Any]] = {}
        """Finite tasks a feature lets an owner turn ask for, by operation name;
        a builder returns the specification and first step, or an awaitable of them."""
        self._activated: dict[str, Callable[..., Any]] = {}
        """What a feature does when a grant for its namespace is activated, by namespace."""
        self._mandate_changed: dict[str, Callable[..., Any]] = {}
        """What a feature does when one of its mandates is paused, resumed, or ended."""
        self._controls: dict[str, Callable[[TaskStore, str, dict[str, Any]], Any]] = {}
        """A feature's own owner controls over its records, by operation name;
        each takes the store, the owner, and the arguments, and answers a dict."""
        self._summaries: dict[str, tuple[frozenset[str], Callable[[Task, tuple[FeatureRecord, ...]], str]]] = {}
        """How a feature describes a task's records to the owner: by namespace,
        the operations that mark a task as its own and the words."""
        self.execution = False
        """Whether a runner exists here: a resumed or answered task is then queued, not parked."""
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

    def bind_approval(self, asker: Asker | None) -> None:
        """The pipeline lends its broker; without one, approval says so."""
        self._asker = asker

    def bind_feature(self, namespace: Namespace, operations: frozenset[str], *,
                     requests: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
                     controls: dict[str, Callable[[TaskStore, str, dict[str, Any]], Any]] | None = None,
                     summary: Callable[[Task, tuple[FeatureRecord, ...]], str] | None = None,
                     activated: Callable[..., Any] | None = None, mandate_changed: Callable[..., Any] | None = None) -> None:
        """A registered feature's own doors: the finite tasks an owner turn may
        ask for (each builds a specification from the owner's arguments, in
        application code) and the words it gives a task's records."""
        if namespace not in self.namespaces:
            raise TaskConflict('only a registered adapter binds a feature')
        for operation, build in (requests or {}).items():
            self._requests[operation] = build
        for operation, control in (controls or {}).items():
            self._controls[operation] = control
        if activated is not None:
            self._activated[namespace.name] = activated
        if mandate_changed is not None:
            self._mandate_changed[namespace.name] = mandate_changed
        if summary is not None:
            self._summaries[namespace.name] = (frozenset(operations), summary)

    async def view(self, binding: TaskBinding | None, task_id: str | None = None) -> dict[str, Any]:
        store = self._store(binding)
        assert binding is not None
        received = 0
        if task_id is not None:
            # The look is the receipt: an attended private owner reading the
            # task's own record has received every notice it owed. Written
            # first, so the view they get already says so.
            received = await store.acknowledge(binding.origin.owner, task_id, binding.origin.lane, fence=binding.fence)
        view = await store.owner_view(binding.origin.owner, task_id, fence=binding.fence)
        if task_id is None:
            view['setups'] = [asdict(setup) for setup in self.setups]
        else:
            view['received'] = received
            task = await store.get(binding.origin.owner, task_id)
            for namespace, (operations, summarize) in self._summaries.items():
                if task.next_step.operation in operations and store.namespace_supported(namespace):
                    try:
                        view['feature'] = summarize(task, await store.records(binding.origin.owner, namespace))
                    except Exception:  # noqa: BLE001 - a feature's words are optional; the record is not
                        log.warning('a feature could not describe its task', exc_info=True)
        return view

    async def _follow_mandate(self, store: TaskStore, owner: str, mandates: tuple[Mandate, ...]) -> None:
        """A mandate moved; the feature that runs under it follows."""
        for mandate in mandates:
            hook = self._mandate_changed.get(mandate.namespace)
            if hook is None:
                continue
            try:
                await hook(store, owner, mandate)
            except Exception:  # noqa: BLE001 - the control committed; the feature's follow-through is logged
                log.warning('a feature could not follow its mandate', exc_info=True)

    def _setup(self, namespace: Any) -> GrantSetup:
        for setup in self.setups:
            if setup.namespace == namespace:
                return setup
        raise ValueError('No feature offers a grant by that name.')

    async def _draft_save(self, binding: TaskBinding, store: TaskStore, args: dict[str, Any], revision: int | None) -> GrantDraft:
        """The owner narrows a setup; everything else in the draft is the adapter's."""
        setup = self._setup(args.get('namespace'))
        chosen = {}
        for field, offered in (('operations', setup.operations), ('targets', setup.targets)):
            picked = args.get(field)
            if not isinstance(picked, list) or not picked or any(not isinstance(p, str) for p in picked):
                raise ValueError(f'Choose at least one of the offered {field}.')
            allowed = {value for value, _ in offered}
            if not set(picked) <= allowed:
                raise ValueError(f'Only the offered {field} can be granted.')
            chosen[field] = tuple(picked)
        draft_id = args.get('draft_id')
        if draft_id is not None and (not isinstance(draft_id, str) or not draft_id):
            raise ValueError('A draft ID is a string.')
        return await store.save_grant_draft(binding.origin.owner, setup.host, setup.namespace, setup.outcome,
                                            Scope(chosen['operations'], chosen['targets']), setup.limits, setup.bindings,
                                            draft_id=draft_id, expected_revision=revision, fence=binding.fence)

    def _question(self, draft: GrantDraft) -> str:
        setup = self._setup(draft.namespace)
        labels = {**dict(setup.operations), **dict(setup.targets)}
        operations = ', '.join(labels.get(o, o) for o in draft.scope.operations)
        targets = ', '.join(labels.get(t, t) for t in draft.scope.targets)
        accounts = ', '.join(value for _, value in draft.bindings)
        limits = draft.limits
        def span(seconds: float, unit_s: float, unit: str, one: str) -> str:
            count = seconds / unit_s
            return one if count == 1 else f'{count:g} {unit}s'
        window = span(limits.window_s, 3600, 'hour', 'hour') if limits.window_s < 86400 else span(limits.window_s, 86400, 'day', 'day')
        return (f'Approve a standing grant for {setup.title}: {operations} on {targets}'
                f'{" as " + accounts if accounts else ""}, up to {limits.max_per_window} per {window} and '
                f'{limits.max_children} in all, for {span(limits.lifetime_s, 86400, "day", "one day")} — okay?')

    async def approve(self, binding: TaskBinding | None, args: dict[str, Any], send: Callable[[str], Awaitable[None]]) -> dict[str, Any]:
        """Approve the exact draft the owner is looking at, through the broker.

        Refuses before asking when the draft is not the one shown, so a
        question is never put about a draft the owner has not seen. A no, a
        timeout, or a draft that moved while the question was open leaves the
        draft; only a yes that matches the draft at activation makes a grant.
        """
        store = self._store(binding)
        assert binding is not None
        draft_id, revision, digest = args.get('draft_id'), args.get('revision'), args.get('digest')
        if not isinstance(draft_id, str) or not draft_id or type(revision) is not int or not isinstance(digest, str) or not digest:
            raise ValueError('An approval names the draft, its revision, and its digest.')
        draft = await store.grant_draft(binding.origin.owner, draft_id)
        if draft.status != 'draft' or draft.revision != revision or draft.digest != digest:
            raise TaskConflict('The draft changed; review it again before approving.')
        if self._asker is None:
            raise TaskStoreError('Approval needs a live broker; this runtime has none.')
        question = self._question(draft)
        if not await self._asker(question, send):
            return {'approved': False, 'draft': asdict(draft)}
        approval_ref = f'chart:{draft.id}:{revision}:{binding.origin.request_id}'
        grant, mandate = await store.activate_grant(
            binding.origin, draft.id, revision, digest, approval_ref, fence=binding.fence,
            record=lambda m: self._record('grant_activate', m, note=f'Approved through the broker as {approval_ref}; no execution dispatched.'))
        started = self._activated.get(mandate.namespace)
        if started is not None:
            # The feature's own first move under its new mandate: for the
            # inbox, the watch. It runs after the activation committed, as
            # the attended owner turn that approved, and its failure leaves
            # the grant standing and says so.
            try:
                await started(store, binding.origin, grant, mandate)
            except Exception:  # noqa: BLE001 - the grant stands; the feature's start is its own affair
                log.warning('a feature could not start under its new mandate', exc_info=True)
        return {'approved': True, 'grant': asdict(grant), 'mandate': asdict(mandate)}

    def _record(self, operation: str, task: Task | Mandate | StandingGrant, note: str = 'Explicit private owner control; no execution dispatched.') -> None:
        if self.journal is not None:
            try:
                self.journal.record(tool=f'task_{operation}', args={'task_id': task.id, 'revision': task.revision},
                                    response=task.status, note=note)
            except Exception:
                log.warning('could not journal a committed task control', exc_info=True)

    async def derive(self, namespace: Namespace, mandate_id: str, expected_revision: int | None, event_key: str, source_revision: str,
                     specification: Specification, step: Step, *, now: float | None = None) -> Task:
        """Runtime-only: a registered adapter's event becomes a child of a standing mandate."""
        if namespace not in self.namespaces:
            raise TaskConflict('only a registered adapter derives work')
        if self.store is None:
            raise TaskStoreError(self.unavailable)
        task = await self.store.derive_task(self.config.owner, mandate_id, expected_revision, namespace.name, event_key, source_revision,
                                            specification, step, now=now)
        self._record('derive', task, note='Derived under a standing mandate; no execution dispatched.')
        return task

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
        elif operation in self._controls:
            result = await self._controls[operation](store, binding.origin.owner, args)
            if self.journal is not None:
                try:
                    self.journal.record(tool=f'task_{operation}', args={k: v for k, v in args.items() if isinstance(v, (str, int))},
                                        response=str(result)[:200], note='Explicit private owner control over a feature\'s records; no execution dispatched.')
                except Exception:
                    log.warning('could not journal a feature control', exc_info=True)
            return dict(result)
        elif operation in self._requests:
            try:
                built = self._requests[operation](args)
                spec, step = await built if inspect.isawaitable(built) else built
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
            task = await store.create(binding.origin, spec, step, fence=binding.fence, record=lambda t: self._record(operation, t))
            return {'task': asdict(task)}
        elif operation == 'grant_draft_save':
            draft = await self._draft_save(binding, store, args, revision)
            return {'draft': asdict(draft)}
        elif operation == 'grant_draft_discard':
            draft_id = args.get('draft_id')
            if not isinstance(draft_id, str) or not draft_id:
                raise ValueError('A draft ID is required.')
            draft = await store.discard_grant_draft(binding.origin.owner, draft_id, revision=revision, fence=binding.fence)
            return {'draft': asdict(draft)}
        elif operation in ('mandate_pause', 'mandate_resume', 'mandate_revoke'):
            mandate_id = args.get('mandate_id')
            if not isinstance(mandate_id, str) or not mandate_id:
                raise ValueError('A mandate ID is required.')
            mandate = await store.mandate_control(binding.origin.owner, mandate_id, operation.removeprefix('mandate_'), revision=revision,
                                                  fence=binding.fence, record=lambda m: self._record(operation, m))
            await self._follow_mandate(store, binding.origin.owner, (mandate,))
            return {'mandate': asdict(mandate)}
        elif operation in ('notices_mute', 'notices_unmute'):
            enabled = await store.set_notify(binding.origin.owner, operation == 'notices_unmute', fence=binding.fence)
            if self.journal is not None:
                try:
                    self.journal.record(tool=f'task_{operation}', args={}, response='on' if enabled else 'off',
                                        note='The notice switch; execution is unaffected.')
                except Exception:
                    log.warning('could not journal the notice switch', exc_info=True)
            return {'notify': enabled}
        elif operation == 'grant_revoke':
            grant_id = args.get('grant_id')
            if not isinstance(grant_id, str) or not grant_id:
                raise ValueError('A grant ID is required.')
            grant = await store.revoke_grant(binding.origin.owner, grant_id, revision=revision,
                                             fence=binding.fence, record=lambda g: self._record(operation, g))
            await self._follow_mandate(store, binding.origin.owner, tuple(m for m in await store.mandates(binding.origin.owner) if m.grant_id == grant.id))
            return {'grant': asdict(grant)}
        else:
            task_id = args.get('task_id')
            if not isinstance(task_id, str) or not task_id:
                raise ValueError('A task ID is required.')
            task = await store.owner_control(binding.origin.owner, task_id, operation, revision=revision,
                                             question_id=args.get('question_id'), answer=args.get('answer'), execution=self.execution,
                                             fence=binding.fence, record=lambda t: self._record(operation, t))
        return {'task': asdict(task)}
