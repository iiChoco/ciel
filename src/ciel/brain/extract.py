"""One model call with one bounded context.

A task that reads the world sometimes needs a model to say what it saw:
which of these sentences is an appointment, what date the message means
by "next Tuesday". The conversational Brain is the wrong instrument for
that. Every turn it runs opens with the world's chips, the memory index,
the projects, the Mac's tools, and the owner's whole recent conversation —
everything the assistant is, and everything a stranger's email must never
reach. Reflection's Witness rule keeps an unattended turn from *acting*,
but it does not keep the payload from *seeing*.

So an extraction is a client of its own: a fresh SDK session per call
with a fixed system prompt, no tools of any kind, no MCP servers, none of
the owner's settings, an empty private working directory, and a JSON
schema for its answer. It receives the bounded payload and whatever
context the caller declares (a timezone, say) and nothing else. The
interview room's structured call is the construction pattern; nothing of
the room's state is shared.

**The call holds the model turn, not the conversation.** It runs under
the Brain's lease — the same lock every conversational turn takes — so
two model turns never overlap, and a user who speaks mid-extraction waits
exactly as they would behind reflection. The runner cancels the
extraction on human input; the cancelled client is closed under a bounded
cleanup and its late result, if any, is dropped.

**The schema is checked twice.** The SDK asks the model for the shape,
and runtime code checks the answer against it again before anyone acts
on a field. A model's confidence is not an authorization.

**There is no fallback.** If this path fails, the caller records a wait;
it never reaches for the conversational client with a prompt that
carries the notebook.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)

_CLEANUP_S = 5.0
"""How long closing a cancelled or finished client may take before it is
abandoned to the SDK; the lease is not held hostage by a wedged subprocess."""

_DENIED_TOOLS = (
    "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep",
    "WebSearch", "WebFetch", "Agent", "Task", "TodoWrite",
    "NotebookEdit", "KillShell", "BashOutput", "Skill",
)

Lease = Callable[[], AbstractAsyncContextManager[None]]


class ExtractionError(RuntimeError):
    """The call did not produce the structure it was asked for."""


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    max_chars: int
    timeout_s: float
    max_budget_usd: float = 0.25


class ExtractionBackend(Protocol):
    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *,
                      limits: ExtractionLimits) -> dict[str, Any]: ...


def _json_object(texts: list[str]) -> dict[str, Any] | None:
    """A JSON object out of a reply that may have wrapped it in a fence."""
    text = "\n".join(texts)
    candidates = [text.strip()]
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        candidates.insert(0, match.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


class AgentSdkExtractor:
    """A ``ClaudeSDKClient`` per call, with nothing attached."""

    def __init__(self, model: str) -> None:
        self._model = model

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *,
                      limits: ExtractionLimits) -> dict[str, Any]:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock

        # mkdtemp is 0700: the subprocess sees an empty directory of its own
        # and never the checkout, the home directory, or another call's.
        workdir = tempfile.mkdtemp(prefix="ciel-extract-")
        structured: Any = None
        texts: list[str] = []
        error: str | None = None
        try:
            options = ClaudeAgentOptions(
                model=self._model,
                system_prompt=system_prompt,
                tools=[],
                allowed_tools=[],
                disallowed_tools=list(_DENIED_TOOLS),
                mcp_servers={},
                permission_mode="dontAsk",
                setting_sources=[],
                max_turns=1,
                cwd=workdir,
                max_budget_usd=limits.max_budget_usd,
                output_format={"type": "json_schema", "schema": schema},
            )
            client = ClaudeSDKClient(options=options)
            try:
                await client.connect()
            except Exception as exc:  # noqa: BLE001 - surfaced as one error type
                raise ExtractionError(f"could not reach the model: {exc}") from exc
            try:
                await client.query(payload)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                texts.append(block.text)
                    elif isinstance(message, ResultMessage):
                        structured = message.structured_output
                        if message.is_error:
                            error = "; ".join(message.errors or []) or message.subtype
                        if structured is None and message.result:
                            texts.append(message.result)
            finally:
                try:
                    await asyncio.wait_for(client.disconnect(), _CLEANUP_S)
                except Exception:  # noqa: BLE001 - a wedged subprocess is the SDK's to reap
                    log.debug("extraction client did not close cleanly", exc_info=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if isinstance(structured, dict):
            return structured
        parsed = _json_object(texts)
        if parsed is not None:
            return parsed
        raise ExtractionError(error or "the model did not return the expected structure")


_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def conforms(schema: dict[str, Any], data: Any, path: str = "$") -> None:
    """The subset of JSON Schema an extraction needs: type, required,
    properties, additionalProperties, enum, items. Raises on the first
    disagreement, naming where."""
    kind = schema.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        ok = False
        for k in kinds:
            expected = _TYPES.get(k)
            if expected is None:
                raise ExtractionError(f"{path}: schema names an unknown type {k!r}")
            if isinstance(data, expected) and not (k in ("integer", "number") and isinstance(data, bool)):
                ok = True
        if not ok:
            raise ExtractionError(f"{path}: expected {kind}")
    if "enum" in schema and data not in schema["enum"]:
        raise ExtractionError(f"{path}: value is not one of the allowed choices")
    if isinstance(data, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in data:
                raise ExtractionError(f"{path}: missing {name!r}")
        for name, value in data.items():
            if name in properties:
                conforms(properties[name], value, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise ExtractionError(f"{path}: unexpected {name!r}")
    if isinstance(data, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(data):
            conforms(schema["items"], item, f"{path}[{index}]")


async def extract_json(backend: ExtractionBackend, lease: Lease, system_prompt: str, payload: str,
                       schema: dict[str, Any], limits: ExtractionLimits) -> dict[str, Any]:
    """One bounded, leased, schema-checked call. Raises ExtractionError;
    a cancellation passes through after the backend has cleaned up."""
    if not isinstance(payload, str) or len(payload) > limits.max_chars:
        raise ExtractionError("payload exceeds the extraction bound")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ExtractionError("an extraction schema describes an object")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ExtractionError("an extraction needs its fixed system prompt")
    async with lease():
        try:
            data = await asyncio.wait_for(backend.extract(system_prompt, payload, schema, limits=limits), limits.timeout_s)
        except asyncio.TimeoutError as exc:
            raise ExtractionError("extraction timed out") from exc
    if not isinstance(data, dict):
        raise ExtractionError("the model did not return an object")
    conforms(schema, data)
    return data


__all__ = ["AgentSdkExtractor", "ExtractionBackend", "ExtractionError", "ExtractionLimits", "Lease", "conforms", "extract_json"]
