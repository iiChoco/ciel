"""Hand sounds on the wake microphone — snaps, claps, and the double clap.

Part of the wake boundary (codename **Characteristic**): a second way of
answering "am I being addressed?", fed the same 30 ms frames as the wake
word and never opening a device of its own.

Why it exists: the wake phrase is the only way to get Ciel's attention
without a keyboard, and there are moments when a phrase is the wrong
instrument — a room where speaking feels odd, a mouth full, a doorway too
far for a whisper. A snap is quick and private; two claps carry across a
room. Neither needs a model, and both are detectable on a laptop's lid
microphone at the pipeline's 16 kHz, which throws away everything above
8 kHz and with it most of what makes these sounds distinctive.

What is measured. Every impulse that clears the onset gate — a step of
``rise_ratio`` inside 2 ms, above the room's floor — is measured four
ways: its **peak**, the **width** of its body above half that peak, its
**tilt** (energy above 2 kHz against the band from 500 Hz to 2 kHz), and
its **fall** 10 ms after the peak. Tilt is what separates a snap from a
clap here; loudness is what separates a clap from a keystroke. The
boundaries live on :class:`ciel.config.GestureConfig` and were read off
one room on 2026-09-06; ``scripts/listen_gestures.py`` re-reads them in
another.

Invariants:

- **Frames in, observations out.** :meth:`GestureDetector.push` takes one
  capture frame and returns every candidate it gated (with the reason it
  was rejected, when it was) and every gesture it completed. A candidate
  row explains; only a gesture row is an input.
- **Time is audio time.** Onsets and pair gaps are counted in samples, so
  replaying a recording and listening live give identical answers and
  scheduler jitter cannot split or merge a pair.
- **Filter state crosses frames.** The high-pass and the envelope carry
  their history from one frame to the next; a peak on a frame boundary
  is the same peak it would be in the middle.
- **A pair is exclusive.** Two claps inside the pair window are one
  double clap and no single claps; a single clap is released only once
  its window has been fully scanned, and a snap or a rejected impulse
  in between breaks the pair.
- **A snap is solitary.** A keystroke arrives in a run; a snap does not.
  With ``snap_quiet_ms`` on, a would-be snap that follows any other gated
  impulse inside that window is rejected as typing cadence, whatever its
  shape.
- **A veto only says no.** When the ear is given one (``audio/audioset.py``),
  it is asked only about impulses the rules accepted, and its answer can
  turn a snap or a clap into a rejection that names what the room was
  doing instead — never the reverse.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields, replace
from typing import Callable

import numpy as np

from ciel.config import FRAME_BYTES, SAMPLE_RATE, GestureConfig

_FRAME = FRAME_BYTES // 2
_PRE = 80
_POST = 560
_RISE = 32
_ENV = 16
_CONTEXT = 15_600
"""What a veto is shown: the 0.975 s of raw audio ending 35 ms after the
peak — one AudioSet frame, with the keystrokes or the sentence around
the impulse inside it."""

Veto = Callable[[np.ndarray], "str | None"]
"""A second opinion that only says no: given the context window, the
sound the impulse was mistaken for (``"typing 0.82"``), or None."""


def first_of(*vetoes: Veto | None) -> Veto | None:
    """Several opinions, asked in order; the first that says no is the
    answer. Cheap ones go first, so the model is not run for a keystroke
    the keyboard already owned up to."""
    asked = [v for v in vetoes if v is not None]
    if not asked:
        return None
    if len(asked) == 1:
        return asked[0]

    def veto(window: np.ndarray) -> str | None:
        for v in asked:
            heard = v(window)
            if heard:
                return heard
        return None

    return veto


@dataclass(frozen=True, slots=True)
class Measurement:
    """Peak time is seconds of audio since reset; peak is high-passed RMS."""

    onset_s: float
    peak: float
    width_ms: float
    tilt: float
    fall_db: float


@dataclass(frozen=True, slots=True)
class Observation:
    """Candidate rows explain recognition; gesture rows are exclusive inputs."""

    type: str
    kind: str
    onset_s: float
    detected_s: float
    peak: float
    width_ms: float
    tilt: float
    fall_db: float
    reason: str = ""
    second_onset_s: float | None = None


def classify(m: Measurement, c: GestureConfig) -> tuple[str, str]:
    """Apply the room's rules and report exactly which cues missed."""
    rules = {
        "snap": {"peak": (c.min_peak, c.snap_max_peak), "width_ms": (0, c.snap_max_ms),
                 "tilt": (c.snap_min_tilt, np.inf), "fall_db": (c.snap_min_fall_db, np.inf)},
        "clap": {"peak": (c.clap_min_peak, np.inf), "width_ms": (0, c.clap_max_ms),
                 "tilt": (0, c.clap_max_tilt), "fall_db": (c.clap_min_fall_db, np.inf)},
    }
    reasons = []
    for kind, rule in rules.items():
        misses = [name for name, (low, high) in rule.items() if not low <= getattr(m, name) < high]
        if not misses:
            return kind, ""
        reasons.append(f"{kind}: {', '.join(misses)}")
    return "rejected", "; ".join(reasons)


class GestureDetector:
    """Feed the same 30 ms PCM frames the pipeline reads; opens no device."""

    def __init__(self, config: GestureConfig | None = None, veto: Veto | None = None) -> None:
        self.config = config or GestureConfig()
        self._veto = veto
        c = self.config
        numbers = [(f.name, getattr(c, f.name)) for f in fields(c)]
        numbers = [(n, v) for n, v in numbers if isinstance(v, (int, float)) and not isinstance(v, bool)]
        # The two windows are switched off by a zero; every other number is a threshold.
        switches = {"snap_quiet_ms", "keyboard_veto_ms"}
        if not all(np.isfinite(v) and (v > 0 or (n in switches and v == 0)) for n, v in numbers):
            raise ValueError("gesture thresholds must be finite and positive")
        if not (c.highpass_hz < SAMPLE_RATE / 2 and c.min_peak < c.snap_max_peak <= 1
                and c.noise_ratio > 1 and c.rise_ratio > 1
                and c.snap_max_ms <= c.clap_max_ms <= 40
                and 40 <= c.refractory_ms <= c.double_min_ms < c.double_max_ms
                and c.min_peak <= c.second_clap_min_peak <= c.clap_min_peak <= 1):
            raise ValueError("inconsistent gesture thresholds or timing windows")
        self._alpha = 1 / (1 + 2 * np.pi * c.highpass_hz / SAMPLE_RATE)
        self.reset()

    def reset(self) -> None:
        """Drop pending pairs and filter history after missing or drained audio."""
        self._samples = 0
        self._hp = np.zeros(_FRAME * 3)
        self._env = np.zeros(_FRAME * 3)
        self._raw = np.zeros(_CONTEXT + _FRAME * 3)
        self._prev_x = self._prev_y = 0.0
        self._squares: deque[float] = deque([0.0] * _ENV, maxlen=_ENV)
        self._sum = 0.0
        self._ambient: deque[float] = deque(maxlen=100)
        self._last_peak: int | None = None
        self._last_gated: int | None = None
        """The sample of the last impulse that cleared the gate, whatever
        became of it — the snap's solitude is measured from here."""
        self._pending: Measurement | None = None
        self._ended = False

    def clear(self) -> None:
        """Forget a half-made pair but keep the ears open.

        The wake boundary resets its detectors after every turn. For the
        wake word that flushes a phrase's tail; here the equivalent is the
        pending clap, and only that — a full :meth:`reset` would also
        restart the warm-up and leave the room deaf to a snap for the
        first half-second after each reply."""
        self._pending = None
        self._last_peak = None

    @property
    def ready(self) -> bool:
        return self._samples * 1000 / SAMPLE_RATE >= self.config.warmup_ms

    def _filter(self, samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        hp, env = np.empty(len(samples)), np.empty(len(samples))
        for i, x in enumerate(samples):
            y = self._alpha * (self._prev_y + x - self._prev_x)
            self._prev_x, self._prev_y = x, y
            square = y * y
            self._sum += square - self._squares[0]
            self._squares.append(square)
            hp[i], env[i] = y, np.sqrt(max(self._sum, 0) / _ENV)
        return hp, env

    def push(self, frame: bytes) -> list[Observation]:
        """Emit diagnostics and completed gestures from one full capture frame."""
        if self._ended:
            raise ValueError("reset the detector before reusing a finished stream")
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"expected {FRAME_BYTES} bytes of mono int16 PCM")
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float64) / 32768
        hp, env = self._filter(samples)
        self._hp = np.concatenate((self._hp[_FRAME:], hp))
        self._env = np.concatenate((self._env[_FRAME:], env))
        self._raw = np.concatenate((self._raw[_FRAME:], samples))
        self._samples += _FRAME
        # Scan a whole frame once, with 35 ms of future audio even at its end.
        # This deliberately overlaps capture frames, so their boundaries do
        # not select the loudest event or truncate a peak's falling edge.
        low = len(self._env) - _POST - _FRAME
        high = low + _FRAME
        base = self._samples - len(self._env)
        c = self.config
        floor = float(np.median(self._ambient)) if self._ambient else 0.001
        rows: list[Observation] = []
        for p in range(low, high):
            sample = base + p
            if sample * 1000 / SAMPLE_RATE < c.warmup_ms:
                continue
            peak = float(self._env[p])
            if not (peak > self._env[p - 1] and peak >= self._env[p + 1]):
                continue
            if peak < max(c.min_peak, c.noise_ratio * floor, c.rise_ratio * self._env[p - _RISE]):
                continue
            # A small leading ripple must not consume the refractory period
            # before the actual impulse reaches its peak a millisecond later.
            if peak < float(np.max(self._env[p:p + _RISE + 1])):
                continue
            if self._last_peak is not None and (sample - self._last_peak) * 1000 < c.refractory_ms * SAMPLE_RATE:
                continue
            self._last_peak = sample
            crowded = self._last_gated is not None and (sample - self._last_gated) * 1000 < c.snap_quiet_ms * SAMPLE_RATE
            self._last_gated = sample
            m = self._measure(p, sample)
            rows.extend(self._accept(m, self._context(p), crowded))
        self._ambient.append(float(np.sqrt(np.mean(self._hp[low:high] ** 2))))
        # Only expire a pair once every eligible second peak has been scanned.
        scanned_s = (base + high - 1) / SAMPLE_RATE
        if self._pending and scanned_s > self._pending.onset_s + c.double_max_ms / 1000:
            rows.append(self._release())
        return rows

    def _measure(self, p: int, sample: int) -> Measurement:
        body = self._hp[p - _PRE:p + _POST]
        env = self._env[p - _PRE:p + _POST]
        peak = float(self._env[p])
        power = np.abs(np.fft.rfft(body * np.hanning(len(body)))) ** 2
        freq = np.fft.rfftfreq(len(body), 1 / SAMPLE_RATE)
        high = float(power[(freq >= 2000) & (freq < 8000)].sum()) + 1e-12
        low = float(power[(freq >= 500) & (freq < 2000)].sum()) + 1e-12
        width = float(np.count_nonzero(env > peak / 2)) * 1000 / SAMPLE_RATE
        fall = float(20 * np.log10(peak / max(float(self._env[p + 160]), 1e-9)))
        return Measurement(sample / SAMPLE_RATE, peak, width, high / low, fall)

    def _context(self, p: int) -> np.ndarray:
        """The second of raw audio ending 35 ms after the peak at ``p`` (an
        index into the filtered rings, whose end is the raw ring's end)."""
        end = len(self._raw) - len(self._env) + p + _POST
        return self._raw[max(0, end - _CONTEXT):end]

    def _row(self, m: Measurement, type_: str, kind: str, reason: str = "", second: float | None = None) -> Observation:
        return Observation(type_, kind, m.onset_s, self._samples / SAMPLE_RATE,
                           m.peak, m.width_ms, m.tilt, m.fall_db, reason, second)

    def _release(self) -> Observation:
        assert self._pending is not None
        m, self._pending = self._pending, None
        return self._row(m, "gesture", "clap")

    def _accept(self, m: Measurement, context: np.ndarray | None = None, crowded: bool = False) -> list[Observation]:
        rows: list[Observation] = []
        c = self.config
        kind, reason = classify(m, c)
        if kind == "snap" and crowded:
            kind, reason = "rejected", f"typing cadence — another impulse inside {c.snap_quiet_ms} ms"
        # The veto is asked only about what the rules accepted, or what the
        # relaxed second-clap rule is about to: never about a rejection.
        vetoed = False
        if self._veto is not None and context is not None and (kind != "rejected" or self._pending):
            heard = self._veto(context)
            if heard:
                kind, reason, vetoed = "rejected", f"sounds like {heard}", True
        if self._pending:
            first = self._pending
            gap = (m.onset_s - first.onset_s) * 1000
            # A fully recognized snap breaks a pair even in the overlap of
            # the snap and relaxed second-clap spectral boundaries.
            if (kind != "snap" and not vetoed and c.double_min_ms - 1e-6 <= gap < c.double_max_ms - 1e-6
                    and m.peak >= c.second_clap_min_peak and m.tilt < c.clap_max_tilt):
                self._pending = None
                rows.append(self._row(m, "candidate", "clap", "second clap; relaxed peak/tilt rule"))
                pair = replace(m, onset_s=first.onset_s)
                rows.append(self._row(pair, "gesture", "double_clap", second=m.onset_s))
                return rows
            rows.append(self._release())
        rows.append(self._row(m, "candidate", kind, reason or ("waiting for second clap" if kind == "clap" else "")))
        if kind == "clap":
            self._pending = m
        elif kind == "snap":
            rows.append(self._row(m, "gesture", "snap"))
        return rows

    def finish(self) -> list[Observation]:
        """Discard unscanned audio lacking a full decay window; flush a single."""
        self._ended = True
        return [self._release()] if self._pending else []


__all__ = ["Measurement", "Observation", "Veto", "first_of", "classify", "GestureDetector"]
