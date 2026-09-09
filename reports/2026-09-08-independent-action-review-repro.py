"""Static evidence for the 2026-09-08 independent-action plan review.

**This is a source inspection, not a runtime probe.** It reproduces the
human-only origin validation, missing task source, existing isolated SDK call,
yes/no broker boundary, and process-home token defaults that motivated the
revisions. It also checks the two plans' local links. No Ciel import, account
access, model call, or runtime-state read is performed.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        raise SystemExit(1)


def source(path: str) -> str:
    return (ROOT / path).read_text()


def node_text(path: str, class_name: str, method: str | None = None) -> str:
    text = source(path)
    tree = ast.parse(text)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = cls if method is None else next(
        item for item in cls.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method
    )
    return ast.get_source_segment(text, node) or ""


def main() -> None:
    validation = node_text("src/ciel/tasks.py", "TaskStore", "_validate_request")
    check("ordinary task creation still requires a private attended human lane",
          "origin.attended is not True" in validation and "origin.private is not True" in validation
          and "('voice','typed','web','discord')" in validation)
    origin = node_text("src/ciel/tasks.py", "Origin")
    check("the existing origin has no derived-parent field", "parent_mandate_id" not in origin)
    ask = node_text("src/ciel/confirm.py", "VoiceConfirmBroker", "ask")
    check("the broker receives a formed question and returns a boolean", "question: str" in ask and "-> bool" in ask)
    sdk_options = node_text("src/ciel/interview/brain.py", "AgentSdkBackend", "_options")
    isolated = node_text("src/ciel/interview/brain.py", "AgentSdkBackend", "ask_json")
    check("the interview supplies a fresh structured-call construction pattern",
          "ClaudeSDKClient(" in isolated and "tools=[]" in sdk_options and "setting_sources=[]" in sdk_options)
    check("the ordinary pipeline adds world context", "_world_block(public=req.public)" in source("src/ciel/pipeline.py"))
    check("the existing ladder has no task source", "TASK" not in node_text("src/ciel/schedule.py", "Source"))
    config = source("src/ciel/config.py")
    check("connector defaults are process-home paths",
          'Path.home() / ".gmail-mcp"' in config and '".config" / "google-calendar-mcp" / "tokens.json"' in config)
    check("core task schema does not yet contain a feature namespace facility",
          "CREATE TABLE feature_namespaces" not in source("src/ciel/tasks.py"))
    for name in ("independent-action", "email-calendar"):
        plan = ROOT / f"design/2026-09-08-{name}-plan.md"
        local = [link for link in re.findall(r"\]\(([^)]+)\)", plan.read_text())
                 if not link.startswith(("https://", "http://"))]
        check(f"the {name} plan's local references resolve",
              all((plan.parent / link.split("#")[0]).exists() for link in local))
    print(f"\nall {len(CHECKS)} static checks passed")


if __name__ == "__main__":
    main()
