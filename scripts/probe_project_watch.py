"""Probe the readings kept under a grant — the Mac's resource watcher and the project adapter.

A temporary home, a temporary task store, a fake queue, no model, no Mac.
Pins, for the watcher: a path the hub names is taken at its state with no
event; a save is reported once, after two polls agree, with the path and
the content hash and nothing else; a burst of saves is one event for the
last content; an unchanged rewrite is none; a deletion is one event saying
gone; a restart catches up on an edit made while down; a dropped path is
forgotten; a path that is not there is watched without an event; a broken
mirror file starts empty. For the adapter: the grant is offered only when
a project has a readable local document, one target per such project; the
approving turn starts the watch under the mandate and the Mac is told what
to watch; a settled change is recorded under its project and an unknown
path is ignored; the watch derives one reading per new hash and none for
a hash already read or a file gone; the reading is kept with every file's
hash and the Mac is then told about the include it followed; a change
that arrives before its reading runs makes that reading superseded and
the newer hash gets its own; a paused mandate pauses the watch, a revoked
one ends it and the Mac watches nothing; the owner's view and the Chart
rows; and with no workbench a reading waits on the machine.

    uv run --no-sync python scripts/probe_project_watch.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import TasksConfig
from ciel.proactive.events import ProactiveEvent
from ciel.proactive.resources import ResourceWatcher
from ciel.project_work import NAMESPACE, LocalWorkbench, ProjectAdapter, WorkLimits
from ciel.projects import ProjectStore
from ciel.task_runner import TaskRunner
from ciel.tasks import DerivedOrigin, HumanOrigin, Scope, TaskStore

CHECKS: list[str] = []
OWNER = "fixture-owner"


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class FakeQueue:
    def __init__(self) -> None:
        self.events: list[ProactiveEvent] = []
        self.keys: set[str] = set()
        self._n = 0

    def next_id(self) -> str:
        self._n += 1
        return f"e{self._n}"

    def push(self, event: ProactiveEvent) -> bool:
        if event.dedupe_key in self.keys:
            return False
        self.keys.add(event.dedupe_key)
        self.events.append(event)
        return True


def digest_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def watcher_checks(root: Path) -> None:
    print("the resource watcher")
    doc = root / "hw03.tex"
    doc.write_text("v1")
    mirror = root / "spoke-resources.json"
    queue = FakeQueue()
    watcher = ResourceWatcher(mirror, queue, poll_s=0.01)
    check("a path the hub names is taken at its state with no event", watcher.set_paths([str(doc)]) == 1 and queue.events == [] and watcher.check(1.0) == 0)
    doc.write_text("v2")
    check("the first poll after a save holds the change pending", watcher.check(2.0) == 0 and queue.events == [])
    check("the second poll that agrees reports it once, with the path and hash and nothing else",
          watcher.check(3.0) == 1 and queue.events[-1].source == "resource" and queue.events[-1].importance == 1
          and queue.events[-1].payload["path"] == str(doc) and queue.events[-1].payload["digest"] == digest_of(doc)
          and "v2" not in json.dumps(queue.events[-1].payload) and watcher.check(4.0) == 0)
    doc.write_text("v3")
    watcher.check(5.0)
    doc.write_text("v4")
    check("a burst restarts the wait: the middle content is never reported", watcher.check(6.0) == 0 and len(queue.events) == 1)
    check("and the last content is reported once it holds still", watcher.check(7.0) == 1 and queue.events[-1].payload["digest"] == digest_of(doc))
    doc.write_text("v4")
    check("an unchanged rewrite is nothing", watcher.check(8.0) == 0 and watcher.check(9.0) == 0 and len(queue.events) == 2)
    doc.unlink()
    watcher.check(10.0)
    check("a deletion is one event saying gone", watcher.check(11.0) == 1 and queue.events[-1].payload["digest"] == "" and "gone" in queue.events[-1].summary)
    doc.write_text("v5")
    watcher.check(12.0)
    watcher.check(13.0)
    later = ResourceWatcher(mirror, queue, poll_s=0.01)
    check("the mirror survives: a fresh watcher knows the path and its last reported hash", later.watched() == [str(doc)] and later.check(14.0) == 0)
    doc.write_text("v6 while down")
    fresh = ResourceWatcher(mirror, queue, poll_s=0.01)
    fresh.check(15.0)
    check("a restart catches up on an edit made while down", fresh.check(16.0) == 1 and queue.events[-1].payload["digest"] == digest_of(doc))
    ghost = root / "ghost.tex"
    check("a dropped path is forgotten and a path that is not there is watched without an event",
          fresh.set_paths([str(ghost)]) == 1 and fresh.watched() == [str(ghost)] and fresh.check(17.0) == 0 and fresh.check(18.0) == 0)
    mirror.write_text("{not json")
    check("a broken mirror starts empty", ResourceWatcher(mirror, queue).watched() == [])


class Fixture:
    def __init__(self, root: Path, *, bench: Any, clock: float) -> None:
        self.tasks = replace(TasksConfig(), directory=root / "tasks", owner=OWNER, enabled=True)
        self.projects = ProjectStore(root / "projects")
        self.watched: list[list[str]] = []
        self.clock = clock

        async def watch(paths: list[str]) -> None:
            self.watched.append(list(paths))

        self.adapter = ProjectAdapter(self.projects, bench, WorkLimits(max_bytes=10000, max_includes=4, include_depth=2, observe_poll_s=30.0,
                                                                        max_reads_per_day=5, lifetime_s=86400.0 * 2),
                                      host="hub", watch=watch, clock=lambda: self.clock)
        self.store: TaskStore | None = None
        self.lock = asyncio.Lock()
        self.runner = TaskRunner(self.tasks, lambda: self.store, (self.adapter,), lease=self.lease, backend=None, clock=lambda: self.clock)

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        async with self.lock:
            yield

    async def open(self) -> TaskStore:
        store = TaskStore(self.tasks)
        store.register(NAMESPACE)
        await store.start()
        self.store = store
        self.adapter.bind_store(lambda: self.store, OWNER)
        return store

    async def run(self, task_id: str, limit: int = 6) -> list[str]:
        assert self.store is not None
        results = []
        for _ in range(limit):
            report = await self.runner.step(now=self.clock)
            if report is None:
                break
            results.append(report.result)
            if report.result in ("done", "waiting", "refused", "abandoned"):
                break
        return results


async def adapter_checks(root: Path) -> None:
    print("\nreadings kept under a grant")
    home = root / "home"
    course = home / "H104" / "hw03"
    course.mkdir(parents=True)
    solution = course / "hw03.tex"
    solution.write_text("\\begin{numedquestion} One. \\begin{framed} Done. \\end{framed} \\end{numedquestion}\n\\input{more}\n")
    (course / "more.tex").write_text("\\begin{numedquestion} Two. \\end{numedquestion}\n")
    bench = LocalWorkbench(home=home, state_dir=root / "state", forbidden=frozenset())
    base = time.time()
    f = Fixture(root, bench=bench, clock=base)
    check("with no readable local document bound, no grant is offered", f.adapter.setup is None)
    f.projects.write("analysis", "hw03 open", description="H104")
    f.projects.bind("analysis", "folder", str(course.parent))
    f.projects.bind("analysis", "solution", str(solution), key="hw03", current=True)
    f.projects.write("proposal", "outline", description="grant")
    f.projects.bind("proposal", "handout", "https://example.test/call.pdf")
    project = f.projects.get("analysis")
    setup = f.adapter.setup
    check("the grant names one target per project with a readable local document, and its two operations",
          setup is not None and setup.targets == ((f"project:{project.id}", "analysis"),)
          and [o for o, _ in setup.operations] == ["resource.poll", "resource.read"] and setup.limits.max_per_window == 5)
    store = await f.open()
    turn = HumanOrigin(OWNER, "activation-turn", "web", ingress_ids=("web:1",))
    draft = await store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome, Scope(tuple(o for o, _ in setup.operations), tuple(t for t, _ in setup.targets)),
                                         setup.limits, setup.bindings, now=base)
    grant, mandate = await store.activate_grant(turn, draft.id, draft.revision, draft.digest, "chart:fixture", now=base)
    await f.adapter.activated(store, turn, grant, mandate)
    watch_record = (await store.records(OWNER, NAMESPACE.name, (f"watch:{mandate.id}",)))[0].payload
    watch = await store.get(OWNER, watch_record["task_id"])
    check("the approving turn starts the watch under the mandate, and the Mac is told to watch the bound document",
          watch.next_step.operation == "resource.poll" and dict(watch.next_step.arguments)["mandate"] == mandate.id
          and isinstance(watch.origin, HumanOrigin) and f.watched[-1] == [str(solution)])
    check("the watch goes round with nothing on record", await f.run(watch.id) == ["checkpointed"] and (await store.get(OWNER, watch.id)).eligible_at >= base + 30)
    check("a change on a path no project binds is ignored", await f.adapter.changed({"path": str(home / "stray.tex"), "digest": "abcd"}) is False)
    first = digest_of(solution)
    check("a settled change is recorded under its project and resource",
          await f.adapter.changed({"path": str(solution), "digest": first, "mtime": "1.0"}) is True
          and next(r.payload for r in await store.records(OWNER, NAMESPACE.name) if r.key == f"change:{solution}")["key"] == "hw03")
    f.clock = base + 31
    await f.run(watch.id)
    children = [t for t in await store.list(OWNER) if isinstance(t.origin, DerivedOrigin)]
    check("the watch derives one reading for the new hash, under the mandate",
          len(children) == 1 and children[0].origin.parent_mandate_id == mandate.id and children[0].origin.event_key == f"reading:{project.id}:hw03:{first}"
          and children[0].next_step.operation == "resource.read")
    await f.run(children[0].id)
    reading = next((r.payload for r in await store.records(OWNER, NAMESPACE.name) if r.key == f"reading:{project.id}:hw03"), None)
    check("the reading is kept: the summary, every file's hash, and the change marked read",
          (await store.get(OWNER, children[0].id)).status == "done" and reading is not None and reading["revisions"][str(solution)] == first
          and str(course / "more.tex") in reading["revisions"]
          and "2 questions: 1 written, 0 in progress, 1 not started" in reading["summary"] and reading["counts"]["total"] == 2
          and next(r.payload for r in await store.records(OWNER, NAMESPACE.name) if r.key == f"change:{solution}").get("read_at") == base + 31)
    check("the Mac is then told about the include the reading followed", f.watched[-1] == sorted([str(solution), str(course / "more.tex")]))
    f.clock = base + 62
    await f.run(watch.id)
    check("a hash already read derives nothing", len([t for t in await store.list(OWNER) if isinstance(t.origin, DerivedOrigin)]) == 1)
    solution.write_text("\\begin{numedquestion} One. \\begin{framed} Done. \\end{framed} \\end{numedquestion}\n\\input{more}\n% edited\n")
    stale = "0000000000000000"
    await f.adapter.changed({"path": str(solution), "digest": stale})
    f.clock = base + 93
    await f.run(watch.id)
    second = digest_of(solution)
    await f.adapter.changed({"path": str(solution), "digest": second})
    f.clock = base + 124
    await f.run(watch.id)
    children = sorted((t for t in await store.list(OWNER) if isinstance(t.origin, DerivedOrigin)), key=lambda t: t.created_at)
    check("a change that arrives before its reading runs gets its own task, and both are on record",
          len(children) == 3 and children[1].origin.event_key.endswith(stale) and children[2].origin.event_key.endswith(second))
    await f.run(children[1].id)
    await f.run(children[2].id)
    reading = next(r.payload for r in await store.records(OWNER, NAMESPACE.name) if r.key == f"reading:{project.id}:hw03")
    check("the stale reading completes as superseded with nothing of it kept, and the newest hash's reading is kept",
          (await store.get(OWNER, children[1].id)).status == "done" and (await store.get(OWNER, children[2].id)).status == "done"
          and reading["revisions"][str(solution)] == second and reading["read_at"] == base + 124 and stale not in json.dumps(reading))
    await f.adapter.changed({"path": str(solution), "digest": ""})
    f.clock = base + 155
    await f.run(watch.id)
    check("a file gone derives nothing; the last reading stands at its age",
          len([t for t in await store.list(OWNER) if isinstance(t.origin, DerivedOrigin)]) == 3
          and f.adapter.listing(await store.records(OWNER, NAMESPACE.name))[0]["title"] == "analysis · hw03")
    rows = f.adapter.listing(await store.records(OWNER, NAMESPACE.name))
    check("the Chart rows say when each reading was made and what it counted",
          rows[0]["state"].startswith("read") and rows[0]["when"] == "1 written, 0 in progress, 1 not started" and rows[0]["controls"] == [])
    check("the owner's view of the watch and of a reading", "1 reading(s) kept" in f.adapter.summarize(await store.get(OWNER, watch.id), await store.records(OWNER, NAMESPACE.name))
          and "2 questions" in f.adapter.summarize(await store.get(OWNER, children[2].id), await store.records(OWNER, NAMESPACE.name)))
    paused = await store.mandate_control(OWNER, mandate.id, "pause", revision=None)
    await f.adapter.mandate_changed(store, OWNER, paused)
    check("a paused mandate pauses the watch", (await store.get(OWNER, watch.id)).status == "paused")
    resumed = await store.mandate_control(OWNER, mandate.id, "resume", revision=None)
    await f.adapter.mandate_changed(store, OWNER, resumed)
    check("a resumed mandate resumes it", (await store.get(OWNER, watch.id)).status == "queued")
    revoked_grant = await store.revoke_grant(OWNER, grant.id, revision=None)
    for m in await store.mandates(OWNER):
        await f.adapter.mandate_changed(store, OWNER, m)
    check("a revoked grant ends the watch, and the Mac watches nothing",
          revoked_grant.status == "revoked" and (await store.get(OWNER, watch.id)).status == "cancelled" and f.watched[-1] == [])
    await store.close()

    print("\nwithout a workbench")
    g = Fixture(root / "nobench", bench=None, clock=base)
    g.projects.write("analysis", "s", description="H104")
    g.projects.bind("analysis", "solution", str(solution), key="hw03")
    from ciel.project_work import reading_request
    from ciel.tasks import Origin
    store = await g.open()
    spec, step = reading_request(g.projects.get("analysis").id, "hw03", "abcd", str(solution))
    task = await store.create(Origin(OWNER, "read-1", "voice"), spec, step, now=base)
    results = await g.run(task.id)
    waiting = await store.get(OWNER, task.id)
    check("a reading with no machine to read on waits on the resource and says so",
          results == ["refused"] and waiting.status == "waiting" and waiting.wait_reason == "resource" and "not reachable" in waiting.detail)
    await store.close()


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ciel-project-watch-") as tmp:
        watcher_checks(Path(tmp))
        await adapter_checks(Path(tmp))
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
