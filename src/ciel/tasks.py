"""A task keeps its place when the conversation and the process disappear.

**A task is a mandate.** Its desired state, observations, scope, actions, and
verification live together. Atlas remains the human-readable project context;
this store holds the runtime's records. Stage one offers no model tools and
executes no actions. Step kinds and owner/origin arguments must come from trusted runtime
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

Async cancellation does not roll back a worker transaction already submitted.
Callers must reread state; originating request IDs deduplicate creation, and
expected revisions make every later stale retry fail explicitly.
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypeVar

from ciel.config import TasksConfig

Status = Literal['queued', 'running', 'verifying', 'waiting', 'paused', 'done', 'failed', 'cancelled']
Phase = Literal['prepared', 'dispatched', 'observed', 'checkpointed', 'verified', 'interrupted', 'unknown']
WaitReason = Literal['owner', 'external', 'resource', 'reconciliation']
_TERMINAL = frozenset(('done', 'failed', 'cancelled'))
_NOTICE = frozenset(('waiting', 'paused', 'done', 'failed', 'cancelled'))
_SCHEMA = 1
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


@dataclass(frozen=True, slots=True)
class Origin:
    owner: str
    request_id: str
    lane: Literal['voice', 'typed', 'web', 'discord']
    attended: bool = True
    private: bool = True


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
    origin: Origin
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


def _spec(raw: dict[str, Any]) -> Specification:
    scope = Scope(tuple(raw['scope']['operations']), tuple(raw['scope']['targets']))
    return Specification(raw['outcome'], scope, tuple(Criterion(**item) for item in raw['criteria']), raw['project'])


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
        if any(type(n) is not int or n < 1 for n in (config.max_active, config.max_attempts, config.max_polls, config.max_record_chars)):
            raise ValueError('task limits must be positive integers')
        if _clock(config.evidence_max_age_s) <= 0:
            raise ValueError('evidence age must be positive')
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
                if version != _SCHEMA or app != _APPLICATION:
                    raise TaskStoreError('unsupported task schema; no migration or downgrade was attempted')
                if self._db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise TaskStoreError('task database failed its integrity check')
            if not fresh:
                self._validate_rows()
            self._db.execute('PRAGMA journal_mode=DELETE')
            self._db.execute('PRAGMA synchronous=EXTRA')
            if fresh:
                self._initialize()
                self._validate_rows()
            return self._transaction(lambda: self._recover(now))
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
            PRAGMA application_id={_APPLICATION};
            PRAGMA user_version={_SCHEMA};
            COMMIT;
        ''')

    def _transaction(self, operation: Callable[[], _T]) -> _T:
        if self._db is None or self._poisoned:
            raise TaskStoreError('task store is not available')
        try:
            self._db.execute('BEGIN IMMEDIATE')
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

    def _validate_request(self, origin: Origin, spec: Specification, step: Step) -> None:
        _text(origin.owner, 'owner')
        _text(origin.request_id, 'request identity')
        if origin.attended is not True or origin.private is not True or origin.lane not in ('voice','typed','web','discord'):
            raise TaskConflict('only an attended private owner request may create task responsibility')
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
        return Task(row['id'], Origin(**request['origin']), _spec(json.loads(row['specification_json'])),
                    _step(json.loads(row['step_json'])), row['status'], row['revision'],
                    row['attempts'], row['max_attempts'], row['polls'], row['max_polls'], row['evidence_max_age'], row['current_attempt'],
                    row['wait_reason'], row['detail'], row['eligible_at'], row['created_at'], row['updated_at'])

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
            'tasks': 'id owner request_id request_json specification_json step_json status revision attempts max_attempts polls max_polls evidence_max_age current_attempt wait_reason detail eligible_at created_at updated_at',
            'attempts': 'id task_id task_revision step_json phase created_at updated_at',
            'evidence': 'attempt_id criterion_id record_json',
            'transitions': 'task_id revision before_status after_status detail at',
            'outbox': 'id task_id revision status outcome detail created_at',
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

    async def create(self, origin: Origin, specification: Specification, step: Step, *, now: float | None = None) -> Task:
        self._validate_request(origin, specification, step)
        data = self._bounded({'origin':asdict(origin), 'specification':asdict(specification), 'step':asdict(step)})
        stamp = _clock(now)
        def write() -> Task:
            assert self._db is not None
            row = self._db.execute('SELECT * FROM tasks WHERE owner=? AND request_id=?', (origin.owner,origin.request_id)).fetchone()
            if row is not None:
                if row['request_json'] != data:
                    raise TaskConflict('originating request was reused for different work')
                return self._task(row)
            count = self._db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
            if count >= self._config.max_active:
                raise TaskLimit('active task allowance exhausted')
            task_id = uuid.uuid4().hex
            self._db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (task_id,origin.owner,origin.request_id,data,self._bounded(asdict(specification)),self._bounded(asdict(step)), 'queued',1,0,
                              self._config.max_attempts,0,self._config.max_polls,self._config.evidence_max_age_s,None,None,'created',stamp,stamp,stamp))
            task = self._get(origin.owner,task_id)
            self._event(task,None)
            return task
        return await self._run(lambda: self._transaction(write))

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
            return result
        return await self._run(lambda: self._transaction(write))

    async def complete(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
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

    async def checkpoint(self, owner: str, task_id: str, revision: int, step: Step, *, eligible_at: float, now: float | None = None) -> Task:
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
            return self._advance(task,'queued',stamp,detail='next step saved',step=step,eligible_at=eligible)
        return await self._run(lambda: self._transaction(write))

    async def wait(self, owner: str, task_id: str, revision: int, reason: WaitReason, detail: str, *, now: float | None = None) -> Task:
        stamp = _clock(now)
        if reason not in ('owner','external','resource'):
            raise ValueError('reconciliation waits belong to recovery')
        _text(detail,'wait explanation')
        def write() -> Task:
            task = self._get(owner,task_id,revision)
            if task.status not in ('queued','verifying'):
                raise TaskConflict('task cannot enter a wait from this state')
            if task.status == 'verifying' and task.next_step.kind == 'mutation':
                raise TaskConflict('checkpoint a scoped verification read before waiting after a mutation')
            self._checkpoint_attempt(task, stamp)
            return self._advance(task,'waiting',stamp,detail=detail,wait_reason=reason)
        return await self._run(lambda: self._transaction(write))

    async def pause(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        return await self._control(owner,task_id,revision,'paused',_clock(now))

    async def cancel(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        return await self._control(owner,task_id,revision,'cancelled',_clock(now))

    async def fail(self, owner: str, task_id: str, revision: int, detail: str, *, now: float | None = None) -> Task:
        _text(detail,'failure explanation')
        return await self._control(owner,task_id,revision,'failed',_clock(now),detail)

    async def _control(self, owner: str, task_id: str, revision: int, status: Status, now: float, detail: str | None = None) -> Task:
        def write() -> Task:
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
                                 wait_reason='reconciliation' if uncertain else None)
        return await self._run(lambda: self._transaction(write))

    async def resume(self, owner: str, task_id: str, revision: int, *, now: float | None = None) -> Task:
        stamp = _clock(now)
        def write() -> Task:
            task = self._get(owner,task_id,revision)
            if task.status not in ('paused','waiting','failed') or task.wait_reason == 'reconciliation':
                raise TaskConflict('task cannot resume without reconciliation or a resumable state')
            assert self._db is not None
            if task.status == 'failed':
                count = self._db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','failed','cancelled')").fetchone()[0]
                if count >= self._config.max_active:
                    raise TaskLimit('active task allowance exhausted')
            if task.attempts >= task.max_attempts:
                raise TaskLimit('task attempt allowance exhausted')
            if task.polls >= task.max_polls:
                raise TaskLimit('task polling allowance exhausted')
            return self._advance(task,'queued',stamp,detail='owner resumed',eligible_at=max(stamp,task.eligible_at))
        return await self._run(lambda: self._transaction(write))

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
