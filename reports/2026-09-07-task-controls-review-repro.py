"""Reproductions for the stage-two task-controls review (2026-09-07).

Each section builds its own temporary store or journal; nothing under
``~/.ciel`` is read or written. Run with ``uv run --no-sync python
reports/2026-09-07-task-controls-review-repro.py``. Every section prints
what it observed; a section that shows the defect exits non-zero at the end.

1. The Chart's task list is the ``max_active`` most recently touched
   records, so recently cancelled tasks push the one live task off the list.
2. ``ActionJournal.record`` is load-append-rewrite with no lock, and the task
   controller now calls it from the store's worker thread while the recorder
   hook calls it from the loop thread; concurrent records lose entries.
3. Two spoken requests for the same watch are two mandates: identity is the
   utterance, not the specification, so the same PR watch can be saved twice.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from pathlib import Path

from ciel.config import JournalConfig, TasksConfig
from ciel.journal import ActionJournal
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.turn import owner_origin

FAILED: list[str] = []


def note(name: str, defect: bool, detail: str) -> None:
    print(f"  {'DEFECT' if defect else 'ok    '} {name}: {detail}")
    if defect:
        FAILED.append(name)


async def list_crowd_out(root: Path) -> None:
    cfg = TasksConfig(enabled=True, directory=root / "tasks", max_active=2)
    controller = TaskController(cfg)
    await controller.start()
    assert controller.store is not None
    try:
        def binding(identity: str) -> TaskBinding:
            return TaskBinding(owner_origin(cfg.owner, "web", identity, namespace="chart"), 1, 1)
        live = (await controller.apply(binding("live"), "create", {"repository": "o/r", "pr": 1, "checks": ["build"]}))["task"]
        for n, name in enumerate(("second", "third")):
            made = (await controller.apply(binding(name), "create", {"repository": "o/r", "pr": 2 + n, "checks": ["build"]}))["task"]
            await controller.apply(binding(name + "-cancel"), "cancel", {"task_id": made["id"]}, revision=made["revision"])
        view = await controller.view(binding("list"))
        listed = [t["id"] for t in view["tasks"]]
        statuses = [t["status"] for t in view["tasks"]]
        note("the one live task is listed", live["id"] not in listed,
             f"list holds {statuses}; live task {'missing' if live['id'] not in listed else 'present'}")
    finally:
        await controller.close()


def journal_race(root: Path) -> None:
    logging.getLogger("ciel.journal").setLevel(logging.CRITICAL)  # the lost-write warnings are the finding; count them instead
    journal = ActionJournal(JournalConfig(dir=root / "journal", max_entries=10_000))
    rounds = 200
    def writer(label: str) -> None:
        for n in range(rounds):
            journal.record(tool=f"{label}_{n}", args={})
    threads = [threading.Thread(target=writer, args=(name,)) for name in ("loop", "worker")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    kept = len(journal.recent(10_000))
    note("concurrent journal records all survive", kept < 2 * rounds,
         f"{kept} of {2 * rounds} entries kept after two threads recorded at once "
         "(the rest raced on one shared .tmp name and were dropped with a warning)")


async def duplicate_watch(root: Path) -> None:
    cfg = TasksConfig(enabled=True, directory=root / "tasks-dup")
    controller = TaskController(cfg)
    await controller.start()
    try:
        args = {"repository": "o/r", "pr": 7, "checks": ["build"]}
        first = (await controller.apply(TaskBinding(owner_origin(cfg.owner, "voice"), 1, 1), "create", args))["task"]
        second = (await controller.apply(TaskBinding(owner_origin(cfg.owner, "voice"), 1, 2), "create", args))["task"]
        note("a second spoken request for the same watch resolves to the saved one", first["id"] != second["id"],
             "two distinct tasks" if first["id"] != second["id"] else "one task")
    finally:
        await controller.close()


async def main(root: Path) -> None:
    print("1. list crowd-out")
    await list_crowd_out(root)
    print("2. journal race")
    journal_race(root)
    print("3. duplicate watch from two utterances (design note, not counted)")
    await duplicate_watch(root)
    FAILED[:] = [f for f in FAILED if "spoken" not in f]
    print("\n" + ("defects reproduced: " + ", ".join(FAILED) if FAILED else "no defects reproduced"))
    raise SystemExit(1 if FAILED else 0)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ciel-task-controls-review-") as tmp:
        asyncio.run(main(Path(tmp)))
