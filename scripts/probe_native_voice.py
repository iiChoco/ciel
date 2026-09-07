"""Probe the native voice (Apple's synthesizer through the Swift helper).

    uv run scripts/probe_native_voice.py            # the engine's contract
    uv run scripts/probe_native_voice.py --ab DIR   # ...and an A/B against piper into DIR

The contract: the helper builds from source and is reused by hash; the
engine reports Apple's voice and rate; a sentence streams as many small
chunks with the first one early; an abandoned sentence (a barge-in) is
cancelled and the next one starts clean and in sync; the config's ``rate``
in words per minute maps onto Apple's scale; a voice that is not
installed is one clear error, so the pipeline's chain falls to piper.

``--ab`` synthesizes the same sentences — the ones Ciel actually says: a
timer, a brief line, a question, a number-heavy one — through both
engines, prints first-audio and total time per sentence, and writes
``native-N.wav`` / ``piper-N.wav`` for listening. Sound is the user's
call; the numbers are the probe's.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import TTSConfig, load_config
from ciel.tts.native import SOURCE, NativeTTS, _speech_rate, ensure_built

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


SENTENCES = [
    "Your ten minute timer is done.",
    "It's a quarter past three. You have a lecture at four in Wheeler Hall, and the 51B leaves in twelve minutes.",
    "Do you want me to pull up the directions?",
    "Readiness eighty-one, sleep score seventy-seven with six hours forty asleep.",
]


async def probe_engine() -> None:
    print("\nthe build")
    bin_dir = Path(tempfile.mkdtemp()) / "bin"
    started = time.monotonic()
    binary = ensure_built(SOURCE, bin_dir)
    built = time.monotonic() - started
    check("the helper builds from source", binary.exists() and built > 0.5)
    again = ensure_built(SOURCE, bin_dir)
    check("...and is reused by hash", again == binary)

    print("\nthe engine")
    tts = NativeTTS(replace(TTSConfig(), engine="native", native_voice="Jamie", rate=190))
    await tts.warm_up()
    check("warm-up learns the voice and the rate",
          tts.voice.startswith("Jamie (premium)") and tts.sample_rate == 22050)
    started = time.monotonic()
    first: float | None = None
    chunks: list[bytes] = []
    async for chunk in tts.stream(SENTENCES[1]):
        if first is None:
            first = time.monotonic() - started
        chunks.append(chunk)
    total = time.monotonic() - started
    seconds = sum(len(c) for c in chunks) / 2 / tts.sample_rate
    print(f"       first audio {first * 1000:.0f} ms, all {total * 1000:.0f} ms, "
          f"{len(chunks)} chunks, {seconds:.2f} s of speech")
    check("a sentence streams as many chunks", len(chunks) > 20)
    check("the first chunk is early", first is not None and first < 0.25)
    check("the audio is the sentence's length", 3.0 < seconds < 9.0)

    # A barge-in: stop pulling after the first chunk, then speak again.
    agen = tts.stream(SENTENCES[1])
    await agen.__anext__()
    await agen.aclose()
    started = time.monotonic()
    got = b""
    async for chunk in tts.stream(SENTENCES[0]):
        got += chunk
    seconds = len(got) / 2 / tts.sample_rate
    check("after an abandoned sentence the next one is clean and in sync",
          0.8 < seconds < 3.0 and time.monotonic() - started < 2.0)
    check("an empty utterance yields nothing", [c async for c in tts.stream("   ")] == [])
    await tts.close()
    check("close ends the helper", tts._proc is None)

    print("\nthe knobs")
    check("175 wpm is Apple's default pace", _speech_rate(175) == 0.5)
    check("faster is faster, clamped", _speech_rate(190) > 0.5 and _speech_rate(10_000) == 1.0)

    print("\nthe failure")
    bad = NativeTTS(replace(TTSConfig(), engine="native", native_voice="Nobody Here"))
    try:
        await bad.warm_up()
        check("a missing voice fails warm-up", False)
    except RuntimeError as exc:
        check("a missing voice fails warm-up with the voices that are there",
              "not installed" in str(exc) and "Jamie" in str(exc))
    check("...leaving no helper behind", bad._proc is None)


async def ab(out: Path) -> None:
    print("\nA/B against piper")
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    from ciel.tts.piper import PiperTTS

    engines = {
        "native": NativeTTS(replace(cfg.tts, engine="native")),
        "piper": PiperTTS(cfg.tts),
    }
    for name, engine in engines.items():
        await engine.warm_up()
    print(f"       native: {engines['native'].voice}; piper: {cfg.tts.piper_voice}")
    print(f"       {'sentence':<10} {'engine':<8} {'first':>8} {'total':>8} {'speech':>8}")
    for i, text in enumerate(SENTENCES, 1):
        for name, engine in engines.items():
            started = time.monotonic()
            first: float | None = None
            pcm = b""
            async for chunk in engine.stream(text):
                if first is None:
                    first = time.monotonic() - started
                pcm += chunk
            total = time.monotonic() - started
            seconds = len(pcm) / 2 / engine.sample_rate
            print(f"       {i:<10} {name:<8} {first * 1000:>6.0f}ms {total * 1000:>6.0f}ms {seconds:>7.2f}s")
            with wave.open(str(out / f"{name}-{i}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(engine.sample_rate)
                w.writeframes(pcm)
    for engine in engines.values():
        await engine.close()
    print(f"       wavs in {out}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ab", metavar="DIR", default=None, help="also A/B against piper into DIR")
    args = parser.parse_args()
    await probe_engine()
    if args.ab:
        await ab(Path(args.ab))
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
