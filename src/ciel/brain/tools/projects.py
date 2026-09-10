"""Project tools — how Ciel opens and maintains its durable workspaces.

Same principle as the memory tools: the descriptions are the training.
Nothing else tells the model to open a project before working on it, to
replace state rather than append to it, or to start a project unprompted the
moment ongoing work begins.

**Where the work lives is the owner's to say.** bind_resource, select_resource,
unbind_resource, and rename_project write the owner's statements into the
project's own file — a path, a URL, a role, which one is current. They are
called on the owner's explicit words, never because a document or a folder
listing suggested a binding; a place Ciel found is something to ask about,
not to bind. These tools exist only on the private brain: the public
channel's client never has them.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_sdk import tool

from ciel.projects import ProjectStore

log = logging.getLogger(__name__)

# Bound at startup, same pattern as the memory store.
_store: ProjectStore | None = None


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


@tool(
    "open_project",
    (
        "Read a project's full current state and recent log. The one-line "
        "index in your system prompt tells you which projects exist; open one "
        "before doing any work on it — your recollection of a project is "
        "stale the moment a conversation ends, and the file is the truth. "
        "Use the exact name from the index."
    ),
    {"name": str},
)
async def open_project(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    name = (args.get("name") or "").strip()
    if not name:
        return _text("A project name is needed.")
    found = _store.resolve(name)
    project = found.project
    if project is None:
        if found.candidates:
            return _text(
                f"'{name}' could mean any of: {', '.join(found.candidates)}. "
                "Ask which one; do not guess."
            )
        return _text(
            f"No project named '{name}'. The index in your system prompt "
            "lists the exact names; update_project creates new ones."
        )
    log_block = (
        "\n".join(f"- {line}" for line in project.log) if project.log else "(empty)"
    )
    aliases = f"Also called: {', '.join(project.aliases)}\n" if project.aliases else ""
    resources = "\n".join(
        f"- {r.key}: {r.role} · {r.source} · {r.locator}"
        + (" · current" if r.current else "") + (f" · opens with {r.opener}" if r.opener else "")
        for r in project.resources
    ) or "(none bound — ask the owner where the work lives, then bind_resource)"
    return _text(
        f"# {project.name} ({project.status})\n{project.description}\n{aliases}\n"
        f"## State\n{project.state}\n\n## Resources\n{resources}\n\n## Recent log\n{log_block}"
    )


@tool(
    "update_project",
    (
        "Create a project or replace its working state. Projects are durable "
        "workspaces for anything spanning multiple conversations — a training "
        "effort, a trip being planned, a long errand. Create one as soon as "
        "such work starts, without being asked.\n\n"
        "`state` must be the complete current picture, written to be read "
        "cold weeks later: where things stand, key values and paths, what's "
        "next. Replace, don't append — condense as you go; the tool refuses "
        "states over four thousand characters. Update whenever reality "
        "moves: a decision, a result, a changed plan.\n\n"
        "`description` is the one-liner for the index (needed when creating); "
        "`status` is active, paused, or done — mark projects done rather "
        "than abandoning them in the index. `aliases` is a comma-separated "
        "list of the other names the owner uses for it ('analysis homework, "
        "h104'); it replaces the list, and open_project honours them."
    ),
    {"name": str, "state": str, "description": str, "status": str, "aliases": str},
)
async def update_project(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    name = (args.get("name") or "").strip()
    state = (args.get("state") or "").strip()
    if not name or not state:
        return _text("Both a project name and a state are needed.")
    try:
        project = _store.write(
            name,
            state,
            description=(args.get("description") or "").strip() or None,
            status=(args.get("status") or "").strip() or None,
        )
        aliases = args.get("aliases")
        if isinstance(aliases, str) and aliases.strip():
            project = _store.set_aliases(project.name, [a for a in aliases.split(",")])
    except ValueError as exc:
        return _text(str(exc))
    return _text(f"Project '{project.name}' saved ({project.status}).")


@tool(
    "log_progress",
    (
        "Append one dated line to a project's log without rewriting its "
        "state — milestones, measurements, decisions worth a timestamp. One "
        "event per call, one sentence per event."
    ),
    {"name": str, "entry": str},
)
async def log_progress(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    name = (args.get("name") or "").strip()
    entry = (args.get("entry") or "").strip()
    if not name or not entry:
        return _text("Both a project name and a log entry are needed.")
    if _store.append_log(name, entry):
        return _text(f"Logged to '{name}'.")
    return _text(f"No project named '{name}' — create it with update_project first.")


@tool(
    "bind_resource",
    (
        "Connect a place where a project's work lives — a file or folder on "
        "the Mac, or a URL — only when the owner has said so in their own "
        "words. `role` is what it is to the project (solution, handout, "
        "folder, draft, reference, dataset, notebook, published…); `locator` "
        "is the path or URL as given. `current` marks it the current one of "
        "its role (one per role); `key` names it (defaults to the role) and "
        "binding a known key replaces it; `opener` is an app to open it with. "
        "A place you found in a listing is something to ask about, never to "
        "bind on your own."
    ),
    {"project": str, "role": str, "locator": str, "current": bool, "key": str, "opener": str},
)
async def bind_resource(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    project = (args.get("project") or "").strip()
    if not project:
        return _text("A project name is needed.")
    found = _store.resolve(project)
    if found.project is None:
        return _text(f"No project named '{project}'." + (f" Did you mean: {', '.join(found.candidates)}?" if found.candidates else ""))
    try:
        resource = _store.bind(
            found.project.name, str(args.get("role") or ""), str(args.get("locator") or ""),
            key=str(args.get("key") or ""), current=bool(args.get("current")), opener=str(args.get("opener") or ""),
        )
    except ValueError as exc:
        return _text(str(exc))
    return _text(f"Bound {resource.key} ({resource.role}, {resource.source}) to '{found.project.name}'"
                 + (" as current." if resource.current else "."))


@tool(
    "select_resource",
    "Make one bound resource the current one of its role — 'this week's homework is hw03' — at the owner's word. `key` is from open_project.",
    {"project": str, "key": str},
)
async def select_resource(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    found = _store.resolve((args.get("project") or "").strip())
    if found.project is None:
        return _text("No such project.")
    try:
        resource = _store.select(found.project.name, str(args.get("key") or ""))
    except ValueError as exc:
        return _text(str(exc))
    return _text(f"{resource.key} is now the current {resource.role} of '{found.project.name}'.")


@tool(
    "unbind_resource",
    "Forget one bound resource at the owner's request. `key` is from open_project. The file or URL itself is untouched.",
    {"project": str, "key": str},
)
async def unbind_resource(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    found = _store.resolve((args.get("project") or "").strip())
    if found.project is None:
        return _text("No such project.")
    try:
        gone = _store.unbind(found.project.name, str(args.get("key") or ""))
    except ValueError as exc:
        return _text(str(exc))
    return _text(f"Unbound {args.get('key')} from '{found.project.name}'." if gone else f"No resource '{args.get('key')}' on '{found.project.name}'.")


@tool(
    "rename_project",
    "Rename a project at the owner's request. Its id, state, log, and bindings stay; the old name becomes an alias so it still resolves.",
    {"name": str, "new_name": str},
)
async def rename_project(args: dict[str, Any]) -> dict[str, Any]:
    if _store is None:
        return _text("Projects are not available right now.")
    found = _store.resolve((args.get("name") or "").strip())
    if found.project is None:
        return _text("No such project.")
    try:
        project = _store.rename(found.project.name, str(args.get("new_name") or ""))
    except ValueError as exc:
        return _text(str(exc))
    return _text(f"Renamed to '{project.name}'; '{found.project.name}' still resolves to it.")


def bind_projects(store: ProjectStore) -> None:
    """Attach the store these tools operate on."""
    global _store
    _store = store


PROJECT_TOOLS = [open_project, update_project, log_progress, bind_resource, select_resource, unbind_resource, rename_project]

__all__ = ["PROJECT_TOOLS", "bind_projects", "open_project", "update_project", "log_progress",
           "bind_resource", "select_resource", "unbind_resource", "rename_project"]
