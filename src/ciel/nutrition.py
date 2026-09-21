"""One private food log, with arithmetic and authority outside the model.

**A meal is a reviewed snapshot.** Sources, portions, diary dates, and the
visible calorie allowance stay with the meal. Repeating it copies those
values; settings changes do not rewrite eating history.

**Saved means recoverable.** SQLite commits the record, before/after history,
and receipt together. Owner approval on 2026-09-13 made this history Inverse's
nutrition source, merged at read time with the existing journal. Undo is a
revision-checked inverse, never a database restore.

**Only an admitted owner changes the diary.** The turn's revocable binding
fences worker transactions. The controller owns conditional broker calls;
nutrition never enters the generic tool gate or recorder. One designated
host and runtime mode hold the writer lock; changing mode cannot fork a diary.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import socket
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ciel.config import Config
from ciel.task_context import TaskBinding

NUTRIENTS = ("calories", "protein_g", "carbs_g", "fat_g")
REASONS = ("portion", "oil", "estimate")
UNITS = {"g": ("mass", 1), "kg": ("mass", 1000), "ml": ("volume", 1), "l": ("volume", 1000), "serving": ("serving", 1)}


def packed(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def number(value: Any, name: str, *, optional: bool = False, maximum: float = 100000) -> float | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be a finite number between 0 and {maximum:g}.")
    return float(value)


def label(value: Any, name: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name} must be nonempty text of at most {limit} characters.")
    return value.strip()


def diary_day(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Choose a diary date as YYYY-MM-DD.")
    return date.fromisoformat(value).isoformat()


def policy(config: Config) -> dict[str, float]:
    n = config.nutrition
    return dict(zip(REASONS, (n.portion_allowance_pct, n.oil_allowance_pct, n.estimate_allowance_pct)))


def readiness(config: Config, *, host: str | None = None, role: str = "hub") -> tuple[bool, str]:
    """The doctor and runtime use the same rule, without opening diary state."""
    n = config.nutrition
    if not n.enabled:
        return True, "off"
    if not config.hub.require_token:
        return False, "set [hub].require_token = true; configure the token in every Chart and spoke client, including loopback"
    if not config.hub.current_token():
        return False, "required hub token is missing; configure [hub].token_file and the same token in each client"
    if not n.owner_host or n.owner_host != (host or socket.gethostname()) or n.owner_role != role:
        return False, f"diary belongs to [nutrition].owner_host and owner_role; this process is {host or socket.gethostname()} ({role})"
    try:
        ZoneInfo(config.timezone or "UTC")
        from ciel.nutrition_photos import validate_limits
        validate_limits(n)
        from ciel.nutrition_dashboard import validate_limits as validate_dashboard_limits
        validate_dashboard_limits(n)
        for field in ("max_library_records", "max_plans_per_day", "bulk_max_meals", "bulk_max_previews"):
            if type(getattr(n,field)) is not int or getattr(n,field) < 1:
                raise ValueError(f"{field} must be a positive integer")
        if n.bulk_max_meals > 100 or not 0 < n.bulk_preview_lifetime_s <= 900:
            raise ValueError("Bulk review needs at most 100 meals and a lifetime of at most 900 seconds")
        if type(n.diary_day_start_hour) is not int or not 0 <= n.diary_day_start_hour <= 23:
            raise ValueError("diary_day_start_hour must be 0–23")
        for reason, margin in policy(config).items():
            number(margin, f"{reason} allowance", maximum=100)
        for name in ("lookup_max_bytes", "lookup_cache_entries", "max_items", "max_meals_per_day", "max_pending_controls", "max_request_bytes", "max_records"):
            if type(getattr(n, name)) is not int or getattr(n, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not 0 < n.lookup_timeout_s <= 30:
            raise ValueError("lookup_timeout_s must be greater than zero and at most 30")
        directory = (n.state_dir or config.state_dir / "nutrition-state").expanduser().absolute()
        if directory in (Path.home(), config.state_dir.expanduser().absolute(), Path("/")):
            raise ValueError("state_dir must be a dedicated nutrition directory")
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        return False, f"nutrition settings: {exc}"
    return True, "nutrition settings ready; tokens required on every /ws client"


def occurrence(args: dict[str, Any], config: Config, *, now: float | None = None) -> dict[str, Any]:
    zone_name = args.get("timezone") or config.timezone or "UTC"
    try:
        zone = ZoneInfo(label(zone_name, "timezone", 100))
    except ZoneInfoNotFoundError:
        raise ValueError("Choose a valid IANA timezone, such as America/Los_Angeles.") from None
    raw = args.get("occurred_at")
    if raw:
        instant = datetime.fromisoformat(label(raw, "eating time", 80))
        if instant.tzinfo is None:
            candidates = [instant.replace(tzinfo=zone, fold=f) for f in (0, 1)]
            valid = [c for c in candidates if c.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == instant]
            if not valid or len({c.utcoffset() for c in valid}) > 1:
                raise ValueError("That local time is missing or ambiguous; include its UTC offset.")
            instant = valid[0]
        local = instant.astimezone(zone)
    else:
        local = datetime.fromtimestamp(time.time() if now is None else now, zone)
    cutoff = config.nutrition.diary_day_start_hour
    day = local.date() - timedelta(days=int(local.hour < cutoff))
    return {"occurred_at": local.isoformat(), "timezone": zone.key, "utc_offset": int(local.utcoffset().total_seconds()),
            "cutoff": cutoff, "diary_date": day.isoformat()}


def meal_values(args: dict[str, Any], config: Config) -> dict[str, Any]:
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= config.nutrition.max_items:
        raise ValueError(f"A meal needs 1–{config.nutrition.max_items} items.")
    result = []
    allowance = 0.0
    margins = policy(config)
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each food must be an object.")
        name = label(item.get("name"), "food name")
        quantity = number(item.get("quantity", 1), "quantity")
        basis = number(item.get("basis_quantity", 1), "source quantity")
        if not quantity or not basis:
            raise ValueError("Portion and source quantities must be positive.")
        unit, basis_unit = item.get("unit", "serving"), item.get("basis_unit", "serving")
        if unit not in UNITS or basis_unit not in UNITS or UNITS[unit][0] != UNITS[basis_unit][0]:
            raise ValueError("Use compatible mass, volume, or serving units; density is not guessed.")
        factor = quantity * UNITS[unit][1] / (basis * UNITS[basis_unit][1])
        values = {key: number(item.get(key), key, optional=key != "calories") for key in NUTRIENTS}
        totals = {key: round(value * factor, 4) if value is not None else None for key, value in values.items()}
        for key, value in totals.items():
            number(value, key, optional=True)
        reasons = item.get("uncertainty", [])
        if not isinstance(reasons, list) or any(r not in REASONS for r in reasons):
            raise ValueError("Uncertainty reasons are portion, oil, or estimate.")
        reasons = sorted(set(reasons))
        source = item.get("source", "supplied")
        if source not in ("supplied", "label", "usda", "estimate"):
            raise ValueError("Choose supplied, label, usda, or estimate as the source.")
        if source == "estimate" and "estimate" not in reasons:
            reasons.append("estimate")
        allowance += totals["calories"] * sum(margins[r] for r in reasons) / 100
        result.append({"name": name, "quantity": quantity, "unit": unit, "basis_quantity": basis,
                       "basis_unit": basis_unit, **values, "totals": totals, "source": source,
                       "source_id": str(item.get("source_id", ""))[:100],
                       "preparation": str(item.get("preparation", ""))[:200],
                       "retrieved_at": str(item.get("retrieved_at", ""))[:80], "uncertainty": reasons})
    totals = {}
    coverage = {}
    for key in NUTRIENTS:
        known = [i["totals"][key] for i in result if i["totals"][key] is not None]
        totals[key] = round(sum(known), 2) if known else None
        coverage[key] = len(known)
    allowance = math.ceil(round(allowance, 6))
    totals["calories"] = round(totals["calories"] + allowance, 2)
    return {"name": label(args.get("name", "Meal"), "meal name"), "items": result, "allowance": allowance,
            "policy": {"version": "v1-" + hashlib.sha256(packed(margins).encode()).hexdigest()[:12], "percentages": margins}, "totals": totals, "coverage": coverage}


@dataclass(frozen=True)
class OwnerContext:
    binding: TaskBinding
    session: str
    page: bool = False
    attachments: tuple[Any, ...] = ()
    """Only files admitted with this owner turn may be copied from Chart."""


@dataclass(frozen=True)
class Change:
    kind: str
    record_id: str
    revision: int
    value: dict[str, Any] | None
    action: str
    undo_of: str | None = None
    draft_id: str | None = None
    draft_revision: int | None = None
    related: tuple[Change, ...] = ()
    guards: tuple[tuple[str, str, int], ...] = ()
    strict: bool = False


class NutritionStore:
    """One worker and one file lock; every owner write has durable history."""

    def __init__(self, config: Config, *, host: str | None = None, role: str = "hub") -> None:
        self.config = config
        self.directory = (config.nutrition.state_dir or config.state_dir / "nutrition-state").expanduser().absolute()
        self.host, self.role = host or socket.gethostname(), role
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nutrition")
        self._db: sqlite3.Connection | None = None
        self._lock: Any = None
        self.diary_id = ""

    async def run(self, call: Callable[..., Any], *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._worker, call, *args)

    async def start(self) -> None:
        await self.run(self._open)

    def _open(self) -> None:
        if self.host != self.config.nutrition.owner_host or self.role != self.config.nutrition.owner_role:
            raise ValueError("This host or runtime mode does not own the nutrition diary.")
        if self.directory.is_symlink():
            raise ValueError("Nutrition state must not be a symlink.")
        allowed = {"nutrition.sqlite3", "nutrition.sqlite3-journal", "nutrition.sqlite3-wal", "nutrition.sqlite3-shm", "owner.lock", "nutrition-media"}
        if self.directory.exists() and any(p.name not in allowed or p.is_symlink() for p in self.directory.iterdir()):
            raise ValueError("Choose a dedicated empty nutrition directory or the existing diary directory.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        fd = os.open(self.directory / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        self._lock = os.fdopen(fd, "a+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            self._lock = None
            raise ValueError("Another process owns the nutrition diary.") from None
        path = self.directory / "nutrition.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self._db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        db = self._db
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            raise ValueError("Unsupported nutrition schema version.")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT,
                                            PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS history (seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT UNIQUE NOT NULL,
          digest TEXT NOT NULL, at REAL NOT NULL, owner TEXT NOT NULL, session TEXT NOT NULL, action TEXT NOT NULL,
          before_value TEXT NOT NULL, after_value TEXT NOT NULL, receipt TEXT NOT NULL, undo_of TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS cache (id TEXT PRIMARY KEY, query TEXT NOT NULL, body TEXT NOT NULL, at REAL NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        try:
            expected = {"owner_host": self.host, "owner_role": self.role, "owner": self.config.tasks.owner}
            for key, value in expected.items():
                row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                if row and row[0] != value:
                    raise ValueError("The recorded nutrition owner does not match this host, role, or principal.")
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('diary_id',?)", (str(uuid.uuid4()),))
            self.diary_id = db.execute("SELECT value FROM metadata WHERE key='diary_id'").fetchone()[0]
            db.execute(f"PRAGMA user_version={max(3, version)}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def _close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    async def close(self) -> None:
        await self.run(self._close)
        self._worker.shutdown(wait=False)

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise ValueError("Nutrition storage is unavailable.")
        return self._db

    def _record(self, kind: str, record_id: str) -> dict[str, Any]:
        row = self._connection().execute("SELECT revision,body FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return {"kind": kind, "id": record_id, "revision": row["revision"] if row else 0,
                "value": json.loads(row["body"]) if row and row["body"] is not None else None}

    def _receipt(self, operation_id: str, digest: str) -> dict[str, Any] | None:
        row = self._connection().execute("SELECT digest,receipt FROM history WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row["digest"] != digest:
            raise ValueError("This request ID already names a different operation.")
        return json.loads(row["receipt"])

    def _history(self, count: int, record_id: str | None = None, before_seq: int | None = None) -> list[dict[str, Any]]:
        rows = self._connection().execute(
            "SELECT * FROM history WHERE (? IS NULL OR json_extract(after_value,'$.id')=? OR EXISTS (SELECT 1 FROM json_each(after_value,'$.records') WHERE json_extract(value,'$.id')=?)) AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT ?",
            (record_id, record_id, record_id, before_seq, before_seq, max(1, min(count, 50)))).fetchall()
        return [{"seq": r["seq"], "id": "nutrition:" + r["operation_id"], "when": datetime.fromtimestamp(r["at"], timezone.utc).isoformat(),
                 "at": r["at"], "tool": "nutrition_" + r["action"], "args": {"diary_id": self.diary_id, "operation_id": r["operation_id"]},
                 "response": json.loads(r["receipt"])["text"], "undo_tool": "nutrition_undo"} for r in rows]

    def _day(self, day: str) -> dict[str, Any]:
        db = self._connection()
        meals = []
        for row in db.execute("SELECT id,revision,body FROM records WHERE kind='meal' AND body IS NOT NULL AND json_extract(body,'$.diary_date')=? ORDER BY json_extract(body,'$.occurred_at'),id", (day,)):
            meals.append({"id": row["id"], "revision": row["revision"], **json.loads(row["body"])})
        totals = {}
        coverage = {}
        total_items = sum(len(m["items"]) for m in meals)
        for key in NUTRIENTS:
            known = [m["totals"][key] for m in meals if m["totals"][key] is not None]
            totals[key] = round(sum(known), 2) if known else (0 if not meals else None)
            coverage[key] = {"known_items": sum(m["coverage"][key] for m in meals), "total_items": total_items}
        completion = self._record("day", day)
        setting = db.execute("SELECT body FROM records WHERE kind='settings' AND body IS NOT NULL AND id<=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
        return {"diary_id": self.diary_id, "day": day, "meals": meals, "totals": totals, "coverage": coverage,
                "allowance": sum(m["allowance"] for m in meals), "completion": completion,
                "settings": json.loads(setting[0]) if setting else {}, "settings_revision": self._record("settings", day)["revision"],
                "history": self._history(10)}

    def _write_change(self, change: Change) -> tuple[dict[str, Any], dict[str, Any]]:
        db = self._connection()
        before = self._record(change.kind, change.record_id)
        if type(change.revision) is not int or change.revision < 0 or before["revision"] != change.revision:
            raise ValueError("This record changed. Review its current revision and retry.")
        if change.action == "complete" and change.value["complete"] and not change.value["zero_intake"]:
            exists = db.execute("SELECT 1 FROM records WHERE kind='meal' AND body IS NOT NULL AND json_extract(body,'$.diary_date')=? LIMIT 1", (change.record_id,)).fetchone()
            if exists is None:
                raise ValueError("The day is now empty; completing it needs an explicit zero-intake assertion.")
        if change.value is not None:
            if change.kind in ("meal", "plan"):
                date_key = "diary_date" if change.kind == "meal" else "day"
                count = db.execute("SELECT count(*) FROM records WHERE kind=? AND body IS NOT NULL AND id<>? AND json_extract(body,?)=? AND coalesce(json_extract(body,'$.logged'),0)=0",
                                   (change.kind, change.record_id, "$."+date_key, change.value[date_key])).fetchone()[0]
                maximum = self.config.nutrition.max_meals_per_day if change.kind == "meal" else self.config.nutrition.max_plans_per_day
                if count >= maximum and not change.value.get("logged"):
                    raise ValueError("This diary day reached its meal or plan capacity; edit an existing record instead.")
            elif change.kind in ("food", "recipe", "batch"):
                count = db.execute("SELECT count(*) FROM records WHERE kind IN ('food','recipe','batch') AND body IS NOT NULL AND NOT(kind=? AND id=?)", (change.kind, change.record_id)).fetchone()[0]
                if count >= self.config.nutrition.max_library_records:
                    raise ValueError("The saved food and recipe library is full; edit or remove an existing entry.")
        after = {"kind": change.kind, "id": change.record_id, "revision": before["revision"]+1, "value": change.value}
        db.execute("INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                   (change.kind, change.record_id, after["revision"], packed(change.value) if change.value is not None else None))
        return before, after

    def _commit(self, context: OwnerContext, operation_id: str, digest: str, change: Change, direct_undo: bool = False,
                guard: Callable[[], None] | None = None) -> dict[str, Any]:
        with context.binding.fence():
            db = self._connection()
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = self._receipt(operation_id, digest)
                if previous is not None:
                    db.execute("COMMIT")
                    return previous
                if change.strict and guard is None:
                    raise ValueError("Historical recalculation and its Undo require scoped approval on the nutrition page.")
                if guard:
                    guard()
                if not change.undo_of and db.execute("SELECT count(*) FROM history WHERE undo_of IS NULL").fetchone()[0] >= self.config.nutrition.max_records:
                    raise ValueError("Nutrition history capacity reached; Undo remains available and no records were removed.")
                if change.undo_of:
                    target = db.execute("SELECT * FROM history WHERE operation_id=?", (change.undo_of,)).fetchone()
                    if target is None or target["owner"] != context.binding.origin.owner or target["undo_of"]:
                        raise ValueError("That operation cannot be undone.")
                    if json.loads(target["after_value"]).get("strict") and not change.strict:
                        raise ValueError("This Undo requires scoped approval on the nutrition page.")
                    if db.execute("SELECT 1 FROM history WHERE undo_of=?", (change.undo_of,)).fetchone():
                        raise ValueError("That operation was already undone.")
                    if direct_undo:
                        latest = db.execute("SELECT operation_id,session FROM history ORDER BY seq DESC LIMIT 1").fetchone()
                        if latest["operation_id"] != change.undo_of or latest["session"] != context.session or not context.session:
                            raise ValueError("Direct Undo needs the latest operation from this live session.")
                for kind, record_id, revision in change.guards:
                    if self._record(kind, record_id)["revision"] != revision:
                        raise ValueError("A saved source changed; review its current version.")
                changes = (change, *change.related)
                if len({(c.kind,c.record_id) for c in changes}) != len(changes):
                    raise ValueError("An operation cannot change the same record twice.")
                pairs = [self._write_change(c) for c in changes]
                before, after = pairs[0]
                if len(pairs) > 1 or change.strict:
                    before = {"kind":"group", "records":[p[0] for p in pairs], "strict":change.strict}
                    after = {"kind":"group", "records":[p[1] for p in pairs], "strict":change.strict}
                if change.draft_id is not None:
                    draft = self._record("draft", change.draft_id)
                    if draft["revision"] != change.draft_revision or not draft["value"] or draft["value"]["state"] in ("saved", "discarded"):
                        raise ValueError("This photo draft changed; review it before Save.")
                    updated = {**draft["value"], "state":"saved", "meal_id":change.record_id, "job":None}
                    db.execute("UPDATE records SET revision=revision+1,body=? WHERE kind='draft' AND id=?", (packed(updated), change.draft_id))
                value = change.value or pairs[0][0]["value"] or {}
                if change.strict:
                    text = f"{'Undid recalculation for' if change.undo_of else 'Recalculated allowances for'} {len(pairs)} meals."
                elif change.kind == "meal":
                    calories = value.get("totals",{}).get("calories",0)
                    verb = 'Undid' if change.undo_of else {'save':'Saved','repeat':'Logged','catalog_log':'Logged','plan_log':'Logged','delete':'Deleted'}.get(change.action,'Updated')
                    text = f"{verb} {value.get('name','meal')} on {value.get('diary_date','the diary day')}: {calories:g} calories."
                elif change.kind == "weight":
                    verb = "Undid the change to" if change.undo_of else "Removed" if change.value is None else "Saved"
                    text = f"{verb} the weigh-in for {change.record_id}"
                    if value:
                        text += f": {value['value']:g} {value['unit']}"
                    text += "."
                elif change.kind == "day":
                    text = (f"Undid completion change for {change.record_id}." if change.undo_of else
                            f"Marked {change.record_id} {'complete' if value.get('complete') else 'incomplete'}" +
                            (" with explicit zero intake." if value.get("complete") and value.get("zero_intake") else "."))
                elif change.kind in ("food", "recipe", "batch", "plan"):
                    verb = "Undid" if change.undo_of else "Removed" if change.value is None else "Saved"
                    text = f"{verb} {change.kind} {value.get('name','entry')!r}. Nothing added to intake."
                else:
                    text = f"{'Undid' if change.undo_of else 'Saved'} nutrition settings effective {change.record_id}."
                if not change.undo_of:
                    text += " Undo is available."
                receipt = {"operation_id":operation_id,"diary_id":self.diary_id,"action":change.action,
                           "record_id":change.record_id,"revision":pairs[0][1]["revision"],"text":text,"undo_of":change.undo_of}
                if change.strict:
                    receipt["strict"] = True
                db.execute("INSERT INTO history(operation_id,digest,at,owner,session,action,before_value,after_value,receipt,undo_of) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (operation_id,digest,time.time(),context.binding.origin.owner,context.session,change.action,
                            packed(before),packed(after),packed(receipt),change.undo_of))
                if guard:
                    guard()
                db.execute("COMMIT")
                return receipt
            except BaseException:
                db.execute("ROLLBACK")
                raise


class FoodLookup:
    """A bounded USDA lookup; cache entries are snapshots, never model claims."""

    def __init__(self, config: Config) -> None:
        self.config = config.nutrition

    def search(self, query: str) -> list[dict[str, Any]]:
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        fallback = "Cached foods and explicit nutrition values still work. Without those, propose a clearly labeled estimate for review; unknown macros stay unknown."
        try:
            key = self.config.fdc_api_key_file.read_text().strip()
        except FileNotFoundError:
            raise ValueError("USDA lookup is not configured: its API key file is missing. " + fallback) from None
        except (OSError, UnicodeError):
            raise ValueError("USDA lookup is not configured: its API key file cannot be read. " + fallback) from None
        if not key:
            raise ValueError("USDA lookup is not configured: its API key file is empty. " + fallback)
        try:
            url = "https://api.nal.usda.gov/fdc/v1/foods/search?" + urlencode({"api_key": key})
            request = Request(url, data=packed({"query": query, "pageSize": 8, "dataType": ["Foundation", "SR Legacy"]}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=self.config.lookup_timeout_s) as response:
                raw = response.read(self.config.lookup_max_bytes + 1)
            if len(raw) > self.config.lookup_max_bytes:
                raise ValueError("lookup size")
            foods = json.loads(raw).get("foods", [])
            if not isinstance(foods, list):
                raise ValueError("lookup shape")
        except Exception:
            raise ValueError("USDA lookup failed. " + fallback) from None
        result = []
        for food in foods[:8]:
            if not isinstance(food, dict) or not isinstance(food.get("foodNutrients", []), list):
                continue
            values = {}
            for nutrient in food.get("foodNutrients", []):
                if not isinstance(nutrient, dict):
                    continue
                key = {1008: "calories", 1003: "protein_g", 1005: "carbs_g", 1004: "fat_g"}.get(nutrient.get("nutrientId"))
                expected = "KCAL" if key == "calories" else "G"
                if key and str(nutrient.get("unitName", "")).upper() == expected:
                    try:
                        values[key] = number(nutrient.get("value"), key)
                    except ValueError:
                        pass
            if "calories" not in values:
                continue
            identity = str(food.get("fdcId", ""))
            if not identity.isdigit():
                continue
            result.append({"name": str(food.get("description", ""))[:160], "source": "usda", "source_id": identity,
                           "preparation": str(food.get("dataType", ""))[:200], "quantity": 100, "unit": "g",
                           "basis_quantity": 100, "basis_unit": "g", **{k: values.get(k) for k in NUTRIENTS},
                           "uncertainty": [], "retrieved_at": datetime.now(timezone.utc).isoformat()})
        return result


class NutritionController:
    """Page and voice share this conditional gate and transactional store."""

    def __init__(self, config: Config, *, role: str = "hub", host: str | None = None,
                 ask: Callable[[str], Awaitable[bool]] | None = None,
                 emit: Callable[[OwnerContext, dict[str, Any]], Awaitable[None]] | None = None,
                 lookup: FoodLookup | None = None) -> None:
        self.config, self.role, self.host = config, role, host
        self.ask, self.emit = ask, emit
        self.lookup = lookup or FoodLookup(config)
        self.store: NutritionStore | None = None
        from ciel.nutrition_photos import NutritionPhotos
        self.photos = NutritionPhotos(self)
        from ciel.nutrition_library import NutritionLibrary
        self.library = NutritionLibrary(self)
        from ciel.nutrition_dashboard import NutritionDashboard
        self.dashboard = NutritionDashboard(self)
        self.unavailable = "Nutrition is disabled." if not config.nutrition.enabled else "Nutrition storage is starting."

    async def start(self) -> None:
        if not self.config.nutrition.enabled or self.store is not None:
            return
        ready, reason = readiness(self.config, host=self.host, role=self.role)
        if not ready:
            self.unavailable = reason
            return
        store = NutritionStore(self.config, host=self.host, role=self.role)
        try:
            await store.start()
        except BaseException as exc:
            await store.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            self.unavailable = "Nutrition storage could not open; check its dedicated directory, owner, lock, and schema."
        else:
            self.store = store
            self.unavailable = ""
            try:
                await self.photos.start()
            except BaseException as exc:
                self.store = None
                await store.close()
                self.unavailable = "Nutrition photo storage could not open; check its directory and configured limits."
                if isinstance(exc, asyncio.CancelledError):
                    raise

    async def close(self) -> None:
        self.library.reviews.clear()
        await self.photos.close()
        store, self.store = self.store, None
        self.unavailable = "Nutrition storage is closed."
        if store is not None:
            await store.close()

    def _admit(self, context: OwnerContext | None) -> NutritionStore:
        if context is None or not context.binding.origin.private or not context.binding.origin.attended or context.binding.origin.owner != self.config.tasks.owner:
            raise ValueError("Nutrition needs a live private owner turn.")
        with context.binding.fence():
            if self.store is None:
                raise ValueError(self.unavailable)
            return self.store

    async def _read(self, context: OwnerContext, call: Callable[..., Any], *args: Any) -> Any:
        store = self._admit(context)
        def fenced() -> Any:
            with context.binding.fence():
                return call(*args)
        return await store.run(fenced)

    async def recent(self, context: OwnerContext, count: int = 10) -> list[dict[str, Any]]:
        store = self._admit(context)
        return await self._read(context, store._history, count)

    async def inspect_history(self, context: OwnerContext, args: dict[str, Any]) -> dict[str, Any]:
        store = self._admit(context)
        record_id = label(args["record_id"], "record ID", 100) if args.get("record_id") else None
        before_seq = args.get("before_seq")
        if before_seq is not None and (type(before_seq) is not int or before_seq < 1):
            raise ValueError("History cursor must be a positive sequence.")
        entries = await self._read(context, store._history, 50, record_id, before_seq)
        return {"history": entries, "before_seq": entries[-1]["seq"] if entries else None}

    async def view(self, context: OwnerContext, day: str | None = None) -> dict[str, Any]:
        store = self._admit(context)
        selected = diary_day(day) if day else occurrence({}, self.config)["diary_date"]
        result = await self._read(context, store._day, selected)
        result.update(timezone=self.config.timezone or "UTC", cutoff=self.config.nutrition.diary_day_start_hour,
                      policy=policy(self.config), photos=self.photos.capabilities(), dashboard_max_days=self.config.nutrition.dashboard_max_days)
        return result

    async def search(self, context: OwnerContext, query: str) -> dict[str, Any]:
        store = self._admit(context)
        query = label(query, "food search", 120)
        def cached() -> list[dict[str, Any]]:
            return [json.loads(row[0]) for row in store._connection().execute(
                "SELECT body FROM cache WHERE query=? ORDER BY at DESC,id LIMIT 8", (query.casefold(),))]
        found = await self._read(context, cached)
        if found:
            return {"foods": found, "cached": True, "source": "USDA FoodData Central"}
        foods = await asyncio.to_thread(self.lookup.search, query)
        def remember() -> None:
            db = store._connection()
            db.execute("BEGIN IMMEDIATE")
            try:
                for food in foods:
                    db.execute("INSERT INTO cache VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET query=excluded.query,body=excluded.body,at=excluded.at",
                               (food["source_id"], query.casefold(), packed(food), time.time()))
                db.execute("DELETE FROM cache WHERE id NOT IN (SELECT id FROM cache ORDER BY at DESC,id LIMIT ?)",
                           (self.config.nutrition.lookup_cache_entries,))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        await self._read(context, remember)
        return {"foods": foods, "cached": False, "source": "USDA FoodData Central"}

    async def apply(self, context: OwnerContext, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        store = self._admit(context)
        if not isinstance(args, dict) or len(packed(args).encode()) > self.config.nutrition.max_request_bytes:
            raise ValueError("Nutrition request is too large.")
        if operation not in ("save", "repeat", "delete", "complete", "settings", "undo", "weight_save", "weight_delete"):
            raise ValueError("Unknown nutrition operation.")
        origin = context.binding.origin
        digest = hashlib.sha256(packed({"operation": operation, "args": args}).encode()).hexdigest()
        identity = f"{origin.owner}:{origin.request_id}:nutrition:{operation}"
        # One spoken request may name different meals; identical SDK retries
        # still collapse. A page request retains its exact reviewed payload.
        operation_id = hashlib.sha256((identity if context.page else identity + ":" + digest).encode()).hexdigest()
        previous = await self._read(context, store._receipt, operation_id, digest)
        if previous is not None:
            return await self._deliver(context, previous)
        direct_undo = False
        if operation == "undo":
            target_id = args.get("operation_id")
            def target() -> dict[str, Any]:
                db = store._connection()
                row = db.execute("SELECT * FROM history WHERE operation_id=?", (target_id,)).fetchone() if target_id else db.execute("SELECT * FROM history ORDER BY seq DESC LIMIT 1").fetchone()
                if row is None or row["owner"] != origin.owner or row["undo_of"]:
                    raise ValueError("No eligible nutrition operation to undo.")
                if db.execute("SELECT 1 FROM history WHERE undo_of=?", (row["operation_id"],)).fetchone():
                    raise ValueError("That nutrition operation was already undone.")
                latest = db.execute("SELECT operation_id FROM history ORDER BY seq DESC LIMIT 1").fetchone()[0]
                return {**dict(row), "latest": latest}
            record = await self._read(context, target)
            before, after = json.loads(record["before_value"]), json.loads(record["after_value"])
            if after.get("strict"):
                return await self.library.undo_bulk(context, operation_id, digest, record)
            originals = before["records"] if before["kind"] == "group" else [before]
            saved = after["records"] if after["kind"] == "group" else [after]
            inverses = [Change(b["kind"],b["id"],a["revision"],b["value"],"undo") for b,a in zip(originals,saved)]
            change = Change(inverses[0].kind,inverses[0].record_id,inverses[0].revision,inverses[0].value,"undo",record["operation_id"],related=tuple(inverses[1:]))
            direct_undo = bool(context.session and record["session"] == context.session and record["operation_id"] == record["latest"])
        elif operation == "complete":
            day = diary_day(args.get("day"))
            state = await self._read(context, store._day, day)
            complete = args.get("complete")
            if type(complete) is not bool:
                raise ValueError("Completion must be explicitly true or false.")
            if complete and not state["meals"] and args.get("zero_intake") is not True:
                raise ValueError("An empty complete day needs an explicit zero-intake assertion.")
            revision = args.get("revision", state["completion"]["revision"])
            change = Change("day", day, revision, {"complete": complete, "zero_intake": args.get("zero_intake") is True}, operation)
        elif operation in ("weight_save", "weight_delete"):
            day = diary_day(args.get("day"))
            current = await self._read(context, store._record, "weight", day)
            value = None
            if operation == "weight_delete":
                if current["value"] is None:
                    raise ValueError("That weigh-in is absent.")
            else:
                unit = args.get("unit")
                if unit not in ("kg", "lb"):
                    raise ValueError("Choose kilograms (kg) or pounds (lb).")
                amount = number(args.get("value"), "weight", maximum=2204.62 if unit == "lb" else 1000)
                if amount == 0:
                    raise ValueError("A weigh-in must be greater than zero.")
                value = {"day":day, "value":amount, "unit":unit, "kg":amount * (0.45359237 if unit == "lb" else 1)}
            change = Change("weight", day, args.get("revision", current["revision"]), value, operation)
        elif operation == "settings":
            day = diary_day(args.get("effective_from"))
            current = await self._read(context, store._record, "settings", day)
            value = {"effective_from": day, **{key: number(args.get(key), key, optional=True) for key in ("calorie_target", "protein_target_g", "expenditure")}}
            change = Change("settings", day, args.get("revision", current["revision"]), value, operation)
        else:
            record_id = args.get("id") if operation != "repeat" else None
            if operation == "delete" and not record_id:
                raise ValueError("Choose the meal to delete.")
            record_id = label(record_id, "meal ID", 100) if record_id else str(uuid.uuid5(uuid.NAMESPACE_URL, operation_id))
            current = await self._read(context, store._record, "meal", record_id)
            revision = args.get("revision", current["revision"])
            if operation == "delete":
                if current["value"] is None:
                    raise ValueError("That meal is absent.")
                value = None
            elif operation == "repeat":
                source_id = label(args.get("source_id"), "source meal ID", 100)
                source = await self._read(context, store._record, "meal", source_id)
                if source["value"] is None:
                    raise ValueError("That source meal is absent; choose an existing saved meal.")
                if "source_revision" in args and args["source_revision"] != source["revision"]:
                    raise ValueError("The source meal changed; review it before repeating.")
                value = {**source["value"], **occurrence(args, self.config),
                         "copied_from": {"id": source_id, "revision": source["revision"]}}
            else:
                adjusted = dict(args)
                adjusted["items"] = []
                if not isinstance(args.get("items"), list):
                    raise ValueError("A meal needs a list of foods.")
                for item in args["items"]:
                    if not isinstance(item, dict):
                        raise ValueError("Each food must be an object.")
                    if item.get("source") == "usda":
                        def cached_food() -> dict[str, Any]:
                            # Editing a saved source must survive cache eviction
                            # and preserve the values the owner originally saw.
                            for saved in (current["value"] or {}).get("items", []):
                                if saved["source"] == "usda" and saved["source_id"] == str(item.get("source_id", "")):
                                    return saved
                            row = store._connection().execute("SELECT body FROM cache WHERE id=?", (str(item.get("source_id", "")),)).fetchone()
                            if row is None:
                                raise ValueError("Look up that USDA food first, or enter supplied values.")
                            return json.loads(row[0])
                        snapshot = await self._read(context, cached_food)
                        item = {**snapshot, "quantity": item.get("quantity", 100), "unit": item.get("unit", "g"),
                                "uncertainty": item.get("uncertainty", [])}
                    adjusted["items"].append(item)
                fraction = number(args.get("consumed_fraction", 1), "consumed fraction", maximum=1)
                if not fraction:
                    raise ValueError("Nothing eaten means keep or discard the draft, not Save a meal.")
                adjusted["items"] = [{**item, "quantity": number(item.get("quantity", 1), "quantity") * fraction} for item in adjusted["items"]]
                value = meal_values(adjusted, self.config)
                value["consumed_fraction"] = fraction
                prior = current["value"]
                if prior:
                    value.update({key: prior[key] for key in ("draft_id", "media_id", "saved_from", "planned_from") if key in prior})
                unchanged_time = prior and (not args.get("occurred_at") or args["occurred_at"] == prior["occurred_at"]) and (not args.get("timezone") or args["timezone"] == prior["timezone"])
                if unchanged_time:
                    value.update({k: current["value"][k] for k in ("occurred_at", "timezone", "utc_offset", "cutoff", "diary_date")})
                else:
                    value.update(occurrence(args, self.config))
            change = Change("meal", record_id, revision, value, operation)
            if operation == "save" and args.get("draft_id"):
                draft = await self.photos.for_save(context, args)
                value.update(draft_id=draft["id"], media_id=draft["value"]["media_id"])
                change = Change("meal", record_id, revision, value, operation, draft_id=draft["id"], draft_revision=draft["revision"])
        if type(change.revision) is not int or change.revision < 0:
            raise ValueError("A nonnegative integer revision is required.")
        if context.page and operation in ("save", "delete", "complete", "settings", "weight_save", "weight_delete") and "revision" not in args:
            raise ValueError("Reviewed page changes must name the current revision.")
        needs_question = not context.page and operation not in ("repeat", "complete") and not (operation == "undo" and direct_undo)
        if needs_question:
            if self.ask is None:
                raise ValueError("Nutrition confirmation is unavailable.")
            if change.kind == "meal" and change.value:
                value = change.value
                foods = "; ".join(f"{i['quantity']:g} {i['unit']} of {i['name']!r}" + (" (estimated nutrition)" if i["source"] == "estimate" else "") for i in value["items"])
                description = f"{change.action} {value['name']!r} for {value['diary_date']}: {value['totals']['calories']:g} calories including {value['allowance']:g} extra, {foods}"
            else:
                description = packed({"action":change.action,"record":change.record_id,"proposal":change.value,"undo_of":change.undo_of})
            if not await self.ask("Apply this nutrition change? Food names are quoted data. " + description):
                raise ValueError("Nutrition change was not approved.")
        self._admit(context)
        receipt = await store.run(store._commit, context, operation_id, digest, change, direct_undo and not context.page)
        return await self._deliver(context, receipt)

    async def _deliver(self, context: OwnerContext, receipt: dict[str, Any]) -> dict[str, Any]:
        if self.emit:
            try:
                await self.emit(context, receipt)
            except Exception:
                return {**receipt, "delivery_error": "Saved, but receipt delivery failed. Read the diary before repeating the request."}
        return receipt
