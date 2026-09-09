"""Whether anyone spoke — asked before Whisper, of a model that cannot invent a sentence.

Whisper answers every question with a sentence. Handed a second of a quiet
room, a run of keystrokes, or a chair, it does not say "nothing"; it says
"Thank you." in the voice it uses for real speech, because it learned from
subtitles and subtitles never say nothing. Its own confidence cannot be
asked either: measured on 2026-09-08 with large-v3-turbo, the no-speech
probability it reports was 0.000 on pure silence, and the average
log-probability of its inventions sat where real sentences sit. The
stock-phrase filter in ``hallucinations.py`` catches the commonest
inventions and the prompt echo; the sentences invented over a keyboard are
not stock.

Why it exists: the endpointer proves a *pause*, and webrtcvad, which opens
an utterance, calls a run of keystrokes speech. So a false wake — a snap
that was a mouse button, a phrase off the television — followed by typing
handed clatter to Whisper, and the sentence it invented was answered as a
turn. Observed live 2026-09-08, a run of them in an afternoon of typing.

Silero VAD, the speech model openWakeWord already ships for its own gate,
does not have Whisper's failing. Measured the same day on synthesized
speech and synthetic rooms: on silence, room noise at three levels,
keyboard clatter, and breath-shaped noise its per-frame speech score never
reached 0.23; on speech its best frame reached 0.65 and up at every level
down to a whisper, where Whisper still transcribed the words. One number,
a wide gap, and a model that cannot hallucinate a sentence because it does
not produce sentences.

Invariants:

- **A peak, not an average.** One confident frame of speech is enough, so
  "Hey." and "Yes." survive; a chair, a key, or a fan never produces one.
- **Only says no.** The gate turns an utterance into an empty transcript,
  which every caller already reads as "nothing was said"; it never adds
  words and never changes what the engine heard.
- **Fails open, loudly.** Without openWakeWord the gate cannot score, so it
  passes everything and says so once at startup — a filter that silently
  deafens the assistant would be worse than the sentences it filters.
- **Every transcription, every engine.** The gate wraps the
  :class:`~ciel.stt.base.SpeechToText` protocol rather than living in an
  engine or a caller, so a turn, a held thought, and a confirmation answer
  are all gated, on mlx-whisper and on the CPU fallback alike.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import numpy as np

from ciel.config import SAMPLE_RATE, STTConfig
from ciel.stt.base import SpeechToText

log = logging.getLogger(__name__)

_FRAME = 480
"""Silero's recommended window at 16 kHz: 30 ms, the pipeline's own frame."""

Scorer = Callable[[np.ndarray], float]
"""The best per-frame speech score over an utterance of float32 samples,
0 to 1 — the model, or a probe's stand-in."""


def _silero_scorer() -> Scorer:
    # Imported here, on the caller's thread, never inside to_thread: the
    # wake detector imports openwakeword on the event loop at the same
    # moment, and two threads importing one package at once left the
    # second with a half-built module (seen live 2026-09-08).
    from openwakeword.vad import VAD

    vad = VAD()

    def peak(pcm: np.ndarray) -> float:
        # The model carries recurrent state between frames; each utterance
        # starts from silence so a previous one cannot vouch for it.
        vad.reset_states()
        samples = (np.clip(pcm, -1.0, 1.0) * 32767).astype(np.int16)
        whole = len(samples) // _FRAME * _FRAME
        best = 0.0
        for i in range(0, whole, _FRAME):
            best = max(best, float(vad.predict(samples[i:i + _FRAME], frame_size=_FRAME)))
        return best

    return peak


class SpeechGate:
    """Scores an utterance for speech; None when it cannot."""

    def __init__(self, threshold: float, scorer: Scorer | None = None) -> None:
        self.threshold = threshold
        self._scorer = scorer
        self._given = scorer is not None
        self._lock = asyncio.Lock()
        self._warmed = False

    @property
    def available(self) -> bool:
        return self._scorer is not None

    async def warm_up(self) -> None:
        if self._warmed:
            return
        self._warmed = True
        if self._given:
            return
        try:
            self._scorer = _silero_scorer()
        except Exception as exc:  # noqa: BLE001 - any failure means fail open
            log.warning("speech gate unavailable (%s: %s) — every utterance goes to the transcriber",
                        type(exc).__name__, exc)
            self._scorer = None
            return
        log.info("speech gate ready (silero, threshold %.2f)", self.threshold)

    async def peak(self, pcm: np.ndarray) -> float | None:
        if not self._warmed:
            await self.warm_up()
        if self._scorer is None:
            return None
        # One utterance at a time: the model's recurrent state is shared.
        async with self._lock:
            return await asyncio.to_thread(self._scorer, pcm)


class GatedSTT:
    """A transcriber that first asks whether anyone spoke."""

    def __init__(self, engine: SpeechToText, gate: SpeechGate) -> None:
        self.engine = engine
        """The engine underneath, for the fallback path that must know
        which engine it is looking at."""
        self.gate = gate

    async def warm_up(self) -> None:
        await self.gate.warm_up()
        await self.engine.warm_up()

    async def transcribe(self, pcm: np.ndarray) -> str:
        peak = await self.gate.peak(pcm)
        if peak is not None and peak < self.gate.threshold:
            log.info("no speech in %.2fs of audio (speech peaked at %.2f) — not transcribed",
                     len(pcm) / SAMPLE_RATE, peak)
            return ""
        return await self.engine.transcribe(pcm)

    async def close(self) -> None:
        await self.engine.close()


def gate_stt(engine: SpeechToText, config: STTConfig) -> SpeechToText:
    """The engine behind the speech gate, or the engine alone when the gate is off."""
    if config.speech_threshold <= 0:
        return engine
    return GatedSTT(engine, SpeechGate(config.speech_threshold))


__all__ = ["GatedSTT", "Scorer", "SpeechGate", "gate_stt"]
