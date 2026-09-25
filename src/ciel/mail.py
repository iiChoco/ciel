"""Sending mail through an SMTP relay — Ciel's own address.

Where ``gmail.py`` sends *as the user* by borrowing the Gmail connector,
this sends *as Ciel*: a From address on a domain the relay is authorized
for (``ciel@example.com`` through Cloudflare Email Service's SMTP endpoint,
where the username is the literal ``api_token`` and the password is an
API token with the Email Sending permission). Cloudflare relays to the
account's verified destination addresses free of charge, which is exactly
the "email me" case; anything wider needs their paid plan.

Implicit TLS only (SMTPS on 465 — the relay speaks no STARTTLS), stdlib
``smtplib``, synchronous so callers thread it. The recipient and sender
are pinned in config by the user; nothing here chooses either at send
time. ``MailUnavailable`` is the one failure shape both senders share, so
the watcher degrades the same way whichever is wired.

Every message leaves with a Message-ID of Ciel's own making, and the
``SentLedger`` keeps what went out — the id, the recipient, the subject —
in an owner-only file. That is one way a reply is recognised as one: the
reply watcher (``proactive/mail.py``) matches what arrives at Ciel's
address against the ledger by ``In-Reply-To`` and ``References``, and by
subject and correspondent when a client sent neither. Not the only way,
because Cloudflare's relay rewrites the Message-ID on its way out (seen
2026-09-22: the reply quoted an id Ciel never stamped, on Ciel's domain),
so the watcher also takes any thread id on the address's own domain as
proof, and the ledger then only names which message it was. A send that
takes ``in_reply_to`` and ``references`` is the other half: Ciel's answer
lands in the correspondent's thread, not beside it.

A message Ciel sent by itself says so on the wire (``Auto-Submitted:
auto-replied``) and in the ledger (``auto``). Both exist for the same
hazard: two machines answering each other forever. The header is what
well-behaved responders read before writing back, and the ledger's count
is the backstop when they do not.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import socket
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Iterable, Protocol

log = logging.getLogger(__name__)

_SMTP_TIMEOUT_S = 20.0


class MailUnavailable(RuntimeError):
    """The relay refused, the token is wrong, or the network is down."""


class Mailer(Protocol):
    """What the watcher needs from a sender — either implementation."""

    def send(
        self, to: str, subject: str, body: str, sender: str = "",
        *, in_reply_to: str = "", references: str = "", auto: bool = False,
    ) -> str: ...


class SmtpSender:
    """Plain-text mail over SMTPS with a static login."""

    def __init__(
        self, host: str, port: int, user: str, token: str, copy_to: str = "",
        name: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._token = token
        self._copy_to = copy_to
        """Bcc on every message — the sender's own record. smtplib strips
        the header before the wire, so the recipient never sees it."""
        self._name = name
        """The display name in front of the From address. ``formataddr``
        quotes it when punctuation would otherwise split the header, so
        a name is never spliced into the address."""

    def available(self) -> bool:
        return bool(self._token and self._host)

    def send(
        self, to: str, subject: str, body: str, sender: str = "",
        *, in_reply_to: str = "", references: str = "", auto: bool = False,
    ) -> str:
        """Send one message; returns the Message-ID stamped on it.

        The id is minted here rather than left to the relay: it is the
        handle a reply comes back with, and the ledger must hold it before
        the message is out of reach. ``in_reply_to`` and ``references``
        thread an answer under the message it answers."""
        if not to or not sender:
            raise MailUnavailable("SMTP sending needs both a recipient and a From address")
        message = EmailMessage()
        message["From"] = formataddr((self._name, sender)) if self._name else sender
        message["To"] = to
        if self._copy_to and self._copy_to.lower() != to.lower():
            message["Bcc"] = self._copy_to
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = references
        if auto:
            # RFC 3834. A responder that reads it will not answer back,
            # which is the cheap half of not looping; the ledger's daily
            # count is the half that works on responders that do not.
            message["Auto-Submitted"] = "auto-replied"
        message.set_content(body)
        try:
            with smtplib.SMTP_SSL(
                self._host, self._port,
                timeout=_SMTP_TIMEOUT_S, context=ssl.create_default_context(),
            ) as relay:
                relay.login(self._user, self._token)
                refused = relay.send_message(message) or {}
        except smtplib.SMTPAuthenticationError as exc:
            raise MailUnavailable(f"the SMTP relay rejected the token ({exc.smtp_code})") from exc
        except smtplib.SMTPException as exc:
            raise MailUnavailable(f"the SMTP relay refused the message ({exc})") from exc
        except (OSError, socket.timeout) as exc:
            raise MailUnavailable(f"the SMTP relay could not be reached ({exc})") from exc
        # A partial refusal comes back as a dict, not an exception: the
        # recipient refused is a failed send; only the copy refused is a
        # delivered message with a missing record, worth a line in the log.
        if any(r.lower() == to.lower() for r in refused):
            code, reason = next(iter(refused.values()))
            raise MailUnavailable(f"the relay refused {to} ({code} {reason!r})")
        for address, (code, reason) in refused.items():
            log.warning("mail: the copy to %s was refused (%s %r)", address, code, reason)
        return str(message.get("Message-ID", ""))


# ── the ledger: what went out, so a reply can be known as one ────────────────

_LEDGER_KEEP = 500
"""Sends remembered. A reply to something older than the last five hundred
messages from Ciel's address is a reply nobody is waiting for."""


@dataclass(frozen=True, slots=True)
class SentMail:
    message_id: str
    to: str
    subject: str
    sent_at: float
    auto: bool = False
    """Sent by Ciel on its own, with nobody asked. The day's count of
    these is the bound on an auto-reply loop."""


def bare_id(value: str) -> str:
    """A Message-ID as clients compare them: no brackets, no case."""
    return value.strip().strip("<>").strip().lower()


def bare_subject(value: str) -> str:
    """A subject with every reply and forward prefix peeled off, collapsed
    and lowercased — what two messages in one thread have in common when
    a client set no ``In-Reply-To``."""
    text = " ".join(value.split()).lower()
    while True:
        for prefix in ("re:", "fwd:", "fw:", "aw:"):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()
                break
        else:
            return text


class SentLedger:
    """Ciel's outgoing mail by Message-ID, in an owner-only file.

    Read fresh on every call and rewritten whole under a lock: the writer
    is the send tool on the brain's thread, the reader is the inbox scan
    on a watcher's, and a file this small is cheaper to reread than to
    keep coherent in two processes' memory across a re-exec.
    """

    def __init__(self, path: Path, keep: int = _LEDGER_KEEP) -> None:
        self._path = path
        self._keep = keep
        self._lock = threading.Lock()

    def entries(self) -> list[SentMail]:
        """Newest first."""
        try:
            rows = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        out: list[SentMail] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            try:
                out.append(SentMail(
                    str(row["message_id"]), str(row.get("to", "")),
                    str(row.get("subject", "")), float(row.get("sent_at", 0.0)),
                    bool(row.get("auto", False)),
                ))
            except (TypeError, ValueError):
                continue
        return out

    def record(self, message_id: str, to: str, subject: str, sent_at: float, auto: bool = False) -> None:
        if not message_id:
            return
        with self._lock:
            rows = [SentMail(message_id, to, subject, sent_at, auto), *self.entries()][: self._keep]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f".{self._path.name}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump([
                    {"message_id": r.message_id, "to": r.to, "subject": r.subject,
                     "sent_at": r.sent_at, "auto": r.auto}
                    for r in rows
                ], fh)
            os.replace(tmp, self._path)

    def match(self, ids: Iterable[str], sender: str = "", subject: str = "", own: Iterable[str] = ()) -> SentMail | None:
        """The message from Ciel that a reply answers, or None.

        Headers first: any of ``ids`` (a reply's ``In-Reply-To`` and
        ``References``) naming a recorded Message-ID is proof. Then the
        weaker sign clients that strip headers still leave: the same
        subject under its prefixes, from the address the message went to
        — or from one of the user's ``own`` addresses, because the silent
        copy lands in one of those and the user answers from wherever
        they read it.
        """
        wanted = {bare_id(i) for i in ids if i and bare_id(i)}
        entries = self.entries()
        for entry in entries:
            if bare_id(entry.message_id) in wanted:
                return entry
        if sender and subject:
            plain = bare_subject(subject)
            mine = {a.lower() for a in own if a}
            if plain:
                for entry in entries:
                    if bare_subject(entry.subject) != plain:
                        continue
                    if entry.to.lower() == sender.lower() or sender.lower() in mine:
                        return entry
        return None

    def auto_since(self, since: float) -> int:
        """How many messages Ciel sent on its own since ``since`` — the
        day's budget, read from the record rather than from memory, because
        the autoreloader re-execs the process all day long."""
        return sum(1 for e in self.entries() if e.auto and e.sent_at >= since)

    def auto_to_since(self, address: str, since: float) -> int:
        """The same count for one correspondent: the per-person bound that
        catches a loop long before the day's total does."""
        low = address.lower()
        return sum(1 for e in self.entries() if e.auto and e.to.lower() == low and e.sent_at >= since)

    def by_subject(self, subject: str) -> SentMail | None:
        """The newest send with this subject under its prefixes, whoever
        it went to — for naming a reply whose thread is already proven."""
        plain = bare_subject(subject)
        if not plain:
            return None
        return next((e for e in self.entries() if bare_subject(e.subject) == plain), None)


__all__ = ["MailUnavailable", "Mailer", "SentLedger", "SentMail", "SmtpSender", "bare_id", "bare_subject"]
