"""Echo cancellation without asking the Mac to turn its speakers down.

Apple's voice-processing unit couples echo cancellation to ducking other
playback. For an always-open assistant, a conversation near the microphone
therefore changes music volume even before a wake. This backend takes a
nonmuting Core Audio device tap and processes only the microphone with WebRTC.

**Both ears share a clock.** The native helper aggregates the default mic and
stereo output tap, with tap drift compensation. It resamples all three channels
together, detects discontinuities, and hands off bounded blocks. A short capture
hold lets a reference delivered late by the hardware precede its acoustic echo.

**The speaker is untouched.** The existing PortAudio speaker renders Ciel.
The reference includes it and other processes on the default output. Nothing
in this backend mutes or changes the gain of those streams. A separate
zero-filled PortAudio stream keeps the device tap clock running while idle.

**Protection is continuous or visibly unavailable.** WebRTC keeps its adaptive
state between turns, Stop, and queue drains. A paired capture gap resets DSP
and discards queued microphone audio without restarting the spoke. Bad frames, failed helpers, lost
routes, and missing reference delivery end capture; none enables raw listening.
The tapped playback stays in memory and never reaches transcription or disk.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from typing import Self

import numpy as np
import sounddevice as sd

from ciel.audio.input import MicStream, float_to_pcm
from ciel.config import AudioConfig, FRAME_BYTES, SAMPLE_RATE

log = logging.getLogger(__name__)
SOURCE = Path(__file__).parent / "native" / "CielCapture.swift"
HEADER = struct.Struct("<II")
BLOCK_SAMPLES = 160
BLOCK_BYTES = BLOCK_SAMPLES * 3 * 4
MAX_PAYLOAD = 49152
_STARTUP_TIMEOUT = 30.0
_CAPTURE_TIMEOUT = 5.0


def ensure_built(source: Path = SOURCE, bin_dir: Path | None = None) -> Path:
    """Publish a private helper atomically at a stable permission identity."""
    if sys.platform != "darwin":
        raise RuntimeError("WebRTC device capture requires macOS 14.2 or later")
    compiler, signer = shutil.which("swiftc"), shutil.which("codesign")
    if not compiler or not signer:
        raise RuntimeError("WebRTC capture needs the Xcode command-line tools (xcode-select --install)")
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=15)
    if version.returncode:
        raise RuntimeError("Cannot identify the capture compiler")
    metadata = source.with_name("CaptureInfo.plist")
    digest = hashlib.sha256(source.read_bytes() + metadata.read_bytes() + json.dumps(
        [1, os.uname().machine, str(Path(compiler).resolve()), version.stdout]
    ).encode()).hexdigest()
    folder = bin_dir if bin_dir is not None else Path.home() / ".ciel" / "bin"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary, stamp = folder / "cielcapture", folder / "cielcapture.build.json"
    fd = os.open(folder / ".cielcapture-build.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(stamp.read_text())
        except (OSError, ValueError):
            cached = {}
        if (isinstance(cached, dict) and binary.is_file() and not binary.is_symlink()
                and cached.get("fingerprint") == digest
                and cached.get("binary_sha256") == hashlib.sha256(binary.read_bytes()).hexdigest()):
            return binary
        fd, name = tempfile.mkstemp(prefix=".cielcapture-", dir=folder)
        os.close(fd)
        temporary = Path(name)
        manifest: Path | None = None
        try:
            result = subprocess.run([compiler, "-O", "-framework", "AVFoundation", "-Xlinker", "-sectcreate",
                                     "-Xlinker", "__TEXT", "-Xlinker", "__info_plist", "-Xlinker", str(metadata),
                                     "-o", str(temporary), str(source)], capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise RuntimeError(f"Capture helper did not compile: {result.stderr[-2000:]}")
            signed = subprocess.run([signer, "--force", "--sign", "-", "--identifier", "ai.ciel.capture", str(temporary)],
                                    capture_output=True, text=True, timeout=15)
            if signed.returncode:
                raise RuntimeError(f"Capture helper could not be signed: {signed.stderr[-1000:]}")
            temporary.chmod(0o700)
            fd, name = tempfile.mkstemp(prefix=".cielcapture-manifest-", dir=folder)
            manifest = Path(name)
            with os.fdopen(fd, "w") as stream:
                json.dump({"fingerprint": digest, "binary_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest()}, stream)
            temporary.replace(binary)
            manifest.replace(stamp)
        finally:
            temporary.unlink(missing_ok=True)
            if manifest is not None:
                manifest.unlink(missing_ok=True)
    return binary


class EchoProcessor:
    """Feed complete aligned blocks to AEC3; retain filter state across turns."""

    def __init__(self, capture_delay_ms: int) -> None:
        if type(capture_delay_ms) is not int or not 0 <= capture_delay_ms <= 200 or capture_delay_ms % 10:
            raise ValueError("audio.webrtc_capture_delay_ms must be a multiple of 10 between 0 and 200")
        try:
            from pywebrtc_audio import AudioProcessor
        except ImportError as exc:
            raise RuntimeError("WebRTC capture requires pywebrtc-audio; run uv sync --locked --all-extras") from exc
        self._aec = AudioProcessor(sample_rate=SAMPLE_RATE, num_channels=2, echo_cancellation=True,
                                   noise_suppression=False, auto_gain_control=False)
        self._partial = bytearray()
        self._hold: deque[np.ndarray] = deque(np.zeros(BLOCK_SAMPLES, dtype=np.float32) for _ in range(capture_delay_ms // 10))
        self._pcm = bytearray()

    def push(self, payload: bytes) -> list[bytes]:
        if not payload or len(payload) > MAX_PAYLOAD or len(payload) % 12:
            raise ValueError("Malformed microphone/reference packet")
        samples = np.frombuffer(payload, dtype="<f4")
        if not np.isfinite(samples).all():
            raise ValueError("Nonfinite microphone/reference sample")
        self._partial.extend(payload)
        result: list[bytes] = []
        while len(self._partial) >= BLOCK_BYTES:
            block = np.frombuffer(bytes(self._partial[:BLOCK_BYTES]), dtype="<f4").reshape(-1, 3)
            del self._partial[:BLOCK_BYTES]
            self._hold.append(block[:, 0].copy())
            near = self._hold.popleft()
            # Preserve left/right references: downmixing them can erase an
            # out-of-phase signal that still reaches the physical microphone.
            cleaned = self._aec.process(np.repeat(near, 2), np.ascontiguousarray(block[:, 1:]).reshape(-1))
            mono = cleaned.reshape(-1, 2).mean(axis=1)
            if not np.isfinite(mono).all():
                raise ValueError("Echo processor returned nonfinite audio")
            self._pcm.extend(float_to_pcm(mono))
            while len(self._pcm) >= FRAME_BYTES:
                result.append(bytes(self._pcm[:FRAME_BYTES]))
                del self._pcm[:FRAME_BYTES]
        return result


def _silence(data: np.ndarray, frames: int, time_info: object, status: object) -> None:
    """Keep the device tap clock running before any audible playback exists."""
    data.fill(0)


class WebRTCMic(MicStream):
    """A protected microphone backed by the nonmuting helper and AEC3."""

    _warn_on_digital_silence = False

    def __init__(self, config: AudioConfig) -> None:
        if config.input_device is not None or config.output_device is not None:
            raise ValueError("WebRTC capture uses macOS default devices; clear audio.input_device and output_device")
        super().__init__(config)
        self.processor = EchoProcessor(config.webrtc_capture_delay_ms)
        self._clock: sd.OutputStream | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.stderr: asyncio.Task[None] | None = None
        self.ready: asyncio.Future[None] | None = None
        self.error: str | None = None
        self.discontinuities = 0
        self._diagnostic = ""
        self.formats: dict[str, object] = {}

    async def __aenter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        self._closed = False
        self.ready = self._loop.create_future()
        try:
            binary = await asyncio.to_thread(ensure_built)
            # A device tap alone does not advance on an idle output. Keep a
            # separate zero-filled ordinary stream alive even while muted.
            # This is not Apple's voice-processing output and cannot duck.
            rate = sd.query_devices(kind="output")["default_samplerate"]
            self._clock = sd.OutputStream(samplerate=rate, channels=2, dtype="float32", callback=_silence)
            self._clock.start()
            self.proc = await asyncio.create_subprocess_exec(str(binary), stdin=asyncio.subprocess.PIPE,
                                                           stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            self.stderr = asyncio.create_task(self._read_stderr())
            self.reader = asyncio.create_task(self._read())
            await asyncio.wait_for(asyncio.shield(self.ready), _STARTUP_TIMEOUT)
            if self.error or self._closed:
                raise RuntimeError(self.error or "WebRTC capture closed during startup")
            log.info("WebRTC audio ready: echo cancellation on, nonmuting stereo reference, %s Hz hardware, %d ms capture hold",
                     self.formats["hardware_rate"], self._config.webrtc_capture_delay_ms)
            return self
        except asyncio.TimeoutError as exc:
            await self.close()
            raise RuntimeError("WebRTC capture did not start; allow Ciel's Microphone and System Audio Recording access in System Settings") from exc
        except BaseException:
            await self.close()
            raise

    async def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while chunk := await self.proc.stderr.read(4096):
            self._diagnostic = (self._diagnostic + chunk.decode(errors="replace"))[-1500:]

    async def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        hello = False
        try:
            while True:
                timeout = _CAPTURE_TIMEOUT if self.ready is not None and self.ready.done() else _STARTUP_TIMEOUT
                kind, count = HEADER.unpack(await asyncio.wait_for(stream.readexactly(HEADER.size), timeout))
                if count == 0 or count > MAX_PAYLOAD:
                    raise ValueError("Invalid capture packet size")
                payload = await asyncio.wait_for(stream.readexactly(count), timeout)
                if self._clock is None or not self._clock.active:
                    raise RuntimeError("The output reference clock stopped; echo protection is unavailable")
                if kind == 1 and not hello:
                    data = json.loads(payload)
                    if (not isinstance(data, dict) or data.get("rate") != SAMPLE_RATE or data.get("channels") != 3
                            or data.get("nonmuting") is not True or type(data.get("hardware_rate")) not in (int, float)
                            or not 16000 <= data["hardware_rate"] <= 96000):
                        raise ValueError("Capture helper did not establish the nonmuting paired format")
                    self.formats = data
                    hello = True
                elif kind == 2 and hello:
                    for frame in self.processor.push(payload):
                        self._on_audio(frame, FRAME_BYTES // 2, None, None)
                        if self.ready is not None and not self.ready.done():
                            self.ready.set_result(None)
                elif kind == 3 and hello and payload == b"discontinuity":
                    self.processor = EchoProcessor(self._config.webrtc_capture_delay_ms)
                    self.drain()
                    self.discontinuities += 1
                    if self.discontinuities == 1 or self.discontinuities % 100 == 0:
                        log.warning("Capture gap recovered with fresh echo cancellation (%d); spoke remains running", self.discontinuities)
                else:
                    raise ValueError("Unexpected capture protocol frame")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._closed:
                return
            if isinstance(exc, asyncio.IncompleteReadError):
                if self.stderr is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(self.stderr), 1)
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                message = "Capture helper exited" + (": " + self._diagnostic.strip() if self._diagnostic else "")
            elif isinstance(exc, asyncio.TimeoutError):
                message = "Capture stalled; check Microphone and System Audio Recording permission in System Settings"
            else:
                message = str(exc)
            self.error = message
            self._closed = True
            self.drain()
            if self._wakeup is not None:
                self._wakeup.set()
            if self.ready is not None and not self.ready.done():
                self.ready.set_exception(RuntimeError(message))
            log.error("WebRTC echo protection stopped: %s", message)

    async def close(self) -> None:
        await super().close()
        proc, self.proc = self.proc, None
        if proc is not None:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except asyncio.TimeoutError:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
        for task in (self.reader, self.stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (self.reader, self.stderr) if t is not None), return_exceptions=True)
        self.reader = self.stderr = None
        clock, self._clock = self._clock, None
        if clock is not None:
            try:
                clock.stop()
            finally:
                clock.close()
        if self.ready is not None:
            if not self.ready.done():
                self.ready.cancel()
            elif not self.ready.cancelled():
                self.ready.exception()
