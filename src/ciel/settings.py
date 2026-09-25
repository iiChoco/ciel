"""The settings desk — ``~/.ciel/config.toml`` read and written from a page.

Every swappable choice in Ciel is a field on a dataclass in ``config.py``,
four hundred of them across forty sections, and until this module the only
way to change one was to open the TOML file in an editor, remember the
field's name, and restart. That is fine for the person who wrote the field
and poor for the same person a month later, on a phone, wanting the wake
threshold a little lower. This module is the other door onto the same file:
it describes the config as data a page can draw (``snapshot``) and applies a
reviewed set of changes back to the file (``write``). ``remote/web.py``
carries both over the Chart's gated socket; ``remote/settings.html`` is the
page.

It keeps a small number of promises, and they are the reason it is a module
rather than forty lines in the web server.

**The file stays the user's file.** A write edits the lines it must and no
others: comments, blank lines, ordering, and keys this version of Ciel has
never heard of all survive. After the edit the new text is parsed and
compared, key by key, with what the old text held plus the changes asked
for; anything else having moved is a refusal, not a save. A layout the
line editor cannot follow (a dotted key, an inline table) is refused by that
same comparison, with the advice to edit by hand, rather than guessed at.

**A page cannot widen what Ciel may do, or whom it answers to.** The
sections that carry the security posture — the shell, file access, the
confirmation gate, grants, the journal, the servers and their tokens, the
interview room, connectors — are *shown*, because seeing the posture is
useful, and are not writable here. Neither is any path, any field that names
an owner or a command, or any field an environment variable is overriding.
Those are changed in the file, by hand, on purpose.

**Secrets never travel.** A token, cookie, password, or key is reported as
set or unset and nothing more; its value is not in the snapshot and cannot
be written from the page.

**Nothing unreadable is ever saved.** The candidate file is loaded by the
real loader before it replaces the old one, so a value the loader would
refuse is refused here, by name, and the running config is never left
pointing at a file that will not start. The write is atomic and owner-only
(the file can hold tokens), the text it replaced is kept beside it as
``config.toml.previous``, and the action journal, when there is one, records
what changed. A write carries the revision it was drawn from; a file that
moved underneath is a conflict, never an overwrite.

**Settings take effect on restart**, as they always have: the snapshot says
whether the file has moved since this process loaded it.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import re
import socket
import tomllib
from dataclasses import MISSING, fields
from pathlib import Path
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ciel import config as config_module
from ciel.config import _SECTIONS, Config, load_config

log = logging.getLogger(__name__)

GENERAL = "general"
"""The pseudo-section for the file's top-level keys."""

_GENERAL_FIELDS = ("timezone", "log_level", "state_dir")

GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("general", "General", (GENERAL,)),
    ("voice", "Voice and hearing", ("wake", "audio", "gestures", "voice", "stt", "tts")),
    ("mind", "Mind", ("brain", "memory", "reflection", "proactive", "world", "timers", "commands")),
    ("work", "Work", ("tasks", "projects", "learning", "email_calendar", "nutrition", "notes")),
    ("connections", "Connections", ("messages", "mail", "spotify", "oura", "sections", "location")),
    ("surfaces", "Surfaces", ("ui", "shortcuts", "screen", "transcripts", "web", "hub", "spoke", "interview")),
    ("safety", "Safety", ("confirm", "grants", "files", "shell", "journal", "dev")),
)

LOCKED_SECTIONS: dict[str, str] = {
    name: "Part of the security posture: changed in the file by hand, on purpose."
    for name in ("confirm", "grants", "files", "shell", "journal", "web", "hub", "spoke", "interview", "dev")
}

LOCKED_FIELDS: dict[str, str] = {
    # A command Ciel runs, the tools it may use, the people it answers to or
    # writes as, and the switches that let it act outward: a page reachable
    # through a tunnel does not get to move these.
    "sections.refresh_cmd": "A command Ciel runs: changed in the file by hand.",
    "brain.allowed_tools": "What the brain may use: changed in the file by hand.",
    "messages.allow_send": "Lets Ciel send as you: changed in the file by hand.",
    "spotify.confirm_controls": "A confirmation gate: changed in the file by hand.",
    "tasks.owner": "Whom Ciel answers to: changed in the file by hand.",
    "mail.owner": "Whom Ciel answers to: changed in the file by hand.",
    "nutrition.owner_host": "Whom Ciel answers to: changed in the file by hand.",
    "nutrition.owner_role": "Whom Ciel answers to: changed in the file by hand.",
    "email_calendar.allowed_senders": "Whose mail Ciel acts on: changed in the file by hand.",
}

ROOM_SECTIONS = frozenset({"audio", "wake", "gestures", "voice", "stt", "shortcuts", "notes", "screen", "ui"})
"""Sections the room's spoke reads. On a hub they describe a microphone the
hub does not have, and the page says so."""

_SECRET = re.compile(r"(token|secret|password|cookie|api_key)")
_MAX_STR = 4000
_MAX_LIST = 200
_MAX_CHANGES = 100


class SettingsError(ValueError):
    """A write that was refused. ``errors`` names fields; ``stale`` says the
    file moved underneath the page."""

    def __init__(self, message: str, *, errors: dict[str, str] | None = None, stale: bool = False) -> None:
        super().__init__(message)
        self.errors = errors or {}
        self.stale = stale


# ── what the fields are ──────────────────────────────────────────────────────

def _attribute_docs() -> dict[str, dict[str, str]]:
    """Class and attribute docstrings from ``config.py``, by class name.

    An attribute docstring is a string literal after an assignment: the
    project's convention, invisible at runtime, so it is read from source.
    The class's own docstring is kept under the empty key.
    """
    docs: dict[str, dict[str, str]] = {}
    try:
        tree = ast.parse(Path(config_module.__file__).read_text())
    except (OSError, SyntaxError):
        log.debug("could not read config.py for its docstrings", exc_info=True)
        return docs
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        found = {"": ast.get_docstring(node) or ""}
        previous: str | None = None
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                previous = item.target.id
                continue
            if previous and isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                found[previous] = _prose(item.value.value)
            previous = None
        docs[node.name] = found
    return docs


def _prose(text: str) -> str:
    """A docstring as paragraphs of plain prose: reST's double backticks and
    hard wraps removed, paragraph breaks kept."""
    import inspect

    paragraphs = re.split(r"\n\s*\n", inspect.cleandoc(text))
    return "\n\n".join(" ".join(p.split()).replace("``", "") for p in paragraphs if p.strip())


def _members(annotation: Any) -> set[Any]:
    members = set(get_args(annotation)) if get_origin(annotation) in (Union, UnionType) else {annotation}
    return members


def _kind(annotation: Any) -> tuple[str, list[Any], bool]:
    """(kind, choices, nullable) for one field's type. Kind ``other`` is a
    shape the page only shows."""
    members = _members(annotation)
    nullable = type(None) in members
    members.discard(type(None))
    literals = [arg for m in members if get_origin(m) is Literal for arg in get_args(m)]
    if literals and all(get_origin(m) is Literal for m in members):
        return "choice", literals + ([None] if nullable else []), nullable
    if Path in members:
        return "path", [], nullable
    if members == {bool}:
        return "bool", [], nullable
    if members == {int}:
        return "int", [], nullable
    if members == {float} or members == {int, float}:
        return "float", [], nullable
    if members == {str} or members == {int, str}:
        # `int | str` is a device spec: an index or a name fragment. The
        # loader reads a numeric string as the index, so text carries both.
        return "str", [], nullable
    if len(members) == 1 and get_origin(next(iter(members))) is tuple:
        if get_args(next(iter(members))) == (str, ...):
            return "list", [], nullable
    return "other", [], nullable


def _plain(value: Any) -> Any:
    """A config value in the JSON the page reads."""
    if isinstance(value, Path):
        home = str(Path.home())
        text = str(value)
        return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, (list, dict)):
        return json.loads(json.dumps(value, default=str))
    return str(value)


def _default(f: Any) -> Any:
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:
        return f.default_factory()
    return None


def _title(name: str) -> str:
    return {"stt": "Speech to text", "tts": "Text to speech", "ui": "Indicator", "email_calendar": "Email to calendar",
            "dev": "Development", GENERAL: "General"}.get(name, name.replace("_", " ").capitalize())


# ── the file, edited in place ────────────────────────────────────────────────

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def _format(value: Any) -> str:
    """One value as TOML. Strings are basic strings with every control
    character escaped, so nothing a page sends can break out of its line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        text = repr(value)
        return text if any(c in text for c in ".e") else text + ".0"
    if isinstance(value, str):
        out = []
        for ch in value:
            if ch in ('"', "\\"):
                out.append("\\" + ch)
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 0x20 or ord(ch) == 0x7F:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format(v) for v in value) + "]"
    raise ValueError("not a value the file can hold")


def _section_span(lines: list[str], section: str) -> tuple[int, int] | None:
    """(first line after the header, one past the last) of ``[section]``; the
    top of the file, up to the first header, for the general keys."""
    if section == GENERAL:
        end = next((i for i, line in enumerate(lines) if _HEADER.match(line) and not _in_value(lines, i)), len(lines))
        return 0, end
    start = None
    for i, line in enumerate(lines):
        match = _HEADER.match(line)
        if not match or _in_value(lines, i):
            continue
        if start is not None:
            return start, i
        if match.group(1) == section and not line.lstrip().startswith("[["):
            start = i + 1
    return (start, len(lines)) if start is not None else None


def _in_value(lines: list[str], index: int) -> bool:
    """Whether line ``index`` sits inside a multi-line value, where a line
    that looks like ``[header]`` is an array's row, not a table."""
    try:
        tomllib.loads("".join(lines[:index]))
    except tomllib.TOMLDecodeError:
        return True
    return False


def _key_span(lines: list[str], start: int, end: int, key: str) -> tuple[int, int] | None:
    """The lines holding ``key = …`` inside a section, multi-line values
    included: grown one line at a time until the assignment parses."""
    pattern = re.compile(rf"^\s*(?:{re.escape(key)}|\"{re.escape(key)}\")\s*=")
    for i in range(start, end):
        if not pattern.match(lines[i]):
            continue
        for j in range(i + 1, end + 1):
            try:
                tomllib.loads("".join(lines[i:j]))
            except tomllib.TOMLDecodeError:
                continue
            return i, j
        return None
    return None


def _trailing_comment(line: str) -> str:
    """The ``# comment`` ending a one-line assignment, found as the longest
    tail after a ``#`` whose removal still leaves the line parsing to the
    same value, so a ``#`` inside a string is never mistaken for one."""
    try:
        whole = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return ""
    for position in (m.start() for m in re.finditer("#", line)):
        try:
            if tomllib.loads(line[:position]) == whole:
                return line[position:].rstrip("\n")
        except tomllib.TOMLDecodeError:
            continue
    return ""


def edit_toml(text: str, changes: dict[tuple[str, str], Any]) -> str:
    """``text`` with each ``(section, key)`` set to its value, or removed when
    the value is None. Proven before it is returned: the result parses, and
    differs from the original in exactly the keys asked for."""
    before = tomllib.loads(text) if text.strip() else {}
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for (section, key), value in changes.items():
        span = _section_span(lines, section)
        found = _key_span(lines, span[0], span[1], key) if span else None
        if value is None:
            if found:
                del lines[found[0]:found[1]]
            continue
        assignment = f"{key} = {_format(value)}"
        if found:
            comment = _trailing_comment(lines[found[0]]) if found[1] - found[0] == 1 else ""
            indent = re.match(r"\s*", lines[found[0]]).group(0)
            lines[found[0]:found[1]] = [indent + assignment + ("  " + comment if comment else "") + "\n"]
        elif span:
            # After the section's last line that says something, so the blank
            # line before the next header stays where the user put it.
            at = span[1]
            while at > span[0] and not lines[at - 1].strip():
                at -= 1
            lines[at:at] = [assignment + "\n"] + (["\n"] if section == GENERAL and at == span[1] and at < len(lines) else [])
        else:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines += [f"[{section}]\n", assignment + "\n"]

    result = "".join(lines)
    expected = json.loads(json.dumps(before, default=str))
    for (section, key), value in changes.items():
        table = expected if section == GENERAL else expected.setdefault(section, {})
        if value is None:
            table.pop(key, None)
        else:
            table[key] = json.loads(json.dumps(value))
    try:
        after = json.loads(json.dumps(tomllib.loads(result), default=str))
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"The file would not parse after that edit ({exc}); edit it by hand.") from None
    if {k: v for k, v in after.items() if v != {}} != {k: v for k, v in expected.items() if v != {}}:
        raise SettingsError("The file is laid out in a way this page cannot edit safely; change it by hand.")
    return result


# ── the desk ─────────────────────────────────────────────────────────────────

class SettingsDesk:
    """One config file, described for a page and written from one."""

    def __init__(self, path: Path | None = None, *, role: str = "standalone", journal: Any = None, can_restart: bool = True) -> None:
        self._path = path or (Path.home() / ".ciel" / "config.toml")
        self._role = role
        self._journal = journal
        self._can_restart = can_restart
        self._docs = _attribute_docs()
        self._loaded_revision = self.revision()
        """The file as this process found it: a different one now means a
        restart is owed."""

    @property
    def path(self) -> Path:
        return self._path

    def _text(self) -> str:
        try:
            return self._path.read_text()
        except FileNotFoundError:
            return ""

    def revision(self) -> str:
        return hashlib.sha256(self._text().encode()).hexdigest()[:16]

    def _describe(self, raw: dict[str, Any], config: Config) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for name in (n for _, _, names in GROUPS for n in names):
            if name == GENERAL:
                owner: Any = config
                names: tuple[str, ...] = _GENERAL_FIELDS
                kind_of, table, doc = Config, raw, "The keys at the top of the file, above every section."
            else:
                owner = getattr(config, name)
                kind_of, table = type(owner), raw.get(name) if isinstance(raw.get(name), dict) else {}
                names = tuple(f.name for f in fields(owner))
                doc = self._docs.get(kind_of.__name__, {}).get("", "").split("\n\n")[0]
            hints = get_type_hints(kind_of)
            defaults = {f.name: _default(f) for f in fields(kind_of)}
            described = []
            for field_name in names:
                key = f"{name}.{field_name}"
                kind, choices, nullable = _kind(hints[field_name])
                env_name = f"CIEL_{field_name.upper()}" if name == GENERAL else f"CIEL_{name.upper()}_{field_name.upper()}"
                if name == GENERAL and field_name == "timezone":
                    env_name = ""  # the loader reads no variable for it
                source = "env" if env_name and env_name in os.environ else "file" if field_name in table else "default"
                secret = kind == "str" and bool(_SECRET.search(field_name))
                value = getattr(owner, field_name)
                state, reason = "editable", ""
                if secret:
                    state, reason = "secret", "A secret: set in the file by hand, never shown here."
                elif name in LOCKED_SECTIONS:
                    state, reason = "locked", LOCKED_SECTIONS[name]
                elif key in LOCKED_FIELDS:
                    state, reason = "locked", LOCKED_FIELDS[key]
                elif kind in ("path", "other"):
                    state, reason = "locked", "A location on this machine: changed in the file by hand."
                elif source == "env":
                    state, reason = "locked", f"{env_name} is set in the environment and wins over the file."
                described.append({
                    "key": key, "name": field_name, "kind": "str" if secret else kind, "choices": choices, "nullable": nullable,
                    "value": None if secret else _plain(value), "default": None if secret else _plain(defaults.get(field_name)),
                    "source": source, "env_name": env_name if source == "env" else "",
                    "state": state, "reason": reason, "set": bool(value) if secret else True,
                    "doc": self._docs.get(kind_of.__name__, {}).get(field_name, ""),
                })
            sections.append({
                "name": name, "title": _title(name), "doc": doc,
                "where": "room" if name in ROOM_SECTIONS else "",
                "locked": name in LOCKED_SECTIONS, "lock_reason": LOCKED_SECTIONS.get(name, ""),
                "fields": described,
            })
        return sections

    def snapshot(self) -> dict[str, Any]:
        """The whole config as the page draws it: what each field is, what
        it holds, where that came from, and whether the page may change it.
        Raises SettingsError when the file as it stands cannot be read."""
        text = self._text()
        try:
            raw = tomllib.loads(text) if text.strip() else {}
            config = load_config(self._path)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            raise SettingsError(f"The config file cannot be read as it stands: {exc}") from None
        return {
            "machine": {"host": socket.gethostname().split(".")[0], "role": self._role, "path": _plain(self._path)},
            "revision": self.revision(),
            "restart_needed": self.revision() != self._loaded_revision,
            "can_restart": self._can_restart,
            "groups": [{"id": gid, "title": title, "sections": list(names)} for gid, title, names in GROUPS],
            "sections": self._describe(raw, config),
        }

    def _validate(self, described: dict[str, dict[str, Any]], changes: dict[str, Any]) -> tuple[dict[tuple[str, str], Any], dict[str, str]]:
        clean: dict[tuple[str, str], Any] = {}
        errors: dict[str, str] = {}
        for key, value in changes.items():
            spec = described.get(key)
            if spec is None:
                errors[str(key)[:80]] = "not a setting"
                continue
            if spec["state"] != "editable":
                errors[key] = spec["reason"] or "not changed from this page"
                continue
            section, name = key.split(".", 1)
            if value is None:
                clean[(section, name)] = None  # back to the default: the line goes
                continue
            kind = spec["kind"]
            try:
                if kind == "bool":
                    if not isinstance(value, bool):
                        raise ValueError("must be on or off")
                elif kind == "int":
                    if isinstance(value, bool) or not isinstance(value, int):
                        raise ValueError("must be a whole number")
                elif kind == "float":
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                        raise ValueError("must be a number")
                    value = float(value)
                elif kind == "choice":
                    if value not in [c for c in spec["choices"] if c is not None]:
                        raise ValueError("must be one of " + ", ".join(str(c) for c in spec["choices"] if c is not None))
                elif kind == "str":
                    if not isinstance(value, str) or len(value) > _MAX_STR:
                        raise ValueError(f"must be text of at most {_MAX_STR} characters")
                elif kind == "list":
                    if not isinstance(value, list) or len(value) > _MAX_LIST or not all(isinstance(v, str) and len(v) <= _MAX_STR for v in value):
                        raise ValueError(f"must be a list of at most {_MAX_LIST} lines of text")
                    value = [v for v in value]
                else:
                    raise ValueError("not changed from this page")
            except ValueError as exc:
                errors[key] = str(exc)
                continue
            clean[(section, name)] = value
        return clean, errors

    def write(self, changes: dict[str, Any], revision: str) -> dict[str, Any]:
        """Apply reviewed changes to the file. Returns the new snapshot plus
        the keys that moved; raises SettingsError, by field where it can."""
        if not isinstance(changes, dict) or not changes:
            raise SettingsError("Nothing to save.")
        if len(changes) > _MAX_CHANGES:
            raise SettingsError(f"Too many changes at once (at most {_MAX_CHANGES}).")
        if revision != self.revision():
            raise SettingsError("The file changed underneath this page. Reload to see it as it is now.", stale=True)
        current = self.snapshot()
        described = {f["key"]: f for s in current["sections"] for f in s["fields"]}
        clean, errors = self._validate(described, changes)
        if errors:
            raise SettingsError("Some values were refused.", errors=errors)

        text = self._text()
        result = edit_toml(text, clean)
        if result == text:
            return {**current, "changed": []}

        # Proven by the real loader before it replaces anything: a file that
        # will not start is never the one left on disk.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        candidate = self._path.with_name(self._path.name + ".candidate")
        self._write_private(candidate, result)
        try:
            load_config(candidate)
        except (ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
            candidate.unlink(missing_ok=True)
            message = str(exc)
            named = re.match(r"\[(\w+)\]\.(\w+): (.*)", message)
            raise SettingsError("The loader refused that.", errors={f"{named.group(1)}.{named.group(2)}": named.group(3)} if named else {}) from None
        if text:
            self._write_private(self._path.with_name(self._path.name + ".previous"), text)
        os.replace(candidate, self._path)

        changed = sorted(f"{section}.{name}" for section, name in clean)
        if self._journal is not None:
            try:
                self._journal.record(
                    tool="settings.write",
                    args={key: ("(default)" if changes[key] is None else changes[key]) for key in changed},
                    response=f"{self._path} rewritten; the text it replaced is {self._path.name}.previous beside it",
                )
            except Exception:  # noqa: BLE001 - the save stands; the journal is the record, not the gate
                log.debug("could not journal the settings write", exc_info=True)
        log.info("settings: %s changed from the page", ", ".join(changed))
        return {**self.snapshot(), "changed": changed}

    @staticmethod
    def _write_private(path: Path, text: str) -> None:
        # Created 0600 whatever the umask, and set again in case it existed:
        # the config can hold tokens.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o600)
