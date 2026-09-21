"""One model call has one bounded context, and the runtime checks its answer.

A fake backend and a fake SDK module stand in for the model. Pins: a payload
past its bound and a schema that is not an object are refused before any call;
the call holds the lease and a competing lease waits; a timeout and a
cancellation both release the lease and drop the result; the backend's own
error passes through with no fallback; the schema check catches a wrong type,
a missing field, a value outside its choices, an unexpected field, a bad
array item, and a boolean posing as an integer; and the SDK extractor builds
its client with no tools, no MCP servers, no settings, one turn, a private
empty working directory that is removed afterwards, and the schema as its
output format, reads the structured result, treats an error result as an
error, and disconnects even when the query fails. Page images: a text call
reaches a backend that knows nothing of images exactly as before; images
past their count or byte bounds, bytes that are not a PNG, and an entry that
is not a label and bytes are refused before any call; within the bounds the
images reach the backend, and the SDK client is sent one user message of
content blocks — the text, the page label, the PNG as base64 — with the
call's own budget. No model, network, or runtime state is used.

    uv run --no-sync python scripts/probe_extraction.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from ciel.brain.extract import AgentSdkExtractor, ExtractionError, ExtractionLimits, conforms, extract_json

CHECKS: list[str] = []
SCHEMA = {'type': 'object', 'required': ['when', 'kind'],
          'properties': {'when': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['appointment', 'promotion']},
                         'count': {'type': 'integer'}, 'tags': {'type': 'array', 'items': {'type': 'string'}}},
          'additionalProperties': False}
LIMITS = ExtractionLimits(max_chars=64, timeout_s=0.2)


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class FakeBackend:
    def __init__(self, result: Any = None, delay_s: float = 0.0, error: Exception | None = None) -> None:
        self.result, self.delay_s, self.error, self.calls = result, delay_s, error, 0

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *, limits: ExtractionLimits) -> Any:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.result


class Lease:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.held = 0

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        async with self.lock:
            self.held += 1
            try:
                yield
            finally:
                self.held -= 1


async def refused(name: str, call: Any, needle: str = '') -> None:
    try:
        await call
    except ExtractionError as exc:
        check(name, needle in str(exc))
    else:
        check(name, False)


async def probe_call() -> None:
    print('the bounded call')
    lease = Lease()
    good = FakeBackend({'when': '2026-09-15', 'kind': 'appointment'})
    await refused('a payload past its bound is refused before any call',
                  extract_json(good, lease, 'prompt', 'x' * 65, SCHEMA, LIMITS), 'bound')
    await refused('a schema that is not an object is refused before any call',
                  extract_json(good, lease, 'prompt', 'payload', {'type': 'array'}, LIMITS), 'object')
    await refused('an empty system prompt is refused before any call',
                  extract_json(good, lease, ' ', 'payload', SCHEMA, LIMITS), 'system prompt')
    check('nothing reached the backend', good.calls == 0)
    slow = FakeBackend({'when': '2026-09-15', 'kind': 'appointment'}, delay_s=0.05)
    call = asyncio.create_task(extract_json(slow, lease, 'prompt', 'payload', SCHEMA, LIMITS))
    await asyncio.sleep(0.01)
    competing = asyncio.create_task(lease.lock.acquire())
    await asyncio.sleep(0.01)
    check('the call holds the lease and a competing lease waits', lease.held == 1 and not competing.done())
    result = await call
    await competing
    lease.lock.release()
    check('the answer comes back checked', result == {'when': '2026-09-15', 'kind': 'appointment'} and lease.held == 0)
    stuck = FakeBackend({'when': 'never', 'kind': 'appointment'}, delay_s=1.0)
    await refused('a timeout releases the lease and drops the result',
                  extract_json(stuck, lease, 'prompt', 'payload', SCHEMA, LIMITS), 'timed out')
    check('the lease is free after the timeout', not lease.lock.locked())
    call = asyncio.create_task(extract_json(stuck, lease, 'prompt', 'payload', SCHEMA, LIMITS))
    await asyncio.sleep(0.01)
    call.cancel()
    try:
        await call
    except asyncio.CancelledError:
        pass
    check('a cancellation releases the lease and drops the result', call.cancelled() and not lease.lock.locked())
    broken = FakeBackend(error=ExtractionError('the model refused'))
    await refused('the backend\'s own error passes through with no fallback',
                  extract_json(broken, lease, 'prompt', 'payload', SCHEMA, LIMITS), 'refused')
    wrong = FakeBackend(['not', 'an', 'object'])
    await refused('an answer that is not an object is refused', extract_json(wrong, lease, 'prompt', 'payload', SCHEMA, LIMITS), 'object')


def probe_schema() -> None:
    print('\nthe schema is checked again')
    conforms(SCHEMA, {'when': 'soon', 'kind': 'promotion', 'count': 2, 'tags': ['a']})
    check('a conforming answer passes', True)
    cases = [
        ('a wrong type', {'when': 3, 'kind': 'appointment'}, '$.when'),
        ('a missing field', {'when': 'soon'}, "missing 'kind'"),
        ('a value outside its choices', {'when': 'soon', 'kind': 'invoice'}, 'choices'),
        ('an unexpected field', {'when': 'soon', 'kind': 'appointment', 'extra': 1}, "unexpected 'extra'"),
        ('a bad array item', {'when': 'soon', 'kind': 'appointment', 'tags': ['a', 2]}, '$.tags[1]'),
        ('a boolean posing as an integer', {'when': 'soon', 'kind': 'appointment', 'count': True}, '$.count'),
    ]
    for name, data, needle in cases:
        try:
            conforms(SCHEMA, data)
        except ExtractionError as exc:
            check(f'{name} is caught and named', needle in str(exc))
        else:
            check(f'{name} is caught and named', False)
    try:
        conforms({'type': 'thing'}, 1)
    except ExtractionError:
        check('a schema naming an unknown type is itself refused', True)
    else:
        check('a schema naming an unknown type is itself refused', False)


class FakeResult:
    def __init__(self, structured: Any, *, is_error: bool = False, errors: list[str] | None = None, result: str = '') -> None:
        self.structured_output, self.is_error, self.errors, self.subtype, self.result = structured, is_error, errors, 'fixture', result


class FakeClient:
    instances: list['FakeClient'] = []
    script: list[Any] = []
    fail_query = False

    def __init__(self, options: Any) -> None:
        self.options = options
        self.connected = False
        self.disconnected = False
        self.queries: list[str] = []
        self.cwd_existed = False
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        self.connected = True
        self.cwd_existed = Path(self.options.cwd).is_dir() and (os.stat(self.options.cwd).st_mode & 0o777) == 0o700

    async def query(self, text: Any) -> None:
        if FakeClient.fail_query:
            raise RuntimeError('the subprocess died')
        if isinstance(text, str):
            self.queries.append(text)
        else:
            async for message in text:
                self.queries.append(message)

    async def receive_response(self) -> AsyncIterator[Any]:
        for message in FakeClient.script:
            yield message

    async def disconnect(self) -> None:
        self.disconnected = True


def install_fake_sdk() -> None:
    module = types.ModuleType('claude_agent_sdk')

    class ClaudeAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class AssistantMessage:
        def __init__(self, content: list[Any]) -> None:
            self.content = content

    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    module.ClaudeAgentOptions = ClaudeAgentOptions  # type: ignore[attr-defined]
    module.ClaudeSDKClient = FakeClient  # type: ignore[attr-defined]
    module.AssistantMessage = AssistantMessage  # type: ignore[attr-defined]
    module.TextBlock = TextBlock  # type: ignore[attr-defined]
    module.ResultMessage = FakeResult  # type: ignore[attr-defined]
    sys.modules['claude_agent_sdk'] = module


class ImageBackend(FakeBackend):
    """A backend that takes page images: what it was handed is recorded."""

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *, limits: ExtractionLimits,
                      images: tuple[tuple[str, bytes], ...] = ()) -> Any:
        self.images = images
        return await super().extract(system_prompt, payload, schema, limits=limits)


PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 40


async def probe_images() -> None:
    print('\npage images ride the call, bounded')
    lease = Lease()
    text_only = FakeBackend({'when': '2026-09-15', 'kind': 'appointment'})
    result = await extract_json(text_only, lease, 'prompt', 'payload', SCHEMA, LIMITS)
    check('a text call reaches a backend that knows nothing of images exactly as before', result['kind'] == 'appointment' and text_only.calls == 1)
    sighted = ImageBackend({'when': '2026-09-15', 'kind': 'appointment'})
    wide = ExtractionLimits(max_chars=64, timeout_s=0.2, max_images=2, max_image_bytes=100)
    await refused('images are refused before any call while the limits allow none',
                  extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, LIMITS, (('page 1', PNG),)), 'more page images')
    await refused('more images than the bound are refused before any call',
                  extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('a', PNG), ('b', PNG), ('c', PNG))), 'more page images')
    await refused('an image past its byte bound is refused before any call',
                  extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('a', PNG + b'0' * 200),)), 'within the extraction bound')
    await refused('bytes that are neither PNG nor JPEG are refused before any call',
                  extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('a', b'GIF89a' + b'0' * 20),)), 'not a PNG')
    await refused('an image that is not a label and bytes is refused before any call',
                  extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('a', 'not bytes'),)), 'label and PNG or JPEG bytes')  # type: ignore[arg-type]
    check('nothing reached the backend', sighted.calls == 0)
    jpeg = b"\xff\xd8\xff\xe0" + b"fixture" + b"\xff\xd9"
    result = await extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('photo', jpeg),))
    check('a bounded JPEG reaches the same isolated image backend', sighted.images == (('photo',jpeg),))
    await refused('a truncated JPEG is refused before extraction',extract_json(sighted,lease,'prompt','payload',SCHEMA,wide,(('photo',jpeg[:-2]),)),'not a PNG or JPEG')
    result = await extract_json(sighted, lease, 'prompt', 'payload', SCHEMA, wide, (('page 7', PNG),))
    check('within the bounds the images are handed to the backend with the call', result['kind'] == 'appointment' and sighted.images == (('page 7', PNG),))


async def probe_sdk() -> None:
    print('\nthe client with nothing attached')
    install_fake_sdk()
    extractor = AgentSdkExtractor('fixture-model')
    FakeClient.script = [FakeResult({'when': '2026-09-15', 'kind': 'appointment'})]
    result = await extractor.extract('prompt', 'payload', SCHEMA, limits=LIMITS)
    client = FakeClient.instances[-1]
    o = client.options
    check('the structured result is the answer', result == {'when': '2026-09-15', 'kind': 'appointment'} and client.queries == ['payload'])
    check('the client is built with no tools, no MCP servers, no settings, and one turn',
          o.tools == [] and o.allowed_tools == [] and o.mcp_servers == {} and o.setting_sources == [] and o.max_turns == 1
          and o.permission_mode == 'dontAsk' and 'Bash' in o.disallowed_tools and o.model == 'fixture-model'
          and o.system_prompt == 'prompt' and o.max_budget_usd == LIMITS.max_budget_usd)
    check('the schema is the client\'s output format', o.output_format == {'type': 'json_schema', 'schema': SCHEMA})
    check('the working directory was private, empty, and its own, and is gone afterwards',
          client.cwd_existed and 'ciel-extract-' in str(o.cwd) and not Path(o.cwd).exists())
    check('the client is disconnected after the call', client.connected and client.disconnected)
    FakeClient.script = [FakeResult({'when': '2026-09-15', 'kind': 'appointment'})]
    wide = ExtractionLimits(max_chars=64, timeout_s=0.2, max_budget_usd=0.75, max_images=2, max_image_bytes=100)
    result = await extractor.extract('prompt', 'payload', SCHEMA, limits=wide, images=(('page 7', PNG),))
    client = FakeClient.instances[-1]
    sent = client.queries[-1]
    blocks = sent['message']['content'] if isinstance(sent, dict) else []
    check('with images the one user message is content blocks: the text, the page label, the PNG as base64, and its own budget',
          result['kind'] == 'appointment' and isinstance(sent, dict) and sent['type'] == 'user' and blocks[0] == {'type': 'text', 'text': 'payload'}
          and blocks[1] == {'type': 'text', 'text': '[page 7]'} and blocks[2]['type'] == 'image' and blocks[2]['source']['media_type'] == 'image/png'
          and blocks[2]['source']['data'] == __import__('base64').b64encode(PNG).decode('ascii') and client.options.max_budget_usd == 0.75)
    jpeg = b"\xff\xd8\xff\xe0" + b"fixture" + b"\xff\xd9"
    await extractor.extract('prompt','payload',SCHEMA,limits=wide,images=(('photo',jpeg),))
    blocks=FakeClient.instances[-1].queries[-1]['message']['content']
    check('JPEG content blocks declare JPEG MIME and retain the exact bytes',blocks[2]['source']['media_type']=='image/jpeg' and blocks[2]['source']['data']==__import__('base64').b64encode(jpeg).decode('ascii'))
    module = sys.modules['claude_agent_sdk']
    FakeClient.script = [module.AssistantMessage([module.TextBlock('```json\n{"when": "x", "kind": "promotion"}\n```')]),  # type: ignore[attr-defined]
                         FakeResult(None)]
    result = await extractor.extract('prompt', 'payload', SCHEMA, limits=LIMITS)
    check('a fenced answer with no structured result is still read', result == {'when': 'x', 'kind': 'promotion'})
    FakeClient.script = [FakeResult(None, is_error=True, errors=['budget exceeded'])]
    try:
        await extractor.extract('prompt', 'payload', SCHEMA, limits=LIMITS)
    except ExtractionError as exc:
        check('an error result is an error, named', 'budget exceeded' in str(exc))
    else:
        check('an error result is an error, named', False)
    FakeClient.fail_query = True
    try:
        await extractor.extract('prompt', 'payload', SCHEMA, limits=LIMITS)
    except RuntimeError:
        pass
    FakeClient.fail_query = False
    client = FakeClient.instances[-1]
    check('the client is disconnected and its directory removed even when the query fails',
          client.disconnected and not Path(client.options.cwd).exists())


async def main() -> None:
    await probe_call()
    await probe_images()
    probe_schema()
    await probe_sdk()
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    asyncio.run(main())
