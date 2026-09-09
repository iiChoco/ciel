"""Sending mail as the user — by borrowing the Gmail connector's login.

The brain sends mail through the ``[mcp.gmail]`` connector, which saved a
refresh token in ``~/.gmail-mcp/credentials.json`` when it was authorized.
Deterministic code (a watcher that must email without a model in the
loop) needs the same power, and the honest way to get it is the way the
Google Calendar watcher gets its calendar: read the connector's refresh
token, mint access tokens in memory, never write back — two writers of
one token file is how refresh races start.

Two capabilities, each deliberately narrow. The sender sends a plain-text
message: the recipient defaults to the signed-in account itself ("email
me"), read from the Gmail profile and cached; any other address is pinned
in config by the user, never chosen at send time. The reader lists and
fetches messages for the inbox feature, bounded, raw, and read-only: it
changes no label and marks nothing read. Both share the login and the
plumbing; neither writes a token. Synchronous urllib, so callers thread it.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from ciel.mail import MailUnavailable

log = logging.getLogger(__name__)

_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_HTTP_TIMEOUT_S = 15.0
_TOKEN_SKEW_S = 60.0


class GmailUnavailable(MailUnavailable):
    """The connector's tokens are missing, revoked, or Google refused."""


def _refresh_token(saved: object) -> str | None:
    """The refresh token in a connector's token file, whichever connector
    wrote it: the Gmail connector keeps it at the top; the calendar
    connector nests its tokens under an account label, so the first entry
    that has one is taken and a layout change degrades to unavailable."""
    if not isinstance(saved, dict):
        return None
    if isinstance(saved.get("refresh_token"), str) and saved["refresh_token"]:
        return str(saved["refresh_token"])
    for entry in saved.values():
        if isinstance(entry, dict) and isinstance(entry.get("refresh_token"), str) and entry["refresh_token"]:
            return str(entry["refresh_token"])
    return None


class GmailClient:
    """The connector's login and the plumbing every Google capability
    shares: the same OAuth client JSON and a saved refresh token, minted into
    access tokens in memory, never written back."""

    def __init__(self, keys_file: Path, token_file: Path) -> None:
        self._keys_file = keys_file
        self._token_file = token_file
        self._access_token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()
        self._own_address: str | None = None

    def available(self) -> bool:
        """Whether the connector has ever been authorized — a file peek,
        no network, so the watcher can decide at construction."""
        try:
            saved = json.loads(self._token_file.read_text())
            keys = json.loads(self._keys_file.read_text())["installed"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return False
        return bool(_refresh_token(saved) and keys.get("client_id"))

    def own_address(self) -> str:
        """The signed-in account's address — the default "me"."""
        if self._own_address is None:
            profile = self._request("GET", f"{_API}/profile")
            address = profile.get("emailAddress")
            if not isinstance(address, str) or "@" not in address:
                raise GmailUnavailable("the Gmail profile has no address")
            self._own_address = address
        return self._own_address

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token()}",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
                raw = response.read().decode("utf-8")
                # Gmail answers some calls (an empty filter list, a batch
                # modify) with 204 and no body; that is success, not JSON.
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise GmailUnavailable(f"Gmail answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise GmailUnavailable(f"Gmail could not be reached ({exc})") from exc

    def _token(self) -> str:
        with self._lock:
            if (
                self._access_token is not None
                and time.time() < self._token_expiry - _TOKEN_SKEW_S
            ):
                return self._access_token
            try:
                keys = json.loads(self._keys_file.read_text())["installed"]
                refresh = _refresh_token(json.loads(self._token_file.read_text()))
                if refresh is None:
                    raise KeyError("refresh_token")
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise GmailUnavailable(
                    "the Gmail connector is not authorized — authorize "
                    "[mcp.gmail] once and the tokens are picked up"
                ) from exc
            body = urllib.parse.urlencode({
                "client_id": keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            }).encode("ascii")
            request = urllib.request.Request(
                keys.get("token_uri", "https://oauth2.googleapis.com/token"),
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
                    minted = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                raise GmailUnavailable(
                    f"Google refused the refresh token ({exc.code}: {detail}) — "
                    "re-authorize the Gmail connector"
                ) from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                raise GmailUnavailable(f"could not reach Google ({exc})") from exc
            token = minted.get("access_token")
            if not isinstance(token, str) or not token:
                raise GmailUnavailable("Google returned no access token")
            self._access_token = token
            self._token_expiry = time.time() + float(minted.get("expires_in", 3600))
            return token


class GmailSender(GmailClient):
    """Sends as whoever authorized the Gmail connector."""

    def send(self, to: str, subject: str, body: str, sender: str = "") -> str:
        """Send one plain-text message; returns Gmail's message id.

        ``sender`` sets the From header — a "send mail as" alias the account
        has verified (an unverified one is silently rewritten by Gmail to
        the account's own address, so a typo degrades, never fails).
        Empty leaves Gmail to stamp the primary address."""
        message = EmailMessage()
        message["To"] = to or self.own_address()
        if sender:
            message["From"] = sender
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        sent = self._request("POST", f"{_API}/messages/send", {"raw": raw})
        return str(sent.get("id", ""))


class GmailReader(GmailClient):
    """Lists and fetches messages for the inbox feature, read-only.

    Raw RFC 822 bytes, so the feature parses mail with the standard library
    and nothing here interprets a body. Bounded by the caller: one query, one
    page, no label changes, nothing marked read.
    """

    def list_messages(self, query: str, limit: int) -> list[str]:
        """Message ids matching a Gmail search query, newest first, at most ``limit``."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        params = urllib.parse.urlencode({"q": query, "maxResults": str(min(limit, 500))})
        listing = self._request("GET", f"{_API}/messages?{params}")
        return [str(item["id"]) for item in (listing.get("messages") or []) if isinstance(item, dict) and item.get("id")]

    def history_anchor(self) -> str:
        """The mailbox's current history id: where watching starts, so that
        nothing before activation is ever read."""
        profile = self._request("GET", f"{_API}/profile")
        anchor = profile.get("historyId")
        if not isinstance(anchor, (str, int)) or not str(anchor):
            raise GmailUnavailable("the Gmail profile has no history id")
        return str(anchor)

    def history(self, start_history_id: str, page_token: str | None, limit: int) -> tuple[list[str], str | None, str, bool]:
        """One page of messages added since a history id: (ids, next page
        token, the history id the listing is current to, expired). Gmail
        forgets history after a while and answers 404; that is the expired
        case, and the caller resynchronizes its window rather than guess."""
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        params = {"startHistoryId": start_history_id, "historyTypes": "messageAdded", "labelId": "INBOX", "maxResults": str(min(limit, 500))}
        if page_token:
            params["pageToken"] = page_token
        try:
            listing = self._request("GET", f"{_API}/history?{urllib.parse.urlencode(params)}")
        except GmailUnavailable as exc:
            if "answered 404" in str(exc):
                return [], None, start_history_id, True
            raise
        ids: list[str] = []
        for record in listing.get("history") or []:
            for added in (record.get("messagesAdded") or []) if isinstance(record, dict) else []:
                message = added.get("message") if isinstance(added, dict) else None
                if isinstance(message, dict) and message.get("id") and str(message["id"]) not in ids:
                    ids.append(str(message["id"]))
        return ids, listing.get("nextPageToken") or None, str(listing.get("historyId") or start_history_id), False

    def fetch_raw(self, message_id: str) -> tuple[bytes, dict]:
        """One message's raw bytes and Gmail's metadata (thread, labels, internalDate)."""
        encoded = urllib.parse.quote(message_id, safe="")
        payload = self._request("GET", f"{_API}/messages/{encoded}?format=raw")
        raw = payload.get("raw")
        if not isinstance(raw, str) or not raw:
            raise GmailUnavailable("Gmail returned a message without its raw body")
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        meta = {key: payload.get(key) for key in ("threadId", "labelIds", "internalDate", "historyId")}
        return data, meta

__all__ = ["GmailSender", "GmailUnavailable"]
