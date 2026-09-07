"""Who may enter the room: accounts, passwords, and the login cookie.

The owner creates every account; there is no signup. Passwords are
generated, never chosen — four words and two digits, shown once — so the
room never holds a password anyone reuses elsewhere. What it stores is a
scrypt hash and a salt per user, in one JSON file under the room's
directory, written atomically and named on the personal brain's forbidden
list.

A login is a cookie: ``username|id|auth|expiry|hmac``, signed with a
secret the room mints once (owner-only file, like the hub token).
Stateless on purpose — no session table to expire, and a restart logs
nobody out. Disabling an account still takes effect at once, because
every request looks the username up after verifying the signature; and
the cookie names *which* account and *which* password it was issued
under — an immutable id minted at creation, and an ``auth`` version that
every password reset bumps — so a reset logs the old cookies out, and a
username deleted and recreated is a different account to every cookie
the first one issued.

Writes hold a lock across the whole read-modify-write (threads and
processes both: the CLI edits the same file), because atomic replacement
alone still lets the slower of two writers put back its stale copy — a
reset that paused in scrypt while an admin disabled the account would
have re-enabled it. The hash is computed outside the lock; the account
is reloaded inside it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ciel.memory.store import atomic_write, exclusive_lock

log = logging.getLogger(__name__)

ACCOUNTS_FILE = "interview-accounts.json"
SECRET_FILE = "interview.secret"

USERNAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
"""Lowercase, digits, underscore, dash; 2-32 characters. Usernames ride
in the cookie and name a directory, so they are kept boring."""

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

# Short, distinct, easy to say over the phone. Four of these plus two
# digits is ~55 bits, and nobody types it twice: the browser remembers.
_WORDS = (
    "amber", "birch", "cedar", "delta", "ember", "fjord", "glade", "harbor",
    "island", "juniper", "kestrel", "lantern", "meadow", "north", "orchid",
    "pebble", "quartz", "river", "summit", "timber", "umber", "valley",
    "willow", "yonder", "zenith", "anchor", "beacon", "canyon", "dune",
    "falcon", "garnet", "heron", "ivory", "jasper", "koi", "lumen", "marble",
    "nectar", "oak", "prairie", "quill", "ridge", "sable", "tundra", "violet",
)


@dataclass(frozen=True)
class Account:
    username: str
    role: str
    created: str
    disabled: bool = False
    last_seen: str | None = None
    id: str = ""
    """Minted once at creation, never reused: the cookie's real subject."""
    auth: int = 1
    """Bumped by every password reset; a cookie carries the value it was
    issued under and is refused once they differ."""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


MIN_PASSWORD = 8
MAX_PASSWORD = 128


def check_password(password: str) -> None:
    """A chosen password must be at least eight characters. Nothing more
    clever: length is the rule that survives contact with people."""
    if not isinstance(password, str) or not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")


def generate_password() -> str:
    words = "-".join(secrets.choice(_WORDS) for _ in range(4))
    return f"{words}-{secrets.randbelow(90) + 10}"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def _new_id() -> str:
    return secrets.token_hex(8)


class Accounts:
    """The account file, read on demand and written atomically under a
    lock that covers the whole read-modify-write."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mutex = threading.Lock()
        self._upgraded = False

    @property
    def path(self) -> Path:
        return self._path

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer at a time: this process's threads (the mutex), and
        every other process on the file (the flock beside it)."""
        with self._mutex, exclusive_lock(self._path):
            yield

    def _upgrade(self) -> None:
        """Accounts from before ids existed get one, once. Under the lock
        — an id assigned on every read would be a cookie that never
        matched."""
        if self._upgraded:
            return
        with self._locked():
            data = self._load()
            changed = False
            for raw in data["users"].values():
                if isinstance(raw, dict) and not raw.get("id"):
                    raw["id"] = _new_id()
                    raw["auth"] = int(raw.get("auth") or 1)
                    changed = True
            if changed:
                self._save(data)
                log.info("interview accounts upgraded with ids")
            self._upgraded = True

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}}
        except (OSError, ValueError) as exc:
            log.error("could not read %s (%s) — treating as empty", self._path, exc)
            return {"users": {}}
        users = data.get("users")
        return {"users": users if isinstance(users, dict) else {}}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            # Create owner-only before the first write lands.
            self._path.touch(mode=0o600)
        atomic_write(self._path, json.dumps(data, indent=2, sort_keys=True) + "\n")
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _account(name: str, raw: dict[str, Any]) -> Account:
        return Account(
            username=name,
            role=str(raw.get("role", "user")),
            created=str(raw.get("created", "")),
            disabled=bool(raw.get("disabled", False)),
            last_seen=raw.get("last_seen"),
            id=str(raw.get("id") or ""),
            auth=int(raw.get("auth") or 1),
        )

    # ── reads ────────────────────────────────────────────────────────────────

    def get(self, username: str) -> Account | None:
        self._upgrade()
        raw = self._load()["users"].get(username)
        return self._account(username, raw) if isinstance(raw, dict) else None

    def list(self) -> list[Account]:
        self._upgrade()
        users = self._load()["users"]
        return sorted(
            (self._account(n, r) for n, r in users.items() if isinstance(r, dict)),
            key=lambda a: a.username,
        )

    @property
    def empty(self) -> bool:
        return not self._load()["users"]

    def verify(self, username: str, password: str) -> Account | None:
        """The account when the password matches and the account is live;
        None otherwise, with the same work done either way."""
        self._upgrade()
        raw = self._load()["users"].get(username)
        if not isinstance(raw, dict):
            # Burn the same time as a real check so a probe can't tell a
            # missing user from a wrong password by the clock.
            _hash(password, b"\0" * 16)
            return None
        try:
            salt = base64.b64decode(raw["salt"])
            stored = base64.b64decode(raw["scrypt"])
        except (KeyError, ValueError):
            return None
        if not hmac.compare_digest(_hash(password, salt), stored):
            return None
        account = self._account(username, raw)
        return None if account.disabled else account

    # ── writes ───────────────────────────────────────────────────────────────

    def create(
        self, username: str, role: str = "user", password: str | None = None
    ) -> str:
        """Add an account; returns the generated password, the only time
        it is ever visible. ``password`` pins one — for the loopback dev
        account only; the room never lets anyone choose theirs."""
        if not USERNAME.match(username):
            raise ValueError(
                "username must be 2-32 characters: lowercase letters, digits, "
                "underscore, dash"
            )
        if role not in ("user", "admin"):
            raise ValueError("role must be user or admin")
        self._upgrade()
        if username in self._load()["users"]:
            raise ValueError(f"account {username!r} already exists")
        if password is not None and username != "dev":
            check_password(password)
        password = password or generate_password()
        # The slow part outside the lock; the existence check again inside
        # it, where it counts.
        salt = secrets.token_bytes(16)
        digest = _hash(password, salt)
        with self._locked():
            data = self._load()
            if username in data["users"]:
                raise ValueError(f"account {username!r} already exists")
            data["users"][username] = {
                "scrypt": base64.b64encode(digest).decode(),
                "salt": base64.b64encode(salt).decode(),
                "role": role,
                "created": _now(),
                "disabled": False,
                "last_seen": None,
                "id": _new_id(),
                "auth": 1,
            }
            self._save(data)
        return password

    def reset(self, username: str, password: str | None = None) -> str:
        """A new password for an existing account — chosen when given
        (and long enough), generated otherwise. Every cookie issued
        under the old password stops working."""
        self._upgrade()
        if not isinstance(self._load()["users"].get(username), dict):
            raise KeyError(username)
        if password:
            if username != "dev":  # the loopback dev account keeps its short one
                check_password(password)
        else:
            password = generate_password()
        salt = secrets.token_bytes(16)
        digest = _hash(password, salt)
        with self._locked():
            data = self._load()
            raw = data["users"].get(username)
            if not isinstance(raw, dict):
                raise KeyError(username)
            raw["scrypt"] = base64.b64encode(digest).decode()
            raw["salt"] = base64.b64encode(salt).decode()
            raw["auth"] = int(raw.get("auth") or 1) + 1
            self._save(data)
        return password

    def set_disabled(self, username: str, disabled: bool) -> Account:
        self._upgrade()
        with self._locked():
            data = self._load()
            raw = data["users"].get(username)
            if not isinstance(raw, dict):
                raise KeyError(username)
            raw["disabled"] = bool(disabled)
            self._save(data)
            return self._account(username, raw)

    def delete(self, username: str) -> None:
        self._upgrade()
        with self._locked():
            data = self._load()
            if username not in data["users"]:
                raise KeyError(username)
            del data["users"][username]
            self._save(data)

    def touch(self, username: str) -> None:
        """Note a login. Best effort: a failed write is not a failed login."""
        try:
            self._upgrade()
            with self._locked():
                data = self._load()
                raw = data["users"].get(username)
                if isinstance(raw, dict):
                    raw["last_seen"] = _now()
                    self._save(data)
        except OSError:
            log.debug("could not record last_seen for %s", username, exc_info=True)


# ── the cookie ───────────────────────────────────────────────────────────────


def mint_secret(path: Path) -> str:
    """Read the signing secret, or mint one owner-only."""
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secret + "\n")
    log.info("interview cookie secret minted at %s", path)
    return secret


@dataclass(frozen=True)
class Ticket:
    """What a valid cookie claims: this username, under this account id,
    at this password generation. The app checks the claim against the
    live account."""

    username: str
    account_id: str
    auth: int

    def matches(self, account: Account) -> bool:
        return (
            account.username == self.username
            and bool(account.id)
            and hmac.compare_digest(account.id, self.account_id)
            and account.auth == self.auth
        )


def sign_cookie(secret: str, account: Account, expires: float) -> str:
    body = f"{account.username}|{account.id}|{account.auth}|{int(expires)}"
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{mac}"


def read_cookie(secret: str, value: str, now: float | None = None) -> Ticket | None:
    """The claim a cookie makes, when its signature holds and it has not
    expired; None for anything else (an old three-part cookie included:
    those sign in again once)."""
    parts = (value or "").split("|")
    if len(parts) != 5:
        return None
    username, account_id, auth_s, expires_s, mac = parts
    if not USERNAME.match(username) or not re.fullmatch(r"[0-9a-f]{16}", account_id):
        return None
    try:
        auth = int(auth_s)
        expires = int(expires_s)
    except ValueError:
        return None
    expected = hmac.new(
        secret.encode(), f"{username}|{account_id}|{auth}|{expires}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    if (now if now is not None else time.time()) >= expires:
        return None
    return Ticket(username, account_id, auth)


__all__ = [
    "ACCOUNTS_FILE",
    "MIN_PASSWORD",
    "check_password",
    "Account",
    "Accounts",
    "SECRET_FILE",
    "Ticket",
    "USERNAME",
    "generate_password",
    "mint_secret",
    "read_cookie",
    "sign_cookie",
]
