"""Reproduce the findings in reports/2026-09-07-apple-audio-review.md.

1. A synthesis failure closes the Apple microphone: ApplePlayer treats any
   RuntimeError from its chunk source as a device failure and ends the session.
2. The native capture ring drops a tap buffer silently when the worker holds the
   lock; the ring's own overflow flag stays false. Compiled from the ring class
   alone, no engine or device.
3. Playback keeps at most 100 ms scheduled once the first receipt lands, so a
   Python stall longer than that is an audible gap (arithmetic only).

All helper builds and fixtures live in temporary storage. No microphone, model,
device, or configuration under ~/.ciel is touched.

    uv run --no-sync python reports/2026-09-07-apple-audio-review-repro.py
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import AsyncIterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from probe_apple_audio import opened  # noqa: E402
from ciel.audio import apple  # noqa: E402
from ciel.audio.output import CHUNK_MS  # noqa: E402


async def synthesis_failure(tmp: Path) -> bool:
    print("1. a synthesis error is reported as a lost device and closes capture")
    mic, player = await opened(tmp)
    try:
        async def broken_engine() -> AsyncIterator[bytes]:
            yield bytes(1600)
            raise RuntimeError("native voice helper exited")  # a TTS error, from ciel/tts/native.py

        completed = await player.play(broken_engine())
        print(f"   play() -> {completed}, device_lost={player.device_lost}")
        print(f"   microphone closed={mic._closed}, session error={mic.session.error!r}")
        reproduced = (not completed) and player.device_lost and mic._closed and mic.session.error is not None
        print("   reproduced: the microphone is gone although the device never failed" if reproduced else "   not reproduced")
        return reproduced
    finally:
        await player.close()
        await mic.close()


def ring_drop(tmp: Path) -> bool:
    print("2. the capture ring drops a buffer without raising overflow when the lock is busy")
    source = apple.SOURCE.read_text()
    ring = source[source.index("final class CaptureRing"):source.index("final class CaptureConverter")]
    test = tmp / "ring.swift"
    test.write_text(
        "import AVFoundation\nimport Foundation\n"
        "func fail(_ message: String) -> Never { print(message); exit(3) }\n"
        + ring +
        "let format = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1)!\n"
        "let ring = CaptureRing(format)\n"
        "let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!\n"
        "buffer.frameLength = 480\n"
        "ring.lock.lock()      // the worker is inside pop(), copying a slot out\n"
        "ring.push(buffer)     // the render tap fires with 10 ms of microphone\n"
        "ring.lock.unlock()\n"
        "print(\"   after push under contention: count=\\(ring.count) overflow=\\(ring.overflow)\")\n"
        "exit(ring.count == 0 && !ring.overflow ? 0 : 2)\n"
    )
    compiler = shutil.which("swiftc")
    if compiler is None:
        print("   swiftc unavailable; skipped")
        return False
    binary = tmp / "ring"
    build = subprocess.run([compiler, "-O", "-framework", "AVFoundation", "-o", str(binary), str(test)],
                           capture_output=True, text=True, timeout=120)
    if build.returncode:
        print("   build failed:", build.stderr[-500:])
        return False
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    print(run.stdout.rstrip())
    reproduced = run.returncode == 0
    print("   reproduced: 10 ms of speech vanished and nothing was reported" if reproduced else "   not reproduced")
    return reproduced


def scheduling_slack() -> bool:
    print("3. how far ahead of the speaker the Apple player runs")
    outstanding = 3  # apple.py: `if len(waiting) >= 3` before the next submit
    print(f"   chunk = {CHUNK_MS} ms, at most {outstanding} outstanding")
    print(f"   when chunk n's receipt arrives, {(outstanding - 1) * CHUNK_MS} ms is still scheduled;")
    print(f"   chunk n+{outstanding} is submitted only after Python wakes, pulls TTS, writes the pipe,")
    print("   and the helper's main queue schedules it. A stall past that budget is silence.")
    return True


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-apple-review-") as directory:
        tmp = Path(directory)
        results = [await synthesis_failure(tmp), ring_drop(tmp), scheduling_slack()]
    print(f"\n{sum(results)} of {len(results)} findings reproduced")


if __name__ == "__main__":
    asyncio.run(main())
