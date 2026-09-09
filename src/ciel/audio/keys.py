"""The keyboard's own word — a keystroke is not a snap because the Mac
knows a key was pressed, and a click is not one because it knows a button
was.

Part of the wake boundary (codename **Characteristic**), the third opinion
the gesture ear can take beside its rules and AudioSet's veto.

Why it exists: by shape and loudness a mechanical key is a snap, a lone
key in a quiet second does not sound like *typing* to a model trained on
runs of it, and a snap's solitude gate cannot see one key. A mouse button
is worse: as quiet and as bright as a snap across the room, always
solitary, and nothing a model was trained to name. Every acoustic route
ends there. But the sound of a key is made by a key, and macOS keeps, for
every process in the session, the time since the last key went down, up, or
changed modifier state, and the time since the last mouse button did — no
event tap, no keystroke
monitoring, no permission dialog. Asked at the moment the ear judges an
impulse, that number is a verdict.

Invariants:

- **Timestamps only.** The question asked of macOS is "how long since a
  key event" or "how long since a button event", never which key or
  where. Nothing here can see what was typed or clicked.
- **Only a veto.** A keystroke or a click inside ``keyboard_veto_ms``
  turns a snap or a clap the rules accepted into a rejection that names
  it; it never finds a gesture, and it never fires on silence.
- **Inert elsewhere.** Without Quartz — off the Mac, or in a probe — the
  veto answers None, so the ear behaves as if it were not there.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SinceKey = Callable[[], float]
"""Seconds since the last event of one kind — a key, or a mouse button —
whichever of its variants (down, up, modifier change) is more recent."""

# Modifier presses and releases have their own event type; watching only
# ordinary key-down/up leaves Shift and Command clicks outside the veto.
_KEY_EVENTS = ("kCGEventKeyDown", "kCGEventKeyUp", "kCGEventFlagsChanged")
_BUTTON_EVENTS = (
    "kCGEventLeftMouseDown", "kCGEventLeftMouseUp",
    "kCGEventRightMouseDown", "kCGEventRightMouseUp",
    "kCGEventOtherMouseDown", "kCGEventOtherMouseUp",
)


def _quartz_since(*events: str) -> SinceKey | None:
    try:
        import Quartz
    except ImportError:
        return None
    state = Quartz.kCGEventSourceStateCombinedSessionState
    kinds = [getattr(Quartz, name) for name in events]
    ask = Quartz.CGEventSourceSecondsSinceLastEventType

    def since() -> float:
        return min(float(ask(state, kind)) for kind in kinds)

    return since


def _quartz_since_key() -> SinceKey | None:
    return _quartz_since(*_KEY_EVENTS)


def _quartz_since_click() -> SinceKey | None:
    return _quartz_since(*_BUTTON_EVENTS)


class KeyboardVeto:
    """Callable on the ear's context window (unused): the keystroke or the
    click it coincided with, or None."""

    def __init__(
        self,
        window_ms: int,
        since_key: SinceKey | None = None,
        since_click: SinceKey | None = None,
        *,
        log_candidates: bool = False,
    ) -> None:
        self._window_s = window_ms / 1000
        self._log_candidates = log_candidates
        # A probe that supplies one clock gets no other: the Mac's real
        # mouse must not answer for a fixture.
        if since_key is None and since_click is None:
            since_key, since_click = _quartz_since_key(), _quartz_since_click()
        self._asked = [
            (name, ask) for name, ask in (("a keystroke", since_key), ("a click", since_click))
            if ask is not None
        ]
        if not self._asked:
            log.info("keyboard veto off — no Quartz on this host")

    @property
    def available(self) -> bool:
        return bool(self._asked)

    def __call__(self, window: np.ndarray) -> str | None:  # noqa: ARG002 - the audio is irrelevant here
        nearest: tuple[str, float] | None = None
        ages: list[str] = []
        for name, ask in self._asked:
            ago = ask()
            if self._log_candidates:
                ages.append(f"{name} {ago * 1000:.0f} ms ago")
            if 0 <= ago < self._window_s and (nearest is None or ago < nearest[1]):
                nearest = (name, ago)
        if self._log_candidates:
            log.info("gesture input timing: %s; veto window %.0f ms", "; ".join(ages), self._window_s * 1000)
        if nearest is None:
            return None
        return f"{nearest[0]} {nearest[1] * 1000:.0f} ms ago"


__all__ = ["KeyboardVeto", "SinceKey"]
