"""Evidence for the third review of the independent-action plans.

**Review only.** Source clauses establish the remaining derivation and
non-terminal revision ambiguity. A real temporary TaskStore confirms that
resource-wait status does not exempt a step from scope validation. No provider,
model, credentials, or user runtime state are accessed.
"""
from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from pathlib import Path
import tempfile

from ciel.config import MailConfig, SectionsConfig, TasksConfig
from ciel.tasks import Criterion, Origin, Scope, Specification, Step, TaskConflict, TaskStore

ROOT = Path(__file__).resolve().parents[1]
CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


async def main() -> None:
    foundation = (ROOT / "design/2026-09-08-independent-action-plan.md").read_text()
    inbox = (ROOT / "design/2026-09-08-email-calendar-plan.md").read_text()
    check("the revised plan keeps completed action tasks final",
          "A terminal child is never reopened." in foundation)
    check("preview is now a finite human task and derives no children",
          "### Preview needs no grant" in foundation and "and derives no children" in foundation)
    check("credential ownership now matches the existing dataclasses",
          "SectionsConfig.gmail_oauth_keys" in inbox
          and {"gmail_oauth_keys", "gmail_token_file"} <= {f.name for f in fields(SectionsConfig)}
          and "gmail_oauth_keys" not in {f.name for f in fields(MailConfig)})
    check("derive_task still requires the child scope to narrow the parent",
          "authority and budgets still permit the scope, and the child narrows it" in foundation)
    check("the same plan also creates an out-of-grant child in a waiting state",
          "If that operation is outside the parent's grant" in foundation
          and "the child is created waiting" in foundation)
    check("coalescing is specified for all non-terminal children",
          "While a child is non-terminal, a new source\nrevision revises it in place" in foundation)
    check("operation keys include the source revision being changed",
          "source revision, and the operation" in foundation)
    with tempfile.TemporaryDirectory(prefix="ciel-plan-third-review-") as temporary:
        cfg = replace(TasksConfig(), directory=Path(temporary) / "tasks")
        target = "synthetic:event"
        spec = Specification("Create the event", Scope(("inspect", "create"), (target,)),
                             (Criterion("exists", target, "yes", "source-v1"),))
        async with TaskStore(cfg) as store:
            for operation in ("update", "delete"):
                try:
                    await store.create(Origin("owner", f"request-{operation}", "web"), spec,
                                       Step("mutation", operation, target), resource_wait=True, now=100)
                except TaskConflict as exc:
                    check(f"a resource wait does not admit {operation} outside the task's scope", True)
                    print(f"      {exc}")
                else:
                    check(f"a resource wait does not admit {operation} outside the task's scope", False)
    print(f"\nall {len(CHECKS)} review checks reproduced")


if __name__ == "__main__":
    asyncio.run(main())
