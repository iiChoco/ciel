"""Vigil's mail source — a reply arriving at Ciel's own address.

Ciel's address (``[mail]``) could send and never hear back. Whatever a
correspondent wrote in return went where the domain's Email Routing
forwards it — the owner's inbox — and sat there unmentioned: the user
found out by reading their own mail, and Ciel, asked "did Sam answer?",
had no way to know. This module closes the loop. It reads the inbox the
address forwards to through the Gmail connector's login, exactly as the
events-from-email feature does, keeps what is addressed to Ciel, and
raises a Vigil event for each message that answers something Ciel sent.

**Only what was addressed to Ciel is read.** The query is pinned to the
address (``to:ciel@…``, never ``from:`` it, so Ciel's own copies are
skipped); the rest of the inbox is never listed, and nothing is marked
read or moved. **A reply is a reply because its thread says so**: the
``In-Reply-To`` and ``References`` headers are matched against the
Message-IDs Ciel stamped on its own mail, and — because Cloudflare's relay
rewrites the id on the way out — any id on Ciel's own domain in those
headers is proof as well, with the ledger then naming which message by
subject; the subject-and-sender fallback covers clients that strip the
headers, and the user's own addresses count as the recipient there, since
the silent copy is answered from wherever it was read. Mail to the address
that answers nothing is kept for the tool to show and never announced.
**Nothing from
before the watch existed is announced**: the first scan records its
moment, and the window never opens further back than that, so arming the
watch on a full inbox is quiet. **A correspondent's words are quoted, not
vouched for**: the event carries a short excerpt in quotation marks, the
tool hands the brain the new words of a message with the quoted original
stripped, and both say whose words they are. **The scan is one thread,
the queue is the judge**: the watcher observes, dedupes by the mailbox's
own message id, and leaves whether and how to interrupt to the policy,
like every watcher.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import email.utils
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Protocol

from ciel.email_calendar import RawMessage, normalize
from ciel.mail import MailUnavailable, SentLedger, bare_id, bare_subject
from ciel.proactive.events import EventQueue, ProactiveEvent
from ciel.proactive.watchers import WatcherHealth, poll_loop

if TYPE_CHECKING:
    from ciel.config import MailConfig

log = logging.getLogger(__name__)

_FRESH_S = 24 * 3600.0
"""How long a reply stays worth announcing. A day: a reply is a
conversation, and a conversation older than that is one to be read, not
interrupted for."""

_LOOKBACK_S = 48 * 3600.0
"""How far back each scan asks the mailbox. Wide enough that a laptop
shut for a weekend still finds the replies that landed; the seen list and
the queue's dedupe keep the overlap silent."""

_SCAN_LIMIT = 50
_CACHE_KEEP = 50
_SEEN_KEEP = 500
_EXCERPT_CHARS = 140


class InboxReader(Protocol):
    """What the watcher needs from a mailbox — ``GmailReader``'s shape."""

    def available(self) -> bool: ...
    def list_messages(self, query: str, limit: int) -> list[str]: ...
    def fetch_raw(self, message_id: str) -> tuple[bytes, dict]: ...


@dataclass(frozen=True, slots=True)
class Received:
    """One message addressed to Ciel, as the brain and the event see it."""

    id: str
    """The mailbox's own id — the handle ``read_ciel_mail`` shows and
    ``send_as_ciel``'s ``reply_to`` takes."""

    message_id: str
    thread_id: str
    sender: str
    sender_display: str
    subject: str
    text: str
    """The correspondent's new words: quoted material stripped, bounded."""

    received_at: float
    references: tuple[str, ...]
    """The ids the message itself threads under, in order, so an answer
    can carry them on."""

    answers_subject: str
    """The subject of the message from Ciel this one answers; empty when
    it answers nothing the ledger knows."""

    answers_message_id: str
    bulk: bool

    automated: bool = False
    """Written by a machine, not a person: an out-of-office responder, a
    no-reply notice. Still worth telling the user about — it is an answer
    — but never worth answering, which is how two machines start talking
    to each other forever."""


_QUOTE_OPENER = re.compile(r"(^on\b.*\bwrote:$)|(^-{2,}\s*original message\s*-{2,}$)|(^from:\s.+$)", re.IGNORECASE)
_WROTE_TAIL = re.compile(r"\bwrote:$", re.IGNORECASE)


def strip_quoted(text: str) -> str:
    """The new words of a reply: quoted lines dropped, and everything from
    the line that introduces the quoted original onward cut. Clients wrap
    that line, so a line ending in "wrote:" closes it wherever it began."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if _QUOTE_OPENER.match(stripped) or _WROTE_TAIL.search(stripped):
            # A client that wrapped the opener left its first half ("On
            # Mon, … Ciel") on the line before: frame, not words.
            if not stripped.lower().startswith("on ") and kept and kept[-1].lower().startswith("on "):
                kept.pop()
            break
        kept.append(stripped)
    return "\n".join(line for line in kept if line).strip()


def excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    """One spoken-length line of a message, for the event."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def inbox_query(address: str, since: float) -> str:
    """The one search the watcher makes: mail to the address, not from it,
    from ``since`` on. ``after:`` takes epoch seconds."""
    return f"to:{address} -from:{address} -in:spam -in:trash after:{int(since)}"


def _header_ids(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace(",", " ").split() if part.strip())


def _on_domain(ids: tuple[str, ...], address: str) -> str:
    """The first thread id on the address's own domain, or ''. What a
    relay that rewrites Message-IDs still leaves intact."""
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    if not domain:
        return ""
    return next((i for i in ids if bare_id(i).endswith(f"@{domain}")), "")


def parse_received(
    message_id: str, raw: bytes, meta: dict, ledger: SentLedger, max_chars: int,
    *, address: str = "", own: tuple[str, ...] = (),
) -> Received:
    """One message's bytes into a ``Received``: the standard library's
    parse for the body (shared with events-from-email), the threading
    headers read here, and the question of what it answers put to the
    ledger first and to the thread's domain second."""
    received_at = time.time()
    try:
        internal = meta.get("internalDate") if isinstance(meta, dict) else None
        if internal is not None:
            received_at = float(internal) / 1000.0
    except (TypeError, ValueError):
        pass
    body = normalize(RawMessage(message_id, str(meta.get("threadId") or "") if isinstance(meta, dict) else "", raw, received_at), max_chars)
    parsed = email.message_from_bytes(raw, policy=email.policy.default)
    own_id = str(parsed.get("Message-ID", "")).strip()
    in_reply_to = _header_ids(str(parsed.get("In-Reply-To", "")))
    references = _header_ids(str(parsed.get("References", "")))
    thread: list[str] = []
    known: set[str] = set()
    for ref in (*references, *in_reply_to):
        if bare_id(ref) not in known:
            known.add(bare_id(ref))
            thread.append(ref)
    submitted = str(parsed.get("Auto-Submitted", "")).strip().lower()
    precedence = str(parsed.get("Precedence", "")).strip().lower()
    automated = (
        (bool(submitted) and submitted != "no")
        or precedence == "auto_reply"
        or bool(parsed.get("X-Autoreply"))
        or bool(parsed.get("X-Autorespond"))
        or body.sender.split("@")[0] in ("no-reply", "noreply", "do-not-reply", "donotreply", "mailer-daemon", "postmaster")
    )
    answers = ledger.match(thread, sender=body.sender, subject=body.subject, own=own)
    answers_subject = answers.subject if answers else ""
    answers_message_id = answers.message_id if answers else ""
    if answers is None:
        proven = _on_domain(tuple(thread), address)
        if proven:
            # Ciel's domain in the thread but no ledger record: the relay
            # renamed the id, or the send predates the ledger. Name it by
            # the ledger's subject when there is one, else by its own.
            named = ledger.by_subject(body.subject)
            answers_subject = named.subject if named else (bare_subject(body.subject) or body.subject)
            answers_message_id = proven
    return Received(
        id=message_id,
        message_id=own_id,
        thread_id=body.thread_id,
        sender=body.sender,
        sender_display=" ".join(body.sender_display.split()),
        subject=body.subject,
        text=strip_quoted(body.text),
        received_at=received_at,
        references=tuple(thread),
        answers_subject=answers_subject,
        answers_message_id=answers_message_id,
        bulk=body.bulk,
        automated=automated,
    )


def reply_events(received: list[Received], now: float, next_id: Callable[[], str]) -> list[ProactiveEvent]:
    """The events one scan is worth: one per message that answers
    something Ciel sent. Pure, so the probe drives every shape."""
    events: list[ProactiveEvent] = []
    for item in received:
        if not item.answers_subject:
            continue
        who = item.sender_display or item.sender
        words = excerpt(item.text)
        summary = f"{who} replied to my email “{item.answers_subject}”"
        summary += f": “{words}”" if words else " with nothing but the quoted original"
        events.append(ProactiveEvent(
            id=next_id(),
            source="mail",
            importance=2,
            created_at=now,
            expires_at=now + _FRESH_S,
            summary=summary + ".",
            dedupe_key=f"mail:reply:{item.id}",
            payload={
                "id": item.id, "sender": item.sender, "subject": item.subject,
                "answers": item.answers_subject,
                # The auto-reply path reads these rather than the summary:
                # whether answering is safe at all, and who to answer as.
                "automated": "yes" if item.automated else "",
                "who": who,
            },
        ))
    return events


class Inbox:
    """Ciel's side of the owner's mailbox: what arrived for the address,
    scanned on demand, with the last messages kept for the brain.

    One lock, because two callers scan — the watcher on its poll and the
    tool when the user asks — and the state file (the first scan's moment,
    the ids already seen, the cached messages) is owner-only: it holds
    other people's words.
    """

    def __init__(self, reader: InboxReader, config: "MailConfig", ledger: SentLedger, path: Path) -> None:
        self._reader = reader
        self._config = config
        self._ledger = ledger
        self._path = path
        self._lock = threading.Lock()
        self._activated_at = 0.0
        self._seen: list[str] = []
        self._cache: list[Received] = []
        self._load()

    @property
    def address(self) -> str:
        return self._config.address

    def available(self) -> bool:
        return bool(self._reader.available())

    def scan(self, now: float) -> list[Received]:
        """Fetch what arrived since the last scan; returns the new
        messages, oldest first, bulk mail and Ciel's own left out.
        Synchronous — callers thread it."""
        with self._lock:
            if not self._activated_at:
                self._activated_at = now
                self._save()
            since = max(self._activated_at, now - _LOOKBACK_S)
            ids = self._reader.list_messages(inbox_query(self._config.address, since), _SCAN_LIMIT)
            fresh = [i for i in ids if i not in self._seen]
            received: list[Received] = []
            for message_id in reversed(fresh):
                raw, meta = self._reader.fetch_raw(message_id)
                item = parse_received(
                    message_id, raw, meta, self._ledger, self._config.reply_max_chars,
                    address=self._config.address, own=(self._config.owner, self._config.copy_to),
                )
                self._seen.append(message_id)
                if item.bulk or item.sender == self._config.address.lower():
                    continue
                received.append(item)
            if fresh:
                self._seen = self._seen[-_SEEN_KEEP:]
                self._cache = [*reversed(received), *self._cache][:_CACHE_KEEP]
                self._save()
            return received

    def recent(self, limit: int) -> list[Received]:
        """The last messages, newest first, from the cache."""
        with self._lock:
            return list(self._cache[: max(1, limit)])

    def find(self, message_id: str) -> Received | None:
        with self._lock:
            return next((r for r in self._cache if r.id == message_id), None)

    def _load(self) -> None:
        try:
            state = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(state, dict):
            return
        try:
            self._activated_at = float(state.get("activated_at", 0.0))
        except (TypeError, ValueError):
            self._activated_at = 0.0
        self._seen = [str(i) for i in state.get("seen", []) if i]
        cache: list[Received] = []
        for row in state.get("cache", []):
            if not isinstance(row, dict):
                continue
            try:
                row["references"] = tuple(str(r) for r in row.get("references", ()))
                cache.append(Received(**row))
            except TypeError:
                continue
        self._cache = cache

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f".{self._path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({
                "activated_at": self._activated_at,
                "seen": self._seen,
                "cache": [asdict(r) for r in self._cache],
            }, fh, ensure_ascii=False)
        os.replace(tmp, self._path)


class MailWatcher:
    """Polls the inbox for Ciel's address and queues a nudge for each reply."""

    def __init__(self, inbox: Inbox, queue: EventQueue, poll_s: float) -> None:
        self._inbox = inbox
        self._queue = queue
        self._poll_s = poll_s
        self._task: asyncio.Task[None] | None = None
        self._kick = asyncio.Event()
        self._health = WatcherHealth("mail", queue)

    async def start(self) -> None:
        if not self._inbox.available():
            log.warning(
                "mail replies enabled but the Gmail connector is not "
                "authorized on this host — the watcher will idle"
            )
            return
        self._task = asyncio.create_task(self._poll())

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

    async def _poll(self) -> None:
        async def tick() -> None:
            now = time.time()
            try:
                received = await asyncio.to_thread(self._inbox.scan, now)
            except asyncio.CancelledError:
                raise
            except MailUnavailable as exc:
                log.warning("mail scan failed: %s", exc)
                self._health.failed(now)
                return
            except Exception:  # noqa: BLE001 - a failed scan must not kill the watcher
                log.exception("mail scan failed")
                self._health.failed(now)
                return
            for event in reply_events(received, now, self._queue.next_id):
                if self._queue.push(event):
                    log.info("mail reply queued: %s", event.summary)
            self._health.succeeded()

        await poll_loop(self._kick, self._poll_s, tick)


__all__ = [
    "Inbox", "InboxReader", "MailWatcher", "Received", "excerpt", "inbox_query",
    "parse_received", "reply_events", "strip_quoted",
]
