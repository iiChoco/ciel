"""The keyboard's own word — a keystroke is not a snap because the Mac
knows a key was pressed.

Part of the wake boundary (codename **Characteristic**), the third opinion
the gesture ear can take beside its rules and AudioSet's veto.

Why it exists: by shape and loudness a mechanical key is a snap, a lone
key in a quiet second does not sound like *typing* to a model trained on
runs of it, and a snap's solitude gate cannot see one key. Every acoustic
route ends there. But the sound of a key is made by a key, and macOS
keeps, for every process in the session, the time since the last key went
down or up — no event tap, no keystroke monitoring, no permission dialog.
Asked at the moment the ear judges an impulse, that number is a verdict.

Invariants:

- **Timestamps only.** The question asked of macOS is "how long since a
  key event", never which key. Nothing here can see what was typed.
- **Only a veto.** A keystroke inside ``keyboard_veto_ms`` turns a snap or
  a clap the rules accepted into a rejection that says so; it never finds
  a gesture, and it never fires on silence.
- **Inert elsewhere.** Without Quartz — off the Mac, or in a probe — the
  veto answers None, so the ear behaves as if it were not there.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SinceKey = Callable[[], float]
"""Seconds since the last key went down or up, whichever is more recent."""


def _quartz_since_key() -> SinceKey | None:
    try:
        import Quartz
    except ImportError:
        return None
    state = Quartz.kCGEventSourceStateCombinedSessionState
    down, up = Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp
    ask = Quartz.CGEventSourceSecondsSinceLastEventType

    def since_key() -> float:
        return min(float(ask(state, down)), float(ask(state, up)))

    return since_key


class KeyboardVeto:
    """Callable on the ear's context window (unused): the keystroke it
    coincided with, or None."""

    def __init__(self, window_ms: int, since_key: SinceKey | None = None) -> None:
        self._window_s = window_ms / 1000
        self._since_key = since_key if since_key is not None else _quartz_since_key()
        if self._since_key is None:
            log.info("keyboard veto off — no Quartz on this host")

    @property
    def available(self) -> bool:
        return self._since_key is not None

    def __call__(self, window: np.ndarray) -> str | None:  # noqa: ARG002 - the audio is irrelevant here
        if self._since_key is None:
            return None
        ago = self._since_key()
        if 0 <= ago < self._window_s:
            return f"a keystroke {ago * 1000:.0f} ms ago"
        return None


__all__ = ["KeyboardVeto", "SinceKey"]
