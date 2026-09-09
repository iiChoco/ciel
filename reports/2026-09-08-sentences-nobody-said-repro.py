"""Reproduction for reports/2026-09-08-sentences-nobody-said.md.

Feeds silence, room noise, keyboard-shaped clatter, and breath-shaped noise
to the configured Whisper model and to Silero VAD, then the same for a few
sentences synthesized by a piper voice at three levels, and prints what
each says. No microphone, no runtime state: pass any piper voice with
``--voice`` (the Ciel voices live under ~/.ciel/piper, but any .onnx from
the piper catalogue will do) and the report's numbers come back.

    uv run --no-sync python reports/2026-09-08-sentences-nobody-said-repro.py --voice /path/to/voice.onnx
"""
from __future__ import annotations

import argparse

import numpy as np

SR = 16000
PROMPT = "A spoken conversation with an assistant named Ciel."


def clicks(rng: np.random.Generator, seconds: float, n: int, level: float) -> np.ndarray:
    x = rng.normal(0, 0.003, int(seconds * SR)).astype(np.float32)
    for t in rng.uniform(0.1, seconds - 0.1, n):
        i = int(t * SR)
        k = np.arange(80)
        x[i:i + 80] += (level * np.exp(-k / 12) * np.sign(rng.normal(size=80))).astype(np.float32)
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", required=True, help="a piper .onnx voice")
    ap.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    args = ap.parse_args()

    import mlx_whisper
    from openwakeword.vad import VAD
    from piper import PiperVoice

    voice = PiperVoice.load(args.voice)

    def synth(text: str) -> np.ndarray:
        chunks = [np.frombuffer(c.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768
                  for c in voice.synthesize(text)]
        x = np.concatenate(chunks)
        sr = voice.config.sample_rate
        t = np.arange(0, len(x) / sr, 1 / SR)
        return np.interp(t, np.arange(len(x)) / sr, x).astype(np.float32)

    vad = VAD()

    def silero(x: np.ndarray) -> tuple[float, float]:
        vad.reset_states()
        pcm = (x * 32767).astype(np.int16)
        n = len(pcm) // 480 * 480
        scores = [float(vad.predict(pcm[i:i + 480], frame_size=480)) for i in range(0, n, 480)]
        return max(scores), float(np.mean(scores))

    rng = np.random.default_rng(1)
    cases: dict[str, np.ndarray] = {}
    for text in ["Yes.", "Hey.", "Play some music.", "Set a timer for ten minutes.",
                 "What does my afternoon look like, and is there anything I should move?"]:
        x = synth(text)
        for gain in (1.0, 0.1, 0.03):
            cases[f"speech {text[:22]!r} x{gain}"] = x * gain
    cases["silence 1.5s"] = np.zeros(int(1.5 * SR), np.float32)
    for level in (0.003, 0.02, 0.05):
        cases[f"noise {level}"] = rng.normal(0, level, 2 * SR).astype(np.float32)
    cases["clatter 2.5s"] = clicks(rng, 2.5, 14, 0.2)
    cases["clatter 4s"] = clicks(rng, 4.0, 30, 0.2)
    cases["breath-shaped noise"] = np.convolve(rng.normal(0, 0.02, 2 * SR), np.ones(40) / 40, "same").astype(np.float32)

    print(f"{'case':42s} {'rms':>6s} {'peak':>5s} {'mean':>5s}  whisper (no_speech_prob, avg_logprob)")
    for name, x in cases.items():
        peak, mean = silero(x)
        r = mlx_whisper.transcribe(x, path_or_hf_repo=args.model, language="en", initial_prompt=PROMPT,
                                   condition_on_previous_text=False, temperature=(0.0, 0.4), verbose=None)
        segs = r["segments"]
        stats = ", ".join(f"{s['no_speech_prob']:.3f}/{s['avg_logprob']:.2f}" for s in segs) or "-"
        print(f"{name:42s} {np.sqrt(np.mean(x * x)):6.4f} {peak:5.2f} {mean:5.2f}  {r['text'].strip()!r} ({stats})")


if __name__ == "__main__":
    main()
