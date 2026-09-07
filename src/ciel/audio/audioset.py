"""A second opinion on hand sounds — AudioSet's ear, used only to say no.

Part of the wake boundary (codename **Characteristic**), beside the
gesture ear in ``audio/gestures.py``.

Why it exists: the gesture ear's rules are four numbers read off one
room, and the sounds that fool them are not other hand sounds but the
room's ordinary ones — a keystroke has a snap's shape at a snap's
loudness, a plosive in speech has a clap's. Telling those apart is what
a model trained on two million labelled clips does well, and YAMNet,
Google's AudioSet classifier, names *typing*, *computer keyboard*,
*speech*, and *music* with confidence on this microphone. It does not
name snaps and claps well: on 2026-09-06 it missed half of a set of
real snaps and called a loud clap a snap. So it is not the judge here.
The rules judge; the model may veto.

Invariants:

- **The model only ever says no.** A gesture the rules did not find is
  never found by the model; a gesture the rules found is dropped when
  the model hears the room doing something else — typing, talking,
  music — above ``veto_threshold``. The reason names what it heard.
- **One file, one hash.** The model is fetched once into the models
  directory from a pinned URL and checked against a pinned SHA-256
  before it is ever loaded; a file that does not match is refused, not
  used. A path in config replaces the download with a model of the same
  interface.
- **Cheap enough for the frame loop.** One second of audio scores in
  about two milliseconds on Apple silicon, and the model is asked only
  about impulses the rules already accepted — a few times a minute.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

YAMNET_URL = "https://huggingface.co/andrelgomes/yamnet-onnx/resolve/main/yamnet.onnx"
"""A tf2onnx export of Google's YAMNet (Apache 2.0), 16 MB, mirrored on
Hugging Face. Input: a float32 mono waveform at 16 kHz of any length.
Output 0: AudioSet scores, one row of 521 per 0.96 s frame."""

YAMNET_SHA256 = "1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19"

SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 15_600
"""One YAMNet frame: 0.975 s. The ear hands over the second that ends
just after the impulse, so the keystrokes or the sentence around it are
in the picture."""

VETO_CLASSES: dict[int, str] = {
    0: "speech",
    2: "conversation",
    132: "music",
    378: "typing",
    380: "computer keyboard",
}
"""AudioSet indices (from yamnet_class_map.csv) of the sounds a snap or
a clap is mistaken for. Not *clicking* or *tick*: a real snap scores a
little of those, and a veto must never be a second, stricter judge."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AudioSetVeto:
    """Callable on a window of samples: the sound it was mistaken for, or None."""

    def __init__(self, model: str, models_dir: Path, threshold: float = 0.3, session=None) -> None:  # noqa: ANN001 - an onnxruntime session, or a probe's fake
        self._spec = model
        self._models_dir = models_dir
        self._threshold = threshold
        self._session = session
        self.path: Path | None = None

    # ── loading ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Fetch the model if it is the pretrained one and missing, verify it,
        and open a session. Blocking: call from a thread or a tester."""
        if self._session is not None:
            # A probe's fake: nothing to fetch, only the input to name.
            self._input = self._session.get_inputs()[0].name
            return
        if self._spec == "yamnet":
            path = self._models_dir / "yamnet.onnx"
            if not path.exists() or _sha256(path) != YAMNET_SHA256:
                self._download(path)
        else:
            path = Path(self._spec).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"gestures.veto_model {self._spec!r} does not exist")
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self.path = path
        log.info("audioset veto ready (%s, threshold %.2f)", path.name, self._threshold)

    async def start(self) -> None:
        await asyncio.to_thread(self.load)

    def _download(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("fetching the AudioSet model (16 MB) into %s", path.parent)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".yamnet-", suffix=".part")
        os.close(fd)
        try:
            urllib.request.urlretrieve(YAMNET_URL, tmp)
            got = _sha256(Path(tmp))
            if got != YAMNET_SHA256:
                raise ValueError(
                    f"the AudioSet model at {YAMNET_URL} does not match its pinned hash "
                    f"(got {got[:12]}…, expected {YAMNET_SHA256[:12]}…); refusing to use it"
                )
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)

    # ── judging ──────────────────────────────────────────────────────────────

    def __call__(self, window: np.ndarray) -> str | None:
        """What the room was doing, if it was doing something a snap or a
        clap is mistaken for; else None. ``window`` is float samples at
        16 kHz, the second ending just after the impulse."""
        if self._session is None:
            return None
        frames = self._session.run(None, {self._input: window.astype(np.float32)})[0]
        scores = frames.max(axis=0)
        index = max(VETO_CLASSES, key=lambda i: float(scores[i]))
        score = float(scores[index])
        if score >= self._threshold:
            return f"{VETO_CLASSES[index]} {score:.2f}"
        return None


__all__ = ["AudioSetVeto", "VETO_CLASSES", "WINDOW_SAMPLES", "YAMNET_URL", "YAMNET_SHA256"]
