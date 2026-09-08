"""Reproduce four review findings with synthetic files and no external services.

The quiet shell writes outside the file workspace, the signup credential's
filename passes both guards, a canceled shell still writes afterward, and a
Mac overwrite has no undo snapshot. Assertions pin the observed defects;
a corrected implementation should make the corresponding assertion fail.
All writes and child processes belong to one temporary directory. No user
configuration, tokens, recordings, models, or live services are loaded.
"""
from __future__ import annotations

import asyncio
import json
import shlex
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ciel.brain.permissions import WorkspaceGuard
from ciel.brain.recorder import ActionRecorder
from ciel.brain.shellguard import ShellGuard, classify
from ciel.config import Config, FilesConfig, JournalConfig, ShellConfig
from ciel.journal import ActionJournal
from ciel.spoke.executor import Executor


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-review-") as folder:
        root = Path(folder)
        workspace = root / "workspace"
        workspace.mkdir()
        cfg = replace(
            Config(), state_dir=root / "state",
            files=FilesConfig(enabled=True, workspace=workspace),
            shell=ShellConfig(enabled=True, command_timeout_s=3.0),
        )
        frames: list[dict[str, Any]] = []
        executor = Executor(cfg, lambda frame: frames.append(frame) or True)
        questions: list[str] = []

        async def decline(question: str) -> bool:
            questions.append(question)
            return False

        left, right = workspace / "left.txt", workspace / "right.txt"
        left.write_text("before\n")
        right.write_text("after\n")
        output = root / "outside-workspace.patch"
        command = "git diff --no-index --output=" + shlex.quote(str(output)) + " " + shlex.quote(str(left)) + " " + shlex.quote(str(right))
        assert WorkspaceGuard(workspace).permits(str(output), write=True) is not None
        assert classify(command, cfg.shell)[0] == "quiet"
        decision = await ShellGuard(cfg.shell, decline)({"tool_name": "Bash", "tool_input": {"command": command}}, "quiet-write", None)
        assert decision == {} and not questions
        result = await executor._shell_run(command)
        assert result["exit"] == 1 and output.exists() and "+after" in output.read_text()
        print("REPRODUCED: git diff --output writes outside the workspace with zero confirmation questions")

        cookie = workspace / ".ciel" / "sections-cookie"
        cookie.parent.mkdir()
        cookie.write_text("session=SYNTHETIC-REVIEW-ONLY")
        cookie.chmod(0o600)
        assert WorkspaceGuard(workspace).permits(str(cookie)) is None
        assert classify("cat " + shlex.quote(str(cookie)), cfg.shell)[0] == "quiet"
        assert await executor._files_read(str(cookie)) == "session=SYNTHETIC-REVIEW-ONLY"
        print("REPRODUCED: owner-only sections-cookie is readable through the file guard and quiet shell")

        ready, late = root / "ready", root / "after-cancel"
        worker = root / "worker.py"
        worker.write_text(
            "from pathlib import Path\nimport time\n"
            f"Path({str(ready)!r}).write_text('ready')\n"
            "time.sleep(0.4)\n"
            f"Path({str(late)!r}).write_text('ran after cancel')\n"
        )
        executor.handle({"type": "tool.request", "rpc_id": "cancel-test", "tool": "shell.run", "args": {"command": shlex.quote(sys.executable) + " " + shlex.quote(str(worker)), "confirmed": True}, "timeout_s": 3.0})
        task = executor._tasks["cancel-test"]
        for _ in range(200):
            if ready.exists():
                break
            await asyncio.sleep(0.01)
        assert ready.exists() and not late.exists()
        executor.handle({"type": "tool.cancel", "rpc_id": "cancel-test"})
        await asyncio.gather(task, return_exceptions=True)
        assert "cancel-test" not in executor._tasks
        await asyncio.sleep(0.65)
        assert late.exists() and not any(f.get("rpc_id") == "cancel-test" for f in frames)
        print("REPRODUCED: tool.cancel clears the task but the shell writes a file afterward")

        journal = ActionJournal(JournalConfig(dir=root / "journal"))
        recorder = ActionRecorder(journal, frozenset({"mcp__ciel__mac_write_file"}))
        target = workspace / "note.txt"
        target.write_text("irreplaceable original")
        payload = {"tool_name": "mcp__ciel__mac_write_file", "tool_input": {"path": str(target), "content": "replacement"}}
        await recorder.before(payload, "remote-overwrite", None)
        response = await executor._files_write(str(target), "replacement")
        await recorder.after({**payload, "tool_response": {"content": [{"type": "text", "text": response}]}}, "remote-overwrite", None)
        entry = journal.recent(1)[0]
        assert target.read_text() == "replacement" and entry["snapshot"] is None and entry["note"] is None
        assert not list((root / "journal" / "snapshots").iterdir())
        print("REPRODUCED: Mac overwrite is journaled with no snapshot and no explanation")
        await executor.close()
    print("All four findings reproduced; temporary fixtures removed.")


if __name__ == "__main__":
    asyncio.run(main())
