"""The resource watcher: a bound document changed on the Mac.

The project-awareness plan's observation half, on the machine the work is
on. The hub names the files it wants watched — the documents bound to
projects under a standing mandate, and the files their readings followed —
and this watcher polls them the way the work watcher polls its files:
a stat every few seconds, a hash only when the stat moved, and one event
when a change has *settled*, meaning the hash read the same on two
consecutive polls. A burst of saves is one event; an unchanged save is
none; a file that disappears is one event saying so.

**Nothing here decides anything.** The event names the path and the
content hash and nothing more; what it means, whether it is read, and
under whose mandate, is the hub's. The watcher never reads a file it was
not told to, never publishes content, and never opens anything.

**A restart catches up.** The watched set and the last settled hash per
file persist beside the timers' mirror, so the spoke's constant re-exec
and the laptop's sleep lose nothing: the first poll after a start compares
the files to the hashes it last reported and publishes what moved while
it was away. The hub dedupes by path and hash, so a resend is free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ciel.proactive.events import ProactiveEvent
from ciel.proactive.watchers import poll_loop

log = logging.getLogger(__name__)

SOURCE = "resource"
"""The event source the hub routes to the project adapter, never to Vigil."""

_MAX_HASH_BYTES = 8 * 1024 * 1024
"""A document larger than this is watched by size and time alone."""


@dataclass(frozen=True, slots=True)
class Seen:
    digest: str
    """The settled content hash, or '' for a file that is not there."""
    size: int
    mtime: float


class ResourceWatcher:
    """Polls the paths the hub named and publishes settled changes."""

    def __init__(self, path: Path, queue: Any, *, poll_s: float = 15.0, max_bytes: int = _MAX_HASH_BYTES) -> None:
        self._path = path
        self._queue = queue
        self._poll_s = poll_s
        self._max_bytes = max_bytes
        self._watched: dict[str, Seen] = {}
        """Path → what was last reported (or first seen) for it."""
        self._pending: dict[str, Seen] = {}
        """Path → a reading that differs from the reported one, awaiting a second poll that agrees."""
        self._kick = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._load()

    # ── the hub's side ───────────────────────────────────────────────────────

    def set_paths(self, paths: list[str]) -> int:
        """Replace the watched set. New paths are taken at their current
        state without an event — the hub asked to be told about *changes*
        — and dropped paths are forgotten. Returns how many are watched."""
        wanted = {self._normal(p) for p in paths if p}
        for gone in [p for p in self._watched if p not in wanted]:
            self._watched.pop(gone, None)
            self._pending.pop(gone, None)
        for path in wanted:
            if path not in self._watched:
                self._watched[path] = self._probe_one(path)
        self._save()
        self.kick()
        return len(self._watched)

    def watched(self) -> list[str]:
        return sorted(self._watched)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def kick(self) -> None:
        self._kick.set()

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        async def tick() -> None:
            if self._watched:
                snapshot = sorted(self._watched)
                readings = await asyncio.to_thread(lambda: {p: self._probe_one(p) for p in snapshot})
                self._settle(readings, time.time())

        await poll_loop(self._kick, self._poll_s, tick)

    def check(self, now: float) -> int:
        """One synchronous poll — the probes' entry. Returns events published."""
        return self._settle({p: self._probe_one(p) for p in sorted(self._watched)}, now)

    # ── the poll ─────────────────────────────────────────────────────────────

    def _probe_one(self, path: str) -> Seen:
        try:
            stat = os.stat(path)
        except OSError:
            return Seen("", 0, 0.0)
        if not os.path.isfile(path):
            return Seen("", 0, 0.0)
        return Seen(self._digest(path, stat.st_size), stat.st_size, stat.st_mtime)

    def _digest(self, path: str, size: int) -> str:
        if size > self._max_bytes:
            return f"size:{size}"
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            return ""
        return digest.hexdigest()[:16]

    def _settle(self, readings: dict[str, Seen], now: float) -> int:
        """A reading that differs from what was reported waits one poll; a
        second reading that agrees with it is a settled change, reported
        once. A reading that differs from the pending one restarts the wait
        — the save is still going on."""
        published = 0
        for path, seen in readings.items():
            reported = self._watched.get(path)
            if reported is None:
                continue
            if seen.digest == reported.digest:
                self._pending.pop(path, None)
                continue
            pending = self._pending.get(path)
            if pending is None or pending.digest != seen.digest:
                self._pending[path] = seen
                continue
            self._pending.pop(path, None)
            self._watched[path] = seen
            name = os.path.basename(path)
            event = ProactiveEvent(
                id=self._queue.next_id(), source=SOURCE, importance=1, created_at=now, expires_at=None,
                summary=f"{name} {'changed' if seen.digest else 'is gone'}",
                dedupe_key=f"{SOURCE}:{path}:{seen.digest or 'gone'}",
                payload={"path": path, "digest": seen.digest, "size": str(seen.size), "mtime": f"{seen.mtime:.3f}"},
            )
            if self._queue.push(event):
                published += 1
        if published:
            self._save()
        return published

    # ── persistence ──────────────────────────────────────────────────────────

    @staticmethod
    def _normal(raw: str) -> str:
        return str(Path(raw).expanduser())

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
            self._watched = {str(p): Seen(str(s.get("digest", "")), int(s.get("size", 0)), float(s.get("mtime", 0.0)))
                             for p, s in dict(raw.get("watched", {})).items()}
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
            log.warning("could not read %s — starting with nothing watched", self._path)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"watched": {p: {"digest": s.digest, "size": s.size, "mtime": s.mtime} for p, s in self._watched.items()}}, indent=2))
            tmp.replace(self._path)
        except OSError:
            log.warning("could not save %s", self._path, exc_info=True)


__all__ = ["ResourceWatcher", "SOURCE", "Seen"]
