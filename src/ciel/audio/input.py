"""Microphone capture.

PortAudio delivers audio on its own high-priority thread and will not wait for
us. Everything here exists to get frames off that thread and into asyncio
without ever blocking it: the callback does a bounded, non-blocking handoff and
returns immediately. If the consumer falls behind, we drop the *oldest* audio
and count it, because dropping stale audio is recoverable and stalling the
callback is not — a blocked callback produces glitches in the input stream
itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from types import TracebackType
from typing import AsyncIterator, Self

import numpy as np
import sounddevice as sd

from ciel.config import CHANNELS, FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, AudioConfig

log = logging.getLogger(__name__)

# A live microphone delivers frames continuously — silence is frames of near
# zero, not the absence of frames — so a multi-second gap means PortAudio
# stopped calling the callback: the input device was lost. frames() would
# otherwise wait forever, silently freezing the whole pipeline.
_STALL_TIMEOUT_S = 3.0
_STALL_LIMIT = 2  # stalls before capture ends; the first gets a warning

# The other way a microphone goes deaf: frames keep arriving and every
# sample is exactly zero. A real room never does that — even a quiet one
# carries dither and hiss — so pure zeros mean the OS is feeding silence:
# an input device with nothing behind it, or a process macOS has not been
# allowed the microphone. Neither is a stall, so the watchdog above never
# notices, and the pipeline sits "ready" hearing nothing.
_SILENCE_WINDOW_S = 5.0


class SilenceWatch:
    """Says, once, when the microphone has delivered only zeros for a while.

    Pure and frame-counted so it is table-testable: :meth:`push` takes each
    frame and returns a sentence on the two transitions — into silence after
    ``window_s`` of it, and back out when the first real sample arrives —
    and ``None`` otherwise.
    """

    def __init__(self, window_s: float = _SILENCE_WINDOW_S, frame_s: float = FRAME_SAMPLES / SAMPLE_RATE) -> None:
        self._frame_s = frame_s
        self._limit = int(round(window_s / frame_s))
        self._zeros = 0
        self.silent = False

    def push(self, frame: bytes) -> str | None:
        if np.frombuffer(frame, dtype=np.int16).any():
            self._zeros = 0
            if self.silent:
                self.silent = False
                return "microphone hears the room again"
            return None
        self._zeros += 1
        if not self.silent and self._zeros >= self._limit:
            self.silent = True
            return (
                f"microphone open but delivering pure silence for {self._zeros * self._frame_s:.0f}s — "
                "the input device has nothing behind it, or this process is not allowed the "
                "microphone (System Settings › Privacy & Security › Microphone)"
            )
        return None



def pcm_to_float(frame: bytes) -> np.ndarray:
    """int16 PCM bytes to float32 in [-1, 1], the form every model wants."""
    return np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0


def float_to_pcm(samples: np.ndarray) -> bytes:
    """Inverse of :func:`pcm_to_float`, with clipping to avoid wraparound."""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


class MicStream:
    """An async iterator of fixed-size PCM frames from the microphone.

    Used as an async context manager::

        async with MicStream(cfg) as mic:
            async for frame in mic.frames():
                ...
    """

    _warn_on_digital_silence = True
    """Raw capture should contain room noise; processed capture may be zero."""

    def __init__(self, config: AudioConfig, max_queued_frames: int = 100) -> None:
        self._config = config
        self._queue: deque[bytes] = deque(maxlen=max_queued_frames)
        self._stream: sd.RawInputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._dropped = 0
        self._closed = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self) -> Self:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        self._closed = False

        device = _resolve_device(self._config.input_device, want_input=True)

        # blocksize is pinned to our frame size so the callback hands us
        # exactly one webrtcvad-sized frame at a time — no repacking, no
        # partial frames to buffer across callbacks.
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            device=device,
            channels=CHANNELS,
            dtype="int16",
            callback=self._on_audio,
        )
        self._stream.start()
        # Named, at INFO, because "which microphone" is the first question
        # when the room seems deaf: the default shuffles when a phone or a
        # headset appears, and the log is where the answer has to be.
        log.info(
            "microphone open: %s (%d Hz, %d-sample frames)",
            _device_name(device),
            SAMPLE_RATE,
            FRAME_SAMPLES,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._wakeup is not None:
            self._wakeup.set()
        if self._dropped:
            log.warning("dropped %d audio frames — consumer fell behind", self._dropped)

    # ── PortAudio callback (runs on PortAudio's thread, not the loop) ────────

    def _on_audio(self, indata, frames: int, time_info, status) -> None:  # noqa: ANN001
        if status:
            # Overflows here mean the OS-level buffer wrapped. Worth knowing
            # about, never worth raising from an audio callback.
            log.debug("portaudio status: %s", status)

        if self._queue.maxlen and len(self._queue) == self._queue.maxlen:
            self._dropped += 1  # deque discards the oldest for us

        # bytes(indata) copies out of PortAudio's buffer, which it reuses the
        # moment this returns. Referencing it later would read torn audio.
        self._queue.append(bytes(indata))

        loop, wakeup = self._loop, self._wakeup
        if loop is not None and wakeup is not None and not wakeup.is_set():
            loop.call_soon_threadsafe(wakeup.set)

    # ── consumption ──────────────────────────────────────────────────────────

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield 30 ms int16 PCM frames until the stream is closed.

        Ends if the microphone stalls (no frames for several seconds), so a
        disconnected input device stops the loop cleanly — the pipeline can
        shut down and be restarted — instead of blocking here forever.
        """
        assert self._wakeup is not None, "MicStream used outside its context manager"
        stalls = 0
        silence = SilenceWatch() if self._warn_on_digital_silence else None
        while not self._closed:
            while self._queue:
                frame = self._queue.popleft()
                if silence is not None:
                    said = silence.push(frame)
                    if said:
                        (log.warning if silence.silent else log.info)("%s", said)
                yield frame
            self._wakeup.clear()
            if self._queue:  # raced with the callback between pop and clear
                continue
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=_STALL_TIMEOUT_S)
                stalls = 0
            except asyncio.TimeoutError:
                if self._closed:
                    break
                stalls += 1
                if stalls < _STALL_LIMIT:
                    log.warning(
                        "no microphone audio for %.0fs — waiting for the device",
                        _STALL_TIMEOUT_S,
                    )
                    continue
                log.error(
                    "no microphone audio for %.0fs — the input device was likely "
                    "disconnected; ending capture so the pipeline can shut down "
                    "instead of hanging",
                    _STALL_TIMEOUT_S * stalls,
                )
                return

    def drain(self) -> None:
        """Discard buffered audio.

        Called after Ciel finishes speaking so that its own voice — captured
        while the speakers were live — isn't waiting in the queue to be
        transcribed as if the user had said it.
        """
        self._queue.clear()


def _device_name(index: int | None) -> str:
    """The human name of a PortAudio input index, ``None`` meaning the default."""
    try:
        chosen = index if index is not None else sd.default.device[0]
        name = sd.query_devices(chosen)["name"]
    except Exception:  # noqa: BLE001 - a name is a courtesy, never a failure
        return "default" if index is None else str(index)
    return f"{name} (default)" if index is None else name


def _resolve_device(spec: int | str | None, *, want_input: bool) -> int | None:
    """Turn a device index or name fragment into a PortAudio index.

    Accepting a substring matters in practice: device indices shuffle when you
    plug in headphones, but "MacBook Pro Microphone" keeps meaning the same
    thing.
    """
    if spec is None or isinstance(spec, int):
        return spec

    needle = spec.lower()
    key = "max_input_channels" if want_input else "max_output_channels"
    for index, device in enumerate(sd.query_devices()):
        if device[key] > 0 and needle in device["name"].lower():
            return index
    raise ValueError(
        f"no {'input' if want_input else 'output'} device matching {spec!r}; "
        f"available: {[d['name'] for d in sd.query_devices() if d[key] > 0]}"
    )


__all__ = ["MicStream", "SilenceWatch", "float_to_pcm", "pcm_to_float", "FRAME_BYTES"]
