"""The recent-actions tool — the model's read side of the action journal.

Undo has to start from ground truth. The model's memory of what it did is a
paraphrase — it remembers "I fixed the config", not the byte-exact previous
contents or the eventId the calendar returned — and after a restart or session
resume it may remember nothing at all. This tool merges the file/action journal and nutrition's transactional
history by time. Owner approval on 2026-09-13 made nutrition history a durable
Inverse source. Source-qualified identities keep file snapshots and validated
nutrition inverses distinct; a failed source is reported as partial history.
"""

from __future__ import annotations

import logging
import hashlib
import json
import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from claude_agent_sdk import tool

from ciel.journal import ActionJournal

log = logging.getLogger(__name__)

# Bound at startup, same pattern as the memory store: the SDK's @tool
# decorator wants plain module-level functions.
_journal: ActionJournal | None = None
_nutrition_reader: Callable[[int], Awaitable[list[dict[str, Any]]]] | None = None


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def _format(entry: dict[str, Any]) -> str:
    lines = [f"{entry.get('when', '?')} — {entry.get('tool', '?')} ({entry.get('id', '?')})"]
    if entry.get("undo_tool"):
        lines.append(f"  undo through: {entry['undo_tool']} (never restore database files)")
    args = entry.get("args") or {}
    if args:
        rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
        lines.append(f"  args: {rendered}")
    if entry.get("snapshot"):
        lines.append(f"  previous contents saved at: {entry['snapshot']}")
    if entry.get("note"):
        lines.append(f"  note: {entry['note']}")
    if entry.get("response"):
        lines.append(f"  result: {entry['response']}")
    return "\n".join(lines)


@tool(
    "recent_actions",
    (
        "List the actions you recently performed — file writes, shell "
        "commands, emails, calendar changes, nutrition — newest first, with their "
        "arguments, results, and (for file edits) the path of a snapshot "
        "holding the file's previous contents.\n\n"
        "Call this FIRST whenever the user says something you did was wrong "
        "or asks you to undo something. Undo from this record, never from "
        "recollection: restore a file by reading its snapshot and writing "
        "those contents back; delete a calendar event you created using the "
        "id in its recorded result. If the record shows the action cannot be "
        "reversed — a sent email is sent — say so plainly. Nutrition history names nutrition_undo and its operation_id; never restore a database file."
    ),
    {"count": int},
)
async def recent_actions(args: dict[str, Any]) -> dict[str, Any]:
    try:
        count = max(1, min(int(args.get("count") or 10), 50))
    except (TypeError, ValueError, OverflowError):
        count = 10
    entries: list[dict[str, Any]] = []
    failures = []
    if _journal is not None:
        try:
            for entry in await asyncio.to_thread(_journal.recent, count):
                identity = entry.get("ref") or hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()
                entries.append({**entry, "id": "journal:" + str(identity), "at": entry.get("ts")})
        except Exception:
            failures.append("The file/action journal is unavailable.")
    if _nutrition_reader is not None:
        try:
            entries.extend(await _nutrition_reader(count))
        except Exception:
            failures.append("Nutrition history is unavailable for this session.")
    if _journal is None and _nutrition_reader is None:
        return _text("Action history is not available right now.")
    def ordered(entry: dict[str, Any]) -> tuple[float, str]:
        try:
            at = float(entry["at"]) if entry.get("at") is not None else datetime.fromisoformat(entry["when"]).timestamp()
        except (ValueError, TypeError, KeyError):
            at = 0.0
        return at, str(entry.get("id", ""))
    entries.sort(key=ordered, reverse=True)
    message = "\n\n".join(_format(e) for e in entries[:count])
    if failures:
        message = "Partial action history. " + " ".join(failures) + ("\n\n" + message if message else "")
    return _text(message or "No actions recorded yet.")


def bind_journal(journal: ActionJournal | None) -> None:
    """Attach the journal this tool reads."""
    global _journal
    _journal = journal


def bind_nutrition_history(reader: Callable[[int], Awaitable[list[dict[str, Any]]]] | None) -> None:
    """Nutrition has its own atomic history, approved by the owner 2026-09-13."""
    global _nutrition_reader
    _nutrition_reader = reader


ACTION_TOOLS = [recent_actions]

__all__ = ["ACTION_TOOLS", "bind_journal", "bind_nutrition_history", "recent_actions"]
