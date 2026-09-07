"""Music on the Mac — Spotify through its AppleScript surface.

Why it exists: the shell guard denies ``osascript`` outright, and rightly —
scripting other applications erases every boundary the tool configs draw.
So there was no way for Ciel to start music at all, and a hand gesture
that starts music (two claps, see ``audio/gestures.py``) needs a door that
is narrow enough to leave the guard's verdict intact: one application, one
verb, one argument that is checked before it is quoted.

Two doors, one order. When the Spotify connector (``spotify.py``) is
connected on this Mac, the gesture goes through the Web API first, which
plays on whichever device is active — the phone in the kitchen included —
and falls back to the desktop app's AppleScript surface when the API has
no player to talk to or no login to talk with. Either way one URI, checked
first, is all that crosses.

Invariants:

- **A URI, never a command.** The only thing that reaches AppleScript is a
  Spotify URI matching :data:`_URI`, so nothing typed into config can
  become script. Anything else is refused before a process is started.
- **Reversible and visible.** Playing is undone by pausing, and every
  start is logged with what it played and how the request went. Nothing
  here asks the confirmation broker: the gesture that starts music is
  itself two deliberate hits, mute gates it, and a wrong song costs one
  tap. The journal lives with the brain, not in the spoke, so the log
  line is this module's record.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

WebPlayer = Callable[[str], str]
"""Plays a URI through Spotify's Web API on the active device, returning a
sentence, or raises ``SpotifyUnavailable`` with the reason it could not."""

_URI = re.compile(r"^spotify:(?:artist|album|playlist|track|show|episode):[A-Za-z0-9]{22}$")
"""What Spotify's "Copy Spotify URI" produces: a kind and a 22-character id."""

TIMEOUT_S = 20.0
"""A first ``osascript`` call often blocks on macOS's automation-permission
dialog, which needs longer than a normal call but must not hang the room."""

Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]
"""How the script is run: argv in, (exit status, combined output) out. Real
runs use :func:`_osascript`; probes hand in a fake so no application is
scripted."""


def valid_uri(uri: str) -> bool:
    return bool(_URI.match(uri))


async def _osascript(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "osascript timed out"
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


def web_player(spotify) -> WebPlayer | None:  # noqa: ANN001 - SpotifyConfig
    """The Web API door, when the connector is switched on with an app.

    Whether a login exists is checked at play time, not here, so a
    connector authorized after the spoke started is used without a
    restart; until then the API raises and the desktop app answers."""
    if spotify is None or not spotify.enabled or not spotify.client_id:
        return None
    from ciel.spotify import SpotifyClient

    client = SpotifyClient(spotify)

    def play_on_active_device(uri: str) -> str:
        client.control("play", uri=uri)
        status = client.status()
        device = (status.get("device") or {}).get("name") if status.get("active") else None
        return f"playing on {device}" if device else "playing"

    return play_on_active_device


async def play(uri: str, run: Runner = _osascript, api: WebPlayer | None = None) -> str:
    """Start Spotify playing ``uri``; the sentence says what happened.

    The URI is passed as an argument to the script, never spliced into it,
    and only after :func:`valid_uri` has agreed it is one. With ``api``
    the Web API is tried first and the desktop app is the fallback."""
    if not valid_uri(uri):
        log.warning("music: refused to play %r — not a Spotify URI", uri)
        return "That is not a Spotify URI."
    if api is not None:
        from ciel.spotify import SpotifyUnavailable

        try:
            said = await asyncio.to_thread(api, uri)
        except SpotifyUnavailable as exc:
            log.info("music: the Web API could not play %s (%s) — trying the desktop app", uri, exc)
        else:
            log.info("music: playing %s through the Web API (%s)", uri, said)
            return f"Spotify Connect: {said}"
    script = (
        'on run argv\n'
        'tell application "Spotify"\n'
        '  play track (item 1 of argv)\n'
        '  delay 0.5\n'
        '  try\n'
        '    return (artist of current track) & " — " & (name of current track)\n'
        '  on error\n'
        '    return "playing"\n'
        '  end try\n'
        'end tell\n'
        'end run'
    )
    status, out = await run(["osascript", "-e", script, uri])
    if status != 0:
        log.warning("music: Spotify would not play %s: %s", uri, out or "no message")
        return f"Spotify would not play: {out or 'no message'}"
    log.info("music: playing %s (%s)", uri, out)
    return out


__all__ = ["valid_uri", "play", "web_player", "TIMEOUT_S", "Runner", "WebPlayer"]
