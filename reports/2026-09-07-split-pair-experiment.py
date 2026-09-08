"""Does the voice-processing far end cause the lisp, and does a split pair cure it?

Standalone: nothing here touches the running spoke or changes Ciel. One
sentence is synthesized once with the configured voice, then the identical PCM
is played three ways through the default speakers:

  A. Ciel's old backend: PortAudio alone, no voice processing running.
  B. The split pair: the Apple helper running capture-only (its output node
     idle) while the voice plays through PortAudio in this process.
  C. The current Apple backend: the voice scheduled inside the engine.

During each play two ears listen: a raw PortAudio microphone and, in B and C,
the helper's processed capture. Each reports its level in the quiet before the
sentence, its level during it, and how many of its frames webrtcvad calls
speech. Processed speech during B is what Ciel would hear of herself with the
split pair; the raw level in B against A shows whether Apple's ducking turns
Ciel's own voice down. Your ears decide the lisp; the script asks at the end.

No audio is written anywhere. Mute Ciel first, or she will answer the sentence.

    uv run --no-sync python reports/2026-09-07-split-pair-experiment.py
"""
from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator

import numpy as np
import sounddevice as sd
import webrtcvad

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.audio.apple import AppleMic, ApplePlayer, AppleSession  # noqa: E402
from ciel.audio.output import Player  # noqa: E402
from ciel.config import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, load_config  # noqa: E402
from ciel.pipeline import build_tts, fallback_tts  # noqa: E402

SENTENCE = ("Six thick thistle sticks. Sister Susie sits sewing socks for soldiers. "
            "The seaside sizzles this season, so listen closely to these whispers.")
QUIET_S = 1.5
TAIL_S = 0.4


def db(rms: float) -> float:
    return 20 * math.log10(max(rms, 1e-6))


class Ear:
    """Timestamped 30 ms frames from one capture path; measured, never stored to disk."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.frames: list[tuple[float, bytes]] = []
        self.lock = threading.Lock()

    def add(self, frame: bytes) -> None:
        with self.lock:
            self.frames.append((time.monotonic(), frame))

    def between(self, start: float, end: float) -> list[bytes]:
        with self.lock:
            return [frame for at, frame in self.frames if start <= at <= end]

    def measure(self, start: float, end: float, vad: webrtcvad.Vad,
                reference: np.ndarray | None = None) -> tuple[float, float, float]:
        """(level dB, speech fraction, coherence with the played sentence).

        Coherence is the peak normalised cross-correlation between what this
        ear captured and the sentence that was played, over lags up to a
        second. It is scale-free, so comfort noise and gain do not move it:
        near zero means none of Ciel's voice survives in this capture.
        """
        frames = self.between(start, end)
        if not frames:
            return float("nan"), float("nan"), float("nan")
        samples = np.frombuffer(b"".join(frames), dtype=np.int16).astype(np.float64) / 32768
        rms = float(np.sqrt(np.mean(samples ** 2)))
        speech = [len(f) == FRAME_BYTES and vad.is_speech(f, SAMPLE_RATE) for f in frames]
        coherence = float("nan")
        if reference is not None and rms > 0:
            size = 1 << (len(samples) + len(reference)).bit_length()
            correlation = np.fft.irfft(np.fft.rfft(samples, size) * np.conj(np.fft.rfft(reference, size)), size)
            lags = correlation[:SAMPLE_RATE]
            coherence = float(np.max(np.abs(lags)) / (np.linalg.norm(samples) * np.linalg.norm(reference)))
        return db(rms), sum(speech) / len(speech), coherence


class RawMic:
    """The raw default microphone, the way Ciel's PortAudio backend hears."""

    def __init__(self, ear: Ear) -> None:
        self.ear = ear
        self.stream: sd.RawInputStream | None = None

    async def __aenter__(self) -> "RawMic":
        self.stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES, channels=1,
                                        dtype="int16", callback=lambda data, *_: self.ear.add(bytes(data)))
        self.stream.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


async def collect(mic: AppleMic, ear: Ear) -> None:
    async for frame in mic.frames():
        ear.add(frame)


async def chunks(pcm: bytes, size: int = 4096) -> AsyncIterator[bytes]:
    for offset in range(0, len(pcm), size):
        yield pcm[offset:offset + size]


def reference_16k(pcm: bytes, rate: int) -> np.ndarray:
    """The played sentence at the microphone rate, for coherence only."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768
    if rate == SAMPLE_RATE:
        return samples
    count = int(len(samples) * SAMPLE_RATE / rate)
    return np.interp(np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples)


Row = tuple[str, str, float, float, float, float, float]


async def trial(label: str, description: str, player: Player, pcm: bytes, reference: np.ndarray,
                ears: list[Ear], vad: webrtcvad.Vad) -> list[Row]:
    await asyncio.to_thread(input, f"\n[{label}] {description}\n    Enter to play, then listen for the lisp... ")
    quiet_start = time.monotonic()
    await asyncio.sleep(QUIET_S)
    play_start = time.monotonic()
    completed = await player.play(chunks(pcm))
    await asyncio.sleep(TAIL_S)
    play_end = time.monotonic()
    if not completed:
        print(f"    playback did not complete (device_lost={player.device_lost})")
    rows: list[Row] = []
    for ear in ears:
        quiet_db, quiet_speech, _ = ear.measure(quiet_start, play_start, vad)
        played_db, speech, coherence = ear.measure(play_start, play_end, vad, reference)
        rows.append((label, ear.name, quiet_db, played_db, quiet_speech, speech, coherence))
        print(f"    {ear.name:<16} quiet {quiet_db:6.1f} dB (speech {quiet_speech:3.0%})   "
              f"during {played_db:6.1f} dB (speech {speech:3.0%})   coherence {coherence:.2f}")
    return rows


async def main() -> None:
    cfg = load_config()
    audio = replace(cfg.audio, input_device=None, output_device=None)
    tts = build_tts(cfg)
    try:
        await tts.warm_up()
    except Exception as exc:  # noqa: BLE001 - the same fallback the spoke takes
        print(f"configured voice failed to start ({type(exc).__name__}); falling back")
        tts = fallback_tts(tts, cfg)
        await tts.warm_up()
    pcm = b"".join([chunk async for chunk in tts.stream(SENTENCE)])
    rate = tts.sample_rate
    print(f"voice: {type(tts).__name__}, {len(pcm) / 2 / rate:.1f} s at {rate} Hz, effect {cfg.tts.effect}")
    print("default devices:", sd.query_devices(sd.default.device[0])["name"], "/",
          sd.query_devices(sd.default.device[1])["name"])
    vad = webrtcvad.Vad(audio.vad_aggressiveness)
    reference = reference_16k(pcm, rate)
    raw, processed = Ear("raw microphone"), Ear("Apple processed")
    results: list[Row] = []
    async with RawMic(raw):
        async with Player(audio, rate) as portaudio:
            results += await trial("A", "Ciel's old backend: PortAudio alone, no voice processing running",
                                   portaudio, pcm, reference, [raw], vad)
        session = AppleSession(replace(audio, backend="apple"), rate)
        mic = AppleMic(session)
        async with mic:
            print(f"    Apple engine rates: {session.formats}")
            collector = asyncio.create_task(collect(mic, processed))
            try:
                async with Player(audio, rate) as portaudio:
                    results += await trial("B", "the split pair: Apple capture-only, the voice through PortAudio",
                                           portaudio, pcm, reference, [raw, processed], vad)
                async with ApplePlayer(session) as engine:
                    results += await trial("C", "the current Apple backend: the voice inside the engine",
                                           engine, pcm, reference, [raw, processed], vad)
                    underruns = getattr(session, "underruns", None)
                    if underruns:
                        print(f"    engine underruns during C: {underruns}")
            finally:
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)
    await tts.close()

    print("\nsummary")
    print(f"  {'play':<5}{'ear':<18}{'quiet dB':>9}{'during dB':>11}{'speech quiet':>13}{'speech during':>14}{'coherence':>10}")
    for label, ear, quiet_db, played_db, quiet_speech, speech, coherence in results:
        print(f"  {label:<5}{ear:<18}{quiet_db:9.1f}{played_db:11.1f}{quiet_speech:13.0%}{speech:14.0%}{coherence:10.2f}")

    def cell(label: str, ear: str, index: int) -> float:
        return next((r[index] for r in results if r[0] == label and r[1] == ear), float("nan"))

    print("\nreading the coherence column")
    print("  A raw is what an uncancelled capture of this sentence scores in this room.")
    print("  C processed near zero: the engine cancels its own playback.")
    print("  B processed is the split pair: how much of Ciel's voice would reach wake detection and endpointing.")
    a_raw, b_split, c_engine = cell("A", "raw microphone", 6), cell("B", "Apple processed", 6), cell("C", "Apple processed", 6)
    if a_raw > 0 and not math.isnan(b_split):
        print(f"  the split pair keeps {b_split / a_raw:.0%} of the uncancelled coherence; the engine keeps {c_engine / a_raw:.0%}.")
    heard = await asyncio.to_thread(input, "\nWhich plays had the lisp? (letters, or none) ")
    heard = {c for c in heard.upper() if c in "ABC"}
    print()
    if heard == {"C"}:
        print("The lisp lives in the engine's far end. The split pair sounds right;")
        print("its coherence and speech-during numbers say whether cancellation still holds.")
    elif heard == {"B", "C"}:
        print("Voice processing alters other processes' output too; a split pair does not help.")
    elif heard == {"A", "B", "C"}:
        print("All three lisp: the Apple route is not the cause.")
    elif not heard:
        print("No lisp in any play: the room trial differs from this sentence or this voice.")
    else:
        print("Unexpected pattern; note it in the report as heard.")
    a_level, b_level = cell("A", "raw microphone", 3), cell("B", "raw microphone", 3)
    if a_level - b_level > 3:
        print(f"B reached the room {a_level - b_level:.1f} dB quieter than A: Apple's ducking is turning Ciel's own voice down.")


if __name__ == "__main__":
    asyncio.run(main())
