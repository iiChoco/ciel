"""A thought can enter Invariant without first becoming a conversation.

**The owner presses Save.** The note is stored verbatim as a reference, with
note provenance, rather than asking a model to interpret or act on it. Its
identity belongs to the draft: retries after a lost receipt cannot create a
second memory, and two ideas with the same opening cannot overwrite each other.

**A draft is not a receipt.** The Mac keeps an owner-only scratchpad across
Escape and process restarts. Only the memory writer's positive receipt clears
it. Split mode writes on the hub; local mode uses the same writer directly.
No note contents enter logs, conversation history, or a public broadcast.

**AppKit owns a separate process.** JSON lines carry show, save, and receipt
messages. A broken window cannot take the microphone down with it. The native
view lives in ``ui/notes.py``; this module can be probed without a display.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel.config import NotesConfig
from ciel.memory.store import Memory, MemoryStore, atomic_write, exclusive_lock

log = logging.getLogger(__name__)
_ID = re.compile(r"[0-9a-f]{32}\Z")


def save_note(store: MemoryStore | None, config: NotesConfig, note_id: str, text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "note.result", "note_id": note_id, "ok": False}
    if not config.enabled or store is None:
        return {**result, "error": "Memory capture is disabled on the brain."}
    if not _ID.fullmatch(note_id) or not text.strip() or len(text) > config.max_chars:
        return {**result, "error": "Use a nonempty note within the configured length limit."}
    path = store.directory / f"note-{note_id}.md"
    try:
        with exclusive_lock(path):
            existing = store.get(path.stem)
            if existing is not None:
                if existing.context != "note" or existing.content != text.strip():
                    return {**result, "error": "This note already has a different saved version; reopen and edit to save a new note."}
            else:
                first = " ".join(text.split())[:120]
                memory = Memory(path.stem, f"Note: {first}", "reference", text,
                                time.time(), path, context="note")
                atomic_write(path, memory.to_markdown(), mode=0o600)
            store._write_human_index()
        return {**result, "ok": True}
    except OSError:
        log.warning("note storage failed; the draft remains on the Mac")
        return {**result, "error": "Memory could not be written. Your draft is still here; try again."}


@dataclass
class Draft:
    path: Path
    text: str = ""
    note_id: str = ""

    @classmethod
    def load(cls, path: Path) -> Draft:
        draft = cls(path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not _ID.fullmatch(data.get("note_id", "")):
                raise ValueError("The saved draft could not be read.")
            draft.text, draft.note_id = data["text"], data["note_id"]
        return draft

    def update(self, text: str) -> None:
        note_id = uuid.uuid4().hex if text != self.text or not self.note_id else self.note_id
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(self.path, json.dumps({"text": text, "note_id": note_id}, ensure_ascii=False), mode=0o600)
        self.text, self.note_id = text, note_id

    def receipt(self, result: dict[str, Any]) -> bool:
        if result.get("note_id") != self.note_id or result.get("ok") is not True:
            return False
        self.path.unlink(missing_ok=True)
        self.text, self.note_id = "", ""
        return True


class NoteRelay:
    """One visible draft in flight; timeouts preserve its id for a safe retry."""

    def __init__(self, send: Callable[[dict[str, Any]], bool], timeout: float) -> None:
        self._send = send
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def save(self, note_id: str, text: str) -> dict[str, Any]:
        failure = {"type": "note.result", "note_id": note_id, "ok": False}
        if self._pending:
            return {**failure, "error": "A note is already saving."}
        future = asyncio.get_running_loop().create_future()
        self._pending[note_id] = future
        try:
            if not self._send({"type": "note.save", "note_id": note_id, "text": text}):
                return {**failure, "error": "The brain is offline. Your draft is here; retry when connected."}
            try:
                return await asyncio.wait_for(future, self._timeout)
            except asyncio.TimeoutError:
                return {**failure, "error": "No save receipt from the brain. Your draft is here; retry when connected."}
        finally:
            self._pending.pop(note_id, None)

    def receive(self, frame: dict[str, Any]) -> None:
        future = self._pending.get(frame.get("note_id"))
        if future is not None and not future.done():
            future.set_result(frame)


class NoteWindow:
    def __init__(self, config: NotesConfig, save: Callable[[str, str], Awaitable[dict[str, Any]]]) -> None:
        self._config = config
        self._save = save
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None

    async def show(self) -> None:
        if not self._config.enabled or sys.platform != "darwin":
            return
        if self._proc is None or self._proc.returncode is not None:
            if self._reader is not None:
                self._reader.cancel()
                await asyncio.gather(self._reader, return_exceptions=True)
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "ciel.ui.notes",
                json.dumps({"dir": str(self._config.dir), "max_chars": self._config.max_chars}),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=max(65536, self._config.max_chars * 12),
            )
            self._reader = asyncio.create_task(self._read(self._proc))
        self._write({"type": "show"})

    def _write(self, frame: dict[str, Any]) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None and proc.stdin is not None:
            try:
                proc.stdin.write((json.dumps(frame) + "\n").encode())
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                log.warning("note window closed; its draft will reopen next time")

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while line := await proc.stdout.readline():
            try:
                frame = json.loads(line)
                if frame.get("type") != "note.save":
                    continue
                result = await self._save(frame["note_id"], frame["text"])
                self._write(result)
            except (ValueError, KeyError):
                log.warning("invalid note window message")
            except Exception:
                self._write({"type": "note.result", "note_id": frame.get("note_id", ""), "ok": False,
                             "error": "Saving failed. Your draft is still here; try again."})

    async def close(self) -> None:
        self._write({"type": "quit"})
        proc, self._proc = self._proc, None
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
