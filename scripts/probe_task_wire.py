"""Two private Chart views steer the same saved task over the real wire.

Temporary stores and an ephemeral loopback server exercise addressed snapshots,
rendered revision conflicts, reconnect, spoke-seat refusal, admission, resource
waits, disabled/open-failed storage, cancelled startup, and bounded pending controls. The hello
advertises protocol support without task data; task frames never enter replay.
No runtime config, token, model, or home state is used. --live serves the real
Chart with synthetic task states for visual checks, still without a brain.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp

from ciel.config import HubConfig, TasksConfig, WebConfig
from ciel.hub.server import HubServer
from ciel.remote.web import Admission, WebLink, admit
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.tasks import Criterion, Evidence, Origin, Scope, Specification, Step, TaskStore
from ciel.turn import owner_origin

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def receive(ws: Any, kind: str) -> dict[str, Any]:
    async with asyncio.timeout(3):
        while True:
            message = await ws.receive_json()
            if message.get('type') == kind:
                return message


async def request(ws: Any, identity: str, operation: str, **fields: Any) -> dict[str, Any]:
    await ws.send_json({'type': 'task.request', 'request_id': identity, 'operation': operation, **fields})
    return await receive(ws, 'task.result')


async def fixture(root: Path) -> tuple[TaskController, HubServer, str, dict[str, Any]]:
    cfg = TasksConfig(enabled=True, directory=root / 'tasks')
    controller = TaskController(cfg)
    await controller.start()
    binding = TaskBinding(owner_origin(cfg.owner, 'web', 'seed', namespace='chart'), 1, 1)
    task = (await controller.apply(binding, 'create', {'repository': 'fixture/repository', 'pr': 12, 'checks': ['build', 'review']}))['task']
    link = HubServer(WebConfig(enabled=True, port=0), HubConfig(token='fixture-token', token_file=root / 'hub.token'))
    link.bind_tasks(controller)
    await link.start()
    port = link._site._server.sockets[0].getsockname()[1]
    return controller, link, f'http://127.0.0.1:{port}', task


async def run(root: Path, live: bool) -> None:
    controller, link, url, task = await fixture(root)
    assert controller.store is not None
    try:
        if live:
            for index, state in enumerate(('owner', 'cancelled', 'done', 'long')):
                target = 'fixture:pr-' + str(index)
                spec = Specification('A long saved request ' + ('with details ' * 35) if state == 'long' else 'Fixture ' + state,
                                     Scope(('inspect',), (target,)), (Criterion('build', target, 'success', 'head-a'),))
                step = Step('read', 'inspect', target)
                record = await controller.store.create(Origin(controller.config.owner, state, 'web'), spec, step)
                if state == 'owner':
                    await controller.store.ask_owner(controller.config.owner, record.id, record.revision, '<img src=x onerror=alert(1)> Which check should remain?', ('build', 'review'))
                elif state == 'cancelled':
                    await controller.store.cancel(controller.config.owner, record.id, record.revision)
                elif state == 'done':
                    attempt = await controller.store.claim(controller.config.owner, record.id, record.revision)
                    attempt = await controller.store.mark_dispatched(controller.config.owner, attempt)
                    record = await controller.store.observe(controller.config.owner, attempt, (Evidence('build', target, 'success', 'fixture', __import__('time').time(), 'head-a'),))
                    await controller.store.complete(controller.config.owner, record.id, record.revision)
                else:
                    await controller.store.wait(controller.config.owner, record.id, record.revision, 'resource', 'Saved; execution is unavailable.')
            print('CHART_FIXTURE_URL=' + url, flush=True)
            await asyncio.Event().wait()
            return
        remote = WebLink(WebConfig(), HubConfig(bind='0.0.0.0', token='fixture-token'))
        remote.bind_tasks(controller)
        check('a remotely bound task hub requires a token even for loopback peers', remote.task_token_required and not admit({'type': 'hello', 'role': 'owner'}, loopback=True, token='fixture-token', require_token=remote.task_token_required).ok)
        before = len(link._task_requests)
        link._task_request({'request_id': 'unadmitted', 'operation': 'list'}, object())
        check('unadmitted sockets never start private task requests', len(link._task_requests) == before)
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url + '/ws') as first, session.ws_connect(url + '/ws') as second:
                for ws in (first, second):
                    await ws.send_json({'type': 'hello', 'role': 'chart', 'client_id': 'shared-tabs'})
                    hello = await receive(ws, 'hello')
                    check('the hello advertises task support without task records', hello['tasks'] is True and task['id'] not in json.dumps(hello))
                listed = await request(first, 'list-first', 'list')
                listed2 = await request(second, 'list-second', 'list')
                check('two admitted views read the same saved task', listed['data']['tasks'][0]['id'] == listed2['data']['tasks'][0]['id'] == task['id'])
                paused = await request(first, 'pause', 'pause', task_id=task['id'], revision=task['revision'])
                check('a Chart pause commits through the shared controller', paused['ok'] and paused['data']['task']['status'] == 'paused')
                stale = await request(second, 'stale-resume', 'resume', task_id=task['id'], revision=task['revision'])
                check('a stale view receives an explicit revision conflict', not stale['ok'] and 'revision' in stale['error'])
                fresh = await request(second, 'fresh', 'inspect', task_id=task['id'])
                check('refresh after a conflict shows the paused record', fresh['data']['task']['status'] == 'paused')
                resumed = await request(second, 'resume', 'resume', task_id=task['id'], revision=fresh['data']['task']['revision'])
                check('a fresh resume still reports execution unavailable', resumed['ok'] and resumed['data']['task']['wait_reason'] == 'resource')
                repeated = await request(second, 'resume', 'resume', task_id=task['id'], revision=fresh['data']['task']['revision'])
                check('a repeated socket control cannot silently repeat the transition', not repeated['ok'])
                check('task frames do not enter the shared replay ring', link._ring.seq == 0)
            async with session.ws_connect(url + '/ws') as fresh_socket:
                await fresh_socket.send_json({'type': 'hello', 'role': 'chart'})
                await receive(fresh_socket, 'hello')
                reopened = await request(fresh_socket, 'reconnect', 'inspect', task_id=task['id'])
                check('a reconnected Chart fetches current durable state', reopened['data']['task']['revision'] == resumed['data']['task']['revision'])
            async with session.ws_connect(url + '/ws') as spoke:
                await spoke.send_json({'type': 'hello', 'role': 'spoke', 'client_id': 'fixture-spoke'})
                await receive(spoke, 'hello')
                await spoke.send_json({'type': 'task.request', 'request_id': 'spoke-list', 'operation': 'list'})
                await spoke.send_json({'type': 'ping'})
                await receive(spoke, 'pong')
                check('spoke task frames are dropped while its normal wire remains usable', not link._task_requests)
        controller.config = replace(controller.config, max_pending_controls=1)
        queued, _ = link._welcome('slow-view', Admission(True, role='chart'))
        queued.get_nowait()
        entered, release = threading.Event(), threading.Event()
        def hold_worker() -> None:
            entered.set()
            release.wait(3)
        blocker = asyncio.create_task(controller.store._run(hold_worker))
        await asyncio.to_thread(entered.wait, 2)
        link._on_frame(json.dumps({'type': 'task.request', 'request_id': 'pending', 'operation': 'list'}), 'slow-view')
        await asyncio.sleep(0)
        link._on_frame(json.dumps({'type': 'task.request', 'request_id': 'busy', 'operation': 'list'}), 'slow-view')
        check('pending Chart handlers have a visible admission bound', 'busy' in json.loads(queued.get_nowait())['error'] and len(link._task_requests) == 1)
        release.set()
        await blocker
        await asyncio.gather(*tuple(link._task_requests))
        queued.get_nowait()
        entered.clear(); release.clear()
        blocker = asyncio.create_task(controller.store._run(hold_worker))
        await asyncio.to_thread(entered.wait, 2)
        current = resumed['data']['task']
        link._on_frame(json.dumps({'type': 'task.request', 'request_id': 'disconnect', 'operation': 'pause', 'task_id': task['id'], 'revision': current['revision']}), 'slow-view')
        await asyncio.sleep(0.01)
        for pending in tuple(link._task_requests):
            pending.cancel()
        await asyncio.gather(*tuple(link._task_requests), return_exceptions=True)
        link._clients.pop('slow-view', None)
        link._peers.pop('slow-view', None)
        release.set()
        await blocker
        check('a disconnected Chart cannot assume its submitted control rolled back', (await controller.store.get(controller.config.owner, task['id'])).status == 'paused')
        await controller.store.owner_control(controller.config.owner, task['id'], 'resume')
        disabled = TaskController(replace(controller.config, enabled=False))
        await disabled.start()
        link.bind_tasks(disabled)
        queue, _ = link._welcome('fixture-view', Admission(True, role='chart'))
        queue.get_nowait()
        link._on_frame(json.dumps({'type': 'task.request', 'request_id': 'disabled', 'operation': 'list'}), 'fixture-view')
        await asyncio.gather(*tuple(link._task_requests))
        check('disabled storage returns an explicit unavailable result', 'disabled' in json.loads(queue.get_nowait())['error'])
        failed_dir = root / 'shared-directory'
        failed_dir.mkdir(mode=0o755)
        failed = TaskController(replace(controller.config, directory=failed_dir))
        await failed.start()
        check('an open-failed store leaves a useful unavailable status', failed.store is None and 'could not open' in failed.unavailable)
        invalid = TaskController(replace(controller.config, max_active=0))
        await invalid.start()
        check('invalid task limits disable the surface without aborting startup', invalid.store is None and bool(invalid.unavailable))
        await invalid.close()
        await failed.close()
        await disabled.close()
        opening, finish_open = threading.Event(), threading.Event()
        class SlowStore(TaskStore):
            def _open(self, stamp: float) -> tuple[Any, ...]:
                opening.set()
                finish_open.wait(3)
                return super()._open(stamp)
        cancelled_config = replace(controller.config, directory=root / 'cancelled-start')
        cancelled = TaskController(cancelled_config)
        with patch('ciel.task_controls.TaskStore', SlowStore):
            starting = asyncio.create_task(cancelled.start())
            await asyncio.to_thread(opening.wait, 2)
            starting.cancel()
            await asyncio.sleep(0)
            finish_open.set()
            result = await asyncio.gather(starting, return_exceptions=True)
        async with TaskStore(cancelled_config):
            check('cancelled startup releases its worker and store ownership', isinstance(result[0], asyncio.CancelledError) and cancelled.store is None)
    finally:
        await link.close()
        await controller.close()
    async with __import__('ciel.tasks', fromlist=['TaskStore']).TaskStore(TasksConfig(directory=root / 'tasks')) as store:
        check('controls survive closing and reopening the runtime store', (await store.get(controller.config.owner, task['id'])).wait_reason == 'resource')
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ciel-task-wire-') as tmp:
        asyncio.run(run(Path(tmp), args.live))
