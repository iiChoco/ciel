"""Reproductions for the stage-two plan review of 2026-09-07. No runtime state.

    uv run --no-sync python reports/2026-09-07-task-controls-plan-review-repro.py

Three facts the review leans on, each shown against the installed code rather
than asserted:

1. The SDK's in-process MCP dispatcher hands a tool handler its arguments and
   nothing else. A ``_meta`` block on the ``tools/call`` request (where a
   tool-use id would ride, if the CLI sent one) is dropped before the handler,
   the MCP request context is never set, and hook callbacks and tool calls are
   spawned as independent tasks with no ordering between them. So the plan's
   step-2 gate — "prove how the tool-use id reaches an in-process task tool
   through the installed SDK dispatcher" — has no channel to prove.

2. The Chart's turn queue coalesces several says into one turn's text, and the
   Discord queue does the same for a same-channel run. Whatever ingress identity
   a message carried is gone by the time a ``TurnRequest`` exists.

3. ``admit`` takes the peer's role from its own hello. On loopback there is no
   token, so an "owner-eligible role" is a string the client chose; admission
   itself is the only credential the wire has.

Real SDK Query dispatcher over an in-memory transport; no model, subprocess,
account, network, or runtime state.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from importlib.metadata import version
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk._internal.query import Query
from mcp.server.lowlevel.server import request_ctx

from ciel.config import DiscordConfig, WebConfig
from ciel.remote.discord import DiscordLink
from ciel.remote.web import WebLink, admit

SEEN: list[dict[str, Any]] = []


@tool('task_probe', 'fixture', {'text': str})
async def task_probe(args: dict[str, Any]) -> dict[str, Any]:
    try:
        ctx: Any = request_ctx.get()
    except LookupError:
        ctx = 'unset'
    SEEN.append({'arguments': args, 'request_ctx': str(ctx)})
    return {'content': [{'type': 'text', 'text': 'ok'}]}


class MemoryTransport:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.written: list[dict[str, Any]] = []

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.messages.get()

    async def write(self, data: str) -> None:
        self.written.append(json.loads(data))

    async def close(self) -> None:
        pass


async def dispatcher() -> None:
    server = create_sdk_mcp_server('ciel', tools=[task_probe])
    transport = MemoryTransport()
    query = Query(transport, is_streaming_mode=True, sdk_mcp_servers={'ciel': server['instance']})
    await query.start()
    order: list[str] = []

    async def hook(payload: dict[str, Any], tool_use_id: str | None, context: object) -> dict[str, Any]:
        order.append(f'hook enters {tool_use_id}')
        await asyncio.sleep(0.05)  # a guard that takes time: a broker question, a store read
        order.append(f'hook returns {tool_use_id}')
        return {}

    query.hook_callbacks['h'] = hook
    try:
        await transport.messages.put({'type': 'control_request', 'request_id': 'r1', 'request': {
            'subtype': 'hook_callback', 'callback_id': 'h', 'tool_use_id': 'toolu_A',
            'input': {'tool_name': 'mcp__ciel__task_probe', 'tool_input': {'text': 'A'}}}})
        await transport.messages.put({'type': 'control_request', 'request_id': 'r2', 'request': {
            'subtype': 'mcp_message', 'server_name': 'ciel', 'message': {
                'jsonrpc': '2.0', 'id': 7, 'method': 'tools/call',
                'params': {'name': 'task_probe', 'arguments': {'text': 'A'},
                           '_meta': {'claudecode/toolUseId': 'toolu_A', 'progressToken': 'toolu_A'}}}}})
        await asyncio.sleep(0.2)
        print(f'1. SDK {version("claude-agent-sdk")}')
        print('   the handler received:', SEEN)
        print('   control requests answered in the order:', [w['response']['request_id'] for w in transport.written])
        print('   hook and call interleaving:', order)
        assert SEEN == [{'arguments': {'text': 'A'}, 'request_ctx': 'unset'}]
        assert [w['response']['request_id'] for w in transport.written] == ['r2', 'r1']
        print('   Reproduced: no tool-use id, no _meta, no request context reaches an in-process tool;')
        print('   the call completed while its own PreToolUse hook was still pending.')
    finally:
        await query.close()


def batching() -> None:
    link = WebLink(WebConfig())
    link._on_frame(json.dumps({'type': 'say', 'text': 'watch PR 12', 'seq': 41}), None)
    link._on_frame(json.dumps({'type': 'say', 'text': 'and PR 13', 'seq': 42}), None)
    batch = link.pop_batch()
    print('2. two Chart says (seq 41, 42) became one turn:', batch)
    assert batch == ('watch PR 12\nand PR 13', None)

    class Channel:
        guild = None
        name = 'dm'

    dm = Channel()  # one DM: the same channel object, so the run coalesces

    class Message:
        def __init__(self, ident: int, text: str) -> None:
            self.id = ident
            self.content = text
            self.channel = dm
            self.guild = None
            self.mentions: list[object] = []

            class Author:
                bot = False
                id = 7

            self.author = Author()

    # The lane persists its last-seen mark on every accepted DM; the fixture
    # must point that at a temporary file, never at ~/.ciel/discord.json.
    with tempfile.TemporaryDirectory(prefix='ciel-plan-review-') as tmp:
        discord = DiscordLink(DiscordConfig(owner_id=7, state_file=Path(tmp) / 'discord.json'))
        discord._on_message(Message(1001, 'watch PR 12'))
        discord._on_message(Message(1002, 'and PR 13'))
        joined = discord.pop_batch()
    print('   two Discord messages (ids 1001, 1002) became one turn:', joined[0] if joined else None)
    assert joined is not None and joined[0] == 'watch PR 12\nand PR 13'
    print('   Reproduced: message ids and seqs do not survive the queue; a TurnRequest has nowhere to carry them.')


def roles() -> None:
    verdict = admit({'type': 'hello', 'role': 'owner', 'client_id': 'x'}, loopback=True, token='', require_token=False)
    print('3. loopback hello claiming role "owner" is admitted as:', verdict.role, '| token checked: no')
    assert verdict.ok and verdict.role == 'owner'
    print('   Reproduced: the role is whatever the client wrote; admission is the credential, on loopback by reach.')


if __name__ == '__main__':
    asyncio.run(dispatcher())
    batching()
    roles()
    print('all three reproductions hold')
