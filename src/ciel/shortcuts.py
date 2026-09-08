"""Three keys give the room a control that does not depend on hearing.

**The keyboard does not become a transcript.** A passive Mac event tap
matches physical key codes and modifiers to three configured actions. It
never reads characters, stores key events, or consumes another app's input.
Only action names cross to asyncio; holding a key fires once.

**The voice loop owns the room.** Native callbacks cannot touch its state.
A bounded queue serializes controls on the owning asyncio loop, even while
a greeting is playing. The hub never starts this listener. Disabled,
unsupported, denied, or invalid configurations leave voice operation intact.

**Ciel asks for its own permission.** Enabled shortcuts request missing Input
Monitoring from the native listener thread, once per listener start. A system
prompt never blocks the audio loop; a denial leaves the keyboard alone.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ciel.config import ShortcutsConfig

log = logging.getLogger(__name__)
_MODIFIERS = {"shift": 1 << 17, "ctrl": 1 << 18, "option": 1 << 19, "cmd": 1 << 20}
_ALIASES = {"control": "ctrl", "alt": "option", "command": "cmd", "esc": "escape"}
_MASK = sum(_MODIFIERS.values())
# These are physical ANSI positions, independent of the active input method.
_KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "space": 49, "escape": 53, "return": 36, "tab": 48,
}


@dataclass(frozen=True, slots=True)
class Chord:
    key: int
    modifiers: int


def parse_chord(value: str) -> Chord:
    parts = [_ALIASES.get(p.strip().lower(), p.strip().lower()) for p in value.split("+")]
    if len(parts) < 2 or parts[-1] not in _KEYS:
        raise ValueError("use modifiers plus a letter, digit, space, escape, return, or tab")
    mods = parts[:-1]
    if any(m not in _MODIFIERS for m in mods) or len(set(mods)) != len(mods):
        raise ValueError("modifiers are ctrl, option, cmd, and shift, each at most once")
    if not any(m in mods for m in ("ctrl", "option", "cmd")):
        raise ValueError("a global shortcut needs ctrl, option, or cmd")
    return Chord(_KEYS[parts[-1]], sum(_MODIFIERS[m] for m in mods))


class Matcher:
    def __init__(self, config: ShortcutsConfig) -> None:
        self.bindings = {parse_chord(getattr(config, action)): action
                         for action in ("talk", "stop", "mute")}
        if len(self.bindings) != 3:
            raise ValueError("talk, stop, and mute must use different shortcuts")
        self._held: set[int] = set()

    def feed(self, key: int, flags: int, *, down: bool, repeat: bool = False) -> str | None:
        if not down:
            self._held.discard(key)
            return None
        chord = Chord(key, flags & _MASK)
        action = self.bindings.get(chord)
        if action is None or repeat or key in self._held:
            return None
        self._held.add(key)
        return action

    def reset(self) -> None:
        self._held.clear()


class GlobalShortcuts:
    def __init__(self, config: ShortcutsConfig, handle: Callable[[str], Awaitable[None]]) -> None:
        self._config = config
        self._handle = handle
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._worker: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=16)

    async def start(self) -> None:
        if not self._config.enabled or self._thread is not None:
            return
        if sys.platform != "darwin":
            log.warning("global shortcuts require macOS")
            return
        try:
            matcher = Matcher(self._config)
            import Quartz
        except (ImportError, ValueError) as exc:
            log.warning("global shortcuts unavailable: %s", exc)
            return
        loop = asyncio.get_running_loop()
        self._stop.clear()
        self._worker = asyncio.create_task(self._consume())
        self._thread = threading.Thread(target=self._listen, args=(Quartz, matcher, loop),
                                        name="ciel-shortcuts", daemon=True)
        self._thread.start()

    def _enqueue(self, action: str) -> None:
        if not self._stop.is_set() and not self._queue.full():
            self._queue.put_nowait(action)

    async def _consume(self) -> None:
        while True:
            action = await self._queue.get()
            try:
                await self._handle(action)
            except Exception:
                log.exception("global shortcut failed: %s", action)

    def _listen(self, q: object, matcher: Matcher, loop: asyncio.AbstractEventLoop) -> None:
        tap = source = None
        try:
            if not q.CGPreflightListenEventAccess():
                log.info("requesting macOS Input Monitoring for global shortcuts")
                q.CGRequestListenEventAccess()
                if not q.CGPreflightListenEventAccess():
                    log.warning("global shortcuts are waiting for Input Monitoring; allow Ciel's Python in System Settings → Privacy & Security and restart Ciel")
                    return
            if self._stop.is_set():
                return

            def callback(proxy: object, kind: int, event: object, context: object) -> object:
                if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                    matcher.reset()
                    q.CGEventTapEnable(tap, True)
                    return event
                action = matcher.feed(
                    q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode),
                    q.CGEventGetFlags(event), down=kind == q.kCGEventKeyDown,
                    repeat=bool(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat)),
                )
                if action is not None and not self._stop.is_set():
                    loop.call_soon_threadsafe(self._enqueue, action)
                return event

            tap = q.CGEventTapCreate(q.kCGSessionEventTap, q.kCGHeadInsertEventTap,
                                     q.kCGEventTapOptionListenOnly,
                                     (1 << q.kCGEventKeyDown) | (1 << q.kCGEventKeyUp),
                                     callback, None)
            if tap is None:
                log.warning("global shortcuts could not open the Mac event tap; check Input Monitoring and restart Ciel")
                return
            source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
            runloop = q.CFRunLoopGetCurrent()
            q.CFRunLoopAddSource(runloop, source, q.kCFRunLoopDefaultMode)
            q.CGEventTapEnable(tap, True)
            log.info("global shortcuts ready")
            while not self._stop.is_set():
                q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.2, False)
        except Exception:
            log.exception("global shortcuts listener stopped")
        finally:
            if tap is not None:
                q.CGEventTapEnable(tap, False)
                q.CFMachPortInvalidate(tap)
            if source is not None:
                q.CFRunLoopRemoveSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopDefaultMode)

    async def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join)
            self._thread = None
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        while not self._queue.empty():
            self._queue.get_nowait()
