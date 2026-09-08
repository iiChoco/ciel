"""Apple's voice processing for the microphone, with the speaker kept outside.

The raw audio path can discard a queue, but it cannot remove audio still
coming from the speakers. This backend runs AVAudioEngine's voice processing
in one Swift process and returns the processed microphone signal before wake
detection, Barn Door, endpointing, or transcription.

**The canceller hears; the old speaker speaks.** Apple's far end treats what
the engine plays as a phone call, and the room heard a lisp
(``reports/2026-09-07-apple-audio-review.md``). The echo reference is taken at
the device rather than at the engine, so by default Ciel's voice plays through
the PortAudio speaker on the same default output and is cancelled all the same.
The engine route stays selectable for comparison.

**On the engine route, a receipt means heard.** Playback stays in flight until Apple's
``dataPlayedBack`` callback, including the last partial chunk. At most twenty
50 ms chunks are outstanding; a stop drops all of them inside the engine.

**A gap leaves evidence.** Begin and end delimit an utterance. A native
empty-queue refill reports starvation; normal endings do not count. The time
reported is a lower bound because audible receipts follow device latency.

**Capture cannot wait for Python.** The helper copies into bounded storage,
converts off the capture callback, and sends mono 16 kHz PCM. This side restores
30 ms frames and retains the existing microphone watchdog and queue limit.

**An unavailable canceller is an unavailable room.** Startup, protocol, or
route failures close capture and fail pending playback. There is no silent
fallback to raw audio. The backend is opt-in until the user's acoustic route
has been checked; it follows macOS's default input and output devices.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator, Callable, Self

from ciel.audio.input import MicStream
from ciel.audio.output import Player
from ciel.config import AudioConfig, FRAME_BYTES, SAMPLE_RATE

log = logging.getLogger(__name__)
SOURCE = Path(__file__).parent / "native" / "CielAudio.swift"
_HEADER = struct.Struct("<BII")
_PLAY, _STOP, _CAPTURE, _PLAYED, _READY, _ERROR, _BEGIN, _END, _UNDERRUN = range(1, 10)
_MAX_PAYLOAD = 65536
_IO_TIMEOUT = 5.0
_PLAYBACK_WINDOW = 20


class AppleAudioError(RuntimeError):
    """A failed device boundary, distinct from a failed synthesizer."""


def ensure_built(source: Path = SOURCE, bin_dir: Path | None = None) -> Path:
    """Rebuild atomically at a stable path when source or compiler changes."""
    if sys.platform != "darwin":
        raise RuntimeError("Apple audio requires macOS")
    compiler = shutil.which("swiftc")
    signer = shutil.which("codesign")
    if compiler is None or signer is None:
        raise RuntimeError("Apple audio needs the Xcode command-line tools (xcode-select --install)")
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=15)
    if version.returncode:
        raise RuntimeError("Apple audio could not identify the Swift compiler")
    bin_dir = bin_dir if bin_dir is not None else Path.home() / ".ciel" / "bin"
    metadata = source.with_name("Info.plist")
    fingerprint = hashlib.sha256(source.read_bytes() + metadata.read_bytes() +
                                 json.dumps([2, os.uname().machine, str(Path(compiler).resolve()), version.stdout]).encode()).hexdigest()
    bin_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = bin_dir / "cielaudio"
    stamp = bin_dir / "cielaudio.build.json"
    # Concurrent startup must not publish mismatched executable/manifest pairs.
    # Replacing the inode leaves an already-running helper's code untouched.
    lock_fd = os.open(bin_dir / ".cielaudio-build.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(stamp.read_text())
        except (OSError, ValueError):
            cached = {}
        if not isinstance(cached, dict):
            cached = {}
        current = binary.is_file() and not binary.is_symlink()
        if current and cached.get("fingerprint") == fingerprint and cached.get("binary_sha256") == hashlib.sha256(binary.read_bytes()).hexdigest():
            _prune_builds(bin_dir)
            return binary
        fd, name = tempfile.mkstemp(prefix=".cielaudio-", dir=bin_dir)
        os.close(fd)
        temporary = Path(name)
        manifest: Path | None = None
        try:
            result = subprocess.run(
                [compiler, "-O", "-framework", "AVFoundation",
                 "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                 "-Xlinker", str(metadata), "-o", str(temporary), str(source)],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode:
                raise RuntimeError(f"Apple audio helper did not compile: {result.stderr[-2000:]}")
            signed = subprocess.run([signer, "--force", "--sign", "-", "--identifier", "ai.ciel.audio", str(temporary)],
                                    capture_output=True, text=True, timeout=15)
            if signed.returncode:
                raise RuntimeError(f"Apple audio helper could not be signed: {signed.stderr[-1000:]}")
            temporary.chmod(0o700)
            record = {"fingerprint": fingerprint, "binary_sha256": hashlib.sha256(temporary.read_bytes()).hexdigest()}
            fd, name = tempfile.mkstemp(prefix=".cielaudio-manifest-", dir=bin_dir)
            manifest = Path(name)
            with os.fdopen(fd, "w") as stream:
                json.dump(record, stream)
            temporary.replace(binary)
            manifest.replace(stamp)
            _prune_builds(bin_dir)
        finally:
            temporary.unlink(missing_ok=True)
            if manifest is not None:
                manifest.unlink(missing_ok=True)
    return binary


def _prune_builds(bin_dir: Path) -> None:
    """Remove only this helper's former hash-named build artifacts."""
    for old in bin_dir.iterdir():
        if re.fullmatch(r"cielaudio-[0-9a-f]{16}", old.name) and old.is_file() and not old.is_symlink():
            try:
                old.unlink()
            except OSError:
                log.warning("Apple audio could not remove an obsolete helper build")


class AppleSession:
    """One subprocess and one reader for the paired input and output."""

    def __init__(self, config: AudioConfig, sample_rate: int) -> None:
        if config.input_device is not None or config.output_device is not None:
            raise ValueError("Apple audio uses macOS default devices; clear audio.input_device and output_device")
        if config.apple_ducking not in ("min", "mid", "max"):
            raise ValueError("audio.apple_ducking must be min, mid, or max")
        if not 8000 <= sample_rate <= 96000:
            raise ValueError("Apple playback sample rate must be between 8000 and 96000 Hz")
        self.config = config
        self.sample_rate = sample_rate
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.stderr: asyncio.Task[None] | None = None
        self.ready: asyncio.Future[None] | None = None
        self.pending: dict[int, asyncio.Future[bool]] = {}
        self.next_id = 0
        self.underruns = 0
        self.formats: dict[str, int | float] = {}
        self.error: str | None = None
        self.mic: AppleMic | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.closing = False

    async def start(self) -> None:
        if self.proc is not None:
            self._healthy()
            return
        self.loop = asyncio.get_running_loop()
        binary = await asyncio.to_thread(ensure_built)
        self.ready = self.loop.create_future()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                str(binary), str(self.sample_rate), self.config.apple_ducking,
                str(self.config.apple_agc).lower(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            self.reader = asyncio.create_task(self._read())
            self.stderr = asyncio.create_task(self._stderr())
            await asyncio.wait_for(asyncio.shield(self.ready), 60.0)
            self._healthy()
            log.info("Apple audio ready: echo cancellation on, 16000 Hz microphone, %d Hz playback, ducking %s",
                     self.sample_rate, self.config.apple_ducking)
            log.info("Apple device formats: input %s Hz, output %s Hz, mixer %s Hz",
                     self.formats["input_rate"], self.formats["output_rate"], self.formats["mixer_rate"])
        except BaseException:
            await self.close()
            raise

    def _healthy(self) -> None:
        if self.error or self.proc is None or self.proc.returncode is not None or self.closing:
            raise AppleAudioError(self.error or "Apple audio is not running")

    def _send(self, kind: int, ident: int = 0, payload: bytes = b"") -> None:
        self._healthy()
        assert self.proc is not None and self.proc.stdin is not None
        try:
            self.proc.stdin.write(_HEADER.pack(kind, ident, len(payload)) + payload)
        except OSError as exc:
            raise AppleAudioError(str(exc)) from exc

    def begin(self) -> None:
        self.underruns = 0
        self._send(_BEGIN)

    def end(self) -> None:
        self._send(_END)

    async def submit(self, pcm: bytes) -> asyncio.Future[bool]:
        if not pcm or len(pcm) % 2 or len(pcm) > _MAX_PAYLOAD:
            raise ValueError("Apple playback needs a bounded whole-sample PCM chunk")
        self._healthy()
        self.next_id = self.next_id % 0xFFFFFFFF + 1
        ident = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            self._send(_PLAY, ident, pcm)
            assert self.proc is not None and self.proc.stdin is not None
            await asyncio.wait_for(self.proc.stdin.drain(), _IO_TIMEOUT)
        except BaseException as exc:
            self.pending.pop(ident, None)
            future.cancel()
            if isinstance(exc, (OSError, asyncio.TimeoutError)):
                raise AppleAudioError(str(exc) or "Apple playback write timed out") from exc
            raise
        return future

    def stop(self) -> None:
        if self.proc is not None and not self.error and not self.closing:
            try:
                self._send(_STOP)
            except (RuntimeError, OSError):
                pass
        for future in self.pending.values():
            if not future.done():
                future.set_result(False)
        self.pending.clear()

    def _failed(self, message: str) -> None:
        self.stop()
        self.error = message
        if not self.closing:
            log.error("Apple audio stopped: %s", message)
        if self.ready is not None and not self.ready.done():
            self.ready.set_exception(RuntimeError(message))
        for future in self.pending.values():
            if not future.done():
                future.set_result(False)
        self.pending.clear()
        if self.mic is not None:
            self.mic._closed = True
            self.mic.drain()
            if self.mic._wakeup is not None:
                self.mic._wakeup.set()

    async def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        try:
            while True:
                kind, ident, count = _HEADER.unpack(await stream.readexactly(_HEADER.size))
                if count > _MAX_PAYLOAD:
                    raise ValueError("Oversized Apple audio frame")
                payload = await stream.readexactly(count)
                if kind == _CAPTURE:
                    if len(payload) % 2:
                        raise ValueError("Partial sample in Apple microphone frame")
                    if self.mic is not None:
                        self.mic.receive(payload)
                elif kind == _PLAYED:
                    if payload not in (b"\x00", b"\x01"):
                        raise ValueError("Invalid Apple playback receipt")
                    future = self.pending.pop(ident, None)
                    if future is not None and not future.done():
                        future.set_result(payload == b"\x01")
                elif kind == _READY:
                    hello = json.loads(payload)
                    if hello.get("rate") != SAMPLE_RATE or hello.get("echo_cancelled") is not True:
                        raise ValueError("Apple audio did not confirm echo cancellation and microphone format")
                    if self.ready is None or self.ready.done():
                        raise ValueError("Unexpected Apple audio ready frame")
                    for name in ("input_rate", "output_rate", "mixer_rate"):
                        value = hello.get(name)
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 8000 <= value <= 192000:
                            raise ValueError("Apple audio did not report valid device rates")
                        self.formats[name] = value
                    self.ready.set_result(None)
                elif kind == _UNDERRUN:
                    gap = json.loads(payload)
                    count, milliseconds = gap.get("count"), gap.get("gap_ms")
                    if (type(count) is not int or count < 1 or
                            type(milliseconds) not in (int, float) or not 0 <= milliseconds <= 86400000):
                        raise ValueError("Invalid Apple playback underrun report")
                    self.underruns = count
                    log.warning("Apple playback underrun: queue empty for at least %.1f ms (gap %d in this utterance)",
                                milliseconds, count)
                elif kind == _ERROR:
                    raise RuntimeError(payload.decode(errors="replace")[:1000])
                else:
                    raise ValueError("Unknown Apple audio frame")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = "Apple audio helper exited" if isinstance(exc, asyncio.IncompleteReadError) else str(exc)
            self._failed(message)

    async def _stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        # Framework diagnostics can be noisy; drain without retaining or exposing
        # device/user descriptions. The framed error is the actionable message.
        while await self.proc.stderr.read(4096):
            pass

    async def close(self) -> None:
        self.closing = True
        self._failed(self.error or "Apple audio closed")
        proc, self.proc = self.proc, None
        if proc is not None:
            if proc.stdin is not None:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 1.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        for task in (self.reader, self.stderr):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (self.reader, self.stderr) if t is not None), return_exceptions=True)
        self.reader = self.stderr = None
        if self.ready is not None:
            if not self.ready.done():
                self.ready.cancel()
            elif not self.ready.cancelled():
                self.ready.exception()


class AppleMic(MicStream):
    """The existing microphone queue and watchdog, fed by processed PCM."""

    _warn_on_digital_silence = False

    def __init__(self, session: AppleSession) -> None:
        super().__init__(session.config)
        self.session = session
        session.mic = self
        self.partial = bytearray()

    async def __aenter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        self._closed = False
        try:
            await self.session.start()
        except BaseException:
            await self.close()
            raise
        return self

    def receive(self, pcm: bytes) -> None:
        if self._closed:
            return
        self.partial.extend(pcm)
        while len(self.partial) >= FRAME_BYTES:
            frame = bytes(self.partial[:FRAME_BYTES])
            del self.partial[:FRAME_BYTES]
            self._on_audio(frame, FRAME_BYTES // 2, None, None)

    def drain(self) -> None:
        super().drain()
        self.partial.clear()

    async def close(self) -> None:
        await self.session.close()
        await super().close()


def _speech_gate(config: AudioConfig) -> "webrtcvad.Vad":
    """The processed-speech test both Apple speakers apply before barge-in."""
    try:
        import webrtcvad
    except ImportError as exc:
        raise RuntimeError("Apple audio requires webrtcvad-wheels from the spoke group; restore the environment with uv sync --locked --all-extras") from exc
    return webrtcvad.Vad(config.vad_aggressiveness)


class SplitPlayer(Player):
    """The PortAudio speaker on the default output, paired with Apple capture.

    Nothing here touches the helper: a lost speaker is a lost utterance, never
    a closed microphone. Stop behaves as it always has on this speaker.
    """

    def __init__(self, config: AudioConfig, sample_rate: int, muted: Callable[[], bool] | None = None) -> None:
        if config.output_device is not None:
            raise ValueError("Apple audio cancels the default output; clear audio.output_device")
        super().__init__(config, sample_rate, muted)
        self._barge_vad = _speech_gate(config)

    def accepts_barge(self, frame: bytes) -> bool:
        """Processed near-end speech, in addition to the loop's energy gate."""
        return len(frame) == FRAME_BYTES and self._barge_vad.is_speech(frame, SAMPLE_RATE)


class ApplePlayer(Player):
    """The engine route: bounded scheduling and audible receipts inside Apple's engine."""

    def __init__(self, session: AppleSession, muted: Callable[[], bool] | None = None) -> None:
        super().__init__(session.config, session.sample_rate, muted)
        self.session = session
        self._generation = 0
        self._stop_wait = asyncio.Event()
        self._barge_vad = _speech_gate(session.config)

    def accepts_barge(self, frame: bytes) -> bool:
        """Processed near-end speech, in addition to the loop's energy gate."""
        return len(frame) == FRAME_BYTES and self._barge_vad.is_speech(frame, SAMPLE_RATE)

    async def open(self) -> None:
        await self.session.start()

    async def close(self) -> None:
        self.stop()
        self.session.stop()

    def stop(self) -> None:
        super().stop()
        generation = self._generation
        def interrupt() -> None:
            if generation == self._generation:
                self._stop_wait.set()
                self.session.stop()
        if self.session.loop is not None and not self.session.loop.is_closed():
            self.session.loop.call_soon_threadsafe(interrupt)

    async def _next(self, chunks: AsyncIterator[bytes]) -> bytes | None:
        pull = asyncio.create_task(anext(chunks))
        stopped = asyncio.create_task(self._stop_wait.wait())
        try:
            done, _ = await asyncio.wait((pull, stopped), return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                return None
            try:
                return pull.result()
            except StopAsyncIteration:
                return None
        finally:
            for task in (pull, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pull, stopped, return_exceptions=True)

    async def _played(self, future: asyncio.Future[bool]) -> bool:
        try:
            return await asyncio.wait_for(future, _IO_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise AppleAudioError("Apple playback receipt timed out") from exc

    async def play(self, chunks: AsyncIterator[bytes]) -> bool:
        requested = time.monotonic()
        async with aclosing(chunks):
            if self._muted is not None and self._muted():
                return True
            async with self._play_lock:
                if self._muted is not None and self._muted():
                    return True
                if self._stop_at is not None and self._stop_at >= requested:
                    return False
                self._generation += 1
                self._stop.clear()
                self._stop_wait.clear()
                self._device_lost = False
                self._playing.set()
                waiting: deque[asyncio.Future[bool]] = deque()
                buffer = bytearray()
                completed = False
                try:
                    self.session.begin()
                    while True:
                        chunk = await self._next(chunks)
                        if self._stop.is_set():
                            return False
                        if chunk is None:
                            break
                        buffer.extend(chunk)
                        while len(buffer) >= self._chunk_bytes:
                            piece = bytes(buffer[:self._chunk_bytes])
                            del buffer[:self._chunk_bytes]
                            waiting.append(await self.session.submit(piece))
                            if len(waiting) >= _PLAYBACK_WINDOW:
                                if not await self._played(waiting.popleft()):
                                    self._device_lost = self.session.error is not None
                                    return False
                            if self._stop.is_set():
                                return False
                    if len(buffer) % 2:
                        raise ValueError("TTS ended with a partial PCM sample")
                    if buffer:
                        waiting.append(await self.session.submit(bytes(buffer)))
                    self.session.end()
                    while waiting:
                        if not await self._played(waiting.popleft()):
                            self._device_lost = self.session.error is not None
                            return False
                    completed = not self._stop.is_set()
                    return completed
                except AppleAudioError as exc:
                    self._device_lost = True
                    self.session._failed(str(exc) or "Apple playback timed out")
                    return False
                finally:
                    if not completed:
                        self.session.stop()
                    self._playing.clear()
