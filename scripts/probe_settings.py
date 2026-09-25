"""Fake-free verification of the settings desk (``ciel/settings.py``) and its wire.

No server, no model, no ``~/.ciel``: every file lives in a temporary
directory. It pins the promises the module's docstring makes.

The description: every section the loader knows is described exactly once,
every field carries its kind and its docstring from ``config.py``, a value
says whether it came from the file, the environment, or the default, and the
groups name only sections that exist.

The posture: secrets are reported as set or unset and their values are in no
snapshot; security-bearing sections, paths, owner and command fields, and
fields the environment overrides are shown and refuse a write; nothing the
page sends can name a setting that does not exist.

The file: a write changes the lines it must and no others — comments, blank
lines, a trailing comment on the edited line, multi-line values, and keys
this version has never heard of survive; a reset removes the line; a missing
section is appended; a top-level key lands above the first table; a string
cannot break out of its line; a layout the editor cannot follow is refused.

The save: a stale revision is a conflict, never an overwrite; a value the
real loader refuses is refused by field and the old file is untouched; the
file, its previous text, and no leftover candidate are owner-only; the
journal records the change; the snapshot says a restart is owed.

The wire: the catalog admits a read and a bounded write, and refuses an
unknown operation or a write with no changes or no revision.

Run it directly:

    uv run --no-sync python scripts/probe_settings.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from ciel import wire
from ciel.config import _SECTIONS
from ciel.settings import GROUPS, LOCKED_SECTIONS, SettingsDesk, SettingsError, edit_toml

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


SAMPLE = '''# my ciel
timezone = "America/Los_Angeles"

[wake]
# how sure the wake word must be
threshold = 0.4  # tuned by ear, not "#" by maths
ack_phrases = [
  "Yes?",  # the usual
  "Mm?",
]
future_key = "kept"

[hub]
token = "sekrit-value"
require_token = true

[shell]
enabled = false

[oura]
token = "oura-sekrit"
'''


class FakeJournal:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def record(self, **entry: Any) -> str:
        self.entries.append(entry)
        return "ref"


def fields_of(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for s in snapshot["sections"] for f in s["fields"]}


def refused(desk: SettingsDesk, changes: dict[str, Any], revision: str) -> SettingsError | None:
    try:
        desk.write(changes, revision)
    except SettingsError as exc:
        return exc
    return None


def probe_description(desk: SettingsDesk) -> None:
    print("the description")
    snap = desk.snapshot()
    names = [s["name"] for s in snap["sections"]]
    check("every section the loader reads is described once, with the top of the file as general",
          sorted(names) == sorted([*_SECTIONS, "general"]) and len(set(names)) == len(names))
    check("the groups name only sections that exist, each once",
          sorted(n for g in snap["groups"] for n in g["sections"]) == sorted(names) and len(GROUPS) == len(snap["groups"]))
    f = fields_of(snap)
    check("a field carries its kind, its default, and its docstring from config.py",
          f["wake.threshold"]["kind"] == "float" and f["wake.threshold"]["default"] == 0.5 and len(f["wake.threshold"]["doc"]) > 20)
    check("a docstring arrives as prose: no reST backticks, no hard wraps",
          all("``" not in x["doc"] and "\n " not in x["doc"] for x in f.values()))
    check("a Literal is a choice with its values; an optional one may be unset",
          f["wake.mode"]["kind"] == "choice" and "hotkey" in f["wake.mode"]["choices"]
          and any(None in x["choices"] for x in f.values() if x["kind"] == "choice"))
    check("a value says where it came from: the file, or the default",
          f["wake.threshold"]["source"] == "file" and f["wake.threshold"]["value"] == 0.4 and f["wake.mode"]["source"] == "default")
    check("a list is a list of lines", f["wake.ack_phrases"]["kind"] == "list" and f["wake.ack_phrases"]["value"] == ["Yes?", "Mm?"])
    check("a section read by the room says so", next(s for s in snap["sections"] if s["name"] == "wake")["where"] == "room")
    check("the machine line names the role and a home-relative path",
          snap["machine"]["role"] == "hub" and snap["machine"]["host"] and not snap["machine"]["path"].startswith(str(Path.home()) + "/."))
    os.environ["CIEL_TTS_ENGINE"] = "say"
    try:
        g = fields_of(desk.snapshot())["tts.engine"]
        check("a field the environment overrides says so, by variable, and is not writable",
              g["source"] == "env" and g["env_name"] == "CIEL_TTS_ENGINE" and g["state"] == "locked" and g["value"] == "say")
        check("...and a write to it is refused by field", "tts.engine" in (refused(desk, {"tts.engine": "piper"}, desk.revision()) or SettingsError("")).errors)
    finally:
        del os.environ["CIEL_TTS_ENGINE"]


def probe_posture(desk: SettingsDesk) -> None:
    print("\nthe posture")
    snap = desk.snapshot()
    f = fields_of(snap)
    wire_text = json.dumps(snap)
    check("a secret is set or unset, and its value is in no snapshot",
          f["hub.token"]["state"] == "secret" and f["hub.token"]["set"] is True and f["hub.token"]["value"] is None
          and "sekrit" not in wire_text and f["spoke.token"]["set"] is False)
    check("every token, cookie, password, and secret field is a secret",
          all(x["state"] == "secret" for k, x in f.items() if x["kind"] == "str" and any(w in x["name"] for w in ("token", "cookie", "password", "secret")) and not x["name"].endswith(("_file", "_dir"))))
    check("security-bearing sections are shown whole and locked whole",
          all(next(s for s in snap["sections"] if s["name"] == n)["locked"] for n in LOCKED_SECTIONS)
          and all(x["state"] in ("locked", "secret") for k, x in f.items() if k.split(".")[0] in LOCKED_SECTIONS)
          and f["shell.enabled"]["value"] is False)
    check("every path is shown and locked", all(x["state"] == "locked" for x in f.values() if x["kind"] in ("path", "other")))
    check("a command, the brain's tools, and the owner fields are locked inside open sections",
          all(f[k]["state"] == "locked" and f[k]["reason"] for k in ("sections.refresh_cmd", "brain.allowed_tools", "tasks.owner", "messages.allow_send")))
    revision = desk.revision()
    before = desk.path.read_text()
    for label, changes in (
        ("the shell cannot be turned on from a page", {"shell.enabled": True}),
        ("a secret cannot be written from a page", {"hub.token": "mine"}),
        ("a path cannot be moved from a page", {"memory.dir": "/tmp/elsewhere"}),
        ("a setting that does not exist is refused by name", {"wake.nonesuch": 1}),
        ("a key with no section is refused", {"nonsense": 1}),
    ):
        exc = refused(desk, changes, revision)
        check(label, exc is not None and set(exc.errors) == set(changes) and desk.path.read_text() == before)
    exc = refused(desk, {"wake.threshold": 0.6, "shell.enabled": True}, revision)
    check("one refused field refuses the whole save", exc is not None and desk.path.read_text() == before)


def probe_kinds(desk: SettingsDesk) -> None:
    print("\nthe kinds")
    revision = desk.revision()
    for label, changes in (
        ("a switch takes on or off, not a word", {"wake.ack": "yes"}),
        ("a whole number is not a fraction", {"audio.silence_ms": 1.5}),
        ("a whole number is not a switch", {"audio.silence_ms": True}),
        ("a number is not text", {"wake.threshold": "0.5"}),
        ("a choice is one of its values", {"wake.mode": "sometimes"}),
        ("a list is lines of text", {"wake.ack_phrases": ["ok", 3]}),
        ("text has an end", {"stt.initial_prompt": "x" * 5000}),
    ):
        exc = refused(desk, changes, revision)
        check(label, exc is not None and set(exc.errors) == set(changes))
    check("nothing to save is said so", refused(desk, {}, revision) is not None)
    check("a save has a size", refused(desk, {f"wake.k{i}": 1 for i in range(101)}, revision) is not None)


def probe_file() -> None:
    print("\nthe file")
    out = edit_toml(SAMPLE, {("wake", "threshold"): 0.55})
    check("an edit changes its line and no other", [a for a, b in zip(SAMPLE.splitlines(), out.splitlines()) if a != b] == ['threshold = 0.4  # tuned by ear, not "#" by maths'])
    check("the edited line keeps its trailing comment, even one with a # in a string", 'threshold = 0.55  # tuned by ear, not "#" by maths' in out)
    out = edit_toml(SAMPLE, {("wake", "ack_phrases"): ["Yes?"]})
    check("a multi-line value is replaced whole", 'ack_phrases = ["Yes?"]\nfuture_key = "kept"' in out and '"Mm?"' not in out)
    check("a key this version never heard of survives", tomllib.loads(out)["wake"]["future_key"] == "kept")
    out = edit_toml(SAMPLE, {("wake", "threshold"): None})
    check("a reset removes the line and leaves the comment above it", "threshold" not in out and "# how sure" in out)
    check("resetting what was never set changes nothing", edit_toml(SAMPLE, {("tts", "rate"): None}) == SAMPLE)
    out = edit_toml(SAMPLE, {("wake", "mode"): "hotkey"})
    check("a new key joins its section, before the blank line that ends it", 'future_key = "kept"\nmode = "hotkey"\n\n[hub]' in out)
    out = edit_toml(SAMPLE, {("tts", "engine"): "piper"})
    check("a missing section is appended", out.endswith('\n[tts]\nengine = "piper"\n') and out.startswith(SAMPLE))
    out = edit_toml(SAMPLE, {("general", "log_level"): "DEBUG"})
    check("a top-level key lands above the first table", tomllib.loads(out)["log_level"] == "DEBUG" and out.index("log_level") < out.index("[wake]"))
    out = edit_toml("[wake]\nthreshold = 0.4\n", {("general", "timezone"): "UTC"})
    check("...even when the file opens with a table", tomllib.loads(out) == {"timezone": "UTC", "wake": {"threshold": 0.4}})
    check("an empty file takes its first setting", tomllib.loads(edit_toml("", {("wake", "threshold"): 0.6})) == {"wake": {"threshold": 0.6}})
    hostile = 'a"\n[shell]\nenabled = true\n# \\ \x07'
    out = edit_toml(SAMPLE, {("brain", "personality"): hostile})
    parsed = tomllib.loads(out)
    check("a string cannot break out of its line", parsed["brain"]["personality"] == hostile and parsed["shell"] == {"enabled": False})
    check("a whole float keeps its point, and the file says true not True",
          "rate = 2.0\n" in edit_toml("", {("tts", "rate"): 2.0}) and "ack = true\n" in edit_toml("", {("wake", "ack"): True}))
    for label, text in (("a dotted key", 'wake.threshold = 0.4\n'), ("an inline table", 'wake = { threshold = 0.4 }\n')):
        try:
            edit_toml(text, {("wake", "threshold"): 0.6})
            ok = False
        except SettingsError:
            ok = True
        check(f"{label} is a layout the page refuses rather than guesses at", ok)
    tricky = 'notes = """\n[wake]\nthreshold = 9\n"""\n\n[wake]\nthreshold = 0.4\n'
    out = edit_toml(tricky, {("wake", "threshold"): 0.6})
    check("a header inside a multi-line string is not a table", tomllib.loads(out)["wake"]["threshold"] == 0.6 and "threshold = 9" in out)


def probe_save(directory: Path) -> None:
    print("\nthe save")
    path = directory / "save" / "config.toml"
    path.parent.mkdir()
    path.write_text(SAMPLE)
    os.chmod(path, 0o644)
    journal = FakeJournal()
    desk = SettingsDesk(path, role="hub", journal=journal)
    first = desk.snapshot()
    check("a file as this process found it owes no restart", first["restart_needed"] is False)
    out = desk.write({"wake.threshold": 0.55, "tts.engine": "piper", "general.timezone": None}, first["revision"])
    check("a save names what moved", out["changed"] == ["general.timezone", "tts.engine", "wake.threshold"])
    check("the new snapshot holds the new values and owes a restart",
          fields_of(out)["wake.threshold"]["value"] == 0.55 and fields_of(out)["general.timezone"]["source"] == "default" and out["restart_needed"] is True)
    check("the file, and the text it replaced, are the owner's alone; no candidate is left",
          oct(path.stat().st_mode)[-3:] == "600" and oct(path.with_name("config.toml.previous").stat().st_mode)[-3:] == "600"
          and path.with_name("config.toml.previous").read_text() == SAMPLE and sorted(p.name for p in path.parent.iterdir()) == ["config.toml", "config.toml.previous"])
    check("the journal records the keys and values, and where the old text is",
          len(journal.entries) == 1 and journal.entries[0]["tool"] == "settings.write"
          and journal.entries[0]["args"] == {"general.timezone": "(default)", "tts.engine": "piper", "wake.threshold": 0.55})
    exc = refused(desk, {"wake.threshold": 0.7}, first["revision"])
    check("a save drawn from an older file is a conflict, never an overwrite", exc is not None and exc.stale and "0.55" in path.read_text())
    same = desk.write({"wake.threshold": 0.55}, desk.revision())
    check("saving what is already there writes nothing", same["changed"] == [] and len(journal.entries) == 1)

    before = path.read_text()
    path.write_text(before + '\n[proactive]\nenabled = "ture"\n')
    try:
        desk.snapshot()
        ok = False
    except SettingsError as exc:
        ok = "proactive" in str(exc)
    check("a file the loader refuses is reported, by name, not drawn wrong", ok)
    path.write_text("this is not toml = = =\n")
    check("a file that does not parse is reported and left alone", refused(desk, {"wake.threshold": 0.6}, desk.revision()) is not None and path.read_text() == "this is not toml = = =\n")

    fresh = SettingsDesk(directory / "fresh" / "config.toml")
    out = fresh.write({"wake.threshold": 0.6}, fresh.revision())
    check("a machine with no config file gets one, owner-only", out["changed"] == ["wake.threshold"] and oct(fresh.path.stat().st_mode)[-3:] == "600")


def probe_wire() -> None:
    print("\nthe wire")

    def admitted(frame: dict[str, Any]) -> bool:
        try:
            wire.decode(json.dumps(frame), "c2h")
        except wire.WireError:
            return False
        return True

    check("a read is admitted", admitted({"type": "settings.request", "request_id": "r1", "operation": "read"}))
    check("a write names its changes and its revision",
          admitted({"type": "settings.request", "request_id": "r2", "operation": "write", "revision": "abc", "changes": {"wake.threshold": 0.6}})
          and not admitted({"type": "settings.request", "request_id": "r3", "operation": "write", "changes": {"wake.threshold": 0.6}})
          and not admitted({"type": "settings.request", "request_id": "r4", "operation": "write", "revision": "abc", "changes": {}}))
    check("an unknown operation, a missing identity, and an oversized frame are refused",
          not admitted({"type": "settings.request", "request_id": "r5", "operation": "erase"})
          and not admitted({"type": "settings.request", "request_id": "", "operation": "read"})
          and not admitted({"type": "settings.request", "request_id": "r6", "operation": "write", "revision": "abc", "changes": {"stt.initial_prompt": "x" * 70000}}))
    check("the result travels hub to client only",
          bool(wire.encode({"type": "settings.result", "request_id": "r1", "ok": True, "data": {}}, "h2c")) and not admitted({"type": "settings.result", "request_id": "r1", "ok": True, "data": {}}))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        path = directory / "config.toml"
        path.write_text(SAMPLE)
        desk = SettingsDesk(path, role="hub")
        probe_description(desk)
        probe_posture(desk)
        probe_kinds(desk)
        probe_file()
        probe_save(directory)
        probe_wire()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
