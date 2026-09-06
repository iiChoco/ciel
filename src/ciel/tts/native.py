"""Text-to-speech via Apple's own synthesizer, streamed — the Premium voices.

macOS ships neural voices (Premium, ~1 GB each, a free download under
Accessibility → Spoken Content) that the ``say`` engine never showed
Ciel: ``say`` uses whatever compact voice it is given and renders a whole
file before the first byte, which is why piper won on both sound and
latency. This engine drives the same voices through AVSpeechSynthesizer's
buffer callback instead, from a small Swift helper (``native/CielVoice.swift``)
built with ``swiftc`` on first use and kept alive as a subprocess — PCM
leaves the helper as it is synthesized, so the first chunk is early, and
the voice is Apple's best rather than its fallback.

Mac only, like ``say``. The hub keeps piper. On any failure to build,
start, or find the voice the pipeline falls back to piper and then to
``say``, so a missing toolchain costs voice quality, never speech.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import struct
import time
from pathlib import Path
from typing import AsyncIterator

from ciel.config import TTSConfig
from ciel.tts.normalize import speakable

log = logging.getLogger(__name__)

SOURCE = Path(__file__).parent / "native" / "CielVoice.swift"
BIN_DIR = Path.home() / ".ciel" / "bin"

_HEADER = struct.Struct("<BII")
"""kind · id · payload length, as the helper writes them."""
_BEGIN, _PCM, _END, _ERROR, _READY = 1, 2, 3, 4, 5

_DEFAULT_RATE = 0.5
"""AVSpeechUtteranceDefaultSpeechRate — what Apple calls a normal pace,
about 175 words a minute for the English voices."""
_DEFAULT_WPM = 175.0

_DRAIN_TIMEOUT_S = 2.0
"""How long an abandoned utterance may take to answer a cancel before the
helper is considered wedged and restarted."""


def _speech_rate(wpm: int) -> float:
    """``rate`` (words per minute, the ``say`` knob) → Apple's 0–1 scale,
    so one config value paces both engines the same."""
    return max(0.0, min(1.0, _DEFAULT_RATE * float(wpm) / _DEFAULT_WPM))


class NativeTTS:
    """Apple's synthesizer behind the :class:`~ciel.tts.base.TextToSpeech`
    protocol."""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._sample_rate = 22_050
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._voice = ""
        # One utterance through the helper at a time: its frames are a
        # single ordered stream, and the player consumes sentences in
        # order anyway.
        self._lock = asyncio.Lock()
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def voice(self) -> str:
        """The voice the helper actually loaded ("Jamie (premium)")."""
        return self._voice

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def warm_up(self) -> None:
        if self._proc is not None:
            return
        binary = await asyncio.to_thread(ensure_built)
        await self._spawn(binary)
        started = time.monotonic()
        first: float | None = None
        async for _ in self.stream("Ready."):
            if first is None:
                first = time.monotonic() - started
        log.info("native voice ready (%s, %d Hz, first audio %.0f ms)",
                 self._voice, self._sample_rate, (first or 0.0) * 1000)

    async def _spawn(self, binary: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            str(binary), self._config.native_voice,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._proc = proc
        self._stderr_task = asyncio.create_task(self._drain_stderr(proc))
        kind, _, payload = await self._read_frame()
        if kind == _ERROR:
            await self.close()
            raise RuntimeError(payload.decode(errors="replace"))
        if kind != _READY:
            await self.close()
            raise RuntimeError(f"native voice helper sent kind {kind} before ready")
        hello = json.loads(payload.decode())
        self._voice = f"{hello.get('voice')} ({hello.get('quality')})"
        rate = hello.get("rate")
        if isinstance(rate, int) and rate > 0:
            self._sample_rate = rate

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stderr is not None
        try:
            async for line in proc.stderr:
                text = line.decode(errors="replace").rstrip()
                if text:
                    log.debug("native voice: %s", text)
        except Exception:  # noqa: BLE001 - the helper died; close() notices
            pass

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), 1.0)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

    # ── synthesis ────────────────────────────────────────────────────────────

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        text = speakable(text.strip())
        if not text:
            return
        if self._proc is None:
            await self.warm_up()
        async with self._lock:
            uid = self._next_id
            self._next_id += 1
            await self._send({"op": "speak", "id": uid, "text": text,
                              "rate": _speech_rate(self._config.rate)})
            finished = False
            try:
                while True:
                    kind, fid, payload = await self._read_frame()
                    if fid != uid:
                        continue  # a late frame from an utterance already abandoned
                    if kind == _BEGIN:
                        (rate,) = struct.unpack("<I", payload)
                        if rate and rate != self._sample_rate:
                            self._sample_rate = rate
                    elif kind == _PCM:
                        if payload:
                            yield payload
                    elif kind == _END:
                        finished = True
                        return
                    elif kind == _ERROR:
                        finished = True
                        raise RuntimeError(payload.decode(errors="replace"))
            finally:
                if not finished:
                    await self._abandon(uid)

    async def _abandon(self, uid: int) -> None:
        """The consumer stopped pulling (a barge-in): tell the helper, and
        drain to this utterance's end so the next one starts clean. A
        helper that will not answer is restarted rather than trusted."""
        try:
            await self._send({"op": "cancel", "id": uid})
            async with asyncio.timeout(_DRAIN_TIMEOUT_S):
                while True:
                    kind, fid, _ = await self._read_frame()
                    if fid == uid and kind in (_END, _ERROR):
                        return
        except BaseException:  # noqa: BLE001 - cancelled, timed out, or dead: restart
            log.debug("native voice helper did not settle after a cancel — restarting")
            await self.close()

    async def _send(self, obj: dict[str, object]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("native voice helper is not running")
        try:
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            await self.close()
            raise RuntimeError("native voice helper exited") from exc

    async def _read_frame(self) -> tuple[int, int, bytes]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("native voice helper is not running")
        try:
            header = await proc.stdout.readexactly(_HEADER.size)
            kind, fid, length = _HEADER.unpack(header)
            payload = await proc.stdout.readexactly(length) if length else b""
        except asyncio.IncompleteReadError as exc:
            await self.close()
            raise RuntimeError("native voice helper exited") from exc
        return kind, fid, payload


# ── the build ────────────────────────────────────────────────────────────────


def ensure_built(source: Path = SOURCE, bin_dir: Path = BIN_DIR) -> Path:
    """The helper binary for this source, compiled if it is not there.

    Keyed by a hash of the source so an edit rebuilds and an unchanged
    file never does; the command-line tools' ``swiftc`` is enough (no
    Xcode project). Blocking — call it off the loop.
    """
    text = source.read_bytes()
    digest = hashlib.sha256(text).hexdigest()[:12]
    binary = bin_dir / f"cielvoice-{digest}"
    if binary.exists():
        return binary
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise RuntimeError("swiftc not found — install the Xcode command-line tools "
                           "(xcode-select --install) for the native voice")
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp = binary.with_suffix(".tmp")
    import subprocess

    started = time.monotonic()
    result = subprocess.run(
        [swiftc, "-O", "-framework", "AVFoundation", "-o", str(tmp), str(source)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"swiftc failed: {result.stderr.strip()[:2000]}")
    os.chmod(tmp, 0o755)
    tmp.replace(binary)
    # Older builds are dead weight once a new one exists.
    for old in bin_dir.glob("cielvoice-*"):
        if old != binary and not old.name.endswith(".tmp"):
            old.unlink(missing_ok=True)
    log.info("native voice helper built in %.1fs", time.monotonic() - started)
    return binary


__all__ = ["NativeTTS", "ensure_built"]
