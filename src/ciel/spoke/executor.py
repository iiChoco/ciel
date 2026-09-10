"""The spoke's executor: the hub's tool calls, done on the Mac.

Each ``tool.request`` names one of the handlers below and carries its
arguments; the answer goes back as ``tool.result`` with ``content`` or
``error``. The handlers are thin — the real implementations are the
same modules the single process uses (the screen capture, the Messages
client, the locator, the work watcher, the calendar store) — and the
executor's own job is the boundary: JSON in and out, one task per call
so a slow capture never blocks a fast lookup, ``tool.cancel`` honored,
every failure a sentence rather than a traceback on the wire.

The shell and the files are the one place this module has opinions.
The hub's guards already asked the user; this side does not trust that
alone. ``ShellGuard``'s classifier runs again here, from *this*
machine's config: a command the classifier would deny is refused
whatever the hub says, a command that needs a confirmation is refused
unless the hub says it asked, and only the quiet tier runs on the
hub's word alone. The file handlers run the workspace guard's path
check the same way. Defense in depth, because this socket is the whole
distance between "the user's assistant" and "whatever reached the port".

**Cancellation owns the process, not just its coroutine.** Each shell has
its own process group, stopped and reaped on cancellation or either
deadline. Shutdown waits for that cleanup. **Undo reads the Mac first:**
the recorder's private snapshot request applies the write boundary and
returns bounded bytes for the hub to save before the write is allowed on.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ciel.brain.permissions import WorkspaceGuard, forbidden_names
from ciel.brain.shellguard import classify

if TYPE_CHECKING:
    from ciel.config import Config
    from ciel.location import Locator
    from ciel.messages import MessagesClient
    from ciel.proactive.work import WorkWatcher

log = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 12000
"""Shell output past this is cut, with a note — the model reads it into
context, and a runaway `cat` must not cost a whole turn's window."""


class Executor:
    """The handler registry and the in-flight table."""

    def __init__(
        self,
        config: "Config",
        send: Callable[[dict[str, Any]], bool],
        *,
        messages: "MessagesClient | None" = None,
        locator: "Locator | None" = None,
        watcher: "WorkWatcher | None" = None,
        calendar: Any | None = None,
        resources: Any | None = None,
    ) -> None:
        self._config = config
        self._send = send
        self._messages = messages
        self._locator = locator
        self._watcher = watcher
        self._calendar = calendar
        self._resources = resources
        """The resource watcher (``proactive/resources.py``): the hub names what to watch."""
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._workspace: WorkspaceGuard | None = (
            WorkspaceGuard.from_config(config)
            if config.files.enabled
            else None
        )
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {
            "screen.capture": self._screen_capture,
            "messages.find_contacts": self._messages_find,
            "messages.recent": self._messages_recent,
            "messages.send": self._messages_send,
            "location.describe": self._location_describe,
            "location.current": self._location_current,
            "watch.add": self._watch_add,
            "watch.active": self._watch_active,
            "calendar.agenda_today": self._calendar_agenda,
            "shell.run": self._shell_run,
            "files.read": self._files_read,
            "files.snapshot": self._files_snapshot,
            "files.write": self._files_write,
            "files.list": self._files_list,
            "project.read": self._project_read,
            "project.open": self._project_open,
            "project.watch": self._project_watch,
        }

    # ── the wire's side ──────────────────────────────────────────────────────

    def handle(self, frame: dict[str, Any]) -> None:
        """A ``tool.request`` or ``tool.cancel``; returns at once."""
        if frame["type"] == "tool.cancel":
            task = self._tasks.get(frame["rpc_id"])
            if task is not None and not task.done() and not task.cancelling():
                task.cancel()
            return
        rpc_id = frame["rpc_id"]
        if rpc_id in self._tasks:
            return  # a retry cannot orphan the task whose cleanup still owns this id
        task = asyncio.create_task(
            self._run(rpc_id, frame["tool"], frame.get("args") or {},
                      float(frame.get("timeout_s") or 30.0))
        )
        self._tasks[rpc_id] = task

        def finished(done: asyncio.Task[None]) -> None:
            # A task canceled before its first instruction never enters
            # _run; its ledger entry still has to leave.
            self._tasks.pop(rpc_id, None)

        task.add_done_callback(finished)

    async def _run(self, rpc_id: str, tool: str, args: dict[str, Any], timeout: float) -> None:
        handler = self._handlers.get(tool)
        try:
            if handler is None:
                raise ValueError(f"the Mac does not know the tool {tool!r}")
            content = await asyncio.wait_for(handler(**args), timeout)
            result = {"type": "tool.result", "rpc_id": rpc_id, "ok": True, "content": content}
        except asyncio.CancelledError:
            return
        except asyncio.TimeoutError:
            result = {"type": "tool.result", "rpc_id": rpc_id, "ok": False,
                      "error": f"{tool} took longer than {timeout:.0f}s on the Mac"}
        except Exception as exc:  # noqa: BLE001 - every failure is a sentence on the wire
            log.debug("tool %s failed", tool, exc_info=True)
            result = {"type": "tool.result", "rpc_id": rpc_id, "ok": False, "error": str(exc)}
        self._send(result)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── the senses ───────────────────────────────────────────────────────────

    async def _screen_capture(self, max_edge: int = 1568) -> list[str]:
        from ciel.brain.tools.screen import capture_displays

        images = await capture_displays(int(max_edge))
        return [base64.b64encode(data).decode("ascii") for data in images]

    async def _messages_find(self, query: str) -> list[dict[str, str]]:
        if self._messages is None:
            raise RuntimeError("Messages access is off on the Mac")
        return [
            {"name": c.name, "kind": c.kind, "handle": c.handle}
            for c in await self._messages.find_contacts(query)
        ]

    async def _messages_recent(self, limit: int = 20, handle: str | None = None) -> list[dict[str, Any]]:
        if self._messages is None:
            raise RuntimeError("Messages access is off on the Mac")
        return [
            {
                "text": m.text, "from_me": m.from_me, "handle": m.handle,
                "chat": m.chat, "when": m.when.isoformat() if m.when else None,
                "is_group": m.is_group,
            }
            for m in await self._messages.recent(limit=int(limit), handle=handle)
        ]

    async def _messages_send(self, handle: str, text: str) -> bool:
        if self._messages is None:
            raise RuntimeError("Messages access is off on the Mac")
        await self._messages.send(handle, text)
        return True

    async def _location_describe(self, max_age_s: float = 120.0) -> str:
        if self._locator is None:
            raise RuntimeError("location is off on the Mac")
        fix = await asyncio.to_thread(self._locator.current, float(max_age_s))
        return self._locator.describe(fix)

    async def _location_current(self, max_age_s: float = 120.0) -> dict[str, Any] | None:
        if self._locator is None:
            raise RuntimeError("location is off on the Mac")
        fix = await asyncio.to_thread(self._locator.current, float(max_age_s))
        if fix is None:
            return None
        return {
            "at": fix.at, "source": fix.source, "latitude": fix.latitude,
            "longitude": fix.longitude, "accuracy_m": fix.accuracy_m,
            "network": fix.network, "device": fix.device,
        }

    async def _watch_add(self, kind: str, target: str, label: str,
                         timeout_minutes: float = 60.0) -> dict[str, Any]:
        if self._watcher is None:
            raise RuntimeError("background watching is off on the Mac")
        if kind == "file":
            watch = self._watcher.add_file(str(target), str(label), float(timeout_minutes))
        elif kind == "pid":
            watch = self._watcher.add_pid(int(target), str(label), float(timeout_minutes))
        else:
            raise ValueError(f"unknown watch kind {kind!r}")
        return _watch_dict(watch)

    async def _watch_active(self) -> list[dict[str, Any]]:
        if self._watcher is None:
            return []
        return [_watch_dict(w) for w in self._watcher.active()]

    async def _calendar_agenda(self) -> list[str] | None:
        if self._calendar is None:
            return None
        rows = await self._calendar.agenda_today()
        return None if rows is None else list(rows)

    # ── the machine ──────────────────────────────────────────────────────────

    async def _shell_run(self, command: str, confirmed: bool = False) -> dict[str, Any]:
        if not self._config.shell.enabled:
            raise RuntimeError("the shell is off on the Mac")
        tier, reason = classify(command, self._config.shell, forbidden=forbidden_names(self._config))
        if tier == "deny":
            raise RuntimeError(f"refused on the Mac — {reason}")
        if tier != "quiet" and not confirmed:
            raise RuntimeError("refused on the Mac — that command needs the user's yes, and none was given")
        starting = asyncio.create_task(asyncio.create_subprocess_shell(
            command,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._config.files.workspace.expanduser()) if self._config.files.enabled else None,
        ))
        try:
            proc = await asyncio.shield(starting)
        except asyncio.CancelledError:
            # Creation may already have forked while its await is pending.
            # Keep ownership until we have the handle needed to stop it.
            proc = await starting
            await _stop_shell(proc)
            raise
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), self._config.shell.command_timeout_s
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            await _stop_shell(proc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise RuntimeError(
                f"the command ran past {self._config.shell.command_timeout_s:.0f}s and was killed"
            ) from None
        return {
            "exit": proc.returncode,
            "stdout": _cut(out.decode(errors="replace")),
            "stderr": _cut(err.decode(errors="replace")),
        }

    def _path(self, raw: str, *, write: bool) -> Path:
        if self._workspace is None:
            raise RuntimeError("file access is off on the Mac")
        verdict = self._workspace._check(raw, is_write=write)  # noqa: SLF001 - the one check, reused
        if verdict is not None:
            raise RuntimeError(f"refused on the Mac — {verdict}")
        path = Path(raw).expanduser()
        return path if path.is_absolute() else self._workspace.workspace / path

    async def _files_read(self, path: str) -> str:
        target = self._path(path, write=False)
        return _cut(await asyncio.to_thread(target.read_text, "utf-8", "replace"))

    async def _files_snapshot(self, path: str, max_bytes: int) -> dict[str, Any]:
        """The recorder's bounded read, before a write; never a model tool."""
        target = self._path(path, write=True)
        limit = max(0, min(int(max_bytes), self._config.journal.max_snapshot_kb * 1024))

        def read() -> dict[str, Any]:
            try:
                # Read one byte past the bound: a file growing after stat
                # must not turn the snapshot into an unbounded wire frame.
                with target.open("rb") as source:
                    data = source.read(limit + 1)
            except FileNotFoundError:
                return {"data": None, "note": "file did not exist before this call"}
            if len(data) > limit:
                return {"data": None, "note": "file too large to snapshot"}
            return {"data": base64.b64encode(data).decode("ascii"), "note": None}

        return await asyncio.to_thread(read)

    async def _files_write(self, path: str, content: str) -> str:
        target = self._path(path, write=True)

        def write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")

        await asyncio.to_thread(write)
        return f"wrote {len(str(content))} characters to {target}"

    async def _project_read(self, path: str, max_bytes: int) -> dict[str, Any]:
        """A bound document, read as data for the hub's readers. The hub
        checked the path against the project's bindings; this side checks
        it against its own home, state directory, credential names, and
        the document suffixes — the workspace guard is not the rule here,
        and neither is the hub's word alone."""
        from ciel.project_work import check_document_path, read_document_bytes

        resolved, why = check_document_path(path, home=Path.home(), state_dir=self._config.state_dir,
                                            forbidden=forbidden_names(self._config))
        if resolved is None:
            raise RuntimeError(f"refused on the Mac — {why}")
        limit = max(0, min(int(max_bytes), 8 * 1024 * 1024))
        data, note = await asyncio.to_thread(read_document_bytes, resolved, limit)
        if data is None:
            return {"data": None, "note": note}
        return {"data": base64.b64encode(data).decode("ascii"), "note": None}

    async def _project_open(self, target: str, opener: str = "") -> str:
        """Open a document or a page where the owner is. Quiet on purpose:
        nothing is written and nothing leaves the machine; the state
        directory is never opened, and a path that is not there is said."""
        from ciel.project_work import open_target

        opener = " ".join(str(opener or "").split())
        if any(ch in opener for ch in " ;&|$`"):
            raise RuntimeError("refused on the Mac — an opener is one app name")
        return await asyncio.to_thread(open_target, str(target), opener, home=Path.home(), state_dir=self._config.state_dir, runner=self._open_runner)

    _open_runner: Any = None
    """The subprocess runner ``open`` goes through; the probes plant one."""

    async def _project_watch(self, paths: list[str]) -> dict[str, Any]:
        """The hub's watched set, replaced whole. Each path passes the same
        document rules a read does; one that does not is dropped and named,
        never watched on the hub's word alone."""
        from ciel.project_work import check_document_path

        if self._resources is None:
            raise RuntimeError("the Mac is not watching documents (projects are off here)")
        accepted: list[str] = []
        refused: list[str] = []
        for raw in list(paths or [])[:500]:
            resolved, _ = check_document_path(str(raw), home=Path.home(), state_dir=self._config.state_dir,
                                              forbidden=forbidden_names(self._config))
            (accepted if resolved is not None else refused).append(str(resolved) if resolved is not None else str(raw))
        watching = self._resources.set_paths(accepted)
        return {"watching": watching, "refused": refused}

    async def _files_list(self, path: str = ".") -> list[str]:
        target = self._path(path, write=False)
        names = await asyncio.to_thread(lambda: sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir()))
        return names[:500]


async def _stop_shell(proc: asyncio.subprocess.Process) -> None:
    """Stop the owned process group and wait for its shell to be reaped."""
    # The shell may be waiting for children that still own its pipes.
    # Killing just its PID abandons those children to keep working.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    cleanup = asyncio.create_task(proc.communicate())
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


def _watch_dict(watch: Any) -> dict[str, Any]:
    return {
        "id": watch.id, "kind": watch.kind, "target": watch.target,
        "label": watch.label, "created_at": watch.created_at,
        "expires_at": watch.expires_at,
    }


def _cut(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + f"\n… [{len(text) - _MAX_OUTPUT_CHARS} more characters cut]"


__all__ = ["Executor"]
