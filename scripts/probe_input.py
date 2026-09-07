#!/usr/bin/env python
"""Probe the microphone stream's judgement without a microphone.

    uv run scripts/probe_input.py

Pins the silence watch: a room's quiet is not silence (any non-zero sample
keeps it quiet), pure zeros say so once and only after the window, the
first real sample says the room is back, a second stretch of zeros is
reported again, and the window is counted in frames so the judgement is
the same at any wall-clock pace.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.audio.input import SilenceWatch
from ciel.config import FRAME_BYTES, FRAME_SAMPLES

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


ZERO = b"\x00" * FRAME_BYTES
HISS = (np.random.default_rng(1).integers(-3, 4, FRAME_SAMPLES)).astype("<i2").tobytes()
ONE = (np.array([0] * (FRAME_SAMPLES - 1) + [1])).astype("<i2").tobytes()


def main() -> None:
    w = SilenceWatch(window_s=0.3)  # ten frames
    said = [w.push(HISS) for _ in range(100)]
    check("a quiet room's hiss is not silence", not any(said) and not w.silent)
    w = SilenceWatch(window_s=0.3)
    said = [w.push(ZERO) for _ in range(9)]
    check("nine frames of zeros say nothing yet", not any(said) and not w.silent)
    tenth = w.push(ZERO)
    check("the tenth frame of zeros says so, once", tenth is not None and "pure silence" in tenth and w.silent)
    check("...and names the permission a deaf process is missing", "Privacy" in tenth)
    check("...and the time in seconds", "0s" in tenth)
    more = [w.push(ZERO) for _ in range(50)]
    check("further silence is not repeated", not any(more))
    back = w.push(ONE)
    check("a single non-zero sample brings the room back", back == "microphone hears the room again" and not w.silent)
    again = [w.push(ZERO) for _ in range(10)]
    check("a second stretch of zeros is reported again", again[-1] is not None and not any(again[:-1]))
    w = SilenceWatch()
    frames = 0
    while w.push(ZERO) is None:
        frames += 1
    check("the default window is five seconds of frames", frames + 1 == round(5.0 / 0.03))
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
