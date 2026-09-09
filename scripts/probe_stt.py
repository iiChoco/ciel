"""Probe the STT filter layer — the speech gate, hallucinations, and repetition loops.

    uv run scripts/probe_stt.py

All scripted, no microphone, no download: the text filters are pure
functions shared by both Whisper engines, so a regression shows up here in
milliseconds instead of as a wall of "clear" in a live conversation
(observed 2026-08-20 — that exact failure is a fixture below).

The speech gate half pins that an utterance the model hears no speech in
never reaches the engine and comes back as the empty transcript every
caller already reads as silence; that one it hears speech in reaches the
engine untouched; that a peak exactly at the threshold passes; that the
gate is off at zero and on by default, naming the engine it stands in
front of; that warming and closing the gate warm and close the engine;
that without the model it fails open and says so; and, with the Silero
weights openWakeWord ships, that a second and a half of silence and two
seconds of room noise never reach the threshold. Real speech is not
synthesized here; the calibration behind the threshold is in
``reports/2026-09-08-sentences-nobody-said.md``.
"""

import asyncio
import sys
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from ciel.config import SAMPLE_RATE, STTConfig
from ciel.stt.gate import GatedSTT, SpeechGate, gate_stt
from ciel.stt.hallucinations import (
    collapse_repetition,
    looks_hallucinated,
    normalize,
)

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def hallucination_checks() -> None:
    print("stock hallucinations:")
    check("empty is hallucinated", looks_hallucinated(""))
    check("pure punctuation is hallucinated", looks_hallucinated("..."))
    check("'Thank you.' is hallucinated", looks_hallucinated("Thank you."))
    check("a real sentence survives",
          not looks_hallucinated("Thank you for fixing the door."))
    echo = frozenset({normalize("A spoken conversation with Ciel.")})
    check("a prompt echo is hallucinated",
          looks_hallucinated("A spoken conversation with Ciel.", echo))
    check("a doubled prompt echo is hallucinated",
          looks_hallucinated(
              "A spoken conversation with Ciel. A spoken conversation with Ciel.",
              echo,
          ))


def loop_checks() -> None:
    print("repetition loops:")
    wall = "clear " * 300

    # The observed failure: real speech, then the wall.
    observed = "I'm going to use the same way to make a " + wall
    collapsed = collapse_repetition(observed)
    check("the 2026-08-20 wall collapses",
          collapsed == "I'm going to use the same way to make a clear clear")
    check("a head-and-wall transcript is not dropped whole",
          not looks_hallucinated(observed))

    # A wall with no head is noise, dropped entirely.
    check("a pure wall is hallucinated", looks_hallucinated(wall))
    check("a pure phrase wall is hallucinated",
          looks_hallucinated("I'm sorry. " * 40))

    # Multi-word loops collapse too, punctuation notwithstanding.
    check("a 2-gram loop collapses",
          collapse_repetition("He said " + "I'm sorry. " * 40 + "and left.")
          == "He said I'm sorry. I'm sorry. and left.")
    check("punctuation variants continue a run",
          collapse_repetition("okay so clear, clear. clear clear! clear clear clear")
          == "okay so clear, clear.")

    # What must NOT collapse: genuine speech with natural repetition.
    for text in (
        "no no no, that's not what I meant",
        "it was very very very very good",
        "set a timer for ten minutes",
        "the clear answer is to clear the cache and clear the logs",
    ):
        check(f"untouched: {text[:40]!r}", collapse_repetition(text) == text)
    check("five repeats stay (six is the bar)",
          collapse_repetition("go go go go go stop") == "go go go go go stop")

    # The tail after a loop survives.
    check("text after the loop survives",
          collapse_repetition("start " + "beep " * 20 + "then the end")
          == "start beep beep then the end")

    # Degenerate punctuation-only runs are runs too.
    check("a dash wall collapses",
          collapse_repetition("wait — — — — — — — — — —") == "wait — —")

    # Scale: a serious wall stays cheap.
    import time
    big = "one two three " + "clear " * 2000 + "four five"
    started = time.monotonic()
    result = collapse_repetition(big)
    elapsed = time.monotonic() - started
    check("a 2000-word wall collapses correctly",
          result == "one two three clear clear four five")
    check("and in well under a second", elapsed < 1.0)


class _Engine:
    def __init__(self) -> None:
        self.calls = 0
        self.warmed = False
        self.closed = False

    async def transcribe(self, pcm: np.ndarray) -> str:
        self.calls += 1
        return "hello there"

    async def warm_up(self) -> None:
        self.warmed = True

    async def close(self) -> None:
        self.closed = True


async def _gate_checks() -> None:
    print("the speech gate:")
    scored: list[int] = []

    def scorer(pcm: np.ndarray) -> float:
        scored.append(len(pcm))
        return 0.2 if len(pcm) < SAMPLE_RATE else 0.9

    engine = _Engine()
    stt = GatedSTT(engine, SpeechGate(0.5, scorer=scorer))
    await stt.warm_up()
    check("warming the gate warms the engine", engine.warmed)
    quiet = np.zeros(SAMPLE_RATE // 2, np.float32)
    spoken = np.zeros(2 * SAMPLE_RATE, np.float32)
    check("an utterance the model hears no speech in is not transcribed",
          await stt.transcribe(quiet) == "" and engine.calls == 0)
    check("one it hears speech in reaches the engine untouched",
          await stt.transcribe(spoken) == "hello there" and engine.calls == 1)
    check("the gate scored both", scored == [SAMPLE_RATE // 2, 2 * SAMPLE_RATE])
    exact = GatedSTT(_Engine(), SpeechGate(0.5, scorer=lambda pcm: 0.5))
    check("a peak exactly at the threshold passes", await exact.transcribe(quiet) == "hello there")
    check("speech_threshold = 0 is the engine alone",
          gate_stt(engine, replace(STTConfig(), speech_threshold=0)) is engine)
    default = gate_stt(engine, STTConfig())
    check("by default the engine stands behind the gate, and is named",
          isinstance(default, GatedSTT) and default.engine is engine
          and default.gate.threshold == STTConfig().speech_threshold)
    with patch("ciel.stt.gate._silero_scorer", side_effect=RuntimeError("no model")):
        without = SpeechGate(0.5)
        await without.warm_up()
    check("without the model the gate fails open and says so",
          not without.available and await without.peak(quiet) is None)
    check("...and every utterance goes to the transcriber",
          await GatedSTT(_Engine(), without).transcribe(quiet) == "hello there")
    await stt.close()
    check("closing the gate closes the engine", engine.closed)

    real = SpeechGate(0.5)
    await real.warm_up()
    if not real.available:
        print("  (openWakeWord is not installed here — the model's own checks are skipped)")
        return
    silence = await real.peak(np.zeros(3 * SAMPLE_RATE // 2, np.float32))
    check("silero hears no speech in a second and a half of silence",
          silence is not None and silence < 0.1)
    noise = np.random.default_rng(1).normal(0, 0.02, 2 * SAMPLE_RATE).astype(np.float32)
    hiss = await real.peak(noise)
    check("nor in two seconds of room noise", hiss is not None and hiss < 0.5)


def main() -> int:
    asyncio.run(_gate_checks())
    hallucination_checks()
    loop_checks()
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
