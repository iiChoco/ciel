"""The revised plans still need preview and event-change lifecycle decisions.

**This review reproduction uses only temporary state.** A synthetic completed
calendar task demonstrates that the existing store cannot resume, reclaim,
retarget, or ask a new question on that same task. Source inspection establishes
the preview/grant circular dependency; dataclass fields establish the incorrect
credential owner named in the feature plan. It does not implement the proposed
runner or model extraction and makes no provider/model calls.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import fields, replace
from pathlib import Path
import tempfile

from ciel.config import MailConfig, SectionsConfig, TasksConfig
from ciel.tasks import Criterion, Evidence, Origin, Scope, Specification, Step, TaskConflict, TaskStore

ROOT = Path(__file__).resolve().parents[1]
CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


async def refused(name: str, operation: Awaitable[object]) -> None:
    try:
        await operation
    except TaskConflict as exc:
        check(name, True)
        print(f"      {exc}")
    else:
        check(name, False)


async def lifecycle() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-plan-second-review-") as temporary:
        config = replace(TasksConfig(), directory=Path(temporary) / "tasks")
        owner = "synthetic-owner"
        target = "synthetic-calendar:event-1"
        original = Specification(
            "The appointment is present at two",
            Scope(("inspect", "create"), (target,)),
            (Criterion("start", target, "2026-09-15T14:00:00-07:00", "mail-v1"),),
        )
        read = Step("read", "inspect", target)
        async with TaskStore(config) as store:
            task = await store.create(Origin(owner, "synthetic-request", "web"), original, read, now=100)
            attempt = await store.claim(owner, task.id, task.revision, now=101)
            attempt = await store.mark_dispatched(owner, attempt, now=102)
            observed = await store.observe(owner, attempt, (
                Evidence("start", target, "2026-09-15T14:00:00-07:00", "synthetic-calendar", 103, "mail-v1"),
            ), now=104)
            done = await store.complete(owner, task.id, observed.revision, now=105)
            check("a synthetic observed appointment has a terminal task", done.status == "done")
            await refused("the same completed child cannot ask about a reschedule",
                          store.ask_owner(owner, done.id, done.revision, "Move the appointment to three?", ("yes", "no"), now=106))
            await refused("the same completed child cannot resume",
                          store.resume(owner, done.id, done.revision, now=106))
            await refused("the same completed child cannot be claimed for another revision",
                          store.claim(owner, done.id, done.revision, now=106))
            await refused("retargeting does not reopen a completed child",
                          store.retarget(owner, done.id, done.revision, target, "mail-v2", now=106))
            changed = replace(original, criteria=(
                Criterion("start", target, "2026-09-15T15:00:00-07:00", "mail-v2"),
            ))
            await refused("reusing the original request cannot replace its expected time",
                          store.create(task.origin, changed, read, now=106))
        async with TaskStore(config) as reopened:
            retained = await reopened.get(owner, task.id)
            check("restart preserves completion and the original expected time",
                  retained.status == "done" and retained.specification == original)


def static_contracts() -> None:
    foundation = (ROOT / "design/2026-09-08-independent-action-plan.md").read_text()
    inbox = (ROOT / "design/2026-09-08-email-calendar-plan.md").read_text()
    check("the feature promises preview before write permission",
          "Save preview choices without granting writes" in inbox)
    check("the foundation creates its mandate with the approved grant",
          "Activation commits the approved grant and mandate together" in foundation)
    check("derived children require a matching standing grant",
          "owner and grant\nmatch" in foundation
          and "grant_id/revision" in foundation)
    check("the feature commits derived tasks as part of mail-page progress",
          "A message page commits its record write-set and derived tasks" in inbox)
    mail_fields = {field.name for field in fields(MailConfig)}
    sections_fields = {field.name for field in fields(SectionsConfig)}
    check("MailConfig has neither Gmail credential field named by the plan",
          not {"gmail_oauth_keys", "gmail_token_file"} & mail_fields)
    check("SectionsConfig owns both existing Gmail credential fields",
          {"gmail_oauth_keys", "gmail_token_file"} <= sections_fields)
    check("the revised feature still references MailConfig for Gmail credentials",
          "MailConfig.gmail_oauth_keys" in inbox)


async def main() -> None:
    static_contracts()
    await lifecycle()
    print(f"\nall {len(CHECKS)} review checks reproduced")


if __name__ == "__main__":
    asyncio.run(main())
