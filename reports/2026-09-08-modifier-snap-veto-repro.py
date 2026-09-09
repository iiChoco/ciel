"""Reproduce omitted modifier events with fake Quartz and synthetic audio only."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from ciel.audio.keys import KeyboardVeto, _quartz_since
from ciel.audio.gestures import GestureDetector
from probe_gestures import feed, gestures, recording


def main() -> None:
    q = SimpleNamespace(kCGEventSourceStateCombinedSessionState=0, kCGEventKeyDown=10,
                        kCGEventKeyUp=11, kCGEventFlagsChanged=12,
                        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2,
                        kCGEventRightMouseDown=3, kCGEventRightMouseUp=4,
                        kCGEventOtherMouseDown=25, kCGEventOtherMouseUp=26)
    ages = {10: 5.0, 11: 5.0, 12: .04}
    q.CGEventSourceSecondsSinceLastEventType = lambda state, kind: ages.get(kind, 5.0)
    audio = recording([(1, "snap")])
    with patch.dict(sys.modules, {"Quartz": q}):
        old = KeyboardVeto(300, since_key=_quartz_since("kCGEventKeyDown", "kCGEventKeyUp"))
        fixed = KeyboardVeto(300)
        assert gestures(feed(audio, GestureDetector(veto=old))) == ["snap"]
        print("ok: former event list lets a modifier click become a snap")
        assert gestures(feed(audio, GestureDetector(veto=fixed))) == []
        print("ok: modifier-aware veto rejects the same synthetic sound")
        ages[12] = .3
        assert gestures(feed(audio, GestureDetector(veto=fixed))) == ["snap"]
        print("ok: a deliberate snap is eligible once the window expires")
    print("all 3 checks passed")


if __name__ == "__main__":
    main()
