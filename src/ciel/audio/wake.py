"""The wake boundary — how Ciel decides it's being addressed.

Codename: **Characteristic** — the indicator function of "am I being
addressed", one frame at a time.

Detectors are *fed* frames rather than pulling their own. There is exactly one
microphone and exactly one reader of it (the pipeline), so a detector that
opened its own stream would either fail or steal audio from the endpointer.
Push also makes the hotkey and the wake word genuinely interchangeable: both
answer the same question — "has the user addressed us as of this frame?" —
even though only one of them cares about the audio.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class WakeDetector(Protocol):
    """Signals that the user wants Ciel's attention."""

    def push(self, frame: bytes) -> bool:
        """Feed one 30 ms frame; return ``True`` if Ciel was just addressed.

        Must be cheap — it runs on every frame, roughly 33 times a second.
        """
        ...

    def reset(self) -> None:
        """Clear state after a turn.

        Without this the tail of the wake phrase is still sitting in the
        detector's buffer when the turn ends, and immediately re-triggers.
        """
        ...

    async def start(self) -> None:
        ...

    async def close(self) -> None:
        ...


class HotkeyWake:
    """Wake on Enter.

    Armed externally by the pipeline's single stdin reader — a bare Enter on
    the typed lane arms it — rather than opening its own reader. There is
    exactly one reader of stdin for the same reason there is one reader of the
    microphone: two readers of one file descriptor race per line, and an
    asyncio reader also flips the descriptor non-blocking underneath a thread
    blocked in ``readline``. So this detector holds no thread and touches no
    stdin; it only carries the armed flag. Deliberately the dumbest thing that
    works — it is how the rest of the pipeline gets tested before the
    microphone is trusted, and it stays useful whenever the room is noisy.

    ``arm`` and ``push``/``reset`` all run on the event loop (the stdin task
    and the frame loop), never preempting each other, so a plain bool needs no
    lock.
    """

    source = "hotkey"
    """How this detector addresses Ciel, for the Chart's chip."""

    def __init__(self, prompt: str = "[press Enter to talk]") -> None:
        self._prompt = prompt
        self._armed = False

    async def start(self) -> None:
        self._announce()

    def _announce(self) -> None:
        sys.stdout.write(f"\n{self._prompt} ")
        sys.stdout.flush()

    def arm(self) -> None:
        """Register a bare Enter as a wake. Called by the pipeline's stdin
        reader, which owns the one descriptor."""
        self._armed = True

    def push(self, frame: bytes) -> bool:  # noqa: ARG002 - audio is irrelevant here
        if self._armed:
            self._armed = False
            return True
        return False

    def reset(self) -> None:
        self._armed = False
        self._announce()

    async def close(self) -> None:
        return None


class AlwaysAwake:
    """No wake gate — every utterance is treated as addressed.

    Only sane alone in a quiet room. With a television on, Ciel answers it.
    """

    source = "spoken"

    async def start(self) -> None:
        return None

    def push(self, frame: bytes) -> bool:  # noqa: ARG002
        return True

    def reset(self) -> None:
        return None

    async def close(self) -> None:
        return None


class OpenWakeWord:
    """Hands-free wake via openWakeWord.

    Runs under onnxruntime rather than tflite: the tflite runtime has no wheels
    for current Python on macOS, and is unreliable on Apple Silicon besides.

    Defaults to the pretrained ``hey_jarvis`` model — openWakeWord ships no
    "Ciel" model. A custom one is a first-class citizen, not future work:
    point ``model`` at a trained ``.onnx`` path and the registry download is
    skipped, ``scripts/probe_wake_model.py`` qualifies the model before it
    goes live, and the spoken ready line takes the wake phrase from the
    path's stem.
    """

    source = "spoken"

    def __init__(
        self,
        model: str = "hey_jarvis",
        threshold: float = 0.5,
        vad_threshold: float = 0.3,
    ) -> None:
        self._model_name = model
        self._threshold = threshold
        self._vad_threshold = vad_threshold
        self._model = None
        self._cooldown = 0
        self._near_peak = 0.0
        self._near_quiet = 0
        """Near-miss tracking. A failed wake attempt used to leave no trace —
        only threshold-crossing scores were logged — so "it takes three
        tries" produced zero data to tune with. A burst of frames above half
        the threshold that never crosses it is an attempt; its *peak* is
        logged once the burst goes quiet, and that number is exactly what to
        set the threshold just under."""

    async def start(self) -> None:
        import numpy as np  # noqa: F401 - imported for the side effect of failing early
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        # Idempotent, and a no-op once the ONNX files are cached locally.
        # Skipped entirely for a custom model given as a path — the registry
        # only knows the pretrained names, and the file is already on disk.
        if not Path(self._model_name).expanduser().exists():
            await asyncio.to_thread(download_models, [self._model_name])

        self._model = await asyncio.to_thread(
            Model,
            wakeword_models=[self._model_name],
            inference_framework="onnx",
            vad_threshold=self._vad_threshold,
        )
        # A custom model arrives as a path; its stem is the phrase — same
        # rule as the spoken ready line, because the log tells the truth in
        # the same words.
        shown = Path(self._model_name)
        display = shown.stem if shown.suffix else self._model_name
        log.info("wake word ready (%s, threshold %.2f)", display, self._threshold)

    def push(self, frame: bytes) -> bool:
        if self._model is None:
            return False

        import numpy as np

        # Suppress re-triggering on the decaying tail of a detection.
        if self._cooldown > 0:
            self._cooldown -= 1
            self._model.predict(np.frombuffer(frame, dtype=np.int16))
            return False

        scores = self._model.predict(np.frombuffer(frame, dtype=np.int16))
        for name, score in scores.items():
            if score >= self._threshold:
                log.info("wake: %s (%.2f)", name, score)
                # ~1s of frames; the phrase keeps scoring high after the peak.
                self._cooldown = 33
                self._near_peak = 0.0
                return True
            if score >= self._threshold / 2:
                # Inside a possible failed attempt: track its peak.
                self._near_peak = max(self._near_peak, float(score))
                self._near_quiet = 0
        if self._near_peak > 0.0:
            self._near_quiet += 1
            if self._near_quiet > 33:  # ~1s of quiet: the attempt is over
                log.info(
                    "wake near miss: peaked at %.2f (threshold %.2f) — if "
                    "that was a real attempt, the threshold wants to sit "
                    "just under these peaks",
                    self._near_peak, self._threshold,
                )
                self._near_peak = 0.0
                self._near_quiet = 0
        return False

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()
        self._cooldown = 0
        self._near_peak = 0.0
        self._near_quiet = 0

    async def close(self) -> None:
        self._model = None


class GestureWake:
    """Wake on a hand sound — a snap, two claps, or either — or act on one.

    Wraps the gesture ear (``audio/gestures.py``) in the detector shape so
    the frame loop cannot tell it from the wake word: fed the same frames,
    answering the same question. A gesture in ``wake`` answers yes; a
    gesture in ``actions`` runs its callable instead and answers no, so
    two claps can start music without opening a listening window. The
    detector's other rows are diagnostics and are dropped here. Actions
    are scheduled, never awaited: the frame loop must not wait on an
    application.
    """

    def __init__(
        self,
        detector,  # noqa: ANN001 - GestureDetector
        wake: set[str],
        actions: "dict[str, tuple[str, Callable[[], Awaitable[object]]]] | None" = None,
        log_candidates: bool = False,
    ) -> None:
        self._detector = detector
        self._wake = set(wake)
        self._actions = dict(actions or {})
        self._log_candidates = log_candidates
        self._tasks: set[asyncio.Task] = set()
        self.veto = None
        """The ear's second opinion, started with the ear; None when off."""
        self.source = "snap"
        """The gesture that last woke, in the words the Chart shows."""
        names = {"snap": "snap", "double_clap": "clap twice"}
        self.phrases = tuple(
            names[kind] if kind in self._wake else f"{names[kind]} to {self._actions[kind][0]}"
            for kind in ("snap", "double_clap")
            if kind in self._wake or kind in self._actions
        )
        """How the ready line names each gesture, in the order they fire."""

    async def start(self) -> None:
        if self.veto is not None:
            await self.veto.start()
        log.info("gesture wake ready (%s)", ", ".join(self.phrases))

    def push(self, frame: bytes) -> bool:
        for row in self._detector.push(frame):
            if row.type != "gesture":
                if self._log_candidates:
                    log.info(
                        "ear: %s (peak %.2f, width %.1f ms, tilt %.2f, fall %.0f dB)%s",
                        row.kind, row.peak, row.width_ms, row.tilt, row.fall_db,
                        f" — {row.reason}" if row.reason else "",
                    )
                continue
            shown = row.kind.replace("_", " ")
            if row.kind not in self._actions and row.kind not in self._wake:
                # A lone clap released unpaired is the commonest way a
                # double clap fails, and it used to leave no trace.
                if self._log_candidates:
                    log.info(
                        "ear: %s stood alone (peak %.2f, width %.1f ms, tilt %.2f, fall %.0f dB) — no second clap inside %d ms",
                        shown, row.peak, row.width_ms, row.tilt, row.fall_db, self._detector.config.double_max_ms,
                    )
                continue
            if row.kind in self._actions:
                verb, action = self._actions[row.kind]
                log.info("gesture: %s — %s (peak %.2f, tilt %.2f)", shown, verb, row.peak, row.tilt)
                task = asyncio.get_running_loop().create_task(action())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            elif row.kind in self._wake:
                log.info("wake: %s (peak %.2f, tilt %.2f)", shown, row.peak, row.tilt)
                self.source = shown
                return True
        return False

    def reset(self) -> None:
        # A half-made pair must not survive a turn, but the ears stay warm.
        self._detector.clear()

    async def close(self) -> None:
        return None


class AnyWake:
    """Several detectors, one answer: addressed if any of them says so.

    Every member sees every frame, so a wake word's model keeps its buffers
    current even on the frame a snap fires. ``arm`` is exposed only when a
    member has it, so the pipeline's ``hasattr`` test for the hotkey keeps
    meaning what it meant.
    """

    def __init__(self, members: list[WakeDetector]) -> None:
        self._members = members
        self.source = "spoken"
        """Whichever member fired last says how Ciel was addressed."""
        armable = [m for m in members if hasattr(m, "arm")]
        if armable:
            self.arm = lambda: [m.arm() for m in armable]  # type: ignore[attr-defined]

    async def start(self) -> None:
        for member in self._members:
            await member.start()

    def push(self, frame: bytes) -> bool:
        # No short-circuit: every member must consume the frame.
        fired = [member.push(frame) for member in self._members]
        for member, hit in zip(self._members, fired):
            if hit:
                self.source = getattr(member, "source", "spoken")
                break
        return any(fired)

    def reset(self) -> None:
        for member in self._members:
            member.reset()

    async def close(self) -> None:
        for member in self._members:
            await member.close()


def build_wake_detector(config, gestures=None, models_dir: "Path | None" = None, spotify=None) -> WakeDetector:  # noqa: ANN001 - WakeConfig, GestureConfig, SpotifyConfig
    """Pick a detector from config, with the gesture ear beside it when asked.

    ``models_dir`` is where a fetched veto model lives (``~/.ciel/models``);
    the ear needs it only when ``gestures.veto_model`` is set. ``spotify``
    is the connector's config: switched on, two claps go through the Web
    API before the desktop app."""
    if config.mode == "hotkey":
        base: WakeDetector = HotkeyWake()
    elif config.mode == "always":
        return AlwaysAwake()
    elif config.mode == "wakeword":
        base = OpenWakeWord(
            model=config.model,
            threshold=config.threshold,
            vad_threshold=config.vad_threshold,
        )
    else:
        raise ValueError(f"unknown wake mode {config.mode!r}")
    wake = {kind for kind, on in (("snap", config.snap), ("double_clap", config.double_clap == "wake")) if on}
    actions: dict[str, tuple[str, Callable[[], Awaitable[object]]]] = {}
    if config.double_clap == "play":
        from ciel import music

        if not music.valid_uri(config.double_clap_plays):
            raise ValueError(
                f"wake.double_clap_plays must be a Spotify URI such as spotify:artist:<id>, not {config.double_clap_plays!r}"
            )
        uri = config.double_clap_plays
        api = music.web_player(spotify)
        actions["double_clap"] = ("play music", lambda: music.play(uri, api=api))
    if not (wake or actions):
        return base
    from ciel.audio.gestures import GestureDetector
    from ciel.config import GestureConfig

    gestures = gestures or GestureConfig()
    from ciel.audio.gestures import first_of

    keys = None
    if gestures.keyboard_veto_ms > 0:
        from ciel.audio.keys import KeyboardVeto

        keys = KeyboardVeto(gestures.keyboard_veto_ms, log_candidates=gestures.log_candidates)
        if not keys.available:
            keys = None
    model = None
    if gestures.veto_model:
        from ciel.audio.audioset import AudioSetVeto

        model = AudioSetVeto(gestures.veto_model, (models_dir or Path.home() / ".ciel") / "models", gestures.veto_threshold)
    # The keyboard answers first: a tenth of a millisecond, and certain.
    ear = GestureWake(GestureDetector(gestures, first_of(keys, model)), wake, actions, log_candidates=gestures.log_candidates)
    ear.veto = model
    return AnyWake([base, ear])


def wake_phrases(detector: WakeDetector) -> tuple[str, ...]:
    """The gestures a detector answers to, for the ready line."""
    if isinstance(detector, GestureWake):
        return detector.phrases
    if isinstance(detector, AnyWake):
        return tuple(p for m in detector._members for p in wake_phrases(m))
    return ()


__all__ = [
    "WakeDetector",
    "HotkeyWake",
    "AlwaysAwake",
    "OpenWakeWord",
    "GestureWake",
    "AnyWake",
    "build_wake_detector",
    "wake_phrases",
]
