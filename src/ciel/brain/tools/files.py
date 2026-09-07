"""Search inside the workspace, one candidate at a time.

Codename: **Sieve** — every grain is checked; nothing passes on the
strength of where the pile sits.

The built-in ``Grep`` and ``Glob`` take a *root* and then walk it on their
own. The workspace guard sees the root, approves it, and the walk goes
wherever the tree leads — into a ``credentials.json`` two levels down,
through a symlink pointing out of the workspace, into ``.ssh``. Blocking
the ``Read`` that follows is too late when the search's own output already
carried the contents. So the built-ins are refused outright (see
``permissions.py``) and these two tools do the searching instead: the same
guard, applied to every directory entered and every file opened, with
symlinks resolved before the decision — exactly the check a ``Read`` of
that path would get.

Same boundary as the file tools, then: what a search can show is what a
``Read`` could show. Nothing here consults model-supplied exclusions; the
sieve is the guard's, not the model's.

The work is done in a child process, not on the assistant's event loop.
A regular expression is the model's to write, and Python's engine
backtracks: ``^(a+)+$`` against thirty characters that almost match runs
for minutes, and nothing bounds it from inside — not a coroutine timeout
(the match never yields), not a thread (it holds the GIL). A process can
be killed. So each search is a spawned child with the guard handed over,
the parent waits out a hard deadline off the loop, and a child still
running at the deadline is killed and reported as such. The walk's size
limits still apply inside the child; the deadline is for what they
cannot see.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import multiprocessing
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterator

from claude_agent_sdk import tool

from ciel.brain.permissions import WorkspaceGuard

log = logging.getLogger(__name__)

_guard: WorkspaceGuard | None = None

_MAX_FILE_BYTES = 2_000_000
"""Files past this are skipped, not read: a search is for text."""
_MAX_MATCHES = 200
_MAX_LINE_CHARS = 400
_MAX_FILES_LISTED = 300
_MAX_ENTRIES = 50_000
"""A ceiling on directory entries examined, so a search rooted at a huge
tree ends rather than running out the turn."""
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".tox"})
"""Not secrets — just noise nobody searches on purpose."""
_DEADLINE_S = 20.0
"""The hard bound on one search, walk and matching together, after which
the child is killed. A pattern that needs longer is one the model should
narrow; the assistant's loop, sockets, and timers are not held for it."""
_RESULT_S = 5.0
"""After the child has posted its result: how long to wait for it to exit
on its own before it is killed anyway."""


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def bind_files(guard: WorkspaceGuard) -> None:
    """The boundary these tools sieve through — the brain's own guard."""
    global _guard
    _guard = guard


def _resolve_root(raw: str) -> tuple[Path | None, str | None]:
    """The search root as a Path the guard permits, or the refusal."""
    assert _guard is not None
    candidate = Path(raw or ".").expanduser()
    if not candidate.is_absolute():
        candidate = _guard.workspace / candidate
    verdict = _guard.permits(str(candidate))
    if verdict is not None:
        return None, verdict
    root = candidate.resolve()
    if not root.exists():
        return None, f"{raw or '.'} does not exist."
    return root, None


def _walk(root: Path) -> Iterator[Path]:
    """Every regular file under ``root`` the guard would let a Read open.

    Directories are checked before they are entered, files before they
    are yielded, both by resolved path — so a forbidden name anywhere in
    the tree prunes its subtree, and a symlink is judged by its target.
    Symlinked directories are never followed (a loop, or a door out).
    """
    assert _guard is not None
    budget = _MAX_ENTRIES
    if root.is_file():
        if _guard.permits(str(root)) is None:
            yield root
        return
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                listed = sorted(entries, key=lambda e: e.name)
        except OSError:
            continue
        for entry in listed:
            budget -= 1
            if budget <= 0:
                log.info("search stopped at %d entries under %s", _MAX_ENTRIES, root)
                return
            path = Path(entry.path)
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
                continue
            if is_dir:
                if entry.name in _SKIP_DIRS:
                    continue
                if _guard.permits(str(path)) is None:
                    stack.append(path)
                continue
            if is_file and _guard.permits(str(path)) is None:
                yield path


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)) or "."
    except ValueError:
        return str(path)


@tool(
    "search_files",
    (
        "Search file contents in the workspace for a regular expression "
        "(like grep -rn). `path` is a file or directory to search, relative "
        "to the workspace by default; `glob` limits it to matching file "
        "names (e.g. '*.py'). Returns file:line: matches. This is the only "
        "content search: there is no Grep tool."
    ),
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression"},
            "path": {"type": "string", "description": "file or directory; default: the workspace"},
            "glob": {"type": "string", "description": "only files whose name matches, e.g. '*.md'"},
            "ignore_case": {"type": "boolean"},
        },
        "required": ["pattern"],
    },
)
async def search_files(args: dict[str, Any]) -> dict[str, Any]:
    if _guard is None:
        return _text("File access is off.")
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return _text("A pattern is needed.")
    try:
        re.compile(pattern, re.IGNORECASE if args.get("ignore_case") else 0)
    except re.error as exc:
        return _text(f"That pattern does not compile: {exc}")
    root, refusal = _resolve_root(str(args.get("path") or ""))
    if root is None:
        return _text(refusal or "That path is off limits.")
    return _text(await _in_child(_search, args, root))


def _search(args: dict[str, Any], root: Path) -> str:
    """The search itself — runs in the child."""
    regex = re.compile(str(args.get("pattern") or ""), re.IGNORECASE if args.get("ignore_case") else 0)
    name_glob = str(args.get("glob") or "")

    lines: list[str] = []
    files_hit = 0
    truncated = False
    for path in _walk(root):
        if name_glob and not fnmatch.fnmatch(path.name, name_glob):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                hit_here = False
                for n, line in enumerate(fh, 1):
                    if "\0" in line:
                        break  # binary; leave it
                    if regex.search(line):
                        hit_here = True
                        shown = line.rstrip("\n")
                        if len(shown) > _MAX_LINE_CHARS:
                            shown = shown[:_MAX_LINE_CHARS] + "…"
                        lines.append(f"{_relative(path, root)}:{n}: {shown}")
                        if len(lines) >= _MAX_MATCHES:
                            truncated = True
                            break
                files_hit += hit_here
        except OSError:
            continue
        if truncated:
            break
    if not lines:
        return "No matches."
    tail = f"\n… stopped at {_MAX_MATCHES} matches; narrow the pattern or path." if truncated else ""
    return "\n".join(lines) + tail


@tool(
    "find_files",
    (
        "Find files in the workspace by name pattern (like a recursive "
        "glob: '*.md', 'notes-*.txt', 'report.pdf'). `path` is the directory "
        "to search, relative to the workspace by default. Returns paths "
        "relative to that directory. This is the only file finder: there is "
        "no Glob tool."
    ),
    {
        "type": "object",
        "properties": {
            "glob": {"type": "string", "description": "file-name pattern, e.g. '*.py'"},
            "path": {"type": "string", "description": "directory; default: the workspace"},
        },
        "required": ["glob"],
    },
)
async def find_files(args: dict[str, Any]) -> dict[str, Any]:
    if _guard is None:
        return _text("File access is off.")
    name_glob = str(args.get("glob") or "").strip()
    if not name_glob:
        return _text("A file-name pattern is needed.")
    # A pattern with directories in it ('src/*.py') matches on the path
    # relative to the root; a bare one on the name alone.
    on_path = "/" in name_glob
    root, refusal = _resolve_root(str(args.get("path") or ""))
    if root is None:
        return _text(refusal or "That path is off limits.")
    return _text(await _in_child(_find, args, root))


def _find(args: dict[str, Any], root: Path) -> str:
    """The walk and the name match — runs in the child. (A glob is a
    regex underneath, and ``fnmatch`` backtracks like one.)"""
    name_glob = str(args.get("glob") or "").strip()
    on_path = "/" in name_glob
    found: list[str] = []
    for path in _walk(root):
        rel = _relative(path, root)
        if fnmatch.fnmatch(rel if on_path else path.name, name_glob):
            found.append(rel)
            if len(found) >= _MAX_FILES_LISTED:
                found.append(f"… stopped at {_MAX_FILES_LISTED}; narrow the pattern or path.")
                break
    return "\n".join(found) if found else "No files match."


# ── the child ────────────────────────────────────────────────────────────────


def _child_main(conn: Any, guard: WorkspaceGuard, work: Callable[[dict[str, Any], Path], str],
                args: dict[str, Any], root: Path) -> None:
    """Entry point in the spawned process: the same guard, the same walk,
    the result back over the pipe."""
    bind_files(guard)
    try:
        conn.send(work(args, root))
    except Exception as exc:  # noqa: BLE001 - reported, not raised across the pipe
        conn.send(f"The search failed: {exc}")
    finally:
        conn.close()


def _run_child(work: Callable[[dict[str, Any], Path], str], args: dict[str, Any], root: Path,
               deadline: float) -> str:
    """Spawn, wait up to ``deadline`` for the result, kill what is left.
    Blocking — runs on a worker thread, never on the loop."""
    assert _guard is not None
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(child, _guard, work, args, root), daemon=True)
    proc.start()
    child.close()
    try:
        if parent.poll(deadline):
            try:
                result = str(parent.recv())
            except (EOFError, OSError):
                result = "The search failed: its worker exited without a result."
            proc.join(_RESULT_S)
            return result
        log.warning("search under %s killed after %.0fs", root, deadline)
        return (
            f"The search took longer than {deadline:.0f}s and was stopped; "
            "narrow the pattern or the path."
        )
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
            proc.join(_RESULT_S)


async def _in_child(work: Callable[[dict[str, Any], Path], str], args: dict[str, Any], root: Path) -> str:
    """The work in a killable child, with the loop free meanwhile. A
    cancellation (the turn abandoned) is not a leak: the thread finishes
    on its own deadline and the child is killed with it."""
    return await asyncio.to_thread(_run_child, work, args, root, _DEADLINE_S)


FILE_SEARCH_TOOLS = [search_files, find_files]

__all__ = ["FILE_SEARCH_TOOLS", "bind_files", "find_files", "search_files"]
