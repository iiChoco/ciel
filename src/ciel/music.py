"""Music on the Mac — Spotify through its AppleScript surface.

Why it exists: the shell guard denies ``osascript`` outright, and rightly —
scripting other applications erases every boundary the tool configs draw.
So there was no way for Ciel to start music at all, and a hand gesture
that starts music (two claps, see ``audio/gestures.py``) needs a door that
is narrow enough to leave the guard's verdict intact: one application, one
verb, one argument that is checked before it is quoted.

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


async def play(uri: str, run: Runner = _osascript) -> str:
    """Start Spotify playing ``uri``; the sentence says what happened.

    The URI is passed as an argument to the script, never spliced into it,
    and only after :func:`valid_uri` has agreed it is one."""
    if not valid_uri(uri):
        log.warning("music: refused to play %r — not a Spotify URI", uri)
        return "That is not a Spotify URI."
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


__all__ = ["valid_uri", "play", "TIMEOUT_S", "Runner"]
