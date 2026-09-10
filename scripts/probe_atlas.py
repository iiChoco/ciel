"""Probe Atlas's bindings — a project connected to the work it is about.

A temporary project directory, no model, no runtime state. Pins: a file
written before bindings existed loads with its prose byte-exact and no id;
the first write or binding mints an id and a rewrite keeps it; aliases are
one line, survive a fresh store, and a newline or comma in one is refused
or collapsed; a rename moves the file, keeps the id, state, log, and
resources, and leaves the old name as an alias that still resolves; resolve
lands the exact name, an exact alias, a unique partial name or alias, and
the description only as a last resort, and returns candidates rather than a
guess when several match; a resource line carries role, source, current,
opener, and a locator with spaces, round-trips, and a URL is a url source
by itself; one current per role; binding a known key replaces it; the
bound limit holds; a line that does not parse is skipped, not misread; the
index names aliases and counts resources; and the tools open by alias, list
resources, refuse an ambiguous name, bind, select, unbind, and rename.

    uv run --no-sync python scripts/probe_atlas.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.projects import ProjectStore, Resource

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


LEGACY = """---
name: analysis
description: Math H104, real analysis
status: active
created_at: 2026-09-01T10:00:00Z
updated_at: 2026-09-08T18:30:00Z
---

## State

hw03 in progress; problems 1–4 written, 5 open.

## Log

- 2026-09-02T09:00:00Z: started hw03
"""


def store_checks(root: Path) -> None:
    print("a file from before bindings")
    directory = root / "projects"
    directory.mkdir()
    (directory / "analysis.md").write_text(LEGACY, encoding="utf-8")
    store = ProjectStore(directory, log_tail=5)
    project = store.get("analysis")
    assert project is not None
    check("loads with its prose and log intact and no id, aliases, or resources",
          project.id == "" and project.aliases == () and project.resources == ()
          and project.state == "hw03 in progress; problems 1–4 written, 5 open." and project.log == ("2026-09-02T09:00:00Z: started hw03",))
    store.append_log("analysis", "a log line")
    check("a log append on a legacy file leaves it unbound: no id is minted for nothing",
          store.get("analysis").id == "" and (directory / "analysis.md").read_text().count("id:") == 0)
    before = (directory / "analysis.md").read_text()

    print("\nan id is minted once")
    resource = store.bind("analysis", "folder", "~/Berkeley/Math/H104")
    project = store.get("analysis")
    check("the first binding mints a 32-hex id and keeps the prose byte-exact",
          len(project.id) == 32 and all(c in "0123456789abcdef" for c in project.id)
          and project.state == "hw03 in progress; problems 1–4 written, 5 open." and len(project.log) == 2
          and resource == Resource("folder", "folder", "local", "~/Berkeley/Math/H104"))
    body_before = before.split("---\n", 2)[2]
    body_after = (directory / "analysis.md").read_text().split("---\n", 2)[2]
    check("the body of the file is unchanged by a binding; only frontmatter lines were added", body_before == body_after)
    minted = project.id
    store.write("analysis", "hw03 done; hw04 next")
    check("a rewrite keeps the id", store.get("analysis").id == minted and store.get("analysis").resources == (resource,))
    fresh = store.write("proposal", "outline drafted", description="Grant proposal")
    check("a new project is born with an id", len(fresh.id) == 32 and fresh.id != minted)

    print("\naliases")
    store.set_aliases("analysis", ["analysis homework", "h104", "  math  104 \n homework "])
    again = ProjectStore(directory, log_tail=5).get("analysis")
    check("aliases survive a fresh store and a newline in one is collapsed",
          again.aliases == ("analysis homework", "h104", "math 104 homework"))
    try:
        store.set_aliases("analysis", ["a, b"])
        check("an alias with a comma is refused", False)
    except ValueError:
        check("an alias with a comma is refused", True)

    print("\nresolution")
    check("the exact name", store.resolve("analysis").project.name == "analysis")
    check("an exact alias, whatever the case", store.resolve("H104").project.name == "analysis")
    check("a phrase an alias is part of", store.resolve("pull up my analysis homework please").project.name == "analysis")
    check("a phrase that is part of a name", store.resolve("propos").project.name == "proposal")
    store.write("analysis-notes", "notes", description="Reading notes for analysis")
    found = store.resolve("analysis")
    check("the exact name wins over a partial match on another", found.project is not None and found.project.name == "analysis")
    found = store.resolve("analys")
    check("several partial matches are candidates, never a guess",
          found.project is None and set(found.candidates) == {"analysis", "analysis-notes"})
    check("the description counts only when nothing else matched", store.resolve("grant").project.name == "proposal")
    check("nothing is nothing", store.resolve("wake word").project is None and store.resolve("wake word").candidates == ())

    print("\nresources")
    store.bind("analysis", "solution", "~/Berkeley/Math/H104/hw03/hw03.tex", key="hw03", current=True, opener="TeXShop")
    store.bind("analysis", "solution", "~/Berkeley/Math/H104/hw02/hw02.tex", key="hw02")
    store.bind("analysis", "handout", "https://example.test/h104/hw03.pdf")
    project = ProjectStore(directory).get("analysis")
    by_key = {r.key: r for r in project.resources}
    check("a resource line round-trips role, source, current, opener, and locator",
          by_key["hw03"] == Resource("hw03", "solution", "local", "~/Berkeley/Math/H104/hw03/hw03.tex", True, "TeXShop"))
    check("a URL is a url source by itself and the key defaults to the role",
          by_key["handout"] == Resource("handout", "handout", "url", "https://example.test/h104/hw03.pdf"))
    check("the current solution is the one marked, and the folder is the only one of its role",
          project.resource("solution").key == "hw03" and project.resource("folder").key == "folder" and project.resource("nothing") is None)
    store.select("analysis", "hw02")
    project = store.get("analysis")
    check("selecting another makes it the one current of its role",
          project.resource("solution").key == "hw02" and not {r.key: r for r in project.resources}["hw03"].current)
    store.bind("analysis", "solution", "~/Berkeley/Math/H104/hw02/hw02 draft.tex", key="hw02", current=True)
    project = ProjectStore(directory).get("analysis")
    check("binding a known key replaces it, and a locator with spaces round-trips",
          {r.key: r for r in project.resources}["hw02"].locator == "~/Berkeley/Math/H104/hw02/hw02 draft.tex" and len(project.resources) == 4)
    check("unbinding forgets it; unbinding again is nothing", store.unbind("analysis", "hw02") and not store.unbind("analysis", "hw02"))
    for bad in (("", "~/x"), ("draft", ""), ("draft", "~/x", "ftp")):
        try:
            store.bind("analysis", bad[0], bad[1], source=bad[2] if len(bad) > 2 else "")
            check(f"a bad binding {bad!r} is refused", False)
        except ValueError:
            pass
    check("a bad role, locator, or source is refused", True)
    check("a key is reduced to a slug", store.bind("analysis", "draft", "~/x", key="Bad Key!").key == "bad-key" and store.unbind("analysis", "bad-key"))
    small = ProjectStore(root / "small", max_resources=2)
    small.write("p", "s")
    small.bind("p", "a", "~/a")
    small.bind("p", "b", "~/b")
    try:
        small.bind("p", "c", "~/c")
        check("the bound limit holds", False)
    except ValueError as exc:
        check("the bound limit holds", "at most 2" in str(exc))
    text = (directory / "analysis.md").read_text()
    text = text.replace("---\n\n## State", "resource.broken: onlyrole\nresource.also-broken: role local\n---\n\n## State", 1)
    (directory / "analysis.md").write_text(text)
    project = ProjectStore(directory).get("analysis")
    check("a resource line that does not parse is skipped, not misread", {r.key for r in project.resources} == {"folder", "hw03", "handout"})

    print("\nrename")
    old = store.get("analysis")
    renamed = store.rename("analysis", "Real Analysis")
    check("the file moves, the id, state, log, and resources stay, and the old name is an alias",
          renamed.name == "real-analysis" and renamed.id == old.id and renamed.state == old.state and renamed.resources == old.resources
          and not (directory / "analysis.md").exists() and (directory / "real-analysis.md").exists() and "analysis" in renamed.aliases
          and len(store.get("real analysis").log) == 2)
    check("the old name still resolves, and the new one is exact",
          store.resolve("analysis").project.name == "real-analysis" and store.resolve("real analysis").project.name == "real-analysis")
    try:
        store.rename("real analysis", "proposal")
        check("a rename onto an existing name is refused", False)
    except ValueError:
        check("a rename onto an existing name is refused", True)
    index = store.index_prompt()
    check("the index names aliases and counts resources",
          "also called analysis homework, h104, math 104 homework, analysis" in index and "3 resources bound" in index)


async def tool_checks(root: Path) -> None:
    print("\nthe tools")
    from ciel.brain.tools import projects as tools

    store = ProjectStore(root / "tool-projects")
    tools.bind_projects(store)
    text = lambda r: r["content"][0]["text"]
    await tools.update_project.handler({"name": "analysis", "state": "hw03 open", "description": "H104", "aliases": "analysis homework, h104"})
    opened = text(await tools.open_project.handler({"name": "h104"}))
    check("open_project lands by alias and lists aliases and an empty resource list with a hint",
          opened.startswith("# analysis (active)") and "Also called: analysis homework, h104" in opened and "none bound" in opened)
    bound = text(await tools.bind_resource.handler({"project": "analysis homework", "role": "solution", "locator": "~/Berkeley/Math/H104/hw03/hw03.tex", "current": True, "key": "hw03"}))
    check("bind_resource resolves the project by alias and binds as current", bound == "Bound hw03 (solution, local) to 'analysis' as current.")
    opened = text(await tools.open_project.handler({"name": "analysis"}))
    check("open_project lists the resource with its role, source, locator, and current mark",
          "- hw03: solution · local · ~/Berkeley/Math/H104/hw03/hw03.tex · current" in opened)
    await tools.update_project.handler({"name": "analysis-notes", "state": "n", "description": "notes"})
    ambiguous = text(await tools.open_project.handler({"name": "analys"}))
    check("an ambiguous name is a question, not a guess", "could mean any of" in ambiguous and "do not guess" in ambiguous)
    await tools.bind_resource.handler({"project": "analysis", "role": "solution", "locator": "~/x/hw04.tex", "key": "hw04"})
    selected = text(await tools.select_resource.handler({"project": "analysis", "key": "hw04"}))
    check("select_resource makes it current", selected == "hw04 is now the current solution of 'analysis'." and store.get("analysis").resource("solution").key == "hw04")
    check("unbind_resource forgets it", text(await tools.unbind_resource.handler({"project": "analysis", "key": "hw04"})) == "Unbound hw04 from 'analysis'.")
    renamed = text(await tools.rename_project.handler({"name": "h104", "new_name": "real analysis"}))
    check("rename_project keeps the old name resolving", renamed == "Renamed to 'real-analysis'; 'analysis' still resolves to it."
          and store.resolve("analysis").project.name == "real-analysis")
    check("a bad binding reaches the model as words", "needs a locator" in text(await tools.bind_resource.handler({"project": "real analysis", "role": "draft", "locator": ""})))


async def document_checks(root: Path) -> None:
    print("\nthe documents")
    from ciel.brain.tools import projects as tools
    from ciel.project_work import LocalWorkbench, WorkLimits, check_document_path, observe, open_target

    home = root / "home"
    course = home / "Math" / "H104" / "hw03"
    course.mkdir(parents=True)
    (course / "hw03.tex").write_text("\\begin{numedquestion} One. \\begin{framed} TODO \\end{framed} \\end{numedquestion}\n\\input{more}\n\\input{../../../outside}\n")
    (course / "more.tex").write_text("\\begin{numedquestion} Two. \\begin{framed} Written. \\end{framed} \\end{numedquestion}\n")
    (home / "outside.tex").write_text("\\begin{numedquestion} Outside. \\end{numedquestion}\n")
    (course / "draft.md").write_text("# Draft\n\n## Aims\n\n## Method\n\nwritten\n")
    state = root / "state"
    state.mkdir()
    (state / "config.toml").write_text("[x]\n")
    bench = LocalWorkbench(home=home, state_dir=state, forbidden=frozenset({"id_rsa"}))
    store = ProjectStore(root / "doc-projects")
    tools.bind_projects(store)
    tools.bind_workbench(bench, WorkLimits(max_bytes=10000, max_includes=3, include_depth=2))
    text = lambda r: r["content"][0]["text"]
    store.write("analysis", "hw03", description="H104")
    check("nothing bound: the tools say to ask and bind", "ask the owner where the work lives" in text(await tools.project_progress.handler({"project": "analysis"})))
    store.bind("analysis", "folder", str(course.parent))
    store.bind("analysis", "solution", str(course / "hw03.tex"), key="hw03", current=True)
    store.bind("analysis", "draft", str(course / "draft.md"))
    store.bind("analysis", "handout", "https://example.test/hw03.pdf")
    out = text(await tools.project_progress.handler({"project": "h104" if False else "analysis", "role": "solution"}))
    check("a reading follows the include inside the bound folder, not the one that escapes it, and names the gap",
          "2 questions: 1 written, 1 in progress, 0 not started" in out and "more.tex" in out and "../../../outside: included but not readable" in out)
    check("the Markdown draft reads by its own reader", "3 sections: 1 written, 0 in progress, 2 not started" in text(await tools.project_progress.handler({"project": "analysis", "role": "draft"})))
    check("a URL resource is opened, not read", "opened, not read" in text(await tools.project_progress.handler({"project": "analysis", "role": "handout"})))
    out = text(await tools.project_progress.handler({"project": "analysis", "role": "nothing"}))
    check("a role with no resource lists what there is and asks", "has no single resource" in out and "hw03 (solution, current)" in out)
    project = store.get("analysis")
    observed = await observe(project, project.resource(role="solution"), bench, WorkLimits(max_bytes=10, max_includes=3, include_depth=2))
    check("a document past the byte bound is not read, in words", observed.reading is None and "larger than 10" in observed.note)
    store.bind("analysis", "notes", str(course / "notes.pdf"))
    (course / "notes.pdf").write_bytes(b"%PDF")
    check("an unsupported document can be named but not read", "no reader for .pdf" in text(await tools.project_progress.handler({"project": "analysis", "role": "notes"})))
    for raw, why in ((str(state / "config.toml"), "state directory"), (str(home / "id_rsa"), "off limits"), ("/etc/hosts", "home folder"), ("hw03.tex", "absolute")):
        check(f"the local bench refuses {why}", check_document_path(raw, home=home, state_dir=state, forbidden=frozenset({"id_rsa"}))[0] is None)
    ran: list[list[str]] = []

    class Ran:
        returncode = 0
        stderr = ""

    check("open resolves the path, refuses the state directory, and says when a file is not there",
          open_target(str(course / "hw03.tex"), "TeXShop", home=home, state_dir=state, runner=lambda a, **k: ran.append(a) or Ran()) == "opened hw03.tex with TeXShop"
          and ran[-1][:3] == ["/usr/bin/open", "-a", "TeXShop"]
          and open_target(str(state / "config.toml"), "", home=home, state_dir=state, runner=lambda a, **k: Ran()).startswith("refused")
          and open_target(str(course / "gone.tex"), "", home=home, state_dir=state, runner=lambda a, **k: Ran()).startswith("not opened"))
    tools.bind_workbench(None)
    check("without a workbench the tools say the machine is not reachable", "not reachable" in text(await tools.open_document.handler({"project": "analysis", "role": "solution"})))


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ciel-atlas-probe-") as tmp:
        store_checks(Path(tmp))
        await tool_checks(Path(tmp))
        await document_checks(Path(tmp))
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
