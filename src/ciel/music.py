"""Music on the Mac — Spotify through its AppleScript surface.

Why it exists: the shell guard denies ``osascript`` outright, and rightly —
scripting other applications erases every boundary the tool configs draw.
So there was no way for Ciel to start music at all, and a hand gesture
that starts music (two claps, see ``audio/gestures.py``) needs a door that
is narrow enough to leave the guard's verdict intact: one application, one
verb, one argument that is checked before it is quoted.

Two doors, one order. When the Spotify connector (``spotify.py``) is
connected on this Mac, the gesture goes through the Web API first, which
plays on whichever device is active — the phone in the kitchen included.
With no active device the API is asked again, aimed at this Mac by its
Connect device id, which starts the desktop app's player without touching
its window; and if the app is not running at all it is launched hidden and
in the background first. Only when the API has no login to talk with does
the AppleScript surface answer. Either way one URI, checked first, is all
that crosses, and nothing here ever brings Spotify to the front: a
gesture that steals the screen is worse than no gesture.

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

BUNDLE_ID = "com.spotify.client"
"""How the desktop app is addressed, on disk and in AppleScript: an app
found by name breaks the day its bundle is renamed; an identifier does not."""

LAUNCH_WAIT_S = 8.0
"""How long a freshly launched desktop app is given to appear among the
account's Connect devices before the API path gives up."""


def _running() -> bool:
    import subprocess

    return subprocess.run(["pgrep", "-x", "Spotify"], capture_output=True, timeout=5).returncode == 0


def _front_bundle_id() -> str:
    """The bundle identifier of whatever the user is looking at."""
    import re
    import subprocess

    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "bundleid", asn], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001 - a courtesy, never a failure
        return ""
    m = re.search(r'"CFBundleIdentifier"="([^"]+)"', info)
    return m.group(1) if m else ""


def launch_hidden(wait: "Callable[[float], None] | None" = None) -> None:
    """Start the desktop app and give the screen straight back.

    Spotify ignores a hidden, background launch (``open -g -j``) and
    activates itself, so launching it at all puts it in front of what
    the user was doing. The remedy is to remember what that was and
    hand the front back the moment Spotify is up — a flash, not a
    switch. An app already running is left alone: nothing here ever
    activates Spotify on purpose. By bundle identifier throughout: the
    bundle on disk may be called anything ("Spotify (old).app" after an
    update), and LaunchServices finds it either way."""
    import subprocess
    import time as _time

    sleep = wait or _time.sleep
    if _running():
        return
    front = _front_bundle_id()
    subprocess.run(["open", "-g", "-j", "-b", BUNDLE_ID], check=False, capture_output=True, timeout=10)
    deadline = _time.monotonic() + LAUNCH_WAIT_S
    while not _running() and _time.monotonic() < deadline:
        sleep(0.25)
    if front and front != BUNDLE_ID:
        # Spotify takes the front a beat after its process appears; give
        # it back once, then once more for good measure.
        for _ in range(2):
            sleep(0.75)
            subprocess.run(["open", "-b", front], check=False, capture_output=True, timeout=10)


def _this_mac() -> str:
    try:
        import subprocess

        return subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001 - a name is a preference, not a requirement
        return ""

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

    return _web_player(SpotifyClient(spotify))


def _web_player(client, launch: Callable[[], None] = launch_hidden, wait: Callable[[float], None] | None = None) -> WebPlayer:  # noqa: ANN001 - SpotifyClient, or a probe's fake
    """The API door over a client; ``launch`` and ``wait`` are the probe's seams."""
    import time as _time

    from ciel.spotify import SpotifyUnavailable

    sleep = wait or _time.sleep

    def this_mac_device() -> dict | None:
        """This Mac among the account's Connect devices, by name, else any computer."""
        devices = [d for d in client.devices().get("devices", []) if isinstance(d, dict) and d.get("id")]
        mine = _this_mac()
        for d in devices:
            if mine and str(d.get("name", "")).startswith(mine):
                return d
        for d in devices:
            if d.get("type") == "Computer":
                return d
        return None

    def said(fallback: str) -> str:
        status = client.status()
        device = (status.get("device") or {}).get("name") if status.get("active") else None
        return f"playing on {device}" if device else fallback

    def play(uri: str) -> str:
        try:
            client.control("play", uri=uri)
            return said("playing")
        except SpotifyUnavailable as exc:
            if exc.status != 404:
                raise
        # No active device. Aim at this Mac by id — that starts the desktop
        # app's player without touching its window — launching the app
        # hidden first if it is not among the devices yet.
        device = this_mac_device()
        if device is None:
            launch()
            deadline = _time.monotonic() + LAUNCH_WAIT_S
            while device is None and _time.monotonic() < deadline:
                sleep(1.0)
                device = this_mac_device()
        if device is None:
            raise SpotifyUnavailable("No Spotify device is available, and the desktop app did not appear.", 404)
        client.control("play", uri=uri, device_id=str(device["id"]))
        return said(f"playing on {device.get('name') or 'this Mac'}")

    return play


async def play(uri: str, run: Runner = _osascript, api: WebPlayer | None = None, launch: Callable[[], None] = launch_hidden) -> str:
    """Start Spotify playing ``uri``; the sentence says what happened.

    The URI is passed as an argument to the script, never spliced into it,
    and only after :func:`valid_uri` has agreed it is one. With ``api``
    the Web API is tried first and the desktop app is the fallback."""
    if not valid_uri(uri):
        log.warning("music: refused to play %r — not a Spotify URI", uri)
        return "That is not a Spotify URI."
    # Whatever door answers, the app is launched behind the screen, never in front.
    await asyncio.to_thread(launch)
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
        f'tell application id "{BUNDLE_ID}"\n'
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


__all__ = ["valid_uri", "play", "web_player", "launch_hidden", "TIMEOUT_S", "Runner", "WebPlayer"]
