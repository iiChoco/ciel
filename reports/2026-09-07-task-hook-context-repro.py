"""Reproduce runner ContextVar loss through the installed SDK's hook reader.

Uses the real SDK Query dispatcher with an in-memory transport. No model,
subprocess, account, runtime state, or network is used.

    uv run --no-sync python reports/2026-09-07-task-hook-context-repro.py
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextvars import ContextVar
from importlib.metadata import version
from typing import Any

from claude_agent_sdk._internal.query import Query

TASK: ContextVar[str | None] = ContextVar('task_attempt', default=None)


class MemoryTransport:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.responded = asyncio.Event()

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.messages.get()

    async def write(self, data: str) -> None:
        self.responded.set()

    async def close(self) -> None:
        pass


async def main() -> None:
    seen: list[str | None] = []
    transport = MemoryTransport()
    query = Query(transport, is_streaming_mode=True)

    async def hook(payload: dict[str, Any], tool_use_id: str | None,
                   context: object) -> dict[str, Any]:
        seen.append(TASK.get())
        return {}

    query.hook_callbacks['fixture-hook'] = hook
    await query.start()
    token = TASK.set('task-1/attempt-1')
    try:
        await transport.messages.put({
            'type': 'control_request', 'request_id': 'fixture-request',
            'request': {'subtype': 'hook_callback', 'callback_id': 'fixture-hook',
                        'tool_use_id': 'fixture-tool', 'input': {'tool_name': 'Read'}},
        })
        await asyncio.wait_for(transport.responded.wait(), 2)
        print('SDK:', version('claude-agent-sdk'))
        print('runner context:', TASK.get())
        print('hook context:', seen)
        assert seen == [None], 'SDK propagation changed; revisit the plan caveat'
        print('Reproduced: setting the runner ContextVar alone does not bind the hook.')
    finally:
        TASK.reset(token)
        await query.close()


if __name__ == '__main__':
    asyncio.run(main())
