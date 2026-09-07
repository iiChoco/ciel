"""Spotify through its Web API — the account behind the speakers.

The gesture's AppleScript door can start a known URI on this Mac, but it
cannot find a recording or tell which device is playing. This client reads
that account and controls its Spotify Connect player from either host.

**A login stays a credential.** Browser authorization uses PKCE and a
state-checked loopback callback, without an application secret. Tokens are
written atomically, owner-only; a file lock serializes refreshes even when
an old brain and its replacement briefly overlap.

**An accepted request is not observed playback.** Mutations are sent once,
never retried after a timeout. Inverse records each control; Proof
Obligation is optional for this connector. This transport reports what
Spotify accepted, leaving status to a read.
Calls run off the event loop, with finite network and browser deadlines.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import fcntl
import hashlib
import http.server
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from ciel.config import SpotifyConfig, load_config

API_URL = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
PLAYBACK_SCOPES = ("user-read-playback-state", "user-modify-playback-state")
"""What a login must carry: without these there is no player to talk to."""
PLAYLIST_SCOPES = ("playlist-read-private", "playlist-modify-private", "playlist-modify-public")
"""What the playlist calls need. Asked for at every login; a login made
before they were asked for still works for playback and is told, by
name, to connect again when a playlist is touched."""
SCOPES = PLAYBACK_SCOPES + PLAYLIST_SCOPES
_URI = re.compile(r"spotify:(track|album|artist|playlist):[A-Za-z0-9]{22}\Z")
_ITEM_URI = re.compile(r"spotify:(track|episode):[A-Za-z0-9]{22}\Z")
_ID = re.compile(r"[A-Za-z0-9]{22}\Z")


class SpotifyUnavailable(RuntimeError):
    """A bounded, credential-free explanation the tool can speak."""

    def __init__(self, message: str, status: int | None = None, retry_after: float = 0) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        # A bearer header must never follow an unexpected host redirect.
        return None


def request_json(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise SpotifyUnavailable("Spotify's answer was too large.")
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise SpotifyUnavailable("Spotify sent an unreadable answer.")
        return result
    except urllib.error.HTTPError as exc:
        messages = {
            400: "Spotify could not use that request.",
            401: "Spotify needs browser authorization again.",
            403: "Spotify refused access. Check Premium, the app's authorized users, and its granted scopes.",
            404: "Spotify could not find the player or item. Open Spotify on a device and start playback once.",
            429: "Spotify is rate limited. Wait before trying again.",
        }
        retry = 0.0
        if exc.code == 429:
            try:
                retry = float(exc.headers.get("Retry-After", "1"))
                retry = max(1.0, retry) if math.isfinite(retry) else 60.0
            except (TypeError, ValueError):
                retry = 60.0
        exc.close()
        raise SpotifyUnavailable(messages.get(exc.code, f"Spotify answered HTTP {exc.code}."), exc.code, retry) from None
    except (urllib.error.URLError, OSError, ValueError):
        # A server error body or exception may contain a credential or URL.
        raise SpotifyUnavailable("Spotify could not be reached or read. A playback request may have arrived; check status before repeating it.") from None


class OAuthTokens:
    """One token file, read again inside the lock before every refresh."""

    def __init__(self, config: SpotifyConfig, request: Callable[..., dict[str, Any]] = request_json) -> None:
        self.config = config
        self._request = request
        self._lock = threading.Lock()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        path = self.config.token_file
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._lock:
            fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                os.close(fd)

    def _read(self) -> dict[str, Any]:
        try:
            fd = os.open(self.config.token_file, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                data = json.load(stream)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        raise SpotifyUnavailable("Spotify is not connected. Run `uv run --no-sync python -m ciel.spotify authorize` on the brain's host.")

    def _save(self, data: dict[str, Any]) -> None:
        path = self.config.token_file
        # A temporary directory keeps even the unfinished token filename
        # behind the same forbidden basename as the completed credential.
        with tempfile.TemporaryDirectory(prefix=".spotify-", dir=path.parent) as temporary:
            pending = Path(temporary) / path.name
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, path)

    def _exchange(self, fields: dict[str, str], previous: dict[str, Any]) -> dict[str, Any]:
        payload = self._request("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, urllib.parse.urlencode(fields).encode(), self.config.timeout_s)
        access = payload.get("access_token")
        refresh = payload.get("refresh_token") or previous.get("refresh_token")
        expires = payload.get("expires_in")
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh or type(expires) not in (int, float) or not math.isfinite(expires) or expires <= 0:
            raise SpotifyUnavailable("Spotify returned an incomplete authorization. Connect again.")
        scopes = payload.get("scope")
        if scopes is not None and (not isinstance(scopes, str) or not set(PLAYBACK_SCOPES).issubset(scopes.split())):
            raise SpotifyUnavailable("Spotify did not grant the playback permissions. Connect again.")
        granted = scopes.split() if isinstance(scopes, str) else previous.get("scope", [])
        return {"client_id": fields["client_id"], "access_token": access, "refresh_token": refresh, "expires_at": time.time() + expires, "scope": granted}

    def accept_code(self, code: str, verifier: str, redirect: str) -> None:
        with self._locked():
            data = self._exchange({"grant_type": "authorization_code", "client_id": self.config.client_id, "code": code, "code_verifier": verifier, "redirect_uri": redirect}, {})
            self._save(data)

    def granted(self) -> frozenset[str]:
        """The scopes the saved login carries. A login saved before scopes
        were recorded is taken at its word for playback and nothing more."""
        with self._locked():
            data = self._read()
        scope = data.get("scope")
        if isinstance(scope, list) and all(isinstance(x, str) for x in scope):
            return frozenset(scope)
        return frozenset(PLAYBACK_SCOPES)

    def bearer(self) -> str:
        with self._locked():
            data = self._read()
            client_id = data.get("client_id")
            if not isinstance(client_id, str) or not client_id or (self.config.client_id and self.config.client_id != client_id):
                raise SpotifyUnavailable("Spotify's app changed. Connect again with that app.")
            expires = data.get("expires_at", 0)
            access = data.get("access_token")
            if isinstance(access, str) and access and type(expires) in (int, float) and time.time() < expires - 60:
                return access
            refresh = data.get("refresh_token")
            if not isinstance(refresh, str) or not refresh:
                raise SpotifyUnavailable("Spotify needs browser authorization again.")
            data = self._exchange({"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh}, data)
            self._save(data)
            return str(data["access_token"])


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize(config: SpotifyConfig, *, open_browser: Callable[[str], Any] = webbrowser.open, announce: Callable[[str], Any] = print, request: Callable[..., dict[str, Any]] = request_json) -> None:
    """An explicit human command; never offered as a model tool."""
    if not config.client_id.strip():
        raise SpotifyUnavailable("Set [spotify].client_id to your Spotify developer app's Client ID first.")
    if not 1 <= config.redirect_port <= 65535:
        raise SpotifyUnavailable("Spotify's redirect port must be between 1 and 65535.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect = f"http://127.0.0.1:{config.redirect_port}/callback"
    result: dict[str, str] = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            parts = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parts.query)
            valid = query.get("state") == [state]
            code = query.get("code", [])
            error = query.get("error", [])
            accepted = parts.path == "/callback" and valid and ((len(code) == 1 and not error) or (len(error) == 1 and not code))
            self.send_response(200 if accepted else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to Ciel's terminal to finish connecting." if accepted else b"This is not Ciel's pending Spotify login.")
            if accepted:
                result.update({"code": code[0]} if code else {"error": "Spotify authorization was declined."})

    with http.server.HTTPServer(("127.0.0.1", config.redirect_port), Callback) as server:
        server.timeout = 0.5
        # An idle local client must not hold the callback past the browser
        # deadline by opening a socket and sending no HTTP request.
        original_get_request = server.get_request

        def get_request() -> tuple[Any, Any]:
            connection, address = original_get_request()
            connection.settimeout(1.0)
            return connection, address

        server.get_request = get_request
        url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({"response_type": "code", "client_id": config.client_id, "redirect_uri": redirect, "scope": " ".join(SCOPES), "state": state, "code_challenge_method": "S256", "code_challenge": challenge})
        announce("Open this Spotify approval page in your browser:\n" + url)
        try:
            open_browser(url)
        except OSError:
            pass
        deadline = time.monotonic() + config.authorize_timeout_s
        while not result and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in result:
        raise SpotifyUnavailable(result.get("error", "Spotify authorization timed out."))
    OAuthTokens(config, request).accept_code(result["code"], verifier, redirect)
    announce("Spotify is connected. Set [spotify].enabled = true on the brain's host.")


def _item(item: dict[str, Any]) -> dict[str, Any]:
    return {"name": item.get("name"), "uri": item.get("uri"), "artists": [artist.get("name") for artist in item.get("artists", [])], "type": item.get("type")}


def _playlist(item: dict[str, Any]) -> dict[str, Any]:
    tracks = item.get("tracks") if isinstance(item.get("tracks"), dict) else {}
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    return {"name": item.get("name"), "uri": item.get("uri"), "id": item.get("id"), "public": item.get("public"),
            "collaborative": item.get("collaborative"), "owner": owner.get("display_name") or owner.get("id"),
            "items": tracks.get("total"), "description": item.get("description")}


def _playlist_id(playlist: str) -> str:
    """A playlist named by id or by URI; anything else is refused."""
    if not isinstance(playlist, str):
        raise ValueError("Name the playlist by its Spotify ID or URI, from spotify_playlists.")
    if playlist.startswith("spotify:playlist:"):
        playlist = playlist[len("spotify:playlist:"):]
    if not _ID.fullmatch(playlist):
        raise ValueError("Name the playlist by its Spotify ID or URI, from spotify_playlists.")
    return playlist


def _item_uris(uris: Any) -> list[str]:
    if not isinstance(uris, list) or not 1 <= len(uris) <= 100 or not all(isinstance(u, str) and _ITEM_URI.fullmatch(u) for u in uris):
        raise ValueError("Give one to a hundred Spotify track or episode URIs, from spotify_search or spotify_playlist_items.")
    return uris


class SpotifyClient:
    """Fixed endpoints and bounded arguments; no arbitrary authenticated URL."""

    def __init__(self, config: SpotifyConfig, *, tokens: Any | None = None, request: Callable[..., dict[str, Any]] = request_json) -> None:
        self.config = config
        self._tokens = tokens if tokens is not None else OAuthTokens(config, request)
        self._request = request
        self._lock = threading.Lock()
        self._retry_at = 0.0

    def _call(self, method: str, path: str, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if time.monotonic() < self._retry_at:
                raise SpotifyUnavailable("Spotify is rate limited. Wait before trying again.", 429)
            url = API_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
            try:
                bearer = self._tokens.bearer()
                return self._request(method, url, {"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"}, json.dumps(body).encode() if body is not None else None, self.config.timeout_s)
            except SpotifyUnavailable as exc:
                if exc.status == 429:
                    self._retry_at = time.monotonic() + max(1.0, exc.retry_after)
                raise

    def search(self, query: str, kind: str = "track", limit: int = 5) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("Give Spotify a search phrase of 1–500 characters.")
        if kind not in ("track", "album", "artist", "playlist"):
            raise ValueError("Search for a track, album, artist, or playlist.")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Spotify search returns between 1 and 10 results.")
        result = self._call("GET", "/search", {"q": query, "type": kind, "limit": limit})
        return {"items": [_item(item) for item in result.get(kind + "s", {}).get("items", []) if isinstance(item, dict)]}

    def status(self) -> dict[str, Any]:
        result = self._call("GET", "/me/player")
        if not result:
            return {"active": False, "message": "No playback is active. Open Spotify on a device."}
        item = result.get("item")
        return {"active": True, "is_playing": result.get("is_playing"), "progress_ms": result.get("progress_ms"), "device": result.get("device"), "item": _item(item) if isinstance(item, dict) else None}

    def devices(self) -> dict[str, Any]:
        return self._call("GET", "/me/player/devices")

    # ── playlists: the user's own, never anyone else's ────────────────────

    def _needs(self, scope: str) -> None:
        if scope not in self._tokens.granted():
            raise SpotifyUnavailable("Spotify's login predates playlist access. Run `uv run --no-sync python -m ciel.spotify authorize` again to grant it.")

    def playlists(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """The account's own playlists, a page at a time."""
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise ValueError("Playlists come in pages of 1 to 50, from a nonnegative offset.")
        self._needs("playlist-read-private")
        result = self._call("GET", "/me/playlists", {"limit": limit, "offset": offset})
        return {"items": [_playlist(p) for p in result.get("items", []) if isinstance(p, dict)], "total": result.get("total"), "offset": offset}

    def playlist_named(self, name: str) -> dict[str, Any]:
        """The account's playlist called ``name``, case-insensitively, or an
        honest miss. This is the call behind "play my playlist called X":
        the user's own library, never the public catalogue."""
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Give a playlist name of 1–200 characters.")
        wanted = name.strip().casefold()
        offset = 0
        for _ in range(20):  # a thousand playlists is enough to look through
            page = self.playlists(50, offset)
            for item in page["items"]:
                if str(item.get("name") or "").strip().casefold() == wanted:
                    return {"found": True, "playlist": item}
            offset += 50
            if not page["items"] or (page.get("total") or 0) <= offset:
                break
        return {"found": False, "message": f"No playlist of yours is called {name.strip()!r}."}

    def playlist_items(self, playlist: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """What is in one of the account's own playlists. Spotify shows the
        contents only of playlists the user owns or collaborates on."""
        playlist_id = _playlist_id(playlist)
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or offset < 0:
            raise ValueError("Playlist items come in pages of 1 to 50, from a nonnegative offset.")
        self._needs("playlist-read-private")
        try:
            result = self._call("GET", f"/playlists/{playlist_id}/items", {"limit": limit, "offset": offset})
        except SpotifyUnavailable as exc:
            if exc.status == 403:
                raise SpotifyUnavailable("Spotify shows the contents only of playlists you own or collaborate on; this one is not yours to read.", 403) from None
            raise
        items = []
        for entry in result.get("items", []):
            item = entry.get("item") if isinstance(entry, dict) else None
            if isinstance(item, dict):
                items.append({**_item(item), "added_at": entry.get("added_at")})
        return {"items": items, "total": result.get("total"), "offset": offset}

    def playlist_create(self, name: str, description: str = "", public: bool = False) -> dict[str, Any]:
        """A new playlist in the account — private unless asked, so a spoken
        request never publishes one to the profile."""
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Give the new playlist a name of 1–100 characters.")
        if not isinstance(description, str) or len(description) > 300:
            raise ValueError("A playlist description is at most 300 characters.")
        if type(public) is not bool:
            raise ValueError("public is true or false.")
        self._needs("playlist-modify-public" if public else "playlist-modify-private")
        result = self._call("POST", "/me/playlists", None, {"name": name.strip(), "description": description, "public": public})
        return {"created": _playlist(result), "message": "Spotify created the playlist. It is empty until items are added."}

    def playlist_add(self, playlist: str, uris: list[str], position: int | None = None) -> dict[str, Any]:
        """Items onto one of the account's playlists; the snapshot id names
        the version this made, which is what the journal keeps."""
        playlist_id = _playlist_id(playlist)
        items = _item_uris(uris)
        if position is not None and (type(position) is not int or position < 0):
            raise ValueError("position is a nonnegative index, or omitted to append.")
        self._needs("playlist-modify-private")
        body: dict[str, Any] = {"uris": items}
        if position is not None:
            body["position"] = position
        result = self._call("POST", f"/playlists/{playlist_id}/items", None, body)
        return {"added": len(items), "snapshot_id": result.get("snapshot_id"), "message": "Spotify accepted the addition. Read spotify_playlist_items to see the playlist as it is now."}

    def playlist_remove(self, playlist: str, uris: list[str]) -> dict[str, Any]:
        """Every occurrence of each item, off one of the account's playlists."""
        playlist_id = _playlist_id(playlist)
        items = _item_uris(uris)
        self._needs("playlist-modify-private")
        result = self._call("DELETE", f"/playlists/{playlist_id}/items", None, {"items": [{"uri": u} for u in items]})
        return {"removed": len(items), "snapshot_id": result.get("snapshot_id"), "message": "Spotify accepted the removal. Read spotify_playlist_items to see the playlist as it is now."}

    def control(self, action: str, uri: str = "", device_id: str = "", value: int | None = None) -> dict[str, Any]:
        if not isinstance(device_id, str) or len(device_id) > 256 or any(ord(c) < 32 for c in device_id):
            raise ValueError("Choose a device ID returned by spotify_devices.")
        if not isinstance(uri, str):
            raise ValueError("Choose a Spotify URI returned by spotify_search.")
        if uri and (action not in ("play", "queue") or not _URI.fullmatch(uri)):
            raise ValueError("Use a Spotify track, album, artist, or playlist URI only with play; queue accepts tracks.")
        params: dict[str, Any] = {"device_id": device_id} if device_id else {}
        body: dict[str, Any] | None = None
        method = "PUT"
        if value is not None and action not in ("volume", "seek"):
            raise ValueError("Only volume and seek take a numeric value.")
        if action == "play":
            path = "/me/player/play"
            if uri:
                body = {"uris": [uri]} if uri.startswith("spotify:track:") else {"context_uri": uri}
        elif action == "pause":
            path = "/me/player/pause"
        elif action in ("next", "previous"):
            method, path = "POST", "/me/player/" + action
        elif action in ("volume", "seek"):
            if type(value) is not int or not 0 <= value <= (100 if action == "volume" else 2_147_483_647):
                raise ValueError("Volume needs 0–100 percent; seek needs a nonnegative position in milliseconds.")
            path = "/me/player/" + action
            params["volume_percent" if action == "volume" else "position_ms"] = value
        elif action == "transfer":
            if not device_id:
                raise ValueError("Transfer needs a device ID from spotify_devices.")
            path, params, body = "/me/player", {}, {"device_ids": [device_id]}
        elif action == "queue":
            if not uri.startswith("spotify:track:") or not _URI.fullmatch(uri):
                raise ValueError("Queue needs one Spotify track URI.")
            method, path = "POST", "/me/player/queue"
            params["uri"] = uri
        else:
            raise ValueError("Choose play, pause, next, previous, volume, seek, transfer, or queue.")
        self._call(method, path, params, body)
        return {"accepted": action, "message": "Spotify accepted the request. Read spotify_status to observe playback; acceptance alone does not prove it changed."}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Connect Ciel to Spotify without an application secret.")
    parser.add_argument("command", choices=("authorize", "status"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-browser", action="store_true", help="Print the approval link for a browser on another machine.")
    args = parser.parse_args()
    config = load_config(args.config).spotify
    try:
        if args.command == "authorize":
            await asyncio.to_thread(authorize, config, open_browser=(lambda url: None) if args.no_browser else webbrowser.open)
        else:
            print(json.dumps(await asyncio.to_thread(SpotifyClient(config).status), ensure_ascii=False))
    except (SpotifyUnavailable, OSError) as exc:
        raise SystemExit(str(exc) if isinstance(exc, SpotifyUnavailable) else "Spotify could not open its local login or credential file.") from None


if __name__ == "__main__":
    asyncio.run(main())
