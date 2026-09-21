"""The speech-to-text boundary.

One method. Any engine that can turn 16 kHz mono PCM into a string fits here —
mlx-whisper today, faster-whisper as the CPU fallback, a cloud API someday —
without the pipeline knowing which one it's talking to.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Protocol, runtime_checkable

import numpy as np


async def owned_decode(lock: asyncio.Lock, decode: Callable[..., str], *args: object) -> str:
    """Run one synchronous decode in a worker thread, one at a time.

    The engines serialize inference behind a lock because two decodes on
    the same GPU (or the same CPU threads) are slower than two in a row.
    ``async with lock: await to_thread(...)`` does not keep that promise:
    cancelling the coroutine — a dismissed dictation, a barge-in — releases
    the lock while the thread it started is still decoding, and the next
    caller enters beside it. So the lock is owned by the *worker*, not the
    awaiting coroutine: it is released when the thread returns, whoever is
    still listening. The caller's cancellation stays prompt (the wait is
    shielded, the ``CancelledError`` re-raised at once) and its result is
    simply dropped; the caller after it waits its turn.
    """
    await lock.acquire()
    worker = asyncio.ensure_future(asyncio.to_thread(decode, *args))
    worker.add_done_callback(lambda _: lock.release())
    return await asyncio.shield(worker)


@runtime_checkable
class SpeechToText(Protocol):
    """Transcribes captured audio."""

    async def transcribe(self, pcm: np.ndarray) -> str:
        """Turn mono 16 kHz float32 audio in [-1, 1] into text.

        Returns an empty string when the audio contains no intelligible
        speech. Callers treat empty as "nothing was said" and go back to
        waiting rather than sending a blank turn to the model.
        """
        ...

    async def warm_up(self) -> None:
        """Load models and run a throwaway inference.

        Called once at startup so the first real utterance isn't billed for
        several seconds of lazy model loading — the difference between Ciel
        feeling instant and feeling broken on the very first thing you say.
        """
        ...

    async def close(self) -> None:
        ...


__all__ = ["SpeechToText", "owned_decode"]
