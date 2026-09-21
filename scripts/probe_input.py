#!/usr/bin/env python
"""Probe the microphone stream's judgement without a microphone.

    uv run scripts/probe_input.py

Pins the silence watch: a room's quiet is not silence (any non-zero sample
keeps it quiet), pure zeros say so once and only after the window, the
first real sample says the room is back, a second stretch of zeros is
reported again, and the window is counted in frames so the judgement is
the same at any wall-clock pace.

Pins the shut ear, against a bench device that counts its openings: a pause
closes the device and frames() goes on at the frame pace with silence, so
the loop it drives keeps its clock; what the room said before the pause is
not handed over after it; a resume opens the device again and the room
comes back; pausing twice closes once; a microphone held before it was
entered never opens until it is resumed; a resume that fails leaves the
ear shut and still ticking, says why, and can be tried again; and leaving
the context ends frames() even from a shut ear.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.audio.input import MicStream, SilenceWatch
from ciel.config import FRAME_BYTES, FRAME_SAMPLES, AudioConfig

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


ZERO = b"\x00" * FRAME_BYTES
HISS = (np.random.default_rng(1).integers(-3, 4, FRAME_SAMPLES)).astype("<i2").tobytes()
ONE = (np.array([0] * (FRAME_SAMPLES - 1) + [1])).astype("<i2").tobytes()


class BenchMic(MicStream):
    """The base stream's queue and clock over a device that only counts."""

    _warn_on_digital_silence = False

    def __init__(self) -> None:
        super().__init__(AudioConfig())
        self.opens = self.closes = 0
        self.is_open = False
        self.refuse: str | None = None

    async def __aenter__(self):
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        self._closed = False
        if self._held():
            return self
        if self.refuse:
            await self.close()
            raise RuntimeError(self.refuse)
        self.opens += 1
        self.is_open = True
        return self

    async def close(self) -> None:
        if self.is_open:
            self.closes += 1
        self.is_open = False
        await super().close()

    def hear(self, frame: bytes) -> None:
        if self.is_open:
            self._on_audio(frame, FRAME_SAMPLES, None, None)


async def take(frames, n: int) -> list[bytes]:
    return [await asyncio.wait_for(anext(frames), 2.0) for _ in range(n)]


async def probe_shut_ear() -> None:
    print("\nthe shut ear")
    mic = BenchMic()
    async with mic:
        frames = mic.frames()
        mic.hear(ONE)
        check("an open ear hands over the room", await take(frames, 1) == [ONE] and mic.opens == 1)
        mic.hear(ONE)  # said, not yet handed over, when the switch is thrown
        await mic.pause()
        check("a pause closes the device", mic.paused and not mic.is_open and mic.closes == 1)
        mic.hear(ONE)  # a closed device delivers nothing; the bench agrees
        started = asyncio.get_running_loop().time()
        got = await take(frames, 5)
        took = asyncio.get_running_loop().time() - started
        check("a shut ear goes on handing over frames, and they are silence", got == [ZERO] * 5)
        check("...at the frame pace, so the loop keeps its clock", 0.1 <= took < 1.0)
        await mic.pause()
        check("pausing twice closes once", mic.closes == 1)
        await mic.resume()
        mic.hear(HISS)
        check("a resume opens the device again and the room comes back",
              not mic.paused and mic.is_open and mic.opens == 2 and await take(frames, 1) == [HISS])
        await mic.pause()
        mic.refuse = "no such device"
        try:
            await mic.resume()
            said = ""
        except RuntimeError as exc:
            said = str(exc)
        check("a resume that fails says why and leaves the ear shut", said == "no such device" and mic.paused and not mic.is_open)
        check("...and still ticking", await take(frames, 2) == [ZERO] * 2)
        mic.refuse = None
        await mic.resume()
        mic.hear(ONE)
        check("...and can be tried again", not mic.paused and await take(frames, 1) == [ONE])
        await mic.pause()
    ended = False
    try:
        await asyncio.wait_for(anext(frames), 2.0)
    except StopAsyncIteration:
        ended = True
    check("leaving the context ends frames() even from a shut ear", ended)

    held = BenchMic()
    held.hold()
    async with held:
        frames = held.frames()
        check("a microphone held before it was entered never opens", held.opens == 0 and await take(frames, 2) == [ZERO] * 2)
        await held.resume()
        held.hear(HISS)
        check("...until it is resumed", held.opens == 1 and await take(frames, 1) == [HISS])


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
    asyncio.run(probe_shut_ear())
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
