"""A task keeps its place when the conversation and the process disappear.

**A task is a mandate.** Its desired state, observations, scope, actions, and
verification live together. Atlas remains the human-readable project context;
this store holds the runtime's records. The store executes no actions; owner controls save requests and keep them
waiting until an executor is available. Step kinds and owner/origin arguments must come from trusted runtime
context and a validated adapter when tools arrive, never from model claims.
Recording dispatch intent is not an authorization grant.

**One owner, one transaction.** A process lock protects a dedicated private
SQLite directory. All database work runs on one worker thread, with short
transactions spanning task state, attempts, history, evidence, and notification
intent. The rollback journal uses EXTRA synchronization; there is no separate
hosted database. Lock contention refuses one operation after a bounded wait;
only a certain rollback permits reuse. Corrupt, foreign, and newer schemas are
refused, not reset.

**A clean checkpoint is progress.** Only interrupted or uncertain attempts use
the retry allowance. Every claim uses a separate polling allowance; neither
allowance is refilled by a restart or a change to defaults.

**An interruption is not proof of failure.** A prepared attempt never had
permission to dispatch. A dispatched read may be retried; a dispatched mutation
may already have happened and must wait for reconciliation. Neither resuming
nor restarting may turn that uncertainty into another action. Revisions and
attempt identities fence late callbacks. Cancellation cannot undo a sent action.

**Done has evidence.** The only completion path compares fresh observations
with all exact-value criteria for the current attempt and target revision.
A trusted read can retarget a changed head through a revision-checked history
entry, without changing the original request, scope, or expected values.
It commits completion and a durable notice together; reading a notice never
re-executes work. More expressive verifiers and delivery belong to later stages.

**A feature's records have a namespace, not a column.** An adapter registers
a namespace with a version, a payload validator, and a migration; the store
keeps that namespace's records owner-only, revisioned, bounded, and committed
in the same transaction as the task transition they belong to. The store never
reads inside a payload. A namespace it does not recognise, or one newer than
its registration, is preserved untouched and reported unsupported: the tasks
that need it wait, and everything else runs.

**One step is one poll; one extraction is one model call.** The runner asks
for the oldest eligible task, and an attempt it must give up on is spent, not
forgotten: a read is requeued with backoff, a sent mutation waits for
reconciliation, and an exhausted allowance fails the task visibly. Model calls
have their own allowance, captured at creation like the others.

**A mandate can stand.** A standing grant is the owner's approval of one exact
scope, digest and all, through Proof Obligation; the mandate under it is the
responsibility that derives finite tasks from an adapter's events until the
owner pauses or revokes it, its grant expires, or its allowances run out.
Config caps what a grant may hold and can never mint one; a draft is never
executable; completing a child completes nothing above it.

**Nothing is sent that was not first written down.** A mutation's attempt
carries an intent before dispatch: the operation and exact target, the
payload and precondition digests, the authority found at that moment (the
grant at its activated revision, or the owner's approval of this exact
payload), the journal entry that correlates it with Inverse, and a deadline.
An outcome nobody knows is a reconciliation wait, never a retry: recovery
asks the adapter what happened, and after enough unanswered reads asks the
owner; only a verdict, applied or not, moves the task again, and a task the
owner ended meanwhile records the verdict without reviving.

**A result is owed until the owner has seen it.** Completion writes its
notice with the same transaction; that intent outlives any crash. Delivery
is recorded separately, attempt by attempt, with the private destination
that carried it, and a notice stays owed until the owner looks at the task
on a private lane, which is the receipt. Muting notices is a persisted
setting of its own: the runner keeps working under it, and nothing about it
reads as a pause.

**Derived work inherits, it never invents.** A child's origin names its
mandate, grant, adapter, event, and source revision, and nothing that claims
attendance or a human lane. The store admits it only inside the grant's scope,
deduplicates it on its event and source revision so a replay spends nothing,
revises it in place only while no dispatch intent exists and the scope
stands, and never reopens a finished one. A change the grant does not cover
is the adapter's inert proposal, and only the owner's approval of that exact
proposal, through the ordinary create path, becomes a task.

Async cancellation does not roll back a worker transaction already submitted.
Callers must reread state; originating request IDs deduplicate creation, and
expected revisions make every later stale retry fail explicitly.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import math
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypeVar

from ciel.config import TasksConfig

log = logging.getLogger(__name__)

Status = Literal['queued', 'running', 'verifying', 'waiting', 'paused', 'done', 'failed', 'cancelled']
Phase = Literal['prepared', 'dispatched', 'observed', 'checkpointed', 'verified', 'interrupted', 'unknown']
WaitReason = Literal['owner', 'external', 'resource', 'reconciliation']
_TERMINAL = frozenset(('done', 'failed', 'cancelled'))
_NOTICE = frozenset(('waiting', 'paused', 'done', 'failed', 'cancelled'))
_SCHEMA = 7
_POLICY = 1
"""What a grant's scope and limits mean; a grant records the version it was approved under."""
GrantStatus = Literal['active', 'revoked', 'expired']
MandateStatus = Literal['active', 'paused', 'revoked', 'expired']
DraftStatus = Literal['draft', 'activated', 'discarded']
_AUTHORITY_TABLES = '''
            CREATE TABLE grant_drafts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
                host TEXT NOT NULL, scope_json TEXT NOT NULL, bindings_json TEXT NOT NULL, digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('draft','activated','discarded')),
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                namespace TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '', limits_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE grants (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
                status TEXT NOT NULL CHECK(status IN ('active','revoked','expired')),
                scope_json TEXT NOT NULL, digest TEXT NOT NULL, approval_ref TEXT NOT NULL,
                policy_version INTEGER NOT NULL, limits_json TEXT NOT NULL,
                approved_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL, updated_at REAL NOT NULL
            );
            CREATE TABLE mandates (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0),
                status TEXT NOT NULL CHECK(status IN ('active','paused','revoked','expired')),
                outcome TEXT NOT NULL, namespace TEXT NOT NULL, grant_id TEXT NOT NULL REFERENCES grants(id),
                grant_revision INTEGER NOT NULL, children INTEGER NOT NULL, window_start REAL NOT NULL,
                window_count INTEGER NOT NULL, detail TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE derivations (
                owner TEXT NOT NULL, mandate_id TEXT NOT NULL REFERENCES mandates(id), namespace TEXT NOT NULL,
                event_key TEXT NOT NULL, source_revision TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id),
                created_at REAL NOT NULL, PRIMARY KEY(owner,mandate_id,namespace,event_key,source_revision)
            );
'''
_DISPATCH_TABLES = '''
            CREATE TABLE intents (
                action_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id),
                operation TEXT NOT NULL, target TEXT NOT NULL, payload_digest TEXT NOT NULL, precondition_digest TEXT NOT NULL,
                recipe_json TEXT NOT NULL, authority_json TEXT NOT NULL, journal_ref TEXT NOT NULL,
                deadline REAL NOT NULL, created_at REAL NOT NULL,
                resolution TEXT CHECK(resolution IN ('applied','not_applied')), resolved_at REAL, resolved_by TEXT,
                reconcile_reads INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE approvals (
                question_id TEXT PRIMARY KEY REFERENCES questions(id), task_id TEXT NOT NULL REFERENCES tasks(id),
                operation TEXT NOT NULL, target TEXT NOT NULL, payload_digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('asked','approved','declined')),
                answered_revision INTEGER, consumed_by TEXT
            );
'''
_DELIVERY_TABLES = '''
            CREATE TABLE deliveries (
                id TEXT PRIMARY KEY, notice_id TEXT NOT NULL REFERENCES outbox(id), destination TEXT NOT NULL,
                attempt INTEGER NOT NULL CHECK(attempt > 0),
                outcome TEXT NOT NULL CHECK(outcome IN ('sent','failed','acknowledged')),
                detail TEXT NOT NULL, at REAL NOT NULL, retry_at REAL
            );
            CREATE TABLE settings (
                owner TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(owner,key)
            );
'''
_FEATURE_TABLES = '''
            CREATE TABLE feature_namespaces (
                owner TEXT NOT NULL, namespace TEXT NOT NULL,
                schema_version INTEGER NOT NULL CHECK(schema_version > 0),
                PRIMARY KEY(owner,namespace)
            );
            CREATE TABLE feature_records (
                owner TEXT NOT NULL, namespace TEXT NOT NULL, record_key TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0), payload_json TEXT NOT NULL,
                PRIMARY KEY(owner,namespace,record_key),
                FOREIGN KEY(owner,namespace) REFERENCES feature_namespaces(owner,namespace)
            );
'''
RESOURCE_WAIT = "Saved; execution is unavailable."
Fence = Callable[[], AbstractContextManager[None]]
_APPLICATION = 0x4349454C
_T = TypeVar('_T')


class TaskStoreError(RuntimeError):
    """Storage is unavailable; the caller must not execute task work."""


class TaskBusy(TaskStoreError):
    """A lock refused this operation; its rollback is certain and the store is usable."""


class TaskConflict(ValueError):
    """The expected state, revision, request, or owner no longer matches."""


class TaskLimit(ValueError):
    """A persisted allowance or configured admission bound is exhausted."""


class NoAuthority(TaskConflict):
    """Nothing on the record permits this mutation right now."""


@dataclass(frozen=True, slots=True)
class HumanOrigin:
    """A live private owner turn; attendance is not physical room presence."""

    owner: str
    request_id: str
    lane: Literal['voice', 'typed', 'web', 'discord']
    attended: bool = True
    private: bool = True
    ingress_ids: tuple[str, ...] = ()
    approval_ref: str | None = None
    """The broker's record of the exact proposal this turn approved, when the
    task is the answer to one; None for a request made in the owner's words."""
    kind: Literal['human'] = 'human'


Origin = HumanOrigin
"""The name every turn, binding, and tool has used since before there was
another kind; a human origin is still the only one a turn can mint."""


@dataclass(frozen=True, slots=True)
class DerivedOrigin:
    """Work the runtime derived under a standing mandate. It names what it
    inherits from and nothing that claims attendance or a human lane."""

    owner: str
    request_id: str
    parent_mandate_id: str
    parent_revision: int
    grant_id: str
    grant_revision: int
    adapter_namespace: str
    event_key: str
    """The adapter's stable identity for the event and the operation it needs."""
    source_revision: str
    derived_at: float
    trigger_key: str
    """The event key at one source revision: the identity a replay lands on."""
    kind: Literal['derived'] = 'derived'


TaskOrigin = HumanOrigin | DerivedOrigin


@dataclass(frozen=True, slots=True)
class Scope:
    operations: tuple[str, ...]
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Criterion:
    id: str
    target: str
    expected: str
    target_revision: str | None = None


@dataclass(frozen=True, slots=True)
class Step:
    kind: Literal['read', 'mutation']
    operation: str
    target: str
    arguments: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Specification:
    outcome: str
    scope: Scope
    criteria: tuple[Criterion, ...]
    project: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    criterion_id: str
    target: str
    observed: str | None
    source: str
    observed_at: float
    target_revision: str | None = None


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    origin: TaskOrigin
    specification: Specification
    next_step: Step
    status: Status
    revision: int
    attempts: int
    """Attempts that ended without a clean checkpoint or completion."""
    max_attempts: int
    polls: int
    """All claimed execution rounds, including retries and mutations."""
    max_polls: int
    evidence_max_age_s: float
    current_attempt: str | None
    wait_reason: str | None
    detail: str
    eligible_at: float
    created_at: float
    updated_at: float
    model_calls: int
    """Isolated extraction calls made over the task's life."""
    max_model_calls: int


@dataclass(frozen=True, slots=True)
class Namespace:
    """What an adapter tells the store about its records, in application code."""

    name: str
    version: int
    validate: Callable[[dict[str, Any]], None]
    """Raises ValueError for a payload the adapter would not have written."""
    migrate: Callable[[int, dict[str, Any]], dict[str, Any]] | None = None
    """Lifts a payload from an older stored version; None means an older
    store is unsupported rather than silently reinterpreted."""


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    namespace: str
    key: str
    revision: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RecordWrite:
    """One record in a write-set. ``payload`` None deletes; ``expected_revision``
    None skips the check, 0 requires that the record does not exist yet."""

    key: str
    payload: dict[str, Any] | None
    expected_revision: int | None = None


@dataclass(frozen=True, slots=True)
class RecordSet:
    namespace: str
    writes: tuple[RecordWrite, ...]


@dataclass(frozen=True, slots=True)
class Attempt:
    id: str
    task_id: str
    task_revision: int
    step: Step
    phase: Phase
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class Transition:
    revision: int
    before: str | None
    after: Status
    detail: str
    at: float


@dataclass(frozen=True, slots=True)
class Notice:
    id: str
    task_id: str
    revision: int
    status: Status
    outcome: str
    detail: str
    created_at: float


@dataclass(frozen=True, slots=True)
class Delivery:
    """One attempt to put a notice in front of the owner, or the owner's look at it."""

    id: str
    notice_id: str
    destination: str
    """The private lane: spoken, message, held-note, or the lane of the look."""
    attempt: int
    outcome: Literal['sent', 'failed', 'acknowledged']
    detail: str
    at: float
    retry_at: float | None


@dataclass(frozen=True, slots=True)
class Authority:
    """Where the right to send one mutation came from, as found at dispatch."""

    kind: Literal['grant', 'approval']
    grant_id: str | None
    grant_revision: int | None
    approval_ref: str | None


@dataclass(frozen=True, slots=True)
class Intent:
    """Dispatch intent: what one attempt was about to send, written before it was."""

    action_id: str
    task_id: str
    attempt_id: str
    operation: str
    target: str
    payload_digest: str
    precondition_digest: str
    recipe: dict[str, Any]
    """The adapter's own instructions for finding out later whether it happened."""
    authority: Authority
    journal_ref: str
    deadline: float
    """Past this, an unsent intent is abandoned rather than sent late."""
    created_at: float
    resolution: Literal['applied', 'not_applied'] | None
    resolved_at: float | None
    resolved_by: str | None
    reconcile_reads: int


@dataclass(frozen=True, slots=True)
class GrantLimits:
    """What a grant may spend; config caps each, and the smaller number governs."""

    max_children: int
    """Finite tasks the mandate may derive over its life."""
    window_s: float
    max_per_window: int
    """Derivations per window; windows are counted from the persisted start, never a restart."""
    lifetime_s: float
    """From approval to expiry."""


@dataclass(frozen=True, slots=True)
class GrantDraft:
    """What the owner is looking at in Chart; never executable."""

    id: str
    owner: str
    revision: int
    host: str
    """The execution host the bindings were resolved on."""
    namespace: str
    """The one registered adapter the mandate would derive through."""
    outcome: str
    scope: Scope
    limits: GrantLimits
    bindings: tuple[tuple[str, str], ...]
    """Resolved account and target identities, as the adapter's setup names them."""
    digest: str
    """Over everything above but the identity: what the owner's yes is bound to."""
    status: DraftStatus
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class GrantSetup:
    """What an adapter offers the owner to approve: the fields of the Chart
    form, resolved on the execution host in application code. The owner
    narrows the operations and targets; the rest is shown, not chosen."""

    namespace: str
    title: str
    outcome: str
    host: str
    operations: tuple[tuple[str, str], ...]
    """(operation, label) for every operation the adapter can be granted."""
    targets: tuple[tuple[str, str], ...]
    """(target, label): resolved identities such as a calendar, never a free string."""
    bindings: tuple[tuple[str, str], ...]
    """Resolved accounts the grant would act as."""
    limits: GrantLimits


@dataclass(frozen=True, slots=True)
class StandingGrant:
    id: str
    owner: str
    revision: int
    status: GrantStatus
    scope: Scope
    digest: str
    approval_ref: str
    """The broker's record of the yes that made it."""
    policy_version: int
    limits: GrantLimits
    approved_at: float
    expires_at: float
    revoked_at: float | None
    updated_at: float


@dataclass(frozen=True, slots=True)
class Mandate:
    """A standing responsibility under one grant; it derives finite tasks and is never run itself."""

    id: str
    owner: str
    revision: int
    status: MandateStatus
    outcome: str
    namespace: str
    """The one registered adapter whose events derive work here."""
    grant_id: str
    grant_revision: int
    children: int
    window_start: float
    window_count: int
    detail: str
    created_at: float
    updated_at: float


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')


def _clock(value: float | None) -> float:
    now = time.time() if value is None else value
    if isinstance(now, bool) or not isinstance(now, (float, int)) or not math.isfinite(now) or now < 0:
        raise ValueError('time must be a finite UTC epoch')
    return float(now)


def _step(raw: dict[str, Any]) -> Step:
    return Step(raw['kind'], raw['operation'], raw['target'], tuple(tuple(pair) for pair in raw['arguments']))


def _scope(raw: dict[str, Any]) -> Scope:
    return Scope(tuple(raw['operations']), tuple(raw['targets']))


def _spec(raw: dict[str, Any]) -> Specification:
    return Specification(raw['outcome'], _scope(raw['scope']), tuple(Criterion(**item) for item in raw['criteria']), raw['project'])


def _origin(raw: dict[str, Any]) -> TaskOrigin:
    if raw.get('kind', 'human') == 'derived':
        return DerivedOrigin(**raw)
    fields = {'kind': 'human', 'approval_ref': None, **raw, 'ingress_ids': tuple(raw.get('ingress_ids', ()))}
    return HumanOrigin(**fields)


def _trigger(event_key: str, source_revision: str) -> str:
    return _json((event_key, source_revision))


def _digest(host: str, namespace: str, outcome: str, scope: Scope, limits: GrantLimits, bindings: tuple[tuple[str, str], ...]) -> str:
    """What the owner approves: the normalized draft, byte for byte."""
    return hashlib.sha256(_json({'host': host, 'namespace': namespace, 'outcome': outcome, 'scope': asdict(scope),
                                 'limits': asdict(limits), 'bindings': bindings}).encode()).hexdigest()


class TaskStore:
    """Runtime-only storage API; no method dispatches an external operation."""

    def __init__(self, config: TasksConfig) -> None:
        self._config = config
        self._directory = config.directory.expanduser()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ciel-tasks')
        self._db: sqlite3.Connection | None = None
        self._lock_fd: int | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._poisoned = False
        self._namespaces: dict[str, Namespace] = {}
        self._unsupported: set[str] = set()
        if any(type(n) is not int or n < 1 for n in (config.max_active, config.max_attempts, config.max_polls, config.max_record_chars,
                                                      config.max_model_calls, config.max_feature_records,
                                                      config.max_grant_children, config.max_grant_per_window)):
            raise ValueError('task limits must be positive integers')
        if _clock(config.evidence_max_age_s) <= 0 or _clock(config.max_grant_lifetime_s) <= 0:
            raise ValueError('evidence age and grant lifetime must be positive')
        _clock(config.busy_timeout_s)

    async def __aenter__(self) -> TaskStore:
        try:
            await self.start()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _run(self, operation: Callable[[], _T]) -> _T:
        if self._closed:
            raise TaskStoreError('task store is closed')
        future = asyncio.get_running_loop().run_in_executor(self._executor, operation)
        future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(future)

    async def start(self, *, now: float | None = None) -> tuple[Task, ...]:
        stamp = _clock(now)
        return await self._run(lambda: self._open(stamp))

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            async def finish() -> None:
                try:
                    await asyncio.get_running_loop().run_in_executor(self._executor, self._release)
                finally:
                    await asyncio.to_thread(self._executor.shutdown, wait=True)
            self._close_task = asyncio.create_task(finish())
        await asyncio.shield(self._close_task)

    def _release(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _private_file(self, path: Path) -> int:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            os.close(fd)
            raise TaskStoreError('task files must be private regular files owned by this user')
        os.fchmod(fd, 0o600)
        return fd

    def _open(self, now: float) -> tuple[Task, ...]:
        if self._db is not None:
            if self._poisoned:
                raise TaskStoreError('task store requires reopening after a storage failure')
            return ()
        try:
            if self._directory.is_symlink():
                raise TaskStoreError('task directory cannot be a symlink')
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self._directory.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise TaskStoreError('task directory must be dedicated to Ciel and owner-only (0700)')
            self._lock_fd = self._private_file(self._directory / 'owner.lock')
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TaskStoreError('another runtime owns this task store') from exc
            path = self._directory / 'tasks.sqlite3'
            fresh = not path.exists()
            os.close(self._private_file(path))
            for suffix in ('-journal', '-wal', '-shm'):
                sidecar = Path(str(path) + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    os.close(self._private_file(sidecar))
            self._db = sqlite3.connect(path, isolation_level=None, timeout=self._config.busy_timeout_s)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA foreign_keys=ON')
            if not fresh:
                version = self._db.execute('PRAGMA user_version').fetchone()[0]
                app = self._db.execute('PRAGMA application_id').fetchone()[0]
                if app != _APPLICATION or version not in (2, 3, 4, 5, 6, _SCHEMA):
                    raise TaskStoreError('unsupported task schema; no migration or downgrade was attempted')
                if self._db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise TaskStoreError('task database failed its integrity check')
            self._db.execute('PRAGMA journal_mode=DELETE')
            self._db.execute('PRAGMA synchronous=EXTRA')
            if not fresh and version == 2:
                self._migrate_from_2()
            if not fresh and version < 4:
                self._migrate_from_3()
            if not fresh and version < 5:
                self._migrate_from_4()
            if not fresh and version < 6:
                self._migrate_from_5()
            if not fresh and version < _SCHEMA:
                self._migrate_from_6()
            if not fresh:
                self._validate_rows()
            if fresh:
                self._initialize()
                self._validate_rows()
            def open_() -> tuple[Task, ...]:
                self._reconcile_namespaces()
                return self._recover(now)
            return self._transaction(open_)
        except BaseException as exc:
            self._release()
            if isinstance(exc, (sqlite3.Error, OSError, KeyError, TypeError, ValueError)):
                raise TaskStoreError('task store could not be opened; existing data was not reset') from exc
            raise

    def _initialize(self) -> None:
        assert self._db is not None
        self._db.executescript(f'''
            BEGIN IMMEDIATE;
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                request_json TEXT NOT NULL, specification_json TEXT NOT NULL, step_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','verifying','waiting','paused','done','failed','cancelled')),
                revision INTEGER NOT NULL CHECK(revision > 0), attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL, polls INTEGER NOT NULL, max_polls INTEGER NOT NULL, evidence_max_age REAL NOT NULL,
                current_attempt TEXT, wait_reason TEXT, detail TEXT NOT NULL,
                eligible_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                model_calls INTEGER NOT NULL DEFAULT 0, max_model_calls INTEGER NOT NULL DEFAULT {int(self._config.max_model_calls)},
                UNIQUE(owner,request_id)
            );
            CREATE TABLE attempts (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                task_revision INTEGER NOT NULL, step_json TEXT NOT NULL,
                phase TEXT NOT NULL CHECK(phase IN ('prepared','dispatched','observed','checkpointed','verified','interrupted','unknown')),
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE evidence (
                attempt_id TEXT NOT NULL REFERENCES attempts(id), criterion_id TEXT NOT NULL,
                record_json TEXT NOT NULL, PRIMARY KEY(attempt_id,criterion_id)
            );
            CREATE TABLE transitions (
                task_id TEXT NOT NULL REFERENCES tasks(id), revision INTEGER NOT NULL,
                before_status TEXT, after_status TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL,
                PRIMARY KEY(task_id,revision)
            );
            CREATE TABLE outbox (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                revision INTEGER NOT NULL, status TEXT NOT NULL, outcome TEXT NOT NULL,
                detail TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(task_id,revision)
            );
            CREATE TABLE ingress (
                owner TEXT NOT NULL, ingress_id TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(id), PRIMARY KEY(owner,ingress_id)
            );
            CREATE TABLE questions (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                revision INTEGER NOT NULL, prompt TEXT NOT NULL, choices_json TEXT NOT NULL,
                step_json TEXT, answer TEXT, answered_revision INTEGER,
                kind TEXT NOT NULL DEFAULT 'step' CHECK(kind IN ('step','approval','reconciliation'))
            );
            CREATE TABLE bindings (
                attempt_id TEXT NOT NULL REFERENCES attempts(id), task_revision INTEGER NOT NULL,
                client_generation TEXT NOT NULL, tool_use_id TEXT NOT NULL, journal_ref TEXT,
                PRIMARY KEY(client_generation,tool_use_id)
            );
            {_FEATURE_TABLES}
            {_AUTHORITY_TABLES}
            {_DISPATCH_TABLES}
            {_DELIVERY_TABLES}
            PRAGMA application_id={_APPLICATION};
            PRAGMA user_version={_SCHEMA};
            COMMIT;
        ''')

    def _migrate_from_2(self) -> None:
        """Version two knew nothing of model calls or feature records. Both
        arrive with defaults, so every existing task keeps its place and its
        allowances; nothing is reinterpreted."""
        assert self._db is not None
        self._db.executescript(f'''
            BEGIN IMMEDIATE;
            ALTER TABLE tasks ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE tasks ADD COLUMN max_model_calls INTEGER NOT NULL DEFAULT {int(self._config.max_model_calls)};
            {_FEATURE_TABLES}
            PRAGMA user_version=3;
            COMMIT;
        ''')
        log.info('task store migrated from schema 2 to 3')

    def _migrate_from_3(self) -> None:
        """Version three knew one kind of origin and no authority. Every stored
        origin was a human turn and now says so, written exactly as a fresh
        create would write it, so a repeated request still finds its task."""
        assert self._db is not None
        self._db.execute('BEGIN IMMEDIATE')
        for statement in _AUTHORITY_TABLES.split(';'):
            if statement.strip():
                self._db.execute(statement)
        for row in self._db.execute('SELECT id,request_json FROM tasks').fetchall():
            request = json.loads(row['request_json'])
            request['origin'] = asdict(_origin(request['origin']))
            self._db.execute('UPDATE tasks SET request_json=? WHERE id=?', (_json(request), row['id']))
        self._db.execute('PRAGMA user_version=4')
        self._db.execute('COMMIT')
        log.info('task store migrated from schema 3 to 4')

    def _migrate_from_4(self) -> None:
        """Version four's draft did not yet say which adapter, outcome, and
        limits it was for. Nothing could have saved one but a probe, and a
        draft is never executable, so an open one is discarded rather than
        guessed at; activated and discarded ones keep their history."""
        assert self._db is not None
        # A store lifted from three in this same open already has the columns:
        # the tables are created in their current shape, so only a store that
        # actually ran as version four has anything to add.
        present = {row['name'] for row in self._db.execute('PRAGMA table_info(grant_drafts)')}
        additions = ''.join(f"ALTER TABLE grant_drafts ADD COLUMN {name} {kind};" for name, kind in (
            ('namespace', "TEXT NOT NULL DEFAULT ''"), ('outcome', "TEXT NOT NULL DEFAULT ''"), ('limits_json', "TEXT NOT NULL DEFAULT '{}'"))
            if name not in present)
        self._db.executescript(f'''
            BEGIN IMMEDIATE;
            {additions}
            UPDATE grant_drafts SET status='discarded',revision=revision+1 WHERE status='draft';
            PRAGMA user_version=5;
            COMMIT;
        ''')
        log.info('task store migrated from schema 4 to 5')

    def _migrate_from_5(self) -> None:
        """Version five sent nothing, so it has no intents to carry over; its
        questions were all step questions and now say so."""
        assert self._db is not None
        present = {row['name'] for row in self._db.execute('PRAGMA table_info(questions)')}
        addition = "ALTER TABLE questions ADD COLUMN kind TEXT NOT NULL DEFAULT 'step' CHECK(kind IN ('step','approval','reconciliation'));" if 'kind' not in present else ''
        self._db.executescript(f'''
            BEGIN IMMEDIATE;
            {addition}
            {_DISPATCH_TABLES}
            PRAGMA user_version=6;
            COMMIT;
        ''')
        log.info('task store migrated from schema 5 to 6')

    def _migrate_from_6(self) -> None:
        """Version six wrote notices and delivered none; every notice it left
        is still owed, which is exactly what an empty deliveries table says."""
        assert self._db is not None
        self._db.executescript(f'''
            BEGIN IMMEDIATE;
            {_DELIVERY_TABLES}
            PRAGMA user_version={_SCHEMA};
            COMMIT;
        ''')
        log.info('task store migrated from schema 6 to %d', _SCHEMA)

    def register(self, namespace: Namespace) -> None:
        """Application code announces an adapter's records before the store opens."""
        if self._db is not None:
            raise TaskStoreError('namespaces are registered before the store opens')
        _text(namespace.name, 'namespace')
        if type(namespace.version) is not int or namespace.version < 1:
            raise ValueError('a namespace version is a positive integer')
        if namespace.name in self._namespaces:
            raise ValueError('namespace is already registered')
        self._namespaces[namespace.name] = namespace

    def namespace_supported(self, name: str) -> bool:
        return name in self._namespaces and name not in self._unsupported

    def _reconcile_namespaces(self) -> None:
        """Stored versions meet registered ones once, at open. Older records
        are lifted by the adapter's own migration; anything the adapter cannot
        lift, or does not know, is left exactly as it was and marked unsupported."""
        assert self._db is not None
        for row in self._db.execute('SELECT * FROM feature_namespaces').fetchall():
            name, stored = row['namespace'], row['schema_version']
            spec = self._namespaces.get(name)
            if spec is None or stored > spec.version or (stored < spec.version and spec.migrate is None):
                self._unsupported.add(name)
                log.warning('feature namespace %r (stored version %d) is unsupported by this runtime', name, stored)
                continue
            if stored == spec.version:
                continue
            # A savepoint per namespace: one adapter's failed migration rolls
            # back its own records only and leaves the rest of the open intact.
            self._db.execute('SAVEPOINT namespace_migration')
            try:
                assert spec.migrate is not None
                for record in self._db.execute('SELECT * FROM feature_records WHERE owner=? AND namespace=?', (row['owner'], name)).fetchall():
                    payload = spec.migrate(stored, json.loads(record['payload_json']))
                    if not isinstance(payload, dict):
                        raise ValueError('a migrated payload is an object')
                    spec.validate(payload)
                    self._db.execute('UPDATE feature_records SET payload_json=?,revision=revision+1 WHERE owner=? AND namespace=? AND record_key=?',
                                     (self._bounded(payload), row['owner'], name, record['record_key']))
                self._db.execute('UPDATE feature_namespaces SET schema_version=? WHERE owner=? AND namespace=?', (spec.version, row['owner'], name))
                self._db.execute('RELEASE namespace_migration')
                log.info('feature namespace %r migrated from version %d to %d', name, stored, spec.version)
            except Exception:  # noqa: BLE001 - the adapter's migration is its own; the records stay as they were
                self._db.execute('ROLLBACK TO namespace_migration')
                self._db.execute('RELEASE namespace_migration')
                self._unsupported.add(name)
                log.warning('feature namespace %r could not be migrated from version %d; its records are preserved', name, stored, exc_info=True)

    def _namespace(self, name: str) -> Namespace:
        spec = self._namespaces.get(name)
        if spec is None or name in self._unsupported:
            raise TaskStoreError(f'feature namespace {name!r} is unsupported by this runtime')
        return spec

    def _apply_records(self, owner: str, records: RecordSet) -> None:
        assert self._db is not None
        spec = self._namespace(records.namespace)
        _text(owner, 'owner')
        self._db.execute('INSERT OR IGNORE INTO feature_namespaces VALUES(?,?,?)', (owner, spec.name, spec.version))
        seen: set[str] = set()
        for write in records.writes:
            _text(write.key, 'record key')
            if write.key in seen:
                raise ValueError('a write-set names each record once')
            seen.add(write.key)
            expected = write.expected_revision
            if expected is not None and (type(expected) is not int or expected < 0):
                raise ValueError('an expected record revision is a non-negative integer')
            row = self._db.execute('SELECT revision FROM feature_records WHERE owner=? AND namespace=? AND record_key=?',
                                   (owner, spec.name, write.key)).fetchone()
            if write.payload is None:
                if row is None or (expected is not None and row['revision'] != expected):
                    raise TaskConflict('record revision changed or the record does not exist')
                self._db.execute('DELETE FROM feature_records WHERE owner=? AND namespace=? AND record_key=?', (owner, spec.name, write.key))
                continue
            if not isinstance(write.payload, dict):
                raise ValueError('a record payload is an object')
            spec.validate(write.payload)
            data = self._bounded(write.payload)
            if row is None:
                if expected not in (None, 0):
                    raise TaskConflict('record revision changed or the record does not exist')
                count = self._db.execute('SELECT count(*) FROM feature_records WHERE owner=? AND namespace=?', (owner, spec.name)).fetchone()[0]
                if count >= self._config.max_feature_records:
                    raise TaskLimit('feature record allowance exhausted')
                self._db.execute('INSERT INTO feature_records VALUES(?,?,?,?,?)', (owner, spec.name, write.key, 1, data))
            else:
                if expected is not None and row['revision'] != expected:
                    raise TaskConflict('record revision changed or the record does not exist')
                self._db.execute('UPDATE feature_records SET revision=revision+1,payload_json=? WHERE owner=? AND namespace=? AND record_key=?',
                                 (data, owner, spec.name, write.key))

    def _transaction(self, operation: Callable[[], _T], fence: Fence | None = None) -> _T:
        if self._db is None or self._poisoned:
            raise TaskStoreError('task store is not available')
        try:
            self._db.execute('BEGIN IMMEDIATE')
            with fence() if fence else nullcontext():
                result = operation()
                self._db.execute('COMMIT')
                return result
        except BaseException as exc:
            rollback_failed = False
            if self._db.in_transaction:
                try:
                    self._db.execute('ROLLBACK')
                except sqlite3.Error:
                    rollback_failed = True
            if rollback_failed or self._db.in_transaction:
                self._poisoned = True
                raise TaskStoreError('task rollback failed; execution must stop') from exc
            if isinstance(exc, sqlite3.Error):
                code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
                if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise TaskBusy('task database is busy; operation was refused') from exc
                self._poisoned = True
                raise TaskStoreError('task transaction failed; execution must stop') from exc
            raise

    def _bounded(self, value: Any) -> str:
        data = _json(value)
        if len(data) > self._config.max_record_chars:
            raise TaskLimit('task record exceeds its size allowance')
        return data

    def _validate_request(self, origin: TaskOrigin, spec: Specification, step: Step) -> None:
        _text(origin.owner, 'owner')
        _text(origin.request_id, 'request identity')
        if isinstance(origin, HumanOrigin):
            if origin.kind != 'human':
                raise ValueError('a human origin says so')
            if not isinstance(origin.ingress_ids, tuple) or len(set(origin.ingress_ids)) != len(origin.ingress_ids):
                raise ValueError('ingress identities must be a unique immutable tuple')
            for identity in origin.ingress_ids:
                _text(identity, 'ingress identity')
            if origin.attended is not True or origin.private is not True or origin.lane not in ('voice','typed','web','discord'):
                raise TaskConflict('only an attended private owner request may create task responsibility')
            if origin.approval_ref is not None:
                _text(origin.approval_ref, 'approval reference')
        elif isinstance(origin, DerivedOrigin):
            if origin.kind != 'derived':
                raise ValueError('a derived origin says so')
            for value, name in ((origin.parent_mandate_id, 'mandate'), (origin.grant_id, 'grant'), (origin.adapter_namespace, 'namespace'),
                                (origin.event_key, 'event key'), (origin.source_revision, 'source revision')):
                _text(value, name)
            if any(type(value) is not int or value < 1 for value in (origin.parent_revision, origin.grant_revision)):
                raise ValueError('a derived origin names positive parent and grant revisions')
            if origin.trigger_key != _trigger(origin.event_key, origin.source_revision) or _clock(origin.derived_at) != origin.derived_at:
                raise ValueError('a derived origin carries its own trigger key and derivation time')
        else:
            raise TaskConflict('unknown origin kind')
        _text(spec.outcome, 'outcome')
        if not all(isinstance(value,tuple) for value in (spec.scope.operations,spec.scope.targets,spec.criteria)):
            raise ValueError('scope and criteria must be immutable tuples')
        if spec.project is not None and not isinstance(spec.project,str):
            raise ValueError('project must be a name or absent')
        if not spec.scope.operations or not spec.scope.targets or not spec.criteria:
            raise ValueError('a task needs explicit operations, targets, and completion criteria')
        for value in (*spec.scope.operations, *spec.scope.targets):
            _text(value, 'scope entry')
        ids = set()
        for criterion in spec.criteria:
            _text(criterion.id, 'criterion identity')
            if criterion.id in ids or criterion.target not in spec.scope.targets:
                raise ValueError('criteria must be unique and inside the task scope')
            ids.add(criterion.id)
            if not isinstance(criterion.expected, str) or (criterion.target_revision is not None and not isinstance(criterion.target_revision, str)):
                raise ValueError('exact-value criteria require string values and revisions')
        self._validate_step(spec, step)

    def _validate_step(self, spec: Specification, step: Step) -> None:
        if step.kind not in ('read','mutation') or step.operation not in spec.scope.operations or step.target not in spec.scope.targets:
            raise TaskConflict('step is outside the task scope')
        if not isinstance(step.arguments,tuple) or any(not isinstance(pair,tuple) or len(pair) != 2 for pair in step.arguments):
            raise ValueError('arguments must be immutable name/value pairs')
        keys = set()
        for key, value in step.arguments:
            _text(key, 'argument name')
            if key in keys or not isinstance(value, str):
                raise ValueError('step arguments must have unique names and string values')
            keys.add(key)

    def _task(self, row: sqlite3.Row) -> Task:
        request = json.loads(row['request_json'])
        return Task(row['id'], _origin(request['origin']), _spec(json.loads(row['specification_json'])),
                    _step(json.loads(row['step_json'])), row['status'], row['revision'],
                    row['attempts'], row['max_attempts'], row['polls'], row['max_polls'], row['evidence_max_age'], row['current_attempt'],
                    row['wait_reason'], row['detail'], row['eligible_at'], row['created_at'], row['updated_at'],
                    row['model_calls'], row['max_model_calls'])

    def _get(self, owner: str, task_id: str, revision: int | None = None) -> Task:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM tasks WHERE id=? AND owner=?', (task_id, owner)).fetchone()
        if row is None:
            raise TaskConflict('task is not available to this owner')
        task = self._task(row)
        if revision is not None and (type(revision) is not int or task.revision != revision):
            raise TaskConflict('task revision changed')
        return task

    def _validate_rows(self) -> None:
        assert self._db is not None
        columns = {
            'tasks': 'id owner request_id request_json specification_json step_json status revision attempts max_attempts polls max_polls evidence_max_age current_attempt wait_reason detail eligible_at created_at updated_at model_calls max_model_calls',
            'feature_namespaces': 'owner namespace schema_version',
            'feature_records': 'owner namespace record_key revision payload_json',
            'attempts': 'id task_id task_revision step_json phase created_at updated_at',
            'evidence': 'attempt_id criterion_id record_json',
            'transitions': 'task_id revision before_status after_status detail at',
            'outbox': 'id task_id revision status outcome detail created_at',
            'ingress': 'owner ingress_id task_id',
            'questions': 'id task_id revision prompt choices_json step_json answer answered_revision kind',
            'bindings': 'attempt_id task_revision client_generation tool_use_id journal_ref',
            'grant_drafts': 'id owner revision host scope_json bindings_json digest status created_at updated_at namespace outcome limits_json',
            'grants': 'id owner revision status scope_json digest approval_ref policy_version limits_json approved_at expires_at revoked_at updated_at',
            'mandates': 'id owner revision status outcome namespace grant_id grant_revision children window_start window_count detail created_at updated_at',
            'derivations': 'owner mandate_id namespace event_key source_revision task_id created_at',
            'intents': 'action_id task_id attempt_id operation target payload_digest precondition_digest recipe_json authority_json journal_ref deadline created_at resolution resolved_at resolved_by reconcile_reads',
            'approvals': 'question_id task_id operation target payload_digest status answered_revision consumed_by',
            'deliveries': 'id notice_id destination attempt outcome detail at retry_at',
            'settings': 'owner key value updated_at',
        }
        for table, expected in columns.items():
            actual = [row['name'] for row in self._db.execute(f'PRAGMA table_info({table})')]
            if actual != expected.split():
                raise TaskStoreError('task schema is incomplete or unsupported')
        if self._db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise TaskStoreError('task database contains broken references')
        for row in self._db.execute('SELECT * FROM tasks').fetchall():
            task = self._task(row)
            self._validate_request(task.origin, task.specification, task.next_step)
            request = json.loads(row['request_json'])
            original = _spec(request['specification'])
            self._validate_request(task.origin, original, _step(request['step']))
            restored = replace(task.specification, criteria=tuple(
                replace(c, target_revision=old.target_revision)
                for c, old in zip(task.specification.criteria, original.criteria)))
            if restored != original or len(task.specification.criteria) != len(original.criteria):
                raise TaskStoreError('a retarget cannot rewrite the originating mandate')
            if (task.attempts < 0 or task.max_attempts < 1 or task.attempts > task.max_attempts
                    or task.polls < task.attempts or task.max_polls < 1 or task.polls > task.max_polls
                    or task.max_model_calls < 1 or not 0 <= task.model_calls <= task.max_model_calls
                    or _clock(task.evidence_max_age_s) <= 0
                    or any(_clock(value) != value for value in (task.created_at,task.updated_at,task.eligible_at))):
                raise TaskStoreError('task has invalid durable limits or timestamps')
            if task.origin.owner != row['owner'] or task.origin.request_id != row['request_id']:
                raise TaskStoreError('task origin does not match its index')
            history = self._db.execute('SELECT count(*),max(revision) FROM transitions WHERE task_id=?',(task.id,)).fetchone()
            if tuple(history) != (task.revision,task.revision):
                raise TaskStoreError('task history does not match its revision')
            attempts = self._db.execute('SELECT * FROM attempts WHERE task_id=?',(task.id,)).fetchall()
            unsuccessful = sum(a['phase'] in ('interrupted', 'unknown') for a in attempts)
            if len(attempts) != task.polls or unsuccessful != task.attempts:
                raise TaskStoreError('task allowances do not match their attempts')
            for raw_attempt in attempts:
                self._validate_step(task.specification,_step(json.loads(raw_attempt['step_json'])))
                if raw_attempt['phase'] == 'unknown' and task.status not in ('waiting','paused','failed','cancelled'):
                    raise TaskStoreError('an uncertain action cannot become runnable')
            if task.wait_reason == 'reconciliation' and task.status not in ('waiting','paused','failed','cancelled'):
                raise TaskStoreError('reconciliation has inconsistent task state')
            if task.current_attempt is not None:
                attempt = self._db.execute('SELECT * FROM attempts WHERE id=? AND task_id=?', (task.current_attempt, task.id)).fetchone()
                if attempt is None or (task.status == 'running' and attempt['phase'] not in ('prepared','dispatched')) or (task.status == 'verifying' and attempt['phase'] != 'observed'):
                    raise TaskStoreError('task has inconsistent attempt state')
            elif task.status in ('running','verifying'):
                raise TaskStoreError('task lost its current attempt')

        for row in self._db.execute('SELECT * FROM ingress'):
            task = self._get(row['owner'], row['task_id'])
            if row['ingress_id'] not in task.origin.ingress_ids:
                raise TaskStoreError('ingress identity does not belong to its request')
        for row in self._db.execute('SELECT * FROM tasks'):
            task = self._task(row)
            ids = {r[0] for r in self._db.execute('SELECT ingress_id FROM ingress WHERE task_id=?', (task.id,))}
            if ids != set(getattr(task.origin, 'ingress_ids', ())):
                raise TaskStoreError('request lost its ingress identities')
            if isinstance(task.origin, DerivedOrigin):
                origin = task.origin
                mandate = self._db.execute('SELECT * FROM mandates WHERE id=? AND owner=?', (origin.parent_mandate_id, origin.owner)).fetchone()
                if mandate is None or mandate['grant_id'] != origin.grant_id or mandate['namespace'] != origin.adapter_namespace:
                    raise TaskStoreError('derived task lost its mandate')
                alias = self._db.execute('SELECT task_id FROM derivations WHERE owner=? AND mandate_id=? AND namespace=? AND event_key=? AND source_revision=?',
                                         (origin.owner, origin.parent_mandate_id, origin.adapter_namespace, origin.event_key, origin.source_revision)).fetchone()
                if alias is None or alias['task_id'] != task.id:
                    raise TaskStoreError('derived task lost its trigger')
        for row in self._db.execute('SELECT * FROM questions'):
            task_row = self._db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
            task = self._task(task_row)
            choices = json.loads(row['choices_json'])
            if not choices or any(not isinstance(c, str) or not c.strip() for c in choices) or len(set(choices)) != len(choices):
                raise TaskStoreError('owner question lost its choices')
            _text(row['prompt'], 'question')
            if not 0 < row['revision'] <= task.revision or ((row['answer'] is None) != (row['answered_revision'] is None)):
                raise TaskStoreError('owner question has an inconsistent revision')
            if row['answer'] is not None and (row['answer'] not in choices or not row['revision'] < row['answered_revision'] <= task.revision):
                raise TaskStoreError('owner answer does not match its question')
            if row['step_json'] is not None:
                self._validate_step(task.specification, _step(json.loads(row['step_json'])))
        for row in self._db.execute('SELECT * FROM bindings'):
            attempt = self._attempt(row['attempt_id'])
            if not 0 < row['task_revision'] <= attempt.task_revision:
                raise TaskStoreError('tool binding changed its attempt revision')
            _text(row['client_generation'], 'client generation')
            _text(row['tool_use_id'], 'tool-use identity')
        for row in self._db.execute('SELECT * FROM feature_namespaces'):
            _text(row['owner'], 'owner')
            _text(row['namespace'], 'namespace')
        for row in self._db.execute('SELECT * FROM feature_records'):
            _text(row['record_key'], 'record key')
            if not isinstance(json.loads(row['payload_json']), dict):
                raise TaskStoreError('feature record payload is not an object')
        for row in self._db.execute('SELECT * FROM grant_drafts'):
            draft = self._draft_from(row)
            if any(_clock(value) != value for value in (draft.created_at, draft.updated_at)):
                raise TaskStoreError('grant draft has invalid timestamps')
            # A draft that is still open is what the owner may yet approve, so
            # its digest must be the one this runtime would compute; a closed
            # one keeps the digest of the day it was approved or dropped.
            if draft.status == 'draft':
                _text(draft.namespace, 'namespace')
                _text(draft.outcome, 'outcome')
                if draft.digest != _digest(draft.host, draft.namespace, draft.outcome, self._validate_scope(draft.scope), draft.limits,
                                           self._validate_bindings(draft.bindings)):
                    raise TaskStoreError('grant draft does not match its digest')
        for row in self._db.execute('SELECT * FROM grants'):
            grant = self._grant_from(row)
            _text(grant.approval_ref, 'approval reference')
            self._validate_scope(grant.scope)
            limits = grant.limits
            if (any(type(value) is not int or value < 1 for value in (limits.max_children, limits.max_per_window, grant.policy_version))
                    or _clock(limits.window_s) <= 0 or _clock(limits.lifetime_s) <= 0
                    or _clock(grant.expires_at) <= _clock(grant.approved_at) or (grant.status == 'revoked') != (grant.revoked_at is not None)):
                raise TaskStoreError('grant has invalid limits or timestamps')
        for row in self._db.execute('SELECT * FROM mandates'):
            mandate = self._mandate_from(row)
            _text(mandate.outcome, 'outcome')
            _text(mandate.namespace, 'namespace')
            grant_row = self._db.execute('SELECT * FROM grants WHERE id=? AND owner=?', (mandate.grant_id, mandate.owner)).fetchone()
            if grant_row is None or not 0 < mandate.grant_revision <= grant_row['revision']:
                raise TaskStoreError('mandate does not match its grant')
            if (mandate.children < 0 or not 0 <= mandate.window_count <= mandate.children or _clock(mandate.window_start) != mandate.window_start
                    or (grant_row['status'] != 'active' and mandate.status in ('active', 'paused'))):
                raise TaskStoreError('mandate has inconsistent allowances or authority')
        for row in self._db.execute('SELECT * FROM intents'):
            intent = self._intent_from(row)
            attempt = self._attempt(intent.attempt_id)
            if attempt.task_id != intent.task_id or attempt.step.kind != 'mutation' or attempt.step.operation != intent.operation or attempt.step.target != intent.target:
                raise TaskStoreError('intent does not match its attempt')
            if attempt.phase == 'prepared':
                raise TaskStoreError('intent has inconsistent phase')
            for value in (intent.payload_digest, intent.precondition_digest, intent.journal_ref):
                _text(value, 'intent field')
            if intent.authority.kind not in ('grant', 'approval') or (intent.authority.kind == 'grant') != (intent.authority.grant_id is not None):
                raise TaskStoreError('intent has malformed authority')
            if (intent.resolution is None) != (intent.resolved_at is None) or intent.reconcile_reads < 0 or _clock(intent.deadline) <= 0:
                raise TaskStoreError('intent has an inconsistent resolution')
        for row in self._db.execute('SELECT * FROM approvals'):
            question = self._db.execute('SELECT * FROM questions WHERE id=? AND task_id=?', (row['question_id'], row['task_id'])).fetchone()
            if question is None or question['kind'] != 'approval' or (row['status'] != 'asked') != (question['answer'] is not None):
                raise TaskStoreError('approval does not match its question')
        for row in self._db.execute('SELECT * FROM deliveries'):
            delivery = self._delivery_from(row)
            if self._db.execute('SELECT 1 FROM outbox WHERE id=?', (delivery.notice_id,)).fetchone() is None:
                raise TaskStoreError('delivery lost its notice')
            _text(delivery.destination, 'destination')
            if _clock(delivery.at) != delivery.at or (delivery.outcome == 'failed') != (delivery.retry_at is not None):
                raise TaskStoreError('delivery has an inconsistent record')
        for row in self._db.execute('SELECT * FROM settings'):
            _text(row['owner'], 'owner')
            _text(row['key'], 'setting')
        for row in self._db.execute('SELECT * FROM derivations'):
            task_row = self._db.execute('SELECT * FROM tasks WHERE id=? AND owner=?', (row['task_id'], row['owner'])).fetchone()
            if task_row is None:
                raise TaskStoreError('derivation lost its task')
            origin = self._task(task_row).origin
            if (not isinstance(origin, DerivedOrigin) or origin.parent_mandate_id != row['mandate_id']
                    or origin.adapter_namespace != row['namespace'] or origin.event_key != row['event_key']):
                raise TaskStoreError('derivation does not match its task')

    def _event(self, task: Task, before: str | None) -> None:
        assert self._db is not None
        self._db.execute('INSERT INTO transitions VALUES(?,?,?,?,?,?)', (task.id, task.revision, before, task.status, task.detail, task.updated_at))
        if task.status in _NOTICE:
            self._db.execute('INSERT INTO outbox VALUES(?,?,?,?,?,?,?)',
                             (f'{task.id}:{task.revision}', task.id, task.revision, task.status,
                              task.specification.outcome, task.detail, task.updated_at))

    def _advance(self, task: Task, status: Status, now: float, *, detail: str = '', wait_reason: str | None = None,
                 current_attempt: str | None = None, eligible_at: float | None = None, step: Step | None = None) -> Task:
        assert self._db is not None
        self._bounded(detail)
        self._db.execute('UPDATE tasks SET status=?,revision=revision+1,detail=?,wait_reason=?,current_attempt=?,eligible_at=?,step_json=?,updated_at=? WHERE id=? AND revision=?',
                         (status, detail, wait_reason, current_attempt, task.eligible_at if eligible_at is None else eligible_at,
                          self._bounded(asdict(step or task.next_step)), max(now,task.updated_at), task.id, task.revision))
        result = self._get(task.origin.owner, task.id)
        self._event(result, task.status)
        return result

    def _attempt(self, attempt_id: str) -> Attempt:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM attempts WHERE id=?', (attempt_id,)).fetchone()
        if row is None:
            raise TaskConflict('attempt does not exist')
        return Attempt(row['id'], row['task_id'], row['task_revision'], _step(json.loads(row['step_json'])), row['phase'], row['created_at'], row['updated_at'])

    def _current(self, owner: str, attempt: Attempt, phase: Phase) -> Task:
        task = self._get(owner, attempt.task_id, attempt.task_revision)
        stored = self._attempt(attempt.id)
        if task.status != 'running' or task.current_attempt != attempt.id or stored != attempt or stored.phase != phase:
            raise TaskConflict('attempt is no longer current')
        return task

    async def create(self, origin: HumanOrigin, specification: Specification, step: Step, *, now: float | None = None,
                     resource_wait: bool = False, fence: Fence | None = None, record: Callable[[Task], None] | None = None,
                     records: RecordSet | None = None) -> Task:
        """A live owner turn saves a finite task. ``records`` is the write-set
        that must land with it or not at all: an approved proposal marked so
        with its expected revision, which is what keeps a stale or repeated
        approval from making a second task."""
        if not isinstance(origin, HumanOrigin):
            raise TaskConflict('only a live owner turn creates a task; derived work has its own path')
        self._validate_request(origin, specification, step)
        if records is not None:
            self._namespace(records.namespace)
        data = self._bounded({'origin':asdict(origin), 'specification':asdict(specification), 'step':asdict(step)})
        stamp = _clock(now)
        applied = False
        def write() -> Task:
            nonlocal applied
            assert self._db is not None
            row = self._db.execute('SELECT * FROM tasks WHERE owner=? AND request_id=?', (origin.owner,origin.request_id)).fetchone()
            if row is not None:
                if row['request_json'] != data:
                    raise TaskConflict('originating request was reused for different work')
                return self._task(row)
            matches = [self._db.execute('SELECT task_id FROM ingress WHERE owner=? AND ingress_id=?',
                       (origin.owner, identity)).fetchone() for identity in origin.ingress_ids]
            found = {r[0] for r in matches if r is not None}
            if found:
                if len(found) != 1 or any(r is None for r in matches):
                    raise TaskConflict('this batch overlaps a saved request; repeat the new part on its own')
                existing = self._get(origin.owner, next(iter(found)))
                original = json.loads(self._db.execute('SELECT request_json FROM tasks WHERE id=?', (existing.id,)).fetchone()[0])
                if original['specification'] != json.loads(_json(asdict(specification))) or original['step'] != json.loads(_json(asdict(step))):
                    raise TaskConflict('originating request was reused for different work')
                return existing
            count = self._db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
            if count >= self._config.max_active:
                raise TaskLimit('active task allowance exhausted')
            applied = True
            task_id = uuid.uuid4().hex
            self._db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (task_id,origin.owner,origin.request_id,data,self._bounded(asdict(specification)),self._bounded(asdict(step)), 'queued',1,0,
                              self._config.max_attempts,0,self._config.max_polls,self._config.evidence_max_age_s,None,None,'created',stamp,stamp,stamp,
                              0,self._config.max_model_calls))
            task = self._get(origin.owner,task_id)
            self._event(task,None)
            for identity in origin.ingress_ids:
                self._db.execute('INSERT INTO ingress VALUES(?,?,?)', (origin.owner, identity, task.id))
            if records is not None:
                self._apply_records(origin.owner, records)
            if resource_wait:
                task = self._advance(task, 'waiting', stamp, detail=RESOURCE_WAIT, wait_reason='resource')
            return task
        def execute() -> Task:
            result = self._transaction(write, fence)
            if applied and record is not None:
                record(result)
            return result
        return await self._run(execute)

    async def get(self, owner: str, task_id: str) -> Task:
        return await self._run(lambda: self._transaction(lambda: self._get(owner,task_id)))

    async def list(self, owner: str) -> tuple[Task, ...]:
        def read() -> tuple[Task, ...]:
            assert self._db is not None
            return tuple(self._task(row) for row in self._db.execute('SELECT * FROM tasks WHERE owner=? ORDER BY created_at,id',(owner,)))
        return await self._run(lambda: self._transaction(read))

    async def claim(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Attempt:
        stamp = _clock(now)
        def write() -> Attempt:
            assert self._db is not None
            task = self._get(owner,task_id,revision)
            if task.status != 'queued' or task.eligible_at > stamp:
                raise TaskConflict('task is not eligible')
            if task.attempts >= task.max_attempts:
                raise TaskLimit('task attempt allowance exhausted')
            if task.polls >= task.max_polls:
                raise TaskLimit('task polling allowance exhausted')
            attempt_id = uuid.uuid4().hex
            updated = self._advance(task,'running',stamp,detail='attempt prepared',current_attempt=attempt_id)
            self._db.execute('UPDATE tasks SET polls=polls+1 WHERE id=?',(task.id,))
            self._db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',
                             (attempt_id,task.id,updated.revision,self._bounded(asdict(task.next_step)),'prepared',stamp,stamp))
            return self._attempt(attempt_id)
        return await self._run(lambda: self._transaction(write))

    async def mark_dispatched(self, owner: str, attempt: Attempt, *, now: float | None = None) -> Attempt:
        stamp = _clock(now)
        def write() -> Attempt:
            assert self._db is not None
            task = self._current(owner,attempt,'prepared')
            updated = self._advance(task,'running',stamp,detail='dispatch intent committed',current_attempt=attempt.id)
            self._db.execute("UPDATE attempts SET phase='dispatched',task_revision=?,updated_at=? WHERE id=?",(updated.revision,stamp,attempt.id))
            return self._attempt(attempt.id)
        return await self._run(lambda: self._transaction(write))

    async def observe(self, owner: str, attempt: Attempt, evidence: tuple[Evidence, ...], *, now: float | None = None) -> Task:
        stamp = _clock(now)
        data = self._bounded([asdict(item) for item in evidence])
        def write() -> Task:
            assert self._db is not None
            task = self._current(owner,attempt,'dispatched')
            criteria = {c.id:c for c in task.specification.criteria}
            seen = set()
            for raw in json.loads(data):
                item = Evidence(**raw)
                _text(item.source,'evidence source')
                if item.criterion_id in seen or item.criterion_id not in criteria:
                    raise ValueError('evidence must identify distinct task criteria')
                seen.add(item.criterion_id)
                if item.target != criteria[item.criterion_id].target or _clock(item.observed_at) > stamp or item.observed_at < attempt.created_at:
                    raise ValueError('evidence must observe this attempt target at a valid time')
                if item.observed is not None and not isinstance(item.observed,str):
                    raise ValueError('observations must be exact string values or unknown')
                self._db.execute('INSERT INTO evidence VALUES(?,?,?)',(attempt.id,item.criterion_id,_json(raw)))
            result = self._advance(task,'verifying',stamp,detail='observations recorded',current_attempt=attempt.id)
            self._db.execute("UPDATE attempts SET phase='observed',task_revision=?,updated_at=? WHERE id=?",(result.revision,stamp,attempt.id))
            self._db.execute("UPDATE intents SET resolution='applied',resolved_at=?,resolved_by='provider' WHERE attempt_id=? AND resolution IS NULL",
                             (stamp, attempt.id))
            return result
        return await self._run(lambda: self._transaction(write))

    async def complete(self, owner: str, task_id: str, revision: int, *, now: float | None = None,
                       records: RecordSet | None = None) -> Task:
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            task = self._get(owner,task_id,revision)
            if task.status != 'verifying' or task.current_attempt is None:
                raise TaskConflict('completion requires a current verification attempt')
            evidence = {row['criterion_id']:Evidence(**json.loads(row['record_json'])) for row in self._db.execute('SELECT * FROM evidence WHERE attempt_id=?',(task.current_attempt,))}
            for criterion in task.specification.criteria:
                item = evidence.get(criterion.id)
                if item is None or item.target != criterion.target or item.observed != criterion.expected or item.target_revision != criterion.target_revision or not 0 <= stamp-item.observed_at <= task.evidence_max_age_s:
                    raise TaskConflict('completion criteria lack fresh matching evidence')
            self._db.execute("UPDATE attempts SET phase='verified',updated_at=? WHERE id=?",(stamp,task.current_attempt))
            if records is not None:
                self._apply_records(owner, records)
            return self._advance(task,'done',stamp,detail='completion criteria verified')
        return await self._run(lambda: self._transaction(write))

    def _checkpoint_attempt(self, task: Task, now: float) -> None:
        assert self._db is not None
        if task.current_attempt is not None:
            self._db.execute("UPDATE attempts SET phase='checkpointed',updated_at=? WHERE id=?",
                             (now, task.current_attempt))

    async def retarget(self, owner: str, task_id: str, revision: int, target: str,
                       target_revision: str, *, now: float | None = None) -> Task:
        """Bind a target to the head observed by the trusted read adapter.

        Only the revision changes: scope, desired values, and original request
        identity remain fixed. Evidence from another head still cannot complete.
        """
        stamp = _clock(now)
        _text(target_revision, 'target revision')
        def write() -> Task:
            assert self._db is not None
            task = self._get(owner, task_id, revision)
            if task.status != 'verifying' or task.current_attempt is None or task.next_step.kind != 'read':
                raise TaskConflict('retargeting requires a current read observation')
            matching = [c for c in task.specification.criteria if c.target == target]
            if not matching or all(c.target_revision == target_revision for c in matching):
                raise TaskConflict('retargeting requires a changed target inside the mandate')
            evidence = [Evidence(**json.loads(row[0])) for row in self._db.execute(
                'SELECT record_json FROM evidence WHERE attempt_id=?', (task.current_attempt,))]
            if not any(item.target == target and item.target_revision == target_revision
                       and 0 <= stamp - item.observed_at <= task.evidence_max_age_s for item in evidence):
                raise TaskConflict('retargeting requires fresh evidence of the new head')
            specification = replace(task.specification, criteria=tuple(
                replace(c, target_revision=target_revision) if c.target == target else c
                for c in task.specification.criteria))
            self._db.execute('UPDATE tasks SET specification_json=? WHERE id=?',
                             (self._bounded(asdict(specification)), task.id))
            detail = _json({'retarget': target, 'from': {c.id: c.target_revision for c in matching},
                            'to': target_revision})
            result = self._advance(task, 'verifying', stamp, detail=detail, current_attempt=task.current_attempt)
            self._db.execute('UPDATE attempts SET task_revision=?,updated_at=? WHERE id=?',
                             (result.revision, stamp, task.current_attempt))
            return result
        return await self._run(lambda: self._transaction(write))

    async def checkpoint(self, owner: str, task_id: str, revision: int, step: Step, *, eligible_at: float, now: float | None = None,
                         records: RecordSet | None = None) -> Task:
        stamp, eligible = _clock(now), _clock(eligible_at)
        self._bounded(asdict(step))
        def write() -> Task:
            task = self._get(owner,task_id,revision)
            if task.status != 'verifying':
                raise TaskConflict('a checkpoint requires recorded observations')
            self._validate_step(task.specification,step)
            if task.next_step.kind == 'mutation' and step.kind != 'read':
                raise TaskConflict('a mutation must be verified by a read before planning another mutation')
            self._checkpoint_attempt(task, stamp)
            if records is not None:
                self._apply_records(owner, records)
            return self._advance(task,'queued',stamp,detail='next step saved',step=step,eligible_at=eligible)
        return await self._run(lambda: self._transaction(write))

    async def wait(self, owner: str, task_id: str, revision: int, reason: WaitReason, detail: str, *, now: float | None = None,
                   step: Step | None = None, records: RecordSet | None = None) -> Task:
        stamp = _clock(now)
        if reason not in ('owner','external','resource'):
            raise ValueError('reconciliation waits belong to recovery')
        _text(detail,'wait explanation')
        if step is not None:
            self._bounded(asdict(step))
        def write() -> Task:
            task = self._get(owner,task_id,revision)
            if task.status not in ('queued','verifying'):
                raise TaskConflict('task cannot enter a wait from this state')
            if task.status == 'verifying' and task.next_step.kind == 'mutation':
                raise TaskConflict('checkpoint a scoped verification read before waiting after a mutation')
            if step is not None:
                self._validate_step(task.specification, step)
                if task.next_step.kind == 'mutation' and step.kind != 'read':
                    raise TaskConflict('a mutation must be verified by a read before planning another mutation')
            self._checkpoint_attempt(task, stamp)
            if records is not None:
                self._apply_records(owner, records)
            return self._advance(task,'waiting',stamp,detail=detail,wait_reason=reason,step=step)
        return await self._run(lambda: self._transaction(write))

    async def pause(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        return await self._control(owner,task_id,revision,'paused',_clock(now))

    async def cancel(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        return await self._control(owner,task_id,revision,'cancelled',_clock(now))

    async def fail(self, owner: str, task_id: str, revision: int, detail: str, *, now: float | None = None) -> Task:
        _text(detail,'failure explanation')
        return await self._control(owner,task_id,revision,'failed',_clock(now),detail)

    async def _control(self, owner: str, task_id: str, revision: int, status: Status, now: float, detail: str | None = None) -> Task:
        return await self._run(lambda: self._transaction(lambda: self._control_now(owner, task_id, revision, status, now, detail)))

    def _control_now(self, owner: str, task_id: str, revision: int | None, status: Status, now: float, detail: str | None = None) -> Task:
        assert self._db is not None
        task = self._get(owner,task_id,revision)
        if task.status in _TERMINAL or task.status == status:
            raise TaskConflict('task is already terminal or in that state')
        uncertain = task.wait_reason == 'reconciliation'
        if task.current_attempt:
            attempt = self._attempt(task.current_attempt)
            uncertain |= attempt.phase == 'unknown' or (attempt.phase in ('dispatched','observed') and attempt.step.kind == 'mutation')
            if attempt.phase in ('prepared','dispatched','observed'):
                self._db.execute('UPDATE attempts SET phase=?,updated_at=? WHERE id=?',('unknown' if uncertain else 'interrupted',now,attempt.id))
                self._db.execute('UPDATE tasks SET attempts=attempts+1 WHERE id=?', (task.id,))
        return self._advance(task,status,now,detail='in-flight action needs reconciliation' if uncertain else (detail or status),
                             wait_reason='reconciliation' if uncertain else ('owner' if task.wait_reason == 'owner' and status == 'paused' else None))

    async def resume(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        stamp = _clock(now)
        return await self._run(lambda: self._transaction(lambda: self._resume_now(owner, task_id, revision, stamp)))

    def _resume_now(self, owner: str, task_id: str, revision: int | None, stamp: float, *, execution: bool = True) -> Task:
        task = self._get(owner,task_id,revision)
        if task.status not in ('paused','waiting','failed') or task.wait_reason == 'reconciliation':
            raise TaskConflict('task cannot resume without reconciliation or a resumable state')
        assert self._db is not None
        question = self._db.execute('SELECT id FROM questions WHERE task_id=? AND answer IS NULL ORDER BY revision DESC LIMIT 1', (task_id,)).fetchone()
        if task.wait_reason == 'owner' and question is not None:
            if task.status != 'paused':
                raise TaskConflict('answer the waiting question before resuming')
            task = self._advance(task, 'waiting', stamp, detail=task.detail, wait_reason='owner')
            self._db.execute('UPDATE questions SET revision=? WHERE id=?', (task.revision, question['id']))
            return task
        if task.status == 'failed':
            count = self._db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
            if count >= self._config.max_active:
                raise TaskLimit('active task allowance exhausted')
        if task.attempts >= task.max_attempts:
            raise TaskLimit('task attempt allowance exhausted')
        if task.polls >= task.max_polls:
            raise TaskLimit('task polling allowance exhausted')
        return self._advance(task, 'queued' if execution else 'waiting', stamp,
                             detail='owner resumed' if execution else RESOURCE_WAIT,
                             wait_reason=None if execution else 'resource', eligible_at=max(stamp,task.eligible_at))

    async def ask_owner(self, owner: str, task_id: str, revision: int, prompt: str, choices: tuple[str, ...],
                        *, step: Step | None = None, now: float | None = None) -> Task:
        """A trusted runtime supplies exact answer choices and any scoped next step."""
        _text(prompt, 'question')
        if not isinstance(choices, tuple) or not choices or len(set(choices)) != len(choices):
            raise ValueError('a question needs distinct answer choices')
        for choice in choices:
            _text(choice, 'answer choice')
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            task = self._get(owner, task_id, revision)
            if task.status not in ('queued', 'waiting') or task.wait_reason == 'reconciliation':
                raise TaskConflict('task cannot ask an owner question in this state')
            if step is not None:
                self._validate_step(task.specification, step)
            task = self._advance(task, 'waiting', stamp, detail=prompt, wait_reason='owner')
            self._db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,NULL,NULL,'step')",
                             (uuid.uuid4().hex, task.id, task.revision, prompt, self._bounded(choices),
                              self._bounded(asdict(step)) if step else None))
            return task
        return await self._run(lambda: self._transaction(write))

    async def owner_control(self, owner: str, task_id: str, operation: str, *, revision: int | None = None,
                            question_id: str | None = None, answer: str | None = None, execution: bool = False,
                            fence: Fence | None = None, now: float | None = None, record: Callable[[Task], None] | None = None) -> Task:
        """Owner controls; a missing revision means the current record. With
        ``execution`` a resumed or answered task is queued for the runner;
        without one it waits, saved, as it always did."""
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            task = self._get(owner, task_id, revision)
            if operation in ('pause', 'cancel'):
                return self._control_now(owner, task_id, task.revision, 'paused' if operation == 'pause' else 'cancelled', stamp)
            if operation == 'resume':
                return self._resume_now(owner, task_id, task.revision, stamp, execution=execution)
            if operation != 'answer':
                raise ValueError('unknown owner control')
            question = self._db.execute('SELECT * FROM questions WHERE id=? AND task_id=?', (question_id, task.id)).fetchone()
            expected_wait = 'reconciliation' if question is not None and question['kind'] == 'reconciliation' else 'owner'
            if (question is None or question['answer'] is not None or question['revision'] != task.revision
                    or task.status != 'waiting' or task.wait_reason != expected_wait):
                raise TaskConflict('the owner question is no longer waiting')
            if answer not in json.loads(question['choices_json']):
                raise TaskConflict('choose an exact answer; the question is still waiting')
            if question['kind'] == 'reconciliation':
                # The owner's word on an effect nobody could see: the same
                # resolution recovery would record, attributed to the owner.
                attempt_row = self._db.execute("SELECT id FROM attempts WHERE task_id=? AND phase='unknown' ORDER BY created_at DESC LIMIT 1", (task.id,)).fetchone()
                if attempt_row is None:
                    raise TaskConflict('nothing is uncertain any more')
                self._db.execute('UPDATE questions SET answer=?,answered_revision=? WHERE id=?', (answer, task.revision + 1, question_id))
                verdict = 'applied' if answer == 'it happened' else 'not_applied'
                return self._resolve_now(owner, self._attempt(attempt_row['id']), verdict, (), 'owner', stamp, None)
            if task.attempts >= task.max_attempts or task.polls >= task.max_polls:
                raise TaskLimit('task allowance exhausted')
            if question['kind'] == 'approval':
                if answer == 'cancel':
                    self._db.execute("UPDATE approvals SET status='declined',answered_revision=? WHERE question_id=?", (task.revision + 1, question_id))
                    self._db.execute('UPDATE questions SET answer=?,answered_revision=? WHERE id=?', (answer, task.revision + 1, question_id))
                    return self._control_now(owner, task_id, task.revision, 'cancelled', stamp, 'the owner declined the action')
                self._db.execute("UPDATE approvals SET status='approved',answered_revision=? WHERE question_id=?", (task.revision + 1, question_id))
            step = _step(json.loads(question['step_json'])) if question['step_json'] else task.next_step
            self._validate_step(task.specification, step)
            if execution:
                task = self._advance(task, 'queued', stamp, detail='owner answered', step=step, eligible_at=stamp)
            else:
                task = self._advance(task, 'waiting', stamp, detail=RESOURCE_WAIT, wait_reason='resource', step=step)
            self._db.execute('UPDATE questions SET answer=?,answered_revision=? WHERE id=?', (answer, task.revision, question_id))
            return task
        def execute() -> Task:
            result = self._transaction(write, fence)
            if record is not None:
                record(result)
            return result
        return await self._run(execute)

    async def owner_view(self, owner: str, task_id: str | None = None, *, fence: Fence | None = None) -> dict[str, Any]:
        """One bounded private snapshot; callers do not join separate stale reads."""
        def read() -> dict[str, Any]:
            assert self._db is not None
            if task_id is None:
                rows = self._db.execute('SELECT * FROM tasks WHERE owner=? ORDER BY updated_at DESC,id LIMIT ?',
                                        (owner, self._config.max_active)).fetchall()
                result = {'tasks': [asdict(self._task(row)) for row in rows],
                          'mandates': [asdict(self._mandate_from(row)) for row in self._db.execute(
                              'SELECT * FROM mandates WHERE owner=? ORDER BY updated_at DESC,id LIMIT ?', (owner, self._config.max_active))],
                          'grants': [asdict(self._grant_from(row)) for row in self._db.execute(
                              'SELECT * FROM grants WHERE owner=? ORDER BY updated_at DESC,id LIMIT ?', (owner, self._config.max_active))],
                          'drafts': [asdict(self._draft_from(row)) for row in self._db.execute(
                              "SELECT * FROM grant_drafts WHERE owner=? AND status='draft' ORDER BY updated_at DESC,id LIMIT ?", (owner, self._config.max_active))],
                          'notify': self._notify_enabled(owner),
                          'owed': self._db.execute("SELECT count(*) FROM outbox o JOIN tasks t ON o.task_id=t.id WHERE t.owner=? AND o.id IN "
                                                   "(SELECT id FROM outbox) AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.notice_id=o.id AND d.outcome='acknowledged') "
                                                   "AND (o.status IN ('done','failed') OR (o.status='waiting' AND o.revision=t.revision AND t.wait_reason IN ('owner','reconciliation')))",
                                                   (owner,)).fetchone()[0]}
            else:
                task = self._get(owner, task_id)
                questions = [dict(r) for r in self._db.execute('SELECT * FROM questions WHERE task_id=? ORDER BY revision DESC LIMIT 1', (task_id,))]
                question = questions[0] if questions else None
                if question:
                    question['choices'] = json.loads(question.pop('choices_json'))
                    question.pop('step_json')
                result = {'task': asdict(task), 'question': question,
                          'history': [dict(r) for r in self._db.execute('SELECT * FROM transitions WHERE task_id=? ORDER BY revision DESC LIMIT 64', (task_id,))],
                          'evidence': [json.loads(r[0]) for r in self._db.execute('SELECT record_json FROM evidence WHERE attempt_id=(SELECT id FROM attempts WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1) LIMIT 64', (task.id,))],
                          'authority': self._authority_view(task),
                          'unresolved': [asdict(self._intent_from(r)) for r in self._db.execute(
                              'SELECT * FROM intents WHERE task_id=? AND resolution IS NULL ORDER BY created_at LIMIT 8', (task.id,))],
                          'notices': [{**dict(n), 'deliveries': [asdict(self._delivery_from(d)) for d in self._db.execute(
                              'SELECT * FROM deliveries WHERE notice_id=? ORDER BY at,attempt LIMIT 16', (n['id'],))]}
                              for n in self._db.execute('SELECT * FROM outbox WHERE task_id=? ORDER BY created_at DESC LIMIT 8', (task.id,))]}
            self._bounded(result)
            return result
        return await self._run(lambda: self._transaction(read, fence))

    async def bind_call(self, owner: str, attempt: Attempt, generation: str, tool_use_id: str,
                        journal_ref: str | None = None) -> None:
        """Reserve correlation without deriving permission from a tool-use ID."""
        _text(generation, 'client generation')
        _text(tool_use_id, 'tool-use identity')
        self._bounded((generation, tool_use_id, journal_ref))
        def write() -> None:
            assert self._db is not None
            self._get(owner, attempt.task_id)
            if self._attempt(attempt.id) != attempt:
                raise TaskConflict('attempt binding is stale')
            values = (attempt.id, attempt.task_revision, generation, tool_use_id, journal_ref)
            row = self._db.execute('SELECT * FROM bindings WHERE client_generation=? AND tool_use_id=?', (generation, tool_use_id)).fetchone()
            if row is not None and tuple(row) != values:
                raise TaskConflict('tool call is already bound to another attempt')
            self._db.execute('INSERT OR IGNORE INTO bindings VALUES(?,?,?,?,?)', values)
        await self._run(lambda: self._transaction(write))

    def _recover(self, now: float) -> tuple[Task, ...]:
        assert self._db is not None
        recovered = []
        for row in self._db.execute("SELECT * FROM tasks WHERE status='running'").fetchall():
            task = self._task(row)
            assert task.current_attempt is not None
            attempt = self._attempt(task.current_attempt)
            uncertain = attempt.phase == 'dispatched' and attempt.step.kind == 'mutation'
            self._db.execute('UPDATE attempts SET phase=?,updated_at=? WHERE id=?',('unknown' if uncertain else 'interrupted',now,attempt.id))
            self._db.execute('UPDATE tasks SET attempts=attempts+1 WHERE id=?', (task.id,))
            recovered.append(self._advance(task,'waiting' if uncertain else 'queued',now,
                             detail='dispatch outcome unknown' if uncertain else 'interrupted attempt can be retried',
                             wait_reason='reconciliation' if uncertain else None))
        return tuple(recovered)

    async def attempts(self, owner: str, task_id: str) -> tuple[Attempt, ...]:
        def read() -> tuple[Attempt, ...]:
            assert self._db is not None
            self._get(owner,task_id)
            return tuple(self._attempt(row[0]) for row in self._db.execute('SELECT id FROM attempts WHERE task_id=? ORDER BY created_at,id',(task_id,)).fetchall())
        return await self._run(lambda: self._transaction(read))

    async def observations(self, owner: str, attempt_id: str) -> tuple[Evidence, ...]:
        def read() -> tuple[Evidence, ...]:
            assert self._db is not None
            attempt = self._attempt(attempt_id)
            self._get(owner,attempt.task_id)
            return tuple(Evidence(**json.loads(row[0])) for row in self._db.execute(
                'SELECT record_json FROM evidence WHERE attempt_id=? ORDER BY criterion_id',(attempt_id,)))
        return await self._run(lambda: self._transaction(read))

    async def history(self, owner: str, task_id: str) -> tuple[Transition, ...]:
        def read() -> tuple[Transition, ...]:
            assert self._db is not None
            self._get(owner,task_id)
            return tuple(Transition(row['revision'],row['before_status'],row['after_status'],row['detail'],row['at']) for row in self._db.execute('SELECT * FROM transitions WHERE task_id=? ORDER BY revision',(task_id,)))
        return await self._run(lambda: self._transaction(read))

    async def notices(self, owner: str) -> tuple[Notice, ...]:
        def read() -> tuple[Notice, ...]:
            assert self._db is not None
            return tuple(Notice(**dict(row)) for row in self._db.execute('SELECT o.* FROM outbox o JOIN tasks t ON o.task_id=t.id WHERE t.owner=? ORDER BY o.created_at,o.id',(owner,)))
        return await self._run(lambda: self._transaction(read))

    # ── authority: drafts, grants, mandates, derived work ────────────────────

    def _draft_from(self, row: sqlite3.Row) -> GrantDraft:
        limits = json.loads(row['limits_json'])
        return GrantDraft(row['id'], row['owner'], row['revision'], row['host'], row['namespace'], row['outcome'],
                          _scope(json.loads(row['scope_json'])), GrantLimits(**limits) if limits else GrantLimits(1, 1.0, 1, 1.0),
                          tuple(tuple(pair) for pair in json.loads(row['bindings_json'])), row['digest'], row['status'],
                          row['created_at'], row['updated_at'])

    def _grant_from(self, row: sqlite3.Row) -> StandingGrant:
        return StandingGrant(row['id'], row['owner'], row['revision'], row['status'], _scope(json.loads(row['scope_json'])), row['digest'],
                             row['approval_ref'], row['policy_version'], GrantLimits(**json.loads(row['limits_json'])),
                             row['approved_at'], row['expires_at'], row['revoked_at'], row['updated_at'])

    def _mandate_from(self, row: sqlite3.Row) -> Mandate:
        return Mandate(row['id'], row['owner'], row['revision'], row['status'], row['outcome'], row['namespace'], row['grant_id'],
                       row['grant_revision'], row['children'], row['window_start'], row['window_count'], row['detail'],
                       row['created_at'], row['updated_at'])

    def _draft(self, owner: str, draft_id: str) -> GrantDraft:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM grant_drafts WHERE id=? AND owner=?', (draft_id, owner)).fetchone()
        if row is None:
            raise TaskConflict('grant draft is not available to this owner')
        return self._draft_from(row)

    def _grant(self, owner: str, grant_id: str) -> StandingGrant:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM grants WHERE id=? AND owner=?', (grant_id, owner)).fetchone()
        if row is None:
            raise TaskConflict('grant is not available to this owner')
        return self._grant_from(row)

    def _mandate(self, owner: str, mandate_id: str) -> Mandate:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM mandates WHERE id=? AND owner=?', (mandate_id, owner)).fetchone()
        if row is None:
            raise TaskConflict('mandate is not available to this owner')
        return self._mandate_from(row)

    def _validate_scope(self, scope: Scope) -> Scope:
        """The normalized form every digest is taken over: sorted, unique, nonempty."""
        if not isinstance(scope.operations, tuple) or not isinstance(scope.targets, tuple) or not scope.operations or not scope.targets:
            raise ValueError('a scope names its operations and targets')
        for value in (*scope.operations, *scope.targets):
            _text(value, 'scope entry')
        return Scope(tuple(sorted(set(scope.operations))), tuple(sorted(set(scope.targets))))

    def _validate_bindings(self, bindings: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if not isinstance(bindings, tuple) or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in bindings):
            raise ValueError('bindings are immutable name/value pairs')
        names = set()
        for name, value in bindings:
            _text(name, 'binding name')
            if name in names or not isinstance(value, str):
                raise ValueError('bindings have unique names and string values')
            names.add(name)
        return tuple(sorted(bindings))

    def _validate_limits(self, limits: GrantLimits) -> GrantLimits:
        """Config caps what a grant may hold; it can lower one later, never raise one."""
        if any(type(value) is not int or value < 1 for value in (limits.max_children, limits.max_per_window)):
            raise ValueError('grant counts are positive integers')
        if _clock(limits.window_s) <= 0 or _clock(limits.lifetime_s) <= 0:
            raise ValueError('grant windows and lifetimes are positive')
        if (limits.max_children > self._config.max_grant_children or limits.max_per_window > self._config.max_grant_per_window
                or limits.lifetime_s > self._config.max_grant_lifetime_s):
            raise TaskLimit('grant limits exceed the configured caps')
        return limits

    async def save_grant_draft(self, owner: str, host: str, namespace: str, outcome: str, scope: Scope, limits: GrantLimits,
                               bindings: tuple[tuple[str, str], ...] = (), *, draft_id: str | None = None,
                               expected_revision: int | None = None, now: float | None = None, fence: Fence | None = None) -> GrantDraft:
        """A draft is what the owner is looking at, never what anything may do.
        Editing it moves its revision, which is what makes a pending approval stale."""
        _text(owner, 'owner')
        _text(host, 'execution host')
        _text(outcome, 'outcome')
        spec = self._namespace(namespace)
        scope = self._validate_scope(scope)
        limits = self._validate_limits(limits)
        bindings = self._validate_bindings(bindings)
        digest = _digest(host, spec.name, outcome, scope, limits, bindings)
        stamp = _clock(now)
        def write() -> GrantDraft:
            assert self._db is not None
            if draft_id is None:
                new_id = uuid.uuid4().hex
                self._db.execute('INSERT INTO grant_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                 (new_id, owner, 1, host, self._bounded(asdict(scope)), self._bounded(bindings), digest, 'draft', stamp, stamp,
                                  spec.name, outcome, self._bounded(asdict(limits))))
                return self._draft(owner, new_id)
            draft = self._draft(owner, draft_id)
            if draft.status != 'draft':
                raise TaskConflict('the draft is no longer editable')
            if expected_revision is not None and draft.revision != expected_revision:
                raise TaskConflict('draft revision changed')
            self._db.execute('UPDATE grant_drafts SET revision=revision+1,host=?,scope_json=?,bindings_json=?,digest=?,updated_at=?,'
                             'namespace=?,outcome=?,limits_json=? WHERE id=?',
                             (host, self._bounded(asdict(scope)), self._bounded(bindings), digest, max(stamp, draft.updated_at),
                              spec.name, outcome, self._bounded(asdict(limits)), draft_id))
            return self._draft(owner, draft_id)
        return await self._run(lambda: self._transaction(write, fence))

    async def grant_draft(self, owner: str, draft_id: str) -> GrantDraft:
        return await self._run(lambda: self._transaction(lambda: self._draft(owner, draft_id)))

    async def discard_grant_draft(self, owner: str, draft_id: str, *, revision: int | None = None,
                                  now: float | None = None, fence: Fence | None = None) -> GrantDraft:
        stamp = _clock(now)
        def write() -> GrantDraft:
            assert self._db is not None
            draft = self._draft(owner, draft_id)
            if draft.status != 'draft' or (revision is not None and draft.revision != revision):
                raise TaskConflict('the draft is not in that state')
            self._db.execute("UPDATE grant_drafts SET status='discarded',revision=revision+1,updated_at=? WHERE id=?", (max(stamp, draft.updated_at), draft_id))
            return self._draft(owner, draft_id)
        return await self._run(lambda: self._transaction(write, fence))

    async def activate_grant(self, origin: HumanOrigin, draft_id: str, revision: int, digest: str, approval_ref: str, *,
                             now: float | None = None, fence: Fence | None = None,
                             record: Callable[[Mandate], None] | None = None) -> tuple[StandingGrant, Mandate]:
        """The broker's yes, bound to the exact draft revision and digest the owner
        saw, becomes a grant and the standing mandate under it in one transaction.
        The turn that carries the approval is admitted exactly as a create is;
        the draft's limits meet the caps again, in case config lowered them."""
        if not isinstance(origin, HumanOrigin) or origin.attended is not True or origin.private is not True:
            raise TaskConflict('only an attended private owner turn may activate a grant')
        _text(origin.owner, 'owner')
        _text(approval_ref, 'approval reference')
        _text(digest, 'digest')
        if type(revision) is not int:
            raise ValueError('a draft revision is an integer')
        stamp = _clock(now)
        def write() -> tuple[StandingGrant, Mandate]:
            assert self._db is not None
            draft = self._draft(origin.owner, draft_id)
            if draft.status != 'draft' or draft.revision != revision or draft.digest != digest:
                raise TaskConflict('the approval does not match the current draft')
            limits = self._validate_limits(draft.limits)
            spec = self._namespace(draft.namespace)
            grant_id, mandate_id = uuid.uuid4().hex, uuid.uuid4().hex
            self._db.execute('INSERT INTO grants VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (grant_id, origin.owner, 1, 'active', self._bounded(asdict(draft.scope)), draft.digest, approval_ref, _POLICY,
                              self._bounded(asdict(limits)), stamp, stamp + limits.lifetime_s, None, stamp))
            self._db.execute('INSERT INTO mandates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (mandate_id, origin.owner, 1, 'active', draft.outcome, spec.name, grant_id, 1, 0, stamp, 0, 'activated', stamp, stamp))
            self._db.execute("UPDATE grant_drafts SET status='activated',revision=revision+1,updated_at=? WHERE id=?", (stamp, draft_id))
            return self._grant(origin.owner, grant_id), self._mandate(origin.owner, mandate_id)
        def execute() -> tuple[StandingGrant, Mandate]:
            result = self._transaction(write, fence)
            if record is not None:
                record(result[1])
            return result
        return await self._run(execute)

    def _mandate_control_now(self, owner: str, mandate_id: str, operation: str, revision: int | None, now: float) -> Mandate:
        assert self._db is not None
        mandate = self._mandate(owner, mandate_id)
        if revision is not None and mandate.revision != revision:
            raise TaskConflict('mandate revision changed')
        if operation == 'pause':
            if mandate.status != 'active':
                raise TaskConflict('only an active mandate pauses')
            status = 'paused'
        elif operation == 'resume':
            grant = self._grant(owner, mandate.grant_id)
            if mandate.status != 'paused' or grant.status != 'active' or grant.expires_at <= now:
                raise TaskConflict('the mandate cannot resume under its grant')
            status = 'active'
        elif operation == 'revoke':
            if mandate.status in ('revoked', 'expired'):
                raise TaskConflict('the mandate is already ended')
            status = 'revoked'
        else:
            raise ValueError('unknown mandate control')
        self._db.execute('UPDATE mandates SET status=?,revision=revision+1,detail=?,updated_at=? WHERE id=?',
                         (status, operation, max(now, mandate.updated_at), mandate_id))
        return self._mandate(owner, mandate_id)

    async def mandate_control(self, owner: str, mandate_id: str, operation: str, *, revision: int | None = None,
                              now: float | None = None, fence: Fence | None = None,
                              record: Callable[[Mandate], None] | None = None) -> Mandate:
        """Pause, resume, or revoke the responsibility; children already derived keep their own state."""
        stamp = _clock(now)
        def execute() -> Mandate:
            result = self._transaction(lambda: self._mandate_control_now(owner, mandate_id, operation, revision, stamp), fence)
            if record is not None:
                record(result)
            return result
        return await self._run(execute)

    async def revoke_grant(self, owner: str, grant_id: str, *, revision: int | None = None, now: float | None = None,
                           fence: Fence | None = None, record: Callable[[StandingGrant], None] | None = None) -> StandingGrant:
        """De-escalation never waits: the grant ends now, and every mandate under it with it."""
        stamp = _clock(now)
        def write() -> StandingGrant:
            assert self._db is not None
            grant = self._grant(owner, grant_id)
            if grant.status == 'revoked' or (revision is not None and grant.revision != revision):
                raise TaskConflict('the grant is not in that state')
            self._db.execute("UPDATE grants SET status='revoked',revision=revision+1,revoked_at=?,updated_at=? WHERE id=?",
                             (stamp, max(stamp, grant.updated_at), grant_id))
            self._db.execute("UPDATE mandates SET status='revoked',revision=revision+1,detail='grant revoked',updated_at=? "
                             "WHERE grant_id=? AND status NOT IN ('revoked','expired')", (stamp, grant_id))
            return self._grant(owner, grant_id)
        def execute() -> StandingGrant:
            result = self._transaction(write, fence)
            if record is not None:
                record(result)
            return result
        return await self._run(execute)

    def _sweep_expiry(self, owner: str, mandate_id: str, now: float) -> None:
        """A grant past its lifetime is marked so on its own, before any derivation
        is attempted, so the refusal that follows leaves the expiry on the record."""
        assert self._db is not None
        row = self._db.execute('SELECT grant_id FROM mandates WHERE id=? AND owner=?', (mandate_id, owner)).fetchone()
        if row is None:
            return
        grant = self._grant(owner, row['grant_id'])
        if grant.status == 'active' and grant.expires_at <= now:
            self._db.execute("UPDATE grants SET status='expired',revision=revision+1,updated_at=? WHERE id=?", (now, grant.id))
            self._db.execute("UPDATE mandates SET status='expired',revision=revision+1,detail='grant expired',updated_at=? "
                             "WHERE grant_id=? AND status NOT IN ('revoked','expired')", (now, grant.id))

    def _spend_derivation(self, mandate: Mandate, grant: StandingGrant, now: float) -> None:
        """The parent's allowances: a lifetime count, and a windowed one whose
        boundaries are fixed by the persisted window start, never by a restart.
        Effective limits are the grant's, capped by config."""
        assert self._db is not None
        limits = grant.limits
        max_children = min(limits.max_children, self._config.max_grant_children)
        max_per_window = min(limits.max_per_window, self._config.max_grant_per_window)
        if mandate.children >= max_children:
            raise TaskLimit('mandate child allowance exhausted')
        window_start, window_count = mandate.window_start, mandate.window_count
        if now >= window_start + limits.window_s:
            window_start += math.floor((now - window_start) / limits.window_s) * limits.window_s
            window_count = 0
        if window_count >= max_per_window:
            raise TaskLimit('mandate window allowance exhausted')
        self._db.execute('UPDATE mandates SET children=children+1,window_start=?,window_count=?,updated_at=? WHERE id=?',
                         (window_start, window_count + 1, max(now, mandate.updated_at), mandate.id))

    def _frozen(self, child: Task) -> bool:
        """Dispatch intent exists once a mutation attempt has been sent, whatever
        became of it; a child with intent is reconciled, never rewritten."""
        assert self._db is not None
        for row in self._db.execute('SELECT step_json,phase FROM attempts WHERE task_id=?', (child.id,)).fetchall():
            if _step(json.loads(row['step_json'])).kind == 'mutation' and row['phase'] not in ('prepared', 'interrupted'):
                return True
        return False

    def _revise(self, child: Task, origin: DerivedOrigin, specification: Specification, step: Step, now: float) -> Task:
        assert self._db is not None
        assert isinstance(child.origin, DerivedOrigin)
        if child.status in ('running', 'verifying') or child.current_attempt is not None:
            raise TaskConflict('the child is mid-step; revise it after the step settles')
        if self._frozen(child):
            raise TaskConflict('the child has dispatch intent; reconcile it before proposing a change')
        if child.specification.scope != specification.scope:
            raise TaskConflict('a change of scope is a proposal, never a revision')
        origin = replace(origin, request_id=child.origin.request_id)
        self._validate_request(origin, specification, step)
        data = self._bounded({'origin': asdict(origin), 'specification': asdict(specification), 'step': asdict(step)})
        self._db.execute('UPDATE tasks SET request_json=?,specification_json=? WHERE id=?',
                         (data, self._bounded(asdict(specification)), child.id))
        self._db.execute('DELETE FROM questions WHERE task_id=? AND answer IS NULL', (child.id,))
        detail = f'revised from source revision {child.origin.source_revision} to {origin.source_revision}'
        task = self._advance(child, 'paused' if child.status == 'paused' else 'queued', now, detail=detail, eligible_at=now, step=step)
        self._db.execute('INSERT INTO derivations VALUES(?,?,?,?,?,?,?)',
                         (origin.owner, origin.parent_mandate_id, origin.adapter_namespace, origin.event_key, origin.source_revision, task.id, now))
        return task

    async def derive_task(self, owner: str, mandate_id: str, expected_revision: int | None, namespace: str, event_key: str,
                          source_revision: str, specification: Specification, step: Step, *, now: float | None = None) -> Task:
        """A registered adapter's event becomes a finite child of a standing mandate.

        Runtime-only: the caller is application code holding a registered
        namespace, never a tool taking an origin from the model. One transaction
        checks the mandate is active under an active, unexpired grant of the
        revision it was activated with, that the child's scope lies inside the
        grant's, and that the parent's allowances remain. The same event at the
        same source revision returns the child it already made and spends
        nothing. A newer source revision revises an unfinished child in place
        while it has no dispatch intent and the scope stands, keeping the older
        revision as a replay alias; a finished child is never reopened, so the
        revision derives a new one.
        """
        _text(owner, 'owner')
        _text(mandate_id, 'mandate')
        _text(event_key, 'event key')
        _text(source_revision, 'source revision')
        if expected_revision is not None and type(expected_revision) is not int:
            raise ValueError('an expected revision is an integer')
        spec = self._namespace(namespace)
        stamp = _clock(now)
        request_id = hashlib.sha256(_json(('derived', mandate_id, spec.name, event_key, source_revision)).encode()).hexdigest()
        def write() -> Task:
            assert self._db is not None
            mandate = self._mandate(owner, mandate_id)
            if expected_revision is not None and mandate.revision != expected_revision:
                raise TaskConflict('mandate revision changed')
            if mandate.namespace != spec.name:
                raise TaskConflict('the mandate belongs to another adapter')
            grant = self._grant(owner, mandate.grant_id)
            if mandate.status != 'active' or grant.status != 'active' or grant.revision != mandate.grant_revision or grant.expires_at <= stamp:
                raise TaskConflict('the mandate cannot derive work now')
            if not set(specification.scope.operations) <= set(grant.scope.operations) or not set(specification.scope.targets) <= set(grant.scope.targets):
                raise TaskConflict('derived work must stay inside the grant')
            origin = DerivedOrigin(owner, request_id, mandate.id, mandate.revision, grant.id, grant.revision, spec.name,
                                   event_key, source_revision, stamp, _trigger(event_key, source_revision))
            self._validate_request(origin, specification, step)
            data = self._bounded({'origin': asdict(origin), 'specification': asdict(specification), 'step': asdict(step)})
            key = (owner, mandate.id, spec.name, event_key)
            replay = self._db.execute('SELECT task_id FROM derivations WHERE owner=? AND mandate_id=? AND namespace=? AND event_key=? AND source_revision=?',
                                      (*key, source_revision)).fetchone()
            if replay is not None:
                return self._get(owner, replay['task_id'])
            prior = self._db.execute('SELECT task_id FROM derivations WHERE owner=? AND mandate_id=? AND namespace=? AND event_key=? '
                                     'ORDER BY created_at DESC,rowid DESC LIMIT 1', key).fetchone()
            if prior is not None:
                child = self._get(owner, prior['task_id'])
                if child.status not in _TERMINAL:
                    return self._revise(child, origin, specification, step, stamp)
            self._spend_derivation(mandate, grant, stamp)
            count = self._db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
            if count >= self._config.max_active:
                raise TaskLimit('active task allowance exhausted')
            task_id = uuid.uuid4().hex
            self._db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (task_id, owner, request_id, data, self._bounded(asdict(specification)), self._bounded(asdict(step)), 'queued', 1, 0,
                              self._config.max_attempts, 0, self._config.max_polls, self._config.evidence_max_age_s, None, None, 'derived',
                              stamp, stamp, stamp, 0, self._config.max_model_calls))
            task = self._get(owner, task_id)
            self._event(task, None)
            self._db.execute('INSERT INTO derivations VALUES(?,?,?,?,?,?,?)', (*key, source_revision, task.id, stamp))
            return task
        def execute() -> Task:
            self._transaction(lambda: self._sweep_expiry(owner, mandate_id, stamp))
            return self._transaction(write)
        return await self._run(execute)

    async def grant_drafts(self, owner: str) -> tuple[GrantDraft, ...]:
        def read() -> tuple[GrantDraft, ...]:
            assert self._db is not None
            return tuple(self._draft_from(row) for row in self._db.execute('SELECT * FROM grant_drafts WHERE owner=? ORDER BY created_at,id', (owner,)))
        return await self._run(lambda: self._transaction(read))

    async def grants(self, owner: str) -> tuple[StandingGrant, ...]:
        def read() -> tuple[StandingGrant, ...]:
            assert self._db is not None
            return tuple(self._grant_from(row) for row in self._db.execute('SELECT * FROM grants WHERE owner=? ORDER BY approved_at,id', (owner,)))
        return await self._run(lambda: self._transaction(read))

    async def mandates(self, owner: str) -> tuple[Mandate, ...]:
        def read() -> tuple[Mandate, ...]:
            assert self._db is not None
            return tuple(self._mandate_from(row) for row in self._db.execute('SELECT * FROM mandates WHERE owner=? ORDER BY created_at,id', (owner,)))
        return await self._run(lambda: self._transaction(read))

    async def derivations(self, owner: str, mandate_id: str) -> tuple[tuple[str, str, str], ...]:
        """(event key, source revision, task id) for every trigger the mandate has seen, aliases included."""
        def read() -> tuple[tuple[str, str, str], ...]:
            assert self._db is not None
            self._mandate(owner, mandate_id)
            return tuple((row['event_key'], row['source_revision'], row['task_id']) for row in self._db.execute(
                'SELECT * FROM derivations WHERE owner=? AND mandate_id=? ORDER BY created_at,rowid', (owner, mandate_id)))
        return await self._run(lambda: self._transaction(read))

    # ── dispatch: authority, intent, reconciliation ──────────────────────────

    def _intent_from(self, row: sqlite3.Row) -> Intent:
        return Intent(row['action_id'], row['task_id'], row['attempt_id'], row['operation'], row['target'], row['payload_digest'],
                      row['precondition_digest'], json.loads(row['recipe_json']), Authority(**json.loads(row['authority_json'])),
                      row['journal_ref'], row['deadline'], row['created_at'], row['resolution'], row['resolved_at'], row['resolved_by'],
                      row['reconcile_reads'])

    def _intent_of(self, attempt_id: str) -> Intent | None:
        assert self._db is not None
        row = self._db.execute('SELECT * FROM intents WHERE attempt_id=?', (attempt_id,)).fetchone()
        return self._intent_from(row) if row is not None else None

    def _authority_for(self, task: Task, operation: str, target: str, payload_digest: str, now: float) -> Authority:
        """Where the right to send this comes from, checked now, not remembered.

        Derived work: its grant, at the revision the mandate was activated
        with, active and unexpired, its scope holding the operation and the
        target, its mandate not paused or ended. A proposal's task: the
        approval that made it, whose scope is the approved operation. Any
        other owner task: an approval the owner gave to this exact payload
        through a question this store asked. Nothing else is authority.
        """
        assert self._db is not None
        origin = task.origin
        if isinstance(origin, DerivedOrigin):
            mandate = self._mandate(origin.owner, origin.parent_mandate_id)
            grant = self._grant(origin.owner, origin.grant_id)
            if mandate.status != 'active' or grant.status != 'active' or grant.expires_at <= now or grant.revision != origin.grant_revision:
                raise NoAuthority('the grant no longer covers new dispatches')
            if operation not in grant.scope.operations or target not in grant.scope.targets:
                raise NoAuthority('the grant does not cover this operation on this target')
            return Authority('grant', grant.id, grant.revision, None)
        if origin.approval_ref is not None:
            return Authority('approval', None, None, origin.approval_ref)
        row = self._db.execute("SELECT question_id FROM approvals WHERE task_id=? AND operation=? AND target=? AND payload_digest=? "
                               "AND status='approved' AND consumed_by IS NULL ORDER BY rowid LIMIT 1",
                               (task.id, operation, target, payload_digest)).fetchone()
        if row is None:
            raise NoAuthority('the owner has not approved this action')
        return Authority('approval', None, None, f'question:{row["question_id"]}')

    async def authorize(self, owner: str, attempt: Attempt, operation: str, target: str, payload_digest: str, precondition_digest: str,
                        recipe: dict[str, Any], journal_ref: str, *, now: float | None = None) -> Intent:
        """Dispatch intent, durable before anything is sent.

        One transaction binds the attempt, the operation and exact target,
        the payload and precondition digests, the authority found now, the
        journal entry already written, and a deadline; the attempt becomes
        dispatched in the same write. A missing journal reference is a
        refusal: what cannot be correlated with Inverse is not sent.
        """
        _text(operation, 'operation')
        _text(target, 'target')
        _text(payload_digest, 'payload digest')
        _text(precondition_digest, 'precondition digest')
        _text(journal_ref, 'journal reference')
        if not isinstance(recipe, dict):
            raise ValueError('a reconciliation recipe is an object')
        stamp = _clock(now)
        def write() -> Intent:
            assert self._db is not None
            task = self._current(owner, attempt, 'prepared')
            if attempt.step.kind != 'mutation' or attempt.step.operation != operation or attempt.step.target != target:
                raise TaskConflict('the intent must name the mutation the attempt was claimed for')
            authority = self._authority_for(task, operation, target, payload_digest, stamp)
            action_id = uuid.uuid4().hex
            self._db.execute('INSERT INTO intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,0)',
                             (action_id, task.id, attempt.id, operation, target, payload_digest, precondition_digest, self._bounded(recipe),
                              self._bounded(asdict(authority)), journal_ref, stamp + self._config.dispatch_deadline_s, stamp))
            if authority.kind == 'approval' and authority.approval_ref.startswith('question:'):
                self._db.execute('UPDATE approvals SET consumed_by=? WHERE question_id=?', (action_id, authority.approval_ref.removeprefix('question:')))
            updated = self._advance(task, 'running', stamp, detail=f'dispatch intent committed: {operation} on {target}', current_attempt=attempt.id)
            self._db.execute("UPDATE attempts SET phase='dispatched',task_revision=?,updated_at=? WHERE id=?", (updated.revision, stamp, attempt.id))
            return self._intent_of(attempt.id)  # type: ignore[return-value]
        return await self._run(lambda: self._transaction(write))

    async def intents(self, owner: str, task_id: str) -> tuple[Intent, ...]:
        def read() -> tuple[Intent, ...]:
            assert self._db is not None
            self._get(owner, task_id)
            return tuple(self._intent_from(row) for row in self._db.execute('SELECT * FROM intents WHERE task_id=? ORDER BY created_at,rowid', (task_id,)))
        return await self._run(lambda: self._transaction(read))

    async def reconcilable(self, owner: str, *, limit: int = 8) -> tuple[tuple[Task, Attempt, Intent], ...]:
        """Tasks waiting on an outcome nobody knows, oldest first, with the
        attempt and the intent recovery must ask about."""
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        def read() -> tuple[tuple[Task, Attempt, Intent], ...]:
            assert self._db is not None
            found = []
            for row in self._db.execute("SELECT * FROM tasks WHERE owner=? AND status IN ('waiting','cancelled') AND wait_reason='reconciliation' "
                                        'ORDER BY updated_at,id LIMIT ?', (owner, limit)).fetchall():
                task = self._task(row)
                attempt_row = self._db.execute("SELECT id FROM attempts WHERE task_id=? AND phase='unknown' ORDER BY created_at DESC LIMIT 1", (task.id,)).fetchone()
                if attempt_row is None:
                    continue
                attempt = self._attempt(attempt_row['id'])
                intent = self._intent_of(attempt.id)
                if intent is not None and intent.resolution is None:
                    found.append((task, attempt, intent))
            return tuple(found)
        return await self._run(lambda: self._transaction(read))

    def _uncertain(self, owner: str, attempt: Attempt) -> tuple[Task, Intent]:
        task = self._get(owner, attempt.task_id)
        stored = self._attempt(attempt.id)
        intent = self._intent_of(attempt.id)
        if stored.phase != 'unknown' or intent is None or intent.resolution is not None:
            raise TaskConflict('the attempt is not an unresolved uncertain one')
        return task, intent

    async def note_reconcile(self, owner: str, attempt: Attempt, detail: str, *, now: float | None = None) -> Intent:
        """A reconciliation read that could not tell: spent, counted, and the wait kept."""
        _text(detail, 'reconciliation detail')
        stamp = _clock(now)
        def write() -> Intent:
            assert self._db is not None
            task, intent = self._uncertain(owner, attempt)
            # Counted on the intent, not the polls: a reconciliation read is
            # not an execution round, and its own bound ends in the owner's ear.
            self._db.execute('UPDATE tasks SET detail=?,updated_at=? WHERE id=?', (self._bounded(detail) and detail, max(stamp, task.updated_at), task.id))
            self._db.execute('UPDATE intents SET reconcile_reads=reconcile_reads+1 WHERE action_id=?', (intent.action_id,))
            return self._intent_of(attempt.id)  # type: ignore[return-value]
        return await self._run(lambda: self._transaction(write))

    async def resolve(self, owner: str, attempt: Attempt, verdict: Literal['applied', 'not_applied'], evidence: tuple[Evidence, ...], *,
                      by: str, now: float | None = None, next_step: Step | None = None) -> Task:
        """What became of an uncertain effect, once somebody knows.

        Applied: the attempt observed what it sent, its evidence recorded, and
        the task goes on to the verifying read its adapter names. Not applied:
        the attempt was interrupted after all, and the mutation may be planned
        again under fresh authority. A task the owner cancelled or that failed
        meanwhile keeps that state; the effect is recorded, nothing reactivates.
        """
        if verdict not in ('applied', 'not_applied'):
            raise ValueError('a verdict is applied or not_applied')
        _text(by, 'who resolved it')
        stamp = _clock(now)
        self._bounded([asdict(item) for item in evidence])
        return await self._run(lambda: self._transaction(lambda: self._resolve_now(owner, attempt, verdict, evidence, by, stamp, next_step)))

    def _resolve_now(self, owner: str, attempt: Attempt, verdict: str, evidence: tuple[Evidence, ...], by: str, stamp: float,
                     next_step: Step | None) -> Task:
        data = _json([asdict(item) for item in evidence])
        if True:
            assert self._db is not None
            task, intent = self._uncertain(owner, attempt)
            if task.status not in ('waiting', 'paused', 'cancelled', 'failed') or (task.status == 'waiting' and task.wait_reason != 'reconciliation'):
                raise TaskConflict('the task is not waiting on this outcome')
            self._db.execute('UPDATE intents SET resolution=?,resolved_at=?,resolved_by=? WHERE action_id=?', (verdict, stamp, by, intent.action_id))
            if verdict == 'applied':
                criteria = {c.id: c for c in task.specification.criteria}
                for raw in json.loads(data):
                    item = Evidence(**raw)
                    if item.criterion_id not in criteria or item.target != criteria[item.criterion_id].target or _clock(item.observed_at) > stamp:
                        raise ValueError('evidence must observe this task\'s criteria at a valid time')
                    self._db.execute('INSERT OR REPLACE INTO evidence VALUES(?,?,?)', (attempt.id, item.criterion_id, _json(raw)))
                self._db.execute("UPDATE attempts SET phase='observed',updated_at=? WHERE id=?", (stamp, attempt.id))
                # It succeeded after all: the retry allowance it was charged
                # when it became uncertain is returned, as for any clean round.
                self._db.execute('UPDATE tasks SET attempts=attempts-1 WHERE id=? AND attempts>0', (task.id,))
            else:
                self._db.execute("UPDATE attempts SET phase='interrupted',updated_at=? WHERE id=?", (stamp, attempt.id))
            if task.status != 'waiting':
                self._db.execute('UPDATE tasks SET detail=?,updated_at=? WHERE id=?', (f'reconciled while {task.status}: {verdict}', stamp, task.id))
                return self._get(owner, task.id)
            step = next_step or task.next_step
            verify = intent.recipe.get('verify')
            if verdict == 'applied' and next_step is None and isinstance(verify, dict):
                # The read the adapter named when it planned the send, kept in
                # the intent so the owner's word can use it as the adapter would.
                step = _step(verify)
            self._validate_step(task.specification, step)
            if verdict == 'applied' and step.kind != 'read':
                raise TaskConflict('an applied mutation is verified by a read before anything else')
            if task.attempts >= task.max_attempts or task.polls >= task.max_polls:
                return self._advance(task, 'failed', stamp, detail=f'reconciled: {verdict}; allowance exhausted')
            return self._advance(task, 'queued', stamp, detail=f'reconciled by {by}: {verdict}', eligible_at=stamp, step=step)

    def _park_now(self, owner: str, attempt: Attempt, reason: WaitReason, detail: str, stamp: float) -> Task:
        """A claimed attempt that will not be sent, set down cleanly: the
        attempt is checkpointed, not charged, and the task waits with the
        step it was claimed for still next."""
        assert self._db is not None
        task = self._current(owner, attempt, 'prepared')
        self._db.execute("UPDATE attempts SET phase='checkpointed',updated_at=? WHERE id=?", (stamp, attempt.id))
        return self._advance(task, 'waiting', stamp, detail=detail, wait_reason=reason)

    async def park(self, owner: str, attempt: Attempt, reason: WaitReason, detail: str, *, now: float | None = None) -> Task:
        if reason not in ('external', 'resource'):
            raise ValueError('a parked attempt waits on the world or the runtime')
        _text(detail, 'wait explanation')
        stamp = _clock(now)
        return await self._run(lambda: self._transaction(lambda: self._park_now(owner, attempt, reason, detail, stamp)))

    async def ask_approval(self, owner: str, attempt: Attempt, prompt: str, payload_digest: str, *, now: float | None = None) -> Task:
        """A mutation with no standing authority asks the owner for exactly this
        one: the question names the operation, the target, and the payload it
        was planned with, and an approve answer covers nothing else. The
        attempt that planned it is set down cleanly."""
        _text(prompt, 'question')
        _text(payload_digest, 'payload digest')
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            if attempt.step.kind != 'mutation':
                raise TaskConflict('only a mutation asks for approval')
            task = self._park_now(owner, attempt, 'external', prompt, stamp)
            task = self._advance(task, 'waiting', stamp, detail=prompt, wait_reason='owner')
            question_id = uuid.uuid4().hex
            self._db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,NULL,NULL,'approval')",
                             (question_id, task.id, task.revision, prompt, self._bounded(('approve', 'cancel')), self._bounded(asdict(attempt.step))))
            self._db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,'asked',NULL,NULL)",
                             (question_id, task.id, attempt.step.operation, attempt.step.target, payload_digest))
            return task
        return await self._run(lambda: self._transaction(write))

    async def ask_reconciliation(self, owner: str, attempt: Attempt, prompt: str, *, now: float | None = None) -> Task:
        """Recovery could not tell; the owner is asked, in two exact words."""
        _text(prompt, 'question')
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            task, intent = self._uncertain(owner, attempt)
            if task.status != 'waiting':
                raise TaskConflict('only a waiting task asks')
            if self._db.execute("SELECT 1 FROM questions WHERE task_id=? AND kind='reconciliation' AND answer IS NULL", (task.id,)).fetchone():
                raise TaskConflict('the owner is already being asked')
            task = self._advance(task, 'waiting', stamp, detail=prompt, wait_reason='reconciliation')
            self._db.execute("INSERT INTO questions VALUES(?,?,?,?,?,?,NULL,NULL,'reconciliation')",
                             (uuid.uuid4().hex, task.id, task.revision, prompt, self._bounded(('it happened', 'it did not happen')),
                              self._bounded(asdict(attempt.step))))
            return task
        return await self._run(lambda: self._transaction(write))

    # ── delivery: what is owed, what was sent, what was seen ─────────────────

    def _delivery_from(self, row: sqlite3.Row) -> Delivery:
        return Delivery(row['id'], row['notice_id'], row['destination'], row['attempt'], row['outcome'], row['detail'], row['at'], row['retry_at'])

    def _authority_view(self, task: Task) -> dict[str, Any] | None:
        """What currently permits this task's actions, for the owner's eyes."""
        assert self._db is not None
        origin = task.origin
        if isinstance(origin, DerivedOrigin):
            mandate = self._db.execute('SELECT status,revision FROM mandates WHERE id=?', (origin.parent_mandate_id,)).fetchone()
            grant = self._db.execute('SELECT status,revision,expires_at FROM grants WHERE id=?', (origin.grant_id,)).fetchone()
            return {'kind': 'grant', 'grant_id': origin.grant_id, 'grant_status': grant['status'] if grant else 'missing',
                    'grant_current': bool(grant) and grant['revision'] == origin.grant_revision, 'expires_at': grant['expires_at'] if grant else None,
                    'mandate_id': origin.parent_mandate_id, 'mandate_status': mandate['status'] if mandate else 'missing'}
        if origin.approval_ref is not None:
            return {'kind': 'approval', 'approval_ref': origin.approval_ref}
        approved = self._db.execute("SELECT count(*) FROM approvals WHERE task_id=? AND status='approved' AND consumed_by IS NULL", (task.id,)).fetchone()[0]
        return {'kind': 'owner', 'approved_actions': approved}

    def _notify_enabled(self, owner: str) -> bool:
        assert self._db is not None
        row = self._db.execute("SELECT value FROM settings WHERE owner=? AND key='notify'", (owner,)).fetchone()
        return row is None or row['value'] == 'on'

    async def notify_enabled(self, owner: str) -> bool:
        return await self._run(lambda: self._transaction(lambda: self._notify_enabled(owner)))

    async def set_notify(self, owner: str, enabled: bool, *, now: float | None = None, fence: Fence | None = None) -> bool:
        """The notice switch, persisted. It says nothing about execution."""
        _text(owner, 'owner')
        stamp = _clock(now)
        def write() -> bool:
            assert self._db is not None
            self._db.execute("INSERT OR REPLACE INTO settings VALUES(?,'notify',?,?)", (owner, 'on' if enabled else 'off', stamp))
            return bool(enabled)
        return await self._run(lambda: self._transaction(write, fence))

    async def undelivered(self, owner: str, *, now: float | None = None, limit: int = 8) -> tuple[tuple[Notice, int], ...]:
        """Notices still owed, oldest first, each with the number of delivery
        attempts so far. Owed means: a completion or a failure, or the
        question the task is waiting on right now, that the owner has not
        looked at and that no delivery has reached; a failed delivery is owed
        again once its retry time comes."""
        stamp = _clock(now)
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        def read() -> tuple[tuple[Notice, int], ...]:
            assert self._db is not None
            found = []
            rows = self._db.execute(
                "SELECT o.* FROM outbox o JOIN tasks t ON o.task_id=t.id WHERE t.owner=? "
                "AND (o.status IN ('done','failed') OR (o.status='waiting' AND o.revision=t.revision AND t.wait_reason IN ('owner','reconciliation'))) "
                'ORDER BY o.created_at,o.id LIMIT ?', (owner, limit * 4)).fetchall()
            for row in rows:
                deliveries = [self._delivery_from(d) for d in self._db.execute('SELECT * FROM deliveries WHERE notice_id=? ORDER BY at,attempt', (row['id'],))]
                if any(d.outcome in ('sent', 'acknowledged') for d in deliveries):
                    continue
                if deliveries and deliveries[-1].outcome == 'failed' and deliveries[-1].retry_at is not None and deliveries[-1].retry_at > stamp:
                    continue
                found.append((Notice(**dict(row)), len(deliveries)))
                if len(found) >= limit:
                    break
            return tuple(found)
        return await self._run(lambda: self._transaction(read))

    async def record_delivery(self, owner: str, notice_id: str, destination: str, outcome: Literal['sent', 'failed'], detail: str = '', *,
                              now: float | None = None, retry_after_s: float = 0.0) -> Delivery:
        """A delivery attempt, recorded after the fact: sent means the private
        lane took it, failed means it did not and when to try again."""
        _text(destination, 'destination')
        if outcome not in ('sent', 'failed'):
            raise ValueError('a delivery attempt is sent or failed')
        stamp = _clock(now)
        def write() -> Delivery:
            assert self._db is not None
            if self._db.execute('SELECT 1 FROM outbox o JOIN tasks t ON o.task_id=t.id WHERE o.id=? AND t.owner=?', (notice_id, owner)).fetchone() is None:
                raise TaskConflict('notice is not available to this owner')
            attempt = self._db.execute('SELECT count(*) FROM deliveries WHERE notice_id=?', (notice_id,)).fetchone()[0] + 1
            delivery_id = uuid.uuid4().hex
            self._db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)',
                             (delivery_id, notice_id, destination, attempt, outcome, self._bounded(detail) and detail, stamp,
                              stamp + max(0.0, retry_after_s) if outcome == 'failed' else None))
            return self._delivery_from(self._db.execute('SELECT * FROM deliveries WHERE id=?', (delivery_id,)).fetchone())
        return await self._run(lambda: self._transaction(write))

    async def acknowledge(self, owner: str, task_id: str, destination: str, *, now: float | None = None, fence: Fence | None = None) -> int:
        """The owner looked at the task on a private lane: every notice it
        owes is received. Returns how many that was; looking twice is free."""
        _text(destination, 'destination')
        stamp = _clock(now)
        def write() -> int:
            assert self._db is not None
            self._get(owner, task_id)
            count = 0
            for row in self._db.execute('SELECT id FROM outbox WHERE task_id=?', (task_id,)).fetchall():
                if self._db.execute("SELECT 1 FROM deliveries WHERE notice_id=? AND outcome='acknowledged'", (row['id'],)).fetchone():
                    continue
                attempt = self._db.execute('SELECT count(*) FROM deliveries WHERE notice_id=?', (row['id'],)).fetchone()[0] + 1
                self._db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,NULL)',
                                 (uuid.uuid4().hex, row['id'], destination, attempt, 'acknowledged', 'the owner looked', stamp))
                count += 1
            return count
        return await self._run(lambda: self._transaction(write, fence))

    async def deliveries(self, owner: str, notice_id: str) -> tuple[Delivery, ...]:
        def read() -> tuple[Delivery, ...]:
            assert self._db is not None
            if self._db.execute('SELECT 1 FROM outbox o JOIN tasks t ON o.task_id=t.id WHERE o.id=? AND t.owner=?', (notice_id, owner)).fetchone() is None:
                raise TaskConflict('notice is not available to this owner')
            return tuple(self._delivery_from(r) for r in self._db.execute('SELECT * FROM deliveries WHERE notice_id=? ORDER BY at,attempt', (notice_id,)))
        return await self._run(lambda: self._transaction(read))

    # ── the runner's side ─────────────────────────────────────────────────────

    async def eligible(self, owner: str, *, now: float | None = None, limit: int = 8) -> tuple[Task, ...]:
        """The oldest queued tasks whose time has come and whose allowances remain."""
        stamp = _clock(now)
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        def read() -> tuple[Task, ...]:
            assert self._db is not None
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE owner=? AND status='queued' AND eligible_at<=? AND attempts<max_attempts AND polls<max_polls "
                'ORDER BY eligible_at,created_at,id LIMIT ?', (owner, stamp, limit)).fetchall()
            return tuple(self._task(row) for row in rows)
        return await self._run(lambda: self._transaction(read))

    def _live(self, owner: str, attempt: Attempt) -> tuple[Task, Attempt]:
        """The attempt the runner holds, if the store still holds it too: running
        or verifying, current, and not yet checkpointed, verified, or given up."""
        task = self._get(owner, attempt.task_id)
        stored = self._attempt(attempt.id)
        if task.status not in ('running', 'verifying') or task.current_attempt != attempt.id or stored.phase not in ('prepared', 'dispatched', 'observed'):
            raise TaskConflict('attempt is no longer current')
        return task, stored

    async def abandon(self, owner: str, attempt: Attempt, detail: str, *, eligible_at: float, now: float | None = None,
                      sent: bool = True) -> Task:
        """The runner gives an attempt up: a timeout, an adapter failure, the
        owner's voice. The attempt is spent. A read is requeued with backoff,
        a sent mutation waits for reconciliation, and an exhausted allowance
        fails the task where the owner can see why. ``sent=False`` is the
        runner's word that the intent never left: a failed precondition, a
        deadline passed; the intent is closed as not applied and the
        mutation may be planned again."""
        stamp, eligible = _clock(now), _clock(eligible_at)
        _text(detail, 'abandonment explanation')
        def write() -> Task:
            assert self._db is not None
            task, stored = self._live(owner, attempt)
            uncertain = stored.phase == 'dispatched' and stored.step.kind == 'mutation' and sent
            if stored.phase == 'dispatched' and stored.step.kind == 'mutation' and not sent:
                self._db.execute("UPDATE intents SET resolution='not_applied',resolved_at=?,resolved_by='runner' WHERE attempt_id=? AND resolution IS NULL",
                                 (stamp, attempt.id))
            self._db.execute('UPDATE attempts SET phase=?,updated_at=? WHERE id=?', ('unknown' if uncertain else 'interrupted', stamp, attempt.id))
            self._db.execute('UPDATE tasks SET attempts=attempts+1 WHERE id=?', (task.id,))
            task = self._get(owner, task.id)
            if uncertain:
                return self._advance(task, 'waiting', stamp, detail='dispatch outcome unknown', wait_reason='reconciliation')
            if task.attempts >= task.max_attempts:
                return self._advance(task, 'failed', stamp, detail=f'{detail}; attempt allowance exhausted')
            return self._advance(task, 'queued', stamp, detail=detail, eligible_at=max(eligible, stamp))
        return await self._run(lambda: self._transaction(write))

    async def note_model_call(self, owner: str, attempt: Attempt, *, now: float | None = None) -> Task:
        """Spend one model call before it is made; a spent call is never refunded."""
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            task, _ = self._live(owner, attempt)
            if task.model_calls >= task.max_model_calls:
                raise TaskLimit('task model-call allowance exhausted')
            self._db.execute('UPDATE tasks SET model_calls=model_calls+1,updated_at=? WHERE id=?', (max(stamp, task.updated_at), task.id))
            return self._get(owner, task.id)
        return await self._run(lambda: self._transaction(write))

    async def records(self, owner: str, namespace: str, keys: tuple[str, ...] | None = None, *, limit: int = 256) -> tuple[FeatureRecord, ...]:
        """An adapter's own records, by key or in key order, never another namespace's."""
        spec = self._namespace(namespace)
        if type(limit) is not int or limit < 1:
            raise ValueError('limit must be a positive integer')
        if keys is not None and (not isinstance(keys, tuple) or any(not isinstance(k, str) for k in keys)):
            raise ValueError('record keys are a tuple of strings')
        def read() -> tuple[FeatureRecord, ...]:
            assert self._db is not None
            if keys is None:
                rows = self._db.execute('SELECT * FROM feature_records WHERE owner=? AND namespace=? ORDER BY record_key LIMIT ?',
                                        (owner, spec.name, limit)).fetchall()
            else:
                rows = [r for r in (self._db.execute('SELECT * FROM feature_records WHERE owner=? AND namespace=? AND record_key=?',
                                                     (owner, spec.name, key)).fetchone() for key in keys[:limit]) if r is not None]
            return tuple(FeatureRecord(r['namespace'], r['record_key'], r['revision'], json.loads(r['payload_json'])) for r in rows)
        return await self._run(lambda: self._transaction(read))

    async def write_records(self, owner: str, records: RecordSet, *, fence: Fence | None = None) -> None:
        """A write-set on its own, for work that has no task transition to ride on."""
        self._namespace(records.namespace)
        await self._run(lambda: self._transaction(lambda: self._apply_records(owner, records), fence))
