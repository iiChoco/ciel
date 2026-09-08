"""Owner task authority survives the installed SDK's independent reader tasks.

The real Query dispatcher and in-process MCP server run over memory transport,
without a CLI, model, socket, or home state. Private calls, parallel identical
calls, warm-client reuse, cumulative cost, stale draining, drain timeout,
interruption, handler cancellation, and a queued worker commit exercise the
turn fence. The same controller validates PR watches, journals only applied
controls, survives a disabled/failing journal, and refuses absent authority.
Public SDK options carry neither task tools nor private providers or history.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from claude_agent_sdk import ResultMessage, create_sdk_mcp_server
from claude_agent_sdk._internal.query import Query

from ciel.brain.agent import Brain
from ciel.brain.tools.tasks import TASK_TOOLS, bind_tasks, create_task, list_tasks
from ciel.config import Config
from ciel.task_context import TaskAuthority, TaskBinding
from ciel.task_controls import TaskController
from ciel.tasks import Origin, TaskConflict

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class MemoryTransport:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.written: list[dict[str, Any]] = []
        self.expected = 0
        self.cost = 0.0

    async def read_messages(self) -> Any:
        while True:
            yield await self.messages.get()

    async def write(self, data: str) -> None:
        self.written.append(json.loads(data))
        self.expected -= 1
        if self.expected == 0:
            await self.result()

    async def result(self) -> None:
        self.cost += 0.1
        await self.messages.put({'type': 'result', 'session_id': 'fixture-session', 'total_cost_usd': self.cost})

    async def close(self) -> None:
        pass


class DispatcherClient:
    def __init__(self) -> None:
        self.transport = MemoryTransport()
        server = create_sdk_mcp_server('ciel', tools=TASK_TOOLS)
        self.dispatcher = Query(self.transport, is_streaming_mode=True, sdk_mcp_servers={'ciel': server['instance']})
        self.calls = 0
        self.parallel = 1

    async def start(self) -> None:
        await self.dispatcher.start()

    async def send_call(self, identity: str, name: str = 'list_tasks', arguments: dict[str, Any] | None = None) -> None:
        await self.transport.messages.put({'type': 'control_request', 'request_id': identity, 'request': {
            'subtype': 'mcp_message', 'server_name': 'ciel', 'message': {
                'jsonrpc': '2.0', 'id': identity, 'method': 'tools/call',
                'params': {'name': name, 'arguments': arguments or {}, '_meta': {'claudecode/toolUseId': identity}}}}})

    async def query(self, text: str) -> None:
        self.calls += 1
        self.transport.expected = self.parallel
        for index in range(self.parallel):
            await self.send_call(f'call-{self.calls}-{index}')

    async def receive_response(self) -> Any:
        async for message in self.dispatcher.receive_messages():
            if message['type'] == 'result':
                yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False,
                                    num_turns=1, session_id=message['session_id'], total_cost_usd=message['total_cost_usd'])
                return

    async def interrupt(self) -> None:
        for identity in list(self.dispatcher._inflight_requests):
            await self.transport.messages.put({'type': 'control_cancel_request', 'request_id': identity})

    async def disconnect(self) -> None:
        await self.dispatcher.close()


def response(client: DispatcherClient) -> str:
    return json.dumps(client.transport.written[-1])


async def consume(brain: Brain, origin: Origin | None) -> None:
    async with aclosing(brain.ask('fixture owner request', origin=origin)) as stream:
        async for _ in stream:
            pass


class Journal:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.fail = False

    def record(self, **kwargs: Any) -> None:
        if self.fail:
            raise OSError('fixture journal failure')
        self.entries.append(kwargs)


async def main(root: Path) -> None:
    with patch.object(Path, 'home', return_value=root):
        cfg = Config()
    cfg = replace(cfg, tasks=replace(cfg.tasks, enabled=True, directory=root / 'tasks'))
    owner = Origin(cfg.tasks.owner, 'owner-request', 'web', ingress_ids=('chart:minted-one',))
    journal = Journal()
    controller = TaskController(cfg.tasks, journal)
    await controller.start()
    brain = Brain(cfg)
    client = DispatcherClient()
    await client.start()
    brain._client = client
    bind_tasks(controller, brain._task_authority.capture)
    try:
        await consume(brain, owner)
        check('the real SDK dispatcher reaches a private owner list', 'tasks' in response(client) and 'unavailable' not in response(client))
        check('turn end clears authority', brain._task_authority.capture() is None)
        first_client = brain._client
        client.parallel = 2
        await consume(brain, owner)
        check('parallel identical calls need no tool-use-ID matching', all('unavailable' not in json.dumps(w) for w in client.transport.written[-2:]))
        check('clean private turns reuse one client and preserve cost deltas', brain._client is first_client and abs(brain.last_turn_cost_usd - 0.1) < 0.0001)
        client.parallel = 1
        await consume(brain, None)
        check('no request context means no private task data', 'unavailable' in response(client))
        with brain.unattended('fixture reflection'):
            await consume(brain, owner)
        check('Witness engagement refuses even a supplied owner context', 'unavailable' in response(client))
        brain._needs_drain = True
        client.transport.expected = 1
        await client.send_call('stale-during-drain')
        before = len(client.transport.written)
        await consume(brain, owner)
        check('a stale call enters during draining with no authority', 'unavailable' in json.dumps(client.transport.written[before]))
        check('a successfully drained turn acquires its own owner context', 'unavailable' not in response(client) and not brain._needs_drain)
        brain._needs_drain = True
        original_wait = asyncio.wait_for
        def short_wait(awaitable: Any, timeout: float) -> Any:
            return original_wait(awaitable, timeout=0.02 if timeout == 10.0 else timeout)
        with patch('ciel.brain.agent.asyncio.wait_for', short_wait):
            await consume(brain, owner)
        check('drain debt denies calls through cached task definitions for the whole turn', brain._needs_drain and 'unavailable' in response(client))
        await client.transport.result()
        await consume(brain, owner)
        check('a later successful drain restores authority on the warm client', not brain._needs_drain and brain._client is first_client and 'unavailable' not in response(client))
        brain._needs_drain = True
        brain._owed_results = 2
        await client.transport.result()
        with patch('ciel.brain.agent.asyncio.wait_for', short_wait):
            await consume(brain, owner)
        check('one stale result cannot discharge two interrupted queries', brain._needs_drain and 'unavailable' in response(client))
        await client.transport.result()
        await consume(brain, owner)
        check('authority returns only after every owed result has drained', not brain._needs_drain and brain._owed_results == 0 and 'unavailable' not in response(client))
        brain._task_authority.install(owner)
        binding = brain._task_authority.capture()
        created = await create_task.handler({'repository': 'fixture/repo', 'pr': 12, 'checks': ['checks']})
        task = json.loads(created['content'][0]['text'])['task']
        check('the SDK tool saves an offline PR watch in a resource wait', task['status'] == 'waiting' and task['wait_reason'] == 'resource')
        await create_task.handler({'repository': 'fixture/repo', 'pr': 12, 'checks': ['checks']})
        check('a creation retry journals only its first applied change', len(journal.entries) == 1)
        invalid = await create_task.handler({'repository': 'fixture/repo', 'pr': 12, 'checks': []})
        check('unspecified checks ask for clarification', invalid.get('isError') is True)
        # Block the single worker before submitting the control, so revocation
        # must win before that control can acquire its transaction lease.
        entered, release = threading.Event(), threading.Event()
        def hold() -> None:
            entered.set()
            release.wait(3)
        assert controller.store is not None
        blocker = asyncio.create_task(controller.store._run(hold))
        await asyncio.to_thread(entered.wait, 2)
        pending = asyncio.create_task(controller.apply(binding, 'pause', {'task_id': task['id']}))
        await asyncio.sleep(0)
        await brain.interrupt()
        release.set()
        await blocker
        try:
            await pending
        except TaskConflict:
            check('interruption revokes a queued worker control before commit', True)
        else:
            check('interruption revokes a queued worker control before commit', False)
        check('a revoked control leaves durable state untouched', (await controller.store.get(owner.owner, task['id'])).status == 'waiting')
        # A cancelled SDK handler cannot turn an expired lease into a new one.
        brain._task_authority.install(owner)
        entered.clear(); release.clear()
        blocker = asyncio.create_task(controller.store._run(hold))
        await asyncio.to_thread(entered.wait, 2)
        client.transport.expected = 2
        await client.send_call('cancel-me', 'pause_task', {'task_id': task['id']})
        await asyncio.sleep(0.02)
        handle = client.dispatcher._inflight_requests.get('cancel-me')
        await brain.interrupt()
        await asyncio.sleep(0.02)
        release.set()
        await blocker
        await asyncio.sleep(0.02)
        check('the SDK cancels its independent handler on control cancellation', handle is not None and 'cancel-me' not in client.dispatcher._inflight_requests)
        check('SDK cancellation does not let a queued write escape the fence', (await controller.store.get(owner.owner, task['id'])).status == 'waiting')
        brain._task_authority.install(owner)
        binding = brain._task_authority.capture()
        journal.fail = True
        result = await controller.apply(binding, 'pause', {'task_id': task['id']})
        check('a failed optional journal cannot undo a committed control', result['task']['status'] == 'paused')
        controller.journal = None
        result = await controller.apply(binding, 'resume', {'task_id': task['id']})
        check('owner controls work with journaling disabled', result['task']['wait_reason'] == 'resource')
        public = Brain(cfg, memory_index_provider=lambda: (_ for _ in ()).throw(AssertionError('private provider read')), public=True)
        options = public._build_options({'private': object()}, ['mcp__ciel__list_tasks'])
        check('public options carry no private MCP tools, provider, or resume', options.mcp_servers == {} and options.resume is None and options.tools == ['WebSearch', 'WebFetch'])
        public._record_result(ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id='public-session', total_cost_usd=0.2))
        check('public results never overwrite the private session ID', brain._session_id == 'fixture-session' and public._session_id is None)
        await public.close()
    finally:
        await brain.close()
        await controller.close()
        bind_tasks(None, lambda: None)
    check('a closed controller has no store bound', controller.store is None)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='ciel-task-tools-') as tmp:
        asyncio.run(main(Path(tmp)))
