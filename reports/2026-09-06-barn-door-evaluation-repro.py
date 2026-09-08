"""Show the difference between a calibration score and the runtime gate.

Synthetic unit vectors isolate duration policy; they are not recorded speakers
and cannot estimate real false accepts or false rejects. All state is temporary.
Run: uv run --no-sync python reports/2026-09-06-barn-door-evaluation-repro.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

import numpy as np

from ciel.audio.speaker import SpeakerGate, save_profile
from ciel.config import SAMPLE_RATE, VoiceConfig


class Encoder:
    async def warm_up(self) -> None:
        pass

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        return np.array([0.6, 0.8], dtype=np.float32)

    async def close(self) -> None:
        pass


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="barn-door-policy-") as directory:
        profile = Path(directory) / "profile.npz"
        save_profile(profile, [np.array([1.0, 0.0], dtype=np.float32)], threshold=0.70)
        gate = SpeakerGate(VoiceConfig(enabled=True, profile=profile,
                                      model=Path(directory) / "unused.onnx", keep_rejected=0), Encoder())
        await gate.warm_up()
        long_ok, long_score = await gate.check(np.zeros(3 * SAMPLE_RATE, dtype=np.float32))
        short_ok, short_score = await gate.check(np.zeros(int(0.6 * SAMPLE_RATE), dtype=np.float32))
        assert not long_ok and short_ok
        assert abs(long_score - short_score) < 1e-6
        print(f"same synthetic score {long_score:.2f}: rejected at 3 s, accepted at 0.6 s")
        print("A score below the stored threshold can pass the runtime's short-utterance policy.")
        await gate.close()


if __name__ == "__main__":
    asyncio.run(main())
