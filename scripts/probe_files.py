"""Probe the file boundary as the search tools see it.

The workspace guard is a PreToolUse hook: it judges the path a tool names.
A search names a root and then opens files the guard never saw, which is
why ``search_files`` and ``find_files`` exist — they run every directory
and file through the same guard. This drives them over a fixture tree
with a fake secret, a forbidden subtree, and a symlink out, and checks the
built-in walkers are refused whatever they are given. The signup cookie,
including a configured filename, is refused by direct reads and searches.

    PYTHONPATH=src .venv/bin/python scripts/probe_files.py
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import replace

from ciel.brain.permissions import WorkspaceGuard
from ciel.brain.tools import files
from ciel.brain.shellguard import ShellGuard
from ciel.config import Config, FilesConfig
from ciel.brain.permissions import forbidden_names

FAILED = 0
CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    global FAILED
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    FAILED += not ok


async def run(tool, **args) -> str:
    return (await tool.handler(args))["content"][0]["text"]


async def main() -> None:
    print("the sieve")
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        (ws / "sub" / ".ssh").mkdir(parents=True)
        (ws / "notes.md").write_text("fixture_needle here\nplain line\n")
        (ws / "credentials.json").write_text('{"fixture_needle": "FAKE"}')
        (ws / "sub" / ".ssh" / "id_rsa").write_text("fixture_needle key")
        (ws / "sub" / "deep.py").write_text("x = 'fixture_needle'\n" * 3)
        outside = Path(tmp) / "outside.txt"
        outside.write_text("fixture_needle outside\n")
        os.symlink(outside, ws / "link.txt")
        os.symlink(Path(tmp), ws / "up")

        guard = WorkspaceGuard(ws)
        files.bind_files(guard)
        hits = await run(files.search_files, pattern="fixture_needle")
        check("a recursive search finds the ordinary files",
              "notes.md:1:" in hits and "sub/deep.py:1:" in hits)
        check("...and never the forbidden name, the forbidden subtree, or the symlink out",
              "credentials" not in hits and "id_rsa" not in hits and "link.txt" not in hits
              and "outside" not in hits)
        check("the glob filter narrows it", (await run(files.search_files, pattern="fixture_needle", glob="*.py")).count("\n") == 2)
        check("a bad pattern is a message, not a crash", "does not compile" in await run(files.search_files, pattern="("))
        check("no match says so", await run(files.search_files, pattern="zzz-nothing") == "No matches.")

        found = await run(files.find_files, glob="*")
        check("find lists the same set", found.split("\n") == ["notes.md", "sub/deep.py"])
        check("a path glob matches on the relative path",
              await run(files.find_files, glob="sub/*.py") == "sub/deep.py")

        check("a search rooted at a forbidden file is refused",
              "off limits" in await run(files.search_files, pattern="x", path="credentials.json"))
        check("a search rooted outside is refused",
              "outside it" in await run(files.search_files, pattern="x", path="/etc"))
        check("...also by traversal", "outside it" in await run(files.search_files, pattern="x", path="../"))
        check("...also through the symlinked directory",
              "outside it" in await run(files.find_files, glob="*", path="up"))

        for tool in ("Grep", "Glob"):
            for tool_input in ({"pattern": "x"}, {"pattern": "x", "path": str(ws)}):
                verdict = await guard({"tool_name": tool, "tool_input": tool_input}, None, None)
                check(f"the built-in {tool} is refused with {tool_input}",
                      verdict.get("hookSpecificOutput", {}).get("permissionDecision") == "deny")
        check("Read still passes inside", await guard({"tool_name": "Read", "tool_input": {"file_path": str(ws / "notes.md")}}, None, None) == {})

        files.bind_files(WorkspaceGuard(ws, read_only_outside=True))
        hits = await run(files.search_files, pattern="fixture_needle")
        check("read-only-outside lets the symlink out be read, and still not the secrets",
              "link.txt:1:" in hits and "credentials" not in hits and "id_rsa" not in hits)

        # A pattern that backtracks forever is killed at the deadline, and
        # the loop it would have held keeps turning meanwhile.
        (ws / "almost.txt").write_text("a" * 29 + "!\n")
        files._DEADLINE_S = 1.0
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        started = time.monotonic()
        out = await run(files.search_files, pattern="^(a+)+$")
        took = time.monotonic() - started
        beat.cancel()
        check("a catastrophic pattern is stopped at the deadline, not waited on",
              "took longer than 1s" in out and took < 4.0)
        check("...and the event loop kept turning meanwhile", ticks >= 10)
        check("...and no child is left behind", multiprocessing.active_children() == [])
        files._DEADLINE_S = 20.0
        cookie = ws / "sections-cookie"
        alternate = ws / "custom-session"
        cookie.write_text("fixture_session")
        alternate.write_text("fixture_session")
        cfg = replace(Config(), files=FilesConfig(enabled=True, workspace=ws, read_only_outside=True))
        cfg = replace(cfg, sections=replace(cfg.sections, cookie_file=alternate))
        guard = WorkspaceGuard.from_config(cfg)
        files.bind_files(guard)
        check("the signup cookie is refused even inside the workspace", guard.permits(str(cookie)) is not None)
        check("a configured cookie name is refused too", guard.permits(str(alternate)) is not None)
        check("search cannot expose either cookie", await run(files.search_files, pattern="fixture_session") == "No matches.")
        check("find cannot list either cookie", "sections-cookie" not in await run(files.find_files, glob="*")
              and "custom-session" not in await run(files.find_files, glob="*"))
        questions: list[str] = []

        async def approve(question: str) -> bool:
            questions.append(question)
            return True

        shell = ShellGuard(cfg.shell, approve, forbidden=forbidden_names(cfg))
        result = await shell({"tool_name": "Bash", "tool_input": {"command": f"cat {alternate}"}}, None, None)
        check("the configured cookie is denied by the shell without asking", not questions
              and result["hookSpecificOutput"]["permissionDecision"] == "deny")
        check("an ordinary search still answers", "almost.txt:1:" in await run(files.search_files, pattern="a{29}!"))

    print(f"\nall {len(CHECKS)} checks passed" if not FAILED else f"\n{FAILED} FAILED")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
