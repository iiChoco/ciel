"""The work a project is bound to, reached from where the brain runs.

Atlas records where a project's work lives (``projects.py``); the readers
say what a document holds (``readers.py``); this module is the hands
between them — the *workbench*: read a bound document within the bounds
the project sets, open one where the owner is, and turn a resource into a
reading on demand. In the single process the workbench is the filesystem
and ``open``; on the hub it is the spoke's executor one hop away, so a
server never stands in for the Mac the work is on.

**The roots are the registered folder.** A document is read only when it
is a bound resource or lies under a folder the owner bound to the same
project, and an include is followed only within those roots. The brain's
workspace guard is not the rule here: a course folder is nowhere near the
workspace, and the owner naming it is what makes it readable. Both halves
check: this side against the project's bindings, the spoke against its own
home, state directory, and credential names.

**Opening is attended, reading is quiet.** "Pull it up" opens the current
resource of the role asked for, with the app the binding names or the
Mac's default; nothing is written, and a URL is handed to the browser.
A reading is a bounded, deterministic pass — bytes, includes, and depth
capped by ``[projects]`` — answered live on demand.

**Readings are kept under a grant, and only then.** :class:`ProjectAdapter`
is the feature the task runner sees, in the ``atlas`` namespace of the
task store: it offers one standing grant — *readings of bound documents*,
narrowed to the projects the owner picks — and under its mandate a watch
task runs. The Mac's resource watcher (``proactive/resources.py``) reports
a settled change by path and content hash; the hub records it as a
``change`` record, the watch derives one bounded reading task per settled
change, and the reading is committed only if the change it was derived for
is still the newest — a save during the reading makes the old result
obsolete, and the newer save gets its own. Nothing is spoken: a reading is
a fact kept, not news. The set of files the Mac watches is the projects'
bound documents plus every file their last readings followed, and it is
resent whenever the spoke seats.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ciel.projects import Project, ProjectStore, Resource
from ciel.readers import Reading, describe, read, reader_for
from ciel.tasks import (Criterion, Evidence, FeatureRecord, GrantLimits, GrantSetup, HumanOrigin, Mandate, Namespace, RecordSet,
                        RecordWrite, Scope, Specification, StandingGrant, Step, Task, TaskConflict)
from ciel.task_runner import Derivation, Outcome, Preparation, StepContext

log = logging.getLogger(__name__)

DOCUMENT_SUFFIXES = (".tex", ".ltx", ".latex", ".sty", ".cls", ".bib", ".md", ".markdown", ".mdown", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml")
"""What a workbench reads as a document: text the owner writes or that a
document includes. Anything else is located and opened, never read here."""


@dataclass(frozen=True, slots=True)
class WorkLimits:
    max_bytes: int = 2_000_000
    max_includes: int = 20
    include_depth: int = 3
    observe_poll_s: float = 60.0
    """Between the watch task's looks at the settled changes on record."""
    max_reads_per_day: int = 200
    """What the standing grant asks for; [tasks] caps it."""
    lifetime_s: float = 30 * 86400.0
    max_items: int = 200
    """Roster items one kept reading holds; the rest are counted, not listed."""


class Workbench(Protocol):
    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        """The document's bytes, or None and why not, within ``max_bytes``."""

    async def open(self, target: str, opener: str) -> str:
        """Open a path or URL where the owner is; the sentence to tell them."""


def check_document_path(raw: str, *, home: Path, state_dir: Path, forbidden: frozenset[str]) -> tuple[Path | None, str]:
    """The spoke's own opinion of a document path, whatever the hub said:
    under home, not under the state directory, not a credential's name, a
    document suffix. Returns the resolved path or the refusal."""
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return None, "a document is named by an absolute path"
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None, "the path could not be resolved"
    try:
        resolved.relative_to(home.resolve())
    except ValueError:
        return None, "documents are read only under the home folder"
    try:
        resolved.relative_to(state_dir.expanduser().resolve())
        return None, "nothing under the state directory is a document"
    except ValueError:
        pass
    if resolved.name in forbidden or any(part in forbidden for part in resolved.parts):
        return None, "that name is off limits"
    if resolved.suffix.lower() not in DOCUMENT_SUFFIXES:
        return None, f"{resolved.suffix or 'no suffix'} is not a document type the reader takes"
    return resolved, ""


def read_document_bytes(path: Path, max_bytes: int) -> tuple[bytes | None, str | None]:
    """A bounded read: one byte past the bound tells a growing file apart."""
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except FileNotFoundError:
        return None, "the file does not exist"
    except IsADirectoryError:
        return None, "that is a folder, not a document"
    except OSError as exc:
        return None, f"the file could not be read ({exc.__class__.__name__})"
    if len(data) > max_bytes:
        return None, f"the file is larger than {max_bytes} bytes"
    return data, None


class LocalWorkbench:
    """The single process: the owner's machine is this one."""

    def __init__(self, *, home: Path | None = None, state_dir: Path, forbidden: frozenset[str] = frozenset()) -> None:
        self._home = (home or Path.home()).expanduser()
        self._state_dir = state_dir
        self._forbidden = forbidden

    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        resolved, why = check_document_path(path, home=self._home, state_dir=self._state_dir, forbidden=self._forbidden)
        if resolved is None:
            return None, why
        return await asyncio.to_thread(read_document_bytes, resolved, max_bytes)

    async def open(self, target: str, opener: str) -> str:
        return await asyncio.to_thread(open_target, target, opener, home=self._home, state_dir=self._state_dir)


def open_target(target: str, opener: str, *, home: Path, state_dir: Path, runner: Any = subprocess.run) -> str:
    """``open`` on the Mac: a URL to the browser, a path to its app or the
    one named. Nothing under the state directory, nothing that is not there."""
    if target.lower().startswith(("http://", "https://")):
        args = ["/usr/bin/open", *(["-a", opener] if opener else []), target]
        what = "the page"
    else:
        path = Path(target).expanduser()
        try:
            resolved = path.resolve()
            resolved.relative_to(home.resolve())
        except (OSError, RuntimeError, ValueError):
            return "refused: documents open only under the home folder"
        try:
            resolved.relative_to(state_dir.expanduser().resolve())
            return "refused: nothing under the state directory is opened"
        except ValueError:
            pass
        if not resolved.exists():
            return f"not opened: {resolved} does not exist"
        args = ["/usr/bin/open", *(["-a", opener] if opener else []), str(resolved)]
        what = resolved.name
    try:
        result = runner(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not open {what}: {exc.__class__.__name__}"
    if result.returncode != 0:
        return f"could not open {what}: {(result.stderr or '').strip() or f'open exited {result.returncode}'}"
    return f"opened {what}" + (f" with {opener}" if opener else "")


class RemoteWorkbench:
    """The hub: the Mac is the spoke, one call away."""

    def __init__(self, mac: Any) -> None:
        self._mac = mac

    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        try:
            return await self._mac.read_document(path, max_bytes)
        except Exception as exc:  # noqa: BLE001 - the Mac may be gone; that is a sentence
            return None, f"the Mac could not be reached: {exc}"

    async def open(self, target: str, opener: str) -> str:
        try:
            return str(await self._mac.open_document(target, opener))
        except Exception as exc:  # noqa: BLE001
            return f"the Mac could not be reached: {exc}"


def roots_for(project: Project, resource: Resource) -> tuple[Path, ...]:
    """Where a reading may go: the folders bound to the project, else the
    document's own folder. The owner's bindings, not a global rule."""
    folders = [Path(r.locator).expanduser() for r in project.resources if r.source == "local" and r.role in ("folder", "root", "directory")]
    if not folders:
        folders = [Path(resource.locator).expanduser().parent]
    return tuple(folders)


def within(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


@dataclass(frozen=True, slots=True)
class Observed:
    resource: Resource
    reading: Reading | None
    revisions: dict[str, str]
    """A content hash per file the reading covered, the main one first."""
    note: str
    """Why there is no reading, or what to say beside one."""

    def describe(self) -> str:
        head = f"{self.resource.key} ({self.resource.role}): {self.resource.locator}"
        if self.reading is None:
            return f"{head} — {self.note}"
        return f"{head} — {describe(self.reading)}" + (f" {self.note}" if self.note else "")


async def observe(project: Project, resource: Resource, bench: Workbench, limits: WorkLimits) -> Observed:
    """One on-demand reading of a bound local document, includes followed
    within the project's roots, every file hashed. No model, no writes."""
    if resource.source != "local":
        return Observed(resource, None, {}, "a URL is opened, not read here")
    main = Path(resource.locator).expanduser()
    roots = roots_for(project, resource)
    if not within(main, roots):
        return Observed(resource, None, {}, "the document lies outside the folders bound to this project")
    if reader_for(resource.locator) is None:
        return Observed(resource, None, {}, f"no reader for {main.suffix or 'a file with no suffix'}; it can be opened, not read")
    data, why = await bench.read(str(main), limits.max_bytes)
    if data is None:
        return Observed(resource, None, {}, why or "the document could not be read")
    revisions = {main.name: hashlib.sha256(data).hexdigest()[:16]}
    text = data.decode("utf-8", "replace")
    followed = 0
    pending: list[tuple[str, Path]] = []
    loaded: dict[str, str] = {}

    async def preload(reference: str, base: Path, depth: int) -> None:
        """Includes are read ahead, breadth-first, within the roots and the
        limits; the reader's loader then answers from memory, synchronously."""
        nonlocal followed
        if depth > limits.include_depth or followed >= limits.max_includes:
            return
        candidate = (base / reference).with_suffix(".tex") if not reference.endswith(".tex") else base / reference
        key = reference
        if key in loaded or not within(candidate, roots):
            return
        chunk, _ = await bench.read(str(candidate), limits.max_bytes)
        if chunk is None:
            return
        followed += 1
        loaded[key] = chunk.decode("utf-8", "replace")
        revisions[candidate.name] = hashlib.sha256(chunk).hexdigest()[:16]
        for inner in _references(loaded[key]):
            await preload(inner, candidate.parent, depth + 1)

    for reference in _references(text):
        await preload(reference, main.parent, 1)
    reading = read(text, main.name, lambda reference: loaded.get(reference))
    note = "" if followed < limits.max_includes else f"stopped following includes at {limits.max_includes}."
    return Observed(resource, reading, revisions, note)


def _references(text: str) -> list[str]:
    import re
    return [m.group(1).strip() for m in re.finditer(r"\\(?:input|include)\s*\{\s*([^}]*?)\s*\}", text)]


# ── the feature: readings kept under a grant ─────────────────────────────────

NAMESPACE_NAME = "atlas"
OPERATIONS = frozenset({"resource.poll", "resource.read"})


def _validate(payload: dict[str, Any]) -> None:
    kind = payload.get("kind")
    if kind == "change":
        for name in ("path", "digest", "seen_at", "project", "key"):
            if name not in payload:
                raise ValueError(name)
    elif kind == "reading":
        for name in ("project", "key", "path", "revisions", "read_at", "reader", "counts", "summary"):
            if name not in payload:
                raise ValueError(name)
        if not isinstance(payload["revisions"], dict):
            raise ValueError("revisions")
    elif kind == "watch":
        for name in ("task_id", "mandate_id"):
            if name not in payload:
                raise ValueError(name)
    else:
        raise ValueError("kind")


NAMESPACE = Namespace(NAMESPACE_NAME, 1, _validate)


def reading_payload(project: Project, observed: Observed, now: float, *, max_items: int = 200) -> dict[str, Any]:
    """A reading as the store keeps it: bounded, its files' hashes named,
    the document's words quoted in evidence and never anywhere else."""
    reading = observed.reading
    assert reading is not None
    revisions = {str(Path(observed.resource.locator).expanduser().parent / name): digest for name, digest in observed.revisions.items()}
    return {
        "kind": "reading", "project": project.id, "project_name": project.name, "key": observed.resource.key,
        "path": str(Path(observed.resource.locator).expanduser()), "revisions": revisions, "read_at": now,
        "reader": reading.reader, "version": reading.version,
        "items": [{"id": i.id, "kind": i.kind, "title": i.title, "claim": i.claim, "evidence": i.evidence, "file": i.file, "line": i.line}
                  for i in reading.items[:max_items]],
        "listed": min(len(reading.items), max_items), "gaps": list(reading.gaps), "counts": reading.counts(),
        "complete": reading.complete, "summary": describe(reading), "note": observed.note,
    }


def age_words(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 36 * 3600:
        return f"{int(seconds // 3600)} hours ago"
    return f"{int(seconds // 86400)} days ago"


class ProjectAdapter:
    """Readings of bound documents, kept under a standing grant.

    ``resource.poll`` is the watch: it reads the ``change`` records the hub
    wrote as the Mac reported settled changes, and derives one reading task
    per change whose hash no reading has yet, under the mandate. A reading
    task's one step, ``resource.read``, reads the document now through the
    workbench and commits the reading only if the change it was derived for
    is still the newest on record; otherwise it completes as superseded and
    the newer change's own task reads.
    """

    namespace = NAMESPACE
    operations = OPERATIONS

    def __init__(self, projects: ProjectStore, bench: Workbench | None, limits: WorkLimits, *, host: str = "local",
                 watch: Any = None, clock: Any = time.time) -> None:
        self._projects = projects
        self._bench = bench
        self._limits = limits
        self._host = host
        self._watch = watch
        """``async (paths: list[str]) -> None``: tells the Mac what to watch; None when there is no Mac."""
        self._clock = clock
        self._store_getter: Any = lambda: None
        self._owner = ""

    def bind_store(self, store: Any, owner: str) -> None:
        """Where the Mac's reports are written: the controller's store, looked
        up on each report since it opens after the adapter is built."""
        self._store_getter = store if callable(store) else (lambda: store)
        self._owner = owner

    @property
    def _store(self) -> Any:
        return self._store_getter()

    # ── the grant ────────────────────────────────────────────────────────────

    @property
    def setup(self) -> GrantSetup | None:
        targets = tuple((f"project:{p.id}", p.name) for p in self._projects.all()
                        if p.id and any(r.source == "local" and reader_for(r.locator) for r in p.resources))
        if not targets:
            return None
        return GrantSetup(
            NAMESPACE_NAME, "Readings of bound documents",
            "Each bound document is read again after a settled save, and where the work stands is kept",
            self._host,
            (("resource.poll", "watch the bound documents"), ("resource.read", "read one after a settled save")),
            targets,
            (("reads", "deterministic: no model, no writes, nothing spoken"),),
            GrantLimits(max_children=max(1, int(self._limits.max_reads_per_day * self._limits.lifetime_s // 86400)), window_s=86400.0,
                        max_per_window=self._limits.max_reads_per_day, lifetime_s=self._limits.lifetime_s),
        )

    async def activated(self, store: Any, origin: HumanOrigin, grant: StandingGrant, mandate: Mandate) -> None:
        """The feature's first move under a new mandate: the watch, as the
        approving turn, remembered by mandate with the projects it covers."""
        spec, step = watch_request(grant.scope.targets, mandate.id, self._limits)
        task = await store.create(origin, spec, step, now=self._clock())
        await store.write_records(origin.owner, RecordSet(NAMESPACE_NAME, (
            RecordWrite(f"watch:{mandate.id}", {"kind": "watch", "task_id": task.id, "mandate_id": mandate.id, "targets": list(grant.scope.targets)}, None),)))
        await self.sync_watch(store, origin.owner)

    async def mandate_changed(self, store: Any, owner: str, mandate: Mandate) -> None:
        records = await store.records(owner, NAMESPACE_NAME, (f"watch:{mandate.id}",))
        if not records:
            return
        task = await store.get(owner, str(records[0].payload["task_id"]))
        try:
            if mandate.status == "paused" and task.status not in ("paused", "done", "failed", "cancelled"):
                await store.pause(owner, task.id, task.revision)
            elif mandate.status == "active" and task.status == "paused":
                await store.resume(owner, task.id, task.revision)
            elif mandate.status in ("revoked", "expired") and task.status not in ("done", "failed", "cancelled"):
                await store.cancel(owner, task.id, task.revision)
        except TaskConflict:
            log.info("the readings watch for mandate %s was already where its mandate put it", mandate.id)
        await self.sync_watch(store, owner)

    async def watched_paths(self, store: Any, owner: str) -> list[str]:
        """What the Mac should watch: the local documents of every project
        under an active mandate, and every file their kept readings covered."""
        try:
            mandates = await store.mandates(owner)
            records = await store.records(owner, NAMESPACE_NAME)
        except Exception:  # noqa: BLE001 - no store, nothing watched
            return []
        active = {m.id for m in mandates if m.namespace == NAMESPACE_NAME and m.status == "active"}
        targets: set[str] = set()
        for record in records:
            if record.payload.get("kind") == "watch" and record.payload.get("mandate_id") in active:
                targets.update(str(t) for t in record.payload.get("targets", []))
        if not targets:
            return []
        paths: set[str] = set()
        for project in self._projects.all():
            if f"project:{project.id}" not in targets:
                continue
            for resource in project.resources:
                if resource.source == "local" and reader_for(resource.locator):
                    paths.add(str(Path(resource.locator).expanduser()))
        for record in records:
            if record.payload.get("kind") == "reading" and f"project:{record.payload.get('project')}" in targets:
                paths.update(str(p) for p in record.payload.get("revisions", {}))
        return sorted(paths)

    async def sync_watch(self, store: Any, owner: str) -> list[str]:
        paths = await self.watched_paths(store, owner)
        if self._watch is not None:
            try:
                await self._watch(paths)
            except Exception:  # noqa: BLE001 - the Mac may be away; the seat hook resends
                log.info("the Mac could not be told what to watch; it will be told when it seats")
        return paths

    # ── the Mac's report ─────────────────────────────────────────────────────

    def project_for(self, path: str) -> tuple[Project, Resource] | None:
        wanted = str(Path(path).expanduser())
        for project in self._projects.all():
            for resource in project.resources:
                if resource.source == "local" and str(Path(resource.locator).expanduser()) == wanted:
                    return project, resource
            # A file a reading followed belongs to the document that followed it.
        return None

    async def changed(self, payload: dict[str, Any]) -> bool:
        """A settled change from the Mac: recorded by path and hash under
        the project whose document it is, or whose reading followed it.
        Nothing is read here; the watch task derives the reading."""
        if self._store is None:
            return False
        path = str(payload.get("path") or "")
        digest = str(payload.get("digest") or "")
        if not path:
            return False
        found = self.project_for(path)
        key = ""
        project_id = ""
        if found is not None:
            project_id, key = found[0].id, found[1].key
        else:
            for record in await self._store.records(self._owner, NAMESPACE_NAME):
                if record.payload.get("kind") == "reading" and path in record.payload.get("revisions", {}):
                    project_id, key = str(record.payload["project"]), str(record.payload["key"])
                    break
        if not project_id:
            log.debug("a change on %s belongs to no bound project; ignored", path)
            return False
        record_key = f"change:{path}"
        existing = await self._store.records(self._owner, NAMESPACE_NAME, (record_key,))
        now = self._clock()
        write = RecordWrite(record_key, {"kind": "change", "path": path, "digest": digest, "seen_at": now,
                                         "mtime": str(payload.get("mtime") or ""), "project": project_id, "key": key},
                            existing[0].revision if existing else 0)
        try:
            await self._store.write_records(self._owner, RecordSet(NAMESPACE_NAME, (write,)))
        except TaskConflict:
            log.info("a change record moved under the report; the next report wins")
            return False
        return True

    # ── the runner's side ────────────────────────────────────────────────────

    def prepare(self, task: Task, records: tuple[FeatureRecord, ...]) -> Preparation:
        if task.next_step.operation == "resource.read" and self._bench is None:
            return Preparation(wait=("resource", "The owner's machine is not reachable; the document cannot be read until it is."))
        return Preparation()

    async def read(self, ctx: StepContext) -> Outcome:
        if ctx.task.next_step.operation == "resource.poll":
            return await self._poll(ctx)
        return await self._read_one(ctx)

    async def _poll(self, ctx: StepContext) -> Outcome:
        arguments = dict(ctx.task.next_step.arguments)
        mandate_id = arguments.get("mandate", "")
        targets = set(ctx.task.specification.scope.targets)
        readings = {(r.payload["project"], r.payload["key"]): r.payload for r in ctx.records if r.payload.get("kind") == "reading"}
        derivations: list[Derivation] = []
        for record in ctx.records:
            payload = record.payload
            if payload.get("kind") != "change" or f"project:{payload.get('project')}" not in targets:
                continue
            reading = readings.get((payload["project"], payload["key"]))
            if reading is not None and reading.get("revisions", {}).get(payload["path"]) == payload["digest"]:
                continue  # read already at this very hash
            if not payload["digest"]:
                continue  # gone: nothing to read; the last reading stands, at its age
            spec, step = reading_request(str(payload["project"]), str(payload["key"]), str(payload["digest"]), str(payload["path"]))
            derivations.append(Derivation(mandate_id, f"reading:{payload['project']}:{payload['key']}:{payload['digest']}", str(payload["digest"]), spec, step))
        evidence = Evidence("watch", ctx.task.next_step.target, "active", "resources", ctx.now)
        return Outcome(evidence=(evidence,), next_step=ctx.task.next_step, delay_s=self._limits.observe_poll_s, derive=tuple(derivations))

    async def _read_one(self, ctx: StepContext) -> Outcome:
        arguments = dict(ctx.task.next_step.arguments)
        project_id, key, digest, path = arguments.get("project", ""), arguments.get("key", ""), arguments.get("digest", ""), arguments.get("path", "")
        target = ctx.task.next_step.target
        done = Evidence("reading", target, "read", "resources", ctx.now)
        change = next((r for r in ctx.records if r.key == f"change:{path}"), None)
        if change is not None and change.payload.get("digest") != digest:
            # A newer save is on record: this reading would be of the past.
            return Outcome(evidence=(done,), records=RecordSet(NAMESPACE_NAME, ()))
        project = next((p for p in self._projects.all() if p.id == project_id), None)
        resource = project.resource(key=key) if project is not None else None
        if project is None or resource is None:
            return Outcome(evidence=(done,))  # unbound since: nothing to keep
        assert self._bench is not None
        observed = await observe(project, resource, self._bench, self._limits)
        if observed.reading is None:
            return Outcome(evidence=(done,))
        main_digest = observed.revisions.get(Path(resource.locator).expanduser().name, "")
        if change is not None and main_digest != digest:
            # The file moved between the report and this read; the Mac will
            # report the settled hash, and that report's task reads.
            return Outcome(evidence=(done,))
        payload = reading_payload(project, observed, ctx.now, max_items=self._limits.max_items)
        writes = [RecordWrite(f"reading:{project_id}:{key}", payload, None)]
        if change is not None:
            writes.append(RecordWrite(change.key, {**change.payload, "read_at": ctx.now}, change.revision))
        outcome = Outcome(evidence=(done,), records=RecordSet(NAMESPACE_NAME, tuple(writes)))
        if self._watch is not None and set(payload["revisions"]) - {path}:
            # The reading followed files the Mac may not be watching yet.
            store = self._store
            if store is not None:
                try:
                    await self._watch(sorted(set(await self.watched_paths(store, self._owner)) | set(payload["revisions"])))
                except Exception:  # noqa: BLE001
                    log.info("the Mac could not be told about a reading's includes yet")
        return outcome

    # ── the owner's view ─────────────────────────────────────────────────────

    @staticmethod
    def summarize(task: Task, records: tuple[FeatureRecord, ...]) -> str:
        arguments = dict(task.next_step.arguments)
        if task.next_step.operation == "resource.poll":
            changes = [r.payload for r in records if r.payload.get("kind") == "change"]
            kept = [r.payload for r in records if r.payload.get("kind") == "reading"]
            return f"Watching the bound documents: {len(changes)} settled change(s) on record, {len(kept)} reading(s) kept."
        reading = next((r.payload for r in records if r.key == f"reading:{arguments.get('project')}:{arguments.get('key')}"), None)
        if reading is None:
            return "No reading kept for this document yet."
        return str(reading.get("summary", ""))

    def listing(self, records: tuple[FeatureRecord, ...]) -> list[dict[str, Any]]:
        now = self._clock()
        rows = []
        for record in sorted((r for r in records if r.payload.get("kind") == "reading"), key=lambda r: -float(r.payload.get("read_at", 0))):
            payload = record.payload
            counts = payload.get("counts", {})
            rows.append({"key": record.key, "state": f"read {age_words(now - float(payload.get('read_at', now)))}",
                         "title": f"{payload.get('project_name') or payload.get('project')} · {payload.get('key')}",
                         "when": f"{counts.get('answer written', 0)} written, {counts.get('in progress', 0)} in progress, {counts.get('not started', 0)} not started",
                         "location": "", "sender": "", "subject": str(payload.get("path", "")), "reason": "; ".join(payload.get("gaps", [])[:2]),
                         "unresolved": [], "asked": False, "event": None, "proposals": [], "controls": []})
        return rows[:64]


def watch_request(targets: tuple[str, ...], mandate_id: str, limits: WorkLimits) -> tuple[Specification, Step]:
    """The task that keeps the readings current: look at the settled changes
    on record, derive a reading for each new hash, and go round again."""
    first = targets[0] if targets else "project:*"
    spec = Specification("The bound documents are read after each settled save", Scope(("resource.poll", "resource.read"), tuple(targets) or (first,)),
                         (Criterion("watch", first, "ended"),))
    return spec, Step("read", "resource.poll", first, (("mandate", mandate_id),))


def reading_request(project_id: str, key: str, digest: str, path: str) -> tuple[Specification, Step]:
    target = f"project:{project_id}"
    spec = Specification(f"{key} of the project is read at {digest[:8]}", Scope(("resource.read",), (target,)), (Criterion("reading", target, "read"),))
    return spec, Step("read", "resource.read", target, (("project", project_id), ("key", key), ("digest", digest), ("path", path)))


__all__ = ["DOCUMENT_SUFFIXES", "LocalWorkbench", "NAMESPACE", "NAMESPACE_NAME", "Observed", "ProjectAdapter", "RemoteWorkbench", "WorkLimits",
           "Workbench", "age_words", "check_document_path", "observe", "open_target", "read_document_bytes", "reading_payload", "reading_request",
           "roots_for", "watch_request", "within"]
