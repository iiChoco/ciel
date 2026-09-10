"""The work a project is bound to, reached from where the brain runs.

Atlas records where a project's work lives (``projects.py``); the readers
say what a document holds (``readers.py``); this module is the hands
between them — the *workbench*: read a bound document within the bounds
the project sets, open one where the owner is, and turn a resource into a
reading on demand. In the single process the workbench is the filesystem
and ``open``; on the hub it is the spoke's executor one hop away, so a
server never stands in for the Mac the work is on.

**The roots are the registered folder.** A document is read only when it
is a bound resource or lies under a folder the owner bound to the same
project, and an include is followed only within those roots. The brain's
workspace guard is not the rule here: a course folder is nowhere near the
workspace, and the owner naming it is what makes it readable. Both halves
check: this side against the project's bindings, the spoke against its own
home, state directory, and credential names.

**Opening is attended, reading is quiet.** "Pull it up" opens the current
resource of the role asked for, with the app the binding names or the
Mac's default; nothing is written, and a URL is handed to the browser.
A reading is a bounded, deterministic pass — bytes, includes, and depth
capped by ``[projects]`` — and is answered live; keeping readings and
watching for change is the plan's next step.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ciel.projects import Project, Resource
from ciel.readers import Reading, describe, read, reader_for

log = logging.getLogger(__name__)

DOCUMENT_SUFFIXES = (".tex", ".ltx", ".latex", ".sty", ".cls", ".bib", ".md", ".markdown", ".mdown", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml")
"""What a workbench reads as a document: text the owner writes or that a
document includes. Anything else is located and opened, never read here."""


@dataclass(frozen=True, slots=True)
class WorkLimits:
    max_bytes: int = 2_000_000
    max_includes: int = 20
    include_depth: int = 3


class Workbench(Protocol):
    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        """The document's bytes, or None and why not, within ``max_bytes``."""

    async def open(self, target: str, opener: str) -> str:
        """Open a path or URL where the owner is; the sentence to tell them."""


def check_document_path(raw: str, *, home: Path, state_dir: Path, forbidden: frozenset[str]) -> tuple[Path | None, str]:
    """The spoke's own opinion of a document path, whatever the hub said:
    under home, not under the state directory, not a credential's name, a
    document suffix. Returns the resolved path or the refusal."""
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return None, "a document is named by an absolute path"
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None, "the path could not be resolved"
    try:
        resolved.relative_to(home.resolve())
    except ValueError:
        return None, "documents are read only under the home folder"
    try:
        resolved.relative_to(state_dir.expanduser().resolve())
        return None, "nothing under the state directory is a document"
    except ValueError:
        pass
    if resolved.name in forbidden or any(part in forbidden for part in resolved.parts):
        return None, "that name is off limits"
    if resolved.suffix.lower() not in DOCUMENT_SUFFIXES:
        return None, f"{resolved.suffix or 'no suffix'} is not a document type the reader takes"
    return resolved, ""


def read_document_bytes(path: Path, max_bytes: int) -> tuple[bytes | None, str | None]:
    """A bounded read: one byte past the bound tells a growing file apart."""
    try:
        with path.open("rb") as source:
            data = source.read(max_bytes + 1)
    except FileNotFoundError:
        return None, "the file does not exist"
    except IsADirectoryError:
        return None, "that is a folder, not a document"
    except OSError as exc:
        return None, f"the file could not be read ({exc.__class__.__name__})"
    if len(data) > max_bytes:
        return None, f"the file is larger than {max_bytes} bytes"
    return data, None


class LocalWorkbench:
    """The single process: the owner's machine is this one."""

    def __init__(self, *, home: Path | None = None, state_dir: Path, forbidden: frozenset[str] = frozenset()) -> None:
        self._home = (home or Path.home()).expanduser()
        self._state_dir = state_dir
        self._forbidden = forbidden

    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        resolved, why = check_document_path(path, home=self._home, state_dir=self._state_dir, forbidden=self._forbidden)
        if resolved is None:
            return None, why
        return await asyncio.to_thread(read_document_bytes, resolved, max_bytes)

    async def open(self, target: str, opener: str) -> str:
        return await asyncio.to_thread(open_target, target, opener, home=self._home, state_dir=self._state_dir)


def open_target(target: str, opener: str, *, home: Path, state_dir: Path, runner: Any = subprocess.run) -> str:
    """``open`` on the Mac: a URL to the browser, a path to its app or the
    one named. Nothing under the state directory, nothing that is not there."""
    if target.lower().startswith(("http://", "https://")):
        args = ["/usr/bin/open", *(["-a", opener] if opener else []), target]
        what = "the page"
    else:
        path = Path(target).expanduser()
        try:
            resolved = path.resolve()
            resolved.relative_to(home.resolve())
        except (OSError, RuntimeError, ValueError):
            return "refused: documents open only under the home folder"
        try:
            resolved.relative_to(state_dir.expanduser().resolve())
            return "refused: nothing under the state directory is opened"
        except ValueError:
            pass
        if not resolved.exists():
            return f"not opened: {resolved} does not exist"
        args = ["/usr/bin/open", *(["-a", opener] if opener else []), str(resolved)]
        what = resolved.name
    try:
        result = runner(args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not open {what}: {exc.__class__.__name__}"
    if result.returncode != 0:
        return f"could not open {what}: {(result.stderr or '').strip() or f'open exited {result.returncode}'}"
    return f"opened {what}" + (f" with {opener}" if opener else "")


class RemoteWorkbench:
    """The hub: the Mac is the spoke, one call away."""

    def __init__(self, mac: Any) -> None:
        self._mac = mac

    async def read(self, path: str, max_bytes: int) -> tuple[bytes | None, str | None]:
        try:
            return await self._mac.read_document(path, max_bytes)
        except Exception as exc:  # noqa: BLE001 - the Mac may be gone; that is a sentence
            return None, f"the Mac could not be reached: {exc}"

    async def open(self, target: str, opener: str) -> str:
        try:
            return str(await self._mac.open_document(target, opener))
        except Exception as exc:  # noqa: BLE001
            return f"the Mac could not be reached: {exc}"


def roots_for(project: Project, resource: Resource) -> tuple[Path, ...]:
    """Where a reading may go: the folders bound to the project, else the
    document's own folder. The owner's bindings, not a global rule."""
    folders = [Path(r.locator).expanduser() for r in project.resources if r.source == "local" and r.role in ("folder", "root", "directory")]
    if not folders:
        folders = [Path(resource.locator).expanduser().parent]
    return tuple(folders)


def within(path: Path, roots: tuple[Path, ...]) -> bool:
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


@dataclass(frozen=True, slots=True)
class Observed:
    resource: Resource
    reading: Reading | None
    revisions: dict[str, str]
    """A content hash per file the reading covered, the main one first."""
    note: str
    """Why there is no reading, or what to say beside one."""

    def describe(self) -> str:
        head = f"{self.resource.key} ({self.resource.role}): {self.resource.locator}"
        if self.reading is None:
            return f"{head} — {self.note}"
        return f"{head} — {describe(self.reading)}" + (f" {self.note}" if self.note else "")


async def observe(project: Project, resource: Resource, bench: Workbench, limits: WorkLimits) -> Observed:
    """One on-demand reading of a bound local document, includes followed
    within the project's roots, every file hashed. No model, no writes."""
    if resource.source != "local":
        return Observed(resource, None, {}, "a URL is opened, not read here")
    main = Path(resource.locator).expanduser()
    roots = roots_for(project, resource)
    if not within(main, roots):
        return Observed(resource, None, {}, "the document lies outside the folders bound to this project")
    if reader_for(resource.locator) is None:
        return Observed(resource, None, {}, f"no reader for {main.suffix or 'a file with no suffix'}; it can be opened, not read")
    data, why = await bench.read(str(main), limits.max_bytes)
    if data is None:
        return Observed(resource, None, {}, why or "the document could not be read")
    revisions = {main.name: hashlib.sha256(data).hexdigest()[:16]}
    text = data.decode("utf-8", "replace")
    followed = 0
    pending: list[tuple[str, Path]] = []
    loaded: dict[str, str] = {}

    async def preload(reference: str, base: Path, depth: int) -> None:
        """Includes are read ahead, breadth-first, within the roots and the
        limits; the reader's loader then answers from memory, synchronously."""
        nonlocal followed
        if depth > limits.include_depth or followed >= limits.max_includes:
            return
        candidate = (base / reference).with_suffix(".tex") if not reference.endswith(".tex") else base / reference
        key = reference
        if key in loaded or not within(candidate, roots):
            return
        chunk, _ = await bench.read(str(candidate), limits.max_bytes)
        if chunk is None:
            return
        followed += 1
        loaded[key] = chunk.decode("utf-8", "replace")
        revisions[candidate.name] = hashlib.sha256(chunk).hexdigest()[:16]
        for inner in _references(loaded[key]):
            await preload(inner, candidate.parent, depth + 1)

    for reference in _references(text):
        await preload(reference, main.parent, 1)
    reading = read(text, main.name, lambda reference: loaded.get(reference))
    note = "" if followed < limits.max_includes else f"stopped following includes at {limits.max_includes}."
    return Observed(resource, reading, revisions, note)


def _references(text: str) -> list[str]:
    import re
    return [m.group(1).strip() for m in re.finditer(r"\\(?:input|include)\s*\{\s*([^}]*?)\s*\}", text)]


__all__ = ["DOCUMENT_SUFFIXES", "LocalWorkbench", "Observed", "RemoteWorkbench", "WorkLimits", "Workbench", "check_document_path",
           "observe", "open_target", "read_document_bytes", "roots_for", "within"]
