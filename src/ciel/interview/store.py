"""Where the room keeps what happened: one directory per session.

    <dir>/users/<username>/sessions/<session_id>/
        meta.json          mode, timestamps, state, cost — the lobby's row
        brief.json         the company, the case, or the technical brief
        transcript.jsonl   {t, speaker, text}, appended as it is said
        code.jsonl         {t, lang, code, event}, technical mode only
        recording.webm     the browser's chunks, appended in order
        debrief.json       the structured scorecard
        debrief.md         the same, readable

Flat files, one thing each, the house style: a friend's interview can be
inspected with ``cat``, handed to them as a folder, or deleted with
``rm -r``. Metadata is written atomically; the record files are
append-only, so a crash mid-interview loses at most the line in flight.

The directory is named by username, but the session belongs to an
*account*: ``meta.json`` carries the ``owner_id`` the account was minted
with, and every read that answers to a request checks it. A username
deleted and recreated is a different account, and the old sessions are
strangers to it — and when the room deletes an account it moves the
directory aside (``users/.retired/<username>.<id>``), so the new person
starts with an empty lobby and a fresh ledger rather than the old one's.
Sessions from before ids existed are claimed once, at startup, by the
account that holds the username then.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ciel.memory.store import atomic_write, exclusive_lock

log = logging.getLogger(__name__)

SESSION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
STATES = ("prepared", "live", "ended", "debriefed")
RETIRED = ".retired"
"""Under ``users/``: where a deleted account's directory goes."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@dataclass(frozen=True)
class Reservation:
    """One unit taken from a daily budget — *which* budget, exactly, so
    that handing it back after midnight credits the day it was taken
    from and not whatever day it is now."""

    username: str
    day: str
    kind: str


class SessionStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    # ── paths ────────────────────────────────────────────────────────────────

    def user_dir(self, username: str) -> Path:
        return self._root / "users" / username / "sessions"

    def path(self, username: str, session_id: str) -> Path:
        if not SESSION_ID.match(session_id):
            raise KeyError(session_id)
        return self.user_dir(username) / session_id

    def exists(self, username: str, session_id: str) -> bool:
        try:
            return (self.path(username, session_id) / "meta.json").is_file()
        except KeyError:
            return False

    # ── create / meta ────────────────────────────────────────────────────────

    def create(
        self, username: str, mode: str, setup: dict[str, Any], brief: dict[str, Any],
        *, owner_id: str = "",
    ) -> dict[str, Any]:
        while True:
            session_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            path = self.user_dir(username) / session_id
            if not path.exists():
                break
        path.mkdir(parents=True)
        meta = {
            "id": session_id,
            "username": username,
            "owner_id": owner_id,
            "mode": mode,
            "title": brief_title(mode, brief),
            "setup": setup,
            "created": _now_iso(),
            "created_ts": time.time(),
            "started": None,
            "ended": None,
            "duration_s": 0,
            "question_count": 0,
            "state": "prepared",
            "cost_usd": 0.0,
            "tts": None,
            "case_slug": brief.get("slug") if mode == "case" else None,
        }
        self.save_meta(username, session_id, meta)
        self.save_brief(username, session_id, brief)
        return meta

    def load_meta(self, username: str, session_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
        """The session's metadata. With ``owner_id`` given, a session that
        belongs to a different account (or to none) is a KeyError, the
        same as one that does not exist: not theirs, not there."""
        path = self.path(username, session_id) / "meta.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(session_id) from None
        if not isinstance(data, dict):
            raise KeyError(session_id)
        if owner_id is not None and not self.owned_by(data, owner_id):
            raise KeyError(session_id)
        return data

    @staticmethod
    def owned_by(meta: dict[str, Any], owner_id: str) -> bool:
        return bool(owner_id) and meta.get("owner_id") == owner_id

    def save_meta(self, username: str, session_id: str, meta: dict[str, Any]) -> None:
        atomic_write(self.path(username, session_id) / "meta.json", json.dumps(meta, indent=2) + "\n")

    def update_meta(self, username: str, session_id: str, **fields: Any) -> dict[str, Any]:
        meta = self.load_meta(username, session_id)
        meta.update(fields)
        self.save_meta(username, session_id, meta)
        return meta

    def list(self, username: str, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        """Every session in the username's directory — only the account's
        own when ``owner_id`` is given."""
        base = self.user_dir(username)
        if not base.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for child in base.iterdir():
            if not SESSION_ID.match(child.name):
                continue
            try:
                meta = json.loads((child / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            if owner_id is not None and not self.owned_by(meta, owner_id):
                continue
            meta["debriefed"] = (child / "debrief.json").is_file()
            meta["has_recording"] = (child / "recording.webm").is_file()
            rows.append(meta)
        # Two sessions in one second tie on the ISO stamp; the float breaks it.
        rows.sort(key=lambda m: (m.get("created", ""), float(m.get("created_ts") or 0)), reverse=True)
        return rows

    def count_since(self, username: str, since_iso: str) -> int:
        return sum(1 for m in self.list(username) if m.get("created", "") >= since_iso)

    # ── ownership ────────────────────────────────────────────────────────────

    def claim_unowned(self, username: str, owner_id: str) -> int:
        """Sessions from before ids existed get the account's: once, for
        the account holding the username at upgrade time. Returns how
        many were claimed."""
        claimed = 0
        for meta in self.list(username):
            if meta.get("owner_id") or not owner_id:
                continue
            session_id = str(meta.get("id") or "")
            try:
                self.update_meta(username, session_id, owner_id=owner_id)
            except (KeyError, OSError):
                log.warning("could not claim %s/%s for %s", username, session_id, owner_id, exc_info=True)
                continue
            claimed += 1
        return claimed

    def retire(self, username: str, account_id: str) -> Path | None:
        """Move a deleted account's directory out of the way, so the next
        holder of the username starts empty: the sessions and the usage
        ledger both. Returns where it went, or None when there was
        nothing to move."""
        source = self._root / "users" / username
        if not source.is_dir():
            return None
        parking = self._root / "users" / RETIRED
        parking.mkdir(parents=True, exist_ok=True)
        stem = f"{username}.{account_id or 'noid'}"
        target = parking / stem
        n = 1
        while target.exists():
            n += 1
            target = parking / f"{stem}.{n}"
        source.rename(target)
        log.info("interview sessions of %s (%s) retired to %s", username, account_id, target)
        return target

    # ── usage ────────────────────────────────────────────────────────────────
    #
    # The daily budgets, kept apart from the session directories they used
    # to be counted from: a slot is reserved *before* the model is called,
    # under a lock, so two requests in the same instant cannot both pass
    # the same check — and a deleted session keeps its charge, so the cap
    # is a cap on model calls, not on what is left on disk afterwards.

    def usage_path(self, username: str) -> Path:
        return self._root / "users" / username / "usage.json"

    def _usage_load(self, path: Path) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def usage(self, username: str, day: str, kind: str) -> int:
        """What ``kind`` (``sessions``, ``regenerations``) has used on ``day``."""
        days = self._usage_load(self.usage_path(username))
        row = days.get(day)
        return int(row.get(kind, 0)) if isinstance(row, dict) else 0

    def reserve(self, username: str, day: str, kind: str, cap: int) -> Reservation | None:
        """Take one unit of ``kind`` for ``day`` if under ``cap``: the
        reservation to hand back, or None (and nothing taken) at the cap.
        Old days are dropped as they go."""
        path = self.usage_path(username)
        with exclusive_lock(path):
            days = self._usage_load(path)
            row = days.get(day) if isinstance(days.get(day), dict) else {}
            used = int(row.get(kind, 0))
            if used >= cap:
                return None
            row[kind] = used + 1
            days = {d: r for d, r in days.items() if d >= day}
            days[day] = row
            atomic_write(path, json.dumps(days, indent=2, sort_keys=True) + "\n")
        return Reservation(username, day, kind)

    def release(self, reservation: Reservation) -> None:
        """Hand a reservation back — the model call it was for never
        produced anything to charge. It credits the day it was taken
        from; one taken yesterday and released after midnight touches
        nothing today (yesterday's row is already gone)."""
        path = self.usage_path(reservation.username)
        with exclusive_lock(path):
            days = self._usage_load(path)
            row = days.get(reservation.day)
            if isinstance(row, dict) and int(row.get(reservation.kind, 0)) > 0:
                row[reservation.kind] = int(row[reservation.kind]) - 1
                atomic_write(path, json.dumps(days, indent=2, sort_keys=True) + "\n")

    def delete(self, username: str, session_id: str) -> None:
        path = self.path(username, session_id)
        if not path.is_dir():
            raise KeyError(session_id)
        shutil.rmtree(path)

    # ── brief ────────────────────────────────────────────────────────────────

    def save_brief(self, username: str, session_id: str, brief: dict[str, Any]) -> None:
        atomic_write(self.path(username, session_id) / "brief.json", json.dumps(brief, indent=2) + "\n")

    def load_brief(self, username: str, session_id: str) -> dict[str, Any]:
        path = self.path(username, session_id) / "brief.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KeyError(session_id) from None
        return data if isinstance(data, dict) else {}

    # ── append-only records ──────────────────────────────────────────────────

    def _append(self, username: str, session_id: str, name: str, row: dict[str, Any]) -> None:
        path = self.path(username, session_id) / name
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _rows(self, username: str, session_id: str, name: str) -> list[dict[str, Any]]:
        path = self.path(username, session_id) / name
        rows: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except FileNotFoundError:
            pass
        return rows

    def append_transcript(self, username: str, session_id: str, t: float, speaker: str, text: str) -> None:
        self._append(username, session_id, "transcript.jsonl",
                     {"t": round(t, 2), "speaker": speaker, "text": text})

    def transcript(self, username: str, session_id: str) -> list[dict[str, Any]]:
        return self._rows(username, session_id, "transcript.jsonl")

    def append_code(self, username: str, session_id: str, t: float, lang: str, code: str, event: str) -> None:
        self._append(username, session_id, "code.jsonl",
                     {"t": round(t, 2), "lang": lang, "code": code, "event": event})

    def code(self, username: str, session_id: str) -> list[dict[str, Any]]:
        return self._rows(username, session_id, "code.jsonl")

    # ── recording ────────────────────────────────────────────────────────────

    def recording_path(self, username: str, session_id: str) -> Path:
        return self.path(username, session_id) / "recording.webm"

    def append_recording(self, username: str, session_id: str, data: bytes) -> int:
        path = self.recording_path(username, session_id)
        with path.open("ab") as fh:
            fh.write(data)
        return path.stat().st_size

    # ── debrief ──────────────────────────────────────────────────────────────

    def write_debrief(self, username: str, session_id: str, debrief: dict[str, Any], markdown: str) -> None:
        base = self.path(username, session_id)
        atomic_write(base / "debrief.json", json.dumps(debrief, indent=2, ensure_ascii=False) + "\n")
        atomic_write(base / "debrief.md", markdown)

    def debrief(self, username: str, session_id: str) -> tuple[dict[str, Any] | None, str | None]:
        base = self.path(username, session_id)
        try:
            data = json.loads((base / "debrief.json").read_text(encoding="utf-8"))
            md = (base / "debrief.md").read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None, None
        return (data if isinstance(data, dict) else None), md

    # ── usage (admin) ────────────────────────────────────────────────────────

    def usernames(self) -> Iterator[str]:
        base = self._root / "users"
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    yield child.name


def brief_title(mode: str, brief: dict[str, Any]) -> str:
    if mode == "case":
        return str(brief.get("title") or brief.get("client") or "Case")
    company = brief.get("company") if isinstance(brief.get("company"), dict) else brief
    name = str(company.get("name") or "Interview")
    role = brief.get("role") if isinstance(brief.get("role"), dict) else {}
    title = str(role.get("title") or "")
    return f"{name} · {title}" if title else name


__all__ = ["RETIRED", "Reservation", "SESSION_ID", "STATES", "SessionStore", "brief_title"]
