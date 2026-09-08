"""The owner of a tool call comes from the turn that admitted it.

**Authority is captured once.** The SDK calls in-process tools on reader tasks
with arguments alone. The Brain installs a private owner context after draining
its previous turn; a tool snapshots it before yielding. Neither model arguments
nor a tool-use ID can supply authority.

**Revocation meets the transaction.** A worker holds a lease through commit.
Revocation waits off the event loop for an already authorized commit, or wins
first and makes a queued operation refuse. Cancelling a coroutine cannot revoke
an operation which has already committed. No ContextVar crosses reader tasks.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ciel.tasks import Origin, TaskConflict


@dataclass
class _Lease:
    active: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)

    def revoke(self) -> None:
        with self.lock:
            self.active = False


@dataclass(frozen=True)
class TaskBinding:
    origin: Origin
    client_generation: int
    turn_generation: int
    _lease: _Lease = field(default_factory=_Lease, repr=False, compare=False)

    @contextmanager
    def fence(self) -> Iterator[None]:
        with self._lease.lock:
            if not self._lease.active:
                raise TaskConflict('task authority expired; ask again in a live private owner turn')
            yield


class TaskAuthority:
    """One serialized Brain's current owner turn, absent during stale draining."""

    def __init__(self) -> None:
        self.current: TaskBinding | None = None
        self.client_generation = 0
        self.turn_generation = 0

    def capture(self) -> TaskBinding | None:
        return self.current

    def install(self, origin: Origin) -> None:
        if self.current is not None:
            raise RuntimeError('task authority must be cleared before another turn')
        if not origin.attended or not origin.private:
            return
        self.turn_generation += 1
        self.current = TaskBinding(origin, self.client_generation, self.turn_generation)

    async def clear(self, *, new_client: bool = False) -> None:
        previous, self.current = self.current, None
        self.turn_generation += 1
        if new_client:
            self.client_generation += 1
        if previous is not None:
            await asyncio.shield(asyncio.to_thread(previous._lease.revoke))
