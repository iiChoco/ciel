"""Probe the learning module — a book registered once, a bookmark kept in the owner's words.

Synthetic PDFs built by hand in a temporary home (printed page labels, an
outline, a title in the metadata; one with none of those), a temporary
project store and task store, a fake Mac for the hub's side and PDFKit for
the Mac's, no model, no network, no runtime state. Pins: the owner's words
become a section, a printed page, a PDF page, a chapter, or an item in
their kind, a bare number is asked about, and no other words are a place;
a printed page maps through the labels, one the labels lack is asked
about, a label-less book refuses a printed page and takes a PDF page or a
section, and a section is placed by the outline when it names it; a book
is found by filename and by PDF title and never by page text, the search
stops at its bound and says so, a root outside home or under the state
directory is refused, a book must be a PDF under home, a book past the
byte bound or missing is said; registering needs the confirmed edition,
binds the PDF and the study folder, keeps the facts, logs a line, makes the
first book current, and says so instead of doubling; a book resolves by
its own alias without its project, two books keep separate bookmarks, the
current one answers when none is named and several are registered, none
current asks, an ambiguous name asks; a bookmark is kept in the owner's
words and placed, never moved by time or reopening, and survives a fresh
store; open_project shows the books; a missing PDF and a Mac that cannot
be reached are said; a public turn reaches nothing; and closing a book
cancels its tasks and no other's, deletes its records across more than
one page with its requests fenced, deletes no file, and unbinds it.

The [learning] section loads from the config file, and without it the
module is off. What the model wrote is made to compile: Unicode math
becomes LaTeX commands, a command or superscript left outside math is set
in math, specials are escaped, an unbalanced dollar is closed, an unknown
character is marked and never dropped, and the rendered sheets with the
shapes the first live sheets failed on compile under pdflatex with shell
escape off.

A chapter, prepared: windows overlap by one page, the estimator counts a
window's calls with a quarter in reserve and cuts a chapter into tasks that
fit the allowance, whole sections while they fit and a long section by page
range; a chapter's range and sections come from the outline; item ids
follow the grammar; images are the rule for notation. Then the real thing
over a hand-built book with a fake extractor: without the grant, prepare
says so and queues nothing; the grant offers the operations, one target per
book, and new files only; activation starts the watch; prepare queues one
task from the outline, asking again adds nothing, an unplaced chapter and
a bare page range ask; the watch derives the reading task and the request
names it; every window is read in order and an abandoned window is the only
one redone; a statement across the boundary is provisional then whole and
kept once; items carry kind, number, page, id, and a definition's private
reference; notation is re-read from page images two pages a call and the
image reading is kept; an unreadable page is flagged; conventions are kept;
the task stays within its allowance; the publishing task lands both sheets
as new files, verified, and the chapter is prepared; the theorems sheet has
statements, pages, empty boxes, and conventions and no proof or definition
text; the definitions sheet names terms and holds no definition; the reader
reads a sheet back with its ids and spans; the notice says prepared; a
prepared chapter is reopened, a replacement takes the next free numbers
past a name the owner took, nothing is ever replaced; every publication is
journaled; and with images never no image call is made.

The learning steps beside the conversation: with the conversation's lease
held throughout and the owner interrupting after every step, a background
runner over the same store reads the chapter and writes its sheets alone,
spending no attempt, while the ladder's runner, told to leave those
operations alone, sees nothing.

The 2026-09-10 review's seven defects, each pinned as fixed: a grading
batch is whole problems within the bound, a problem too long for one call
is said to be ungraded and not counted, and one the model left out is
carried once then said; a submission whose include cannot be read, lies
outside the study folder, or lies too deep is refused by name before
anything is recorded, and a commented-out include is no dependency; a
scanned book reaches the image extractor when the text found nothing and
the images discover the items; the study folder is the root the Mac
answered, never the hub's home; a set's solution is refused in every
state but graded; a chapter is read from its first page with every gap
between sections; and a publication interrupted before its record
checkpoint is recovered by the verification from the planned path and
digest.

The Mac away mid-chapter: a step that finds the Mac away checkpoints with a
delay, spends no attempt, never parks, and goes on when the Mac is back;
each step is one model call; a reading task an earlier build parked, whose
scope cannot take the images step, is ended by the watch and its request
re-derived afresh.

The review: an all-empty sheet is refused and said; an id not on the
sheet is said with the ones that are; a review is queued once; its
snapshot keeps the written answers by hash and names the empty boxes; an
interrupted assessment runs again from the same snapshot; the assessment
sees the statement, the conventions, and the answer and nothing of the
other sheet; the feedback is a new numbered file with the hashes, the
verdicts, and no argument; the sheet is never edited; the snapshot is
gone once published; the progress record carries the verdict with the
hash assessed; standing reads by hash and an edit reads as stale with
the verdict kept; read_feedback returns the verdicts and gaps; a second
review is a second file with the history kept; a definition's review sees
the book's words as private reference and a finding that repeated them
is withheld; a hint is queued only on request, lands numbered, is
recorded on the item, and one that repeated the book's words is
replaced; a definition is revealed from the record and a theorem by its
page, both recorded; and study_status says where each sheet stands.

Practice and a mock midterm: a practice question is queued as one problem,
book-based, from the prepared chapters up to the bookmark; a midterm
defaults to five problems, sixty minutes, one hundred points, calibrated
to a bound syllabus, and an explicit change is honoured; each question is
one task of three calls and a solve that needed an added assumption is
rejected and rewritten while one the referee rejects twice is withdrawn;
the sheet has the header, the released problems with empty boxes, the
withdrawal, and no solution or rubric anywhere, and the practice sheet
says book-based; the compose call saw the material, the conventions, and
the calibration; solutions appear in no notice, evidence, or notebook
state; a solution is not revealed while the exam is open; a hint during
the exam is written beside the sheet and recorded on the set; a submission
past the cap is refused and nothing recorded; submitting keeps the sheet
and its include whole on record with the hash before acknowledging, and
is final; a watch nearing its polling allowance ends itself and a
successor is derived under the mandate; after a restart the grading reads
the record and not the edited sheet or include, with partial credit, the
total, and the assistance; the courtesy copy is the submission as kept;
read_feedback returns the grade; a problem's solution is revealed from the
record only after the grade; study_status lists the sets; a submission
without the grant is kept and grading waits; and a grant that expired
before the grading could be derived loses nothing.

    uv run --no-sync python scripts/probe_learning.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import LearningConfig, TasksConfig
from ciel.learning import (NAMESPACE, NAMESPACE_NAME, Limits, Place, bookmark_of, check_root, controls, every, parse_place, record_key,
                           resolve_book, resolve_place, status)
from ciel.projects import ProjectStore
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.tasks import Criterion, Origin, RecordSet, RecordWrite, Scope, Specification, Step

CHECKS: list[str] = []
OWNER = "fixture-owner"


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def text_of(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]


# ── a PDF by hand ────────────────────────────────────────────────────────────


def build_pdf(pages: int, *, labels: str = "", outline: list[tuple[str, int, int]] = (), title: str = "", author: str = "",
              text: str = "Page {n}") -> bytes:
    """A small valid PDF: ``labels`` is a /PageLabels Nums array body,
    ``outline`` is (title, page index, depth) with depth 1 or 2, and the
    text is one line per page so a search by page text would find it."""
    objs: dict[int, str] = {}
    page_ids = [3 + i for i in range(pages)]
    content_ids = [3 + pages + i for i in range(pages)]
    font_id = 3 + 2 * pages
    next_id = font_id + 1
    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if labels:
        catalog += f" /PageLabels << /Nums [ {labels} ] >>"
    if outline:
        root = next_id
        next_id += 1
        ids = list(range(next_id, next_id + len(outline)))
        next_id += len(outline)
        catalog += f" /Outlines {root} 0 R /PageMode /UseOutlines"
        tops = [i for i, (_, _, depth) in enumerate(outline) if depth == 1]
        objs[root] = f"<< /Type /Outlines /First {ids[tops[0]]} 0 R /Last {ids[tops[-1]]} 0 R /Count {len(outline)} >>"
        for i, (label, page, depth) in enumerate(outline):
            parent = root if depth == 1 else ids[max(t for t in tops if t < i)]
            siblings = [j for j, (_, _, d) in enumerate(outline) if d == depth and (depth == 1 or max(t for t in tops if t < j) == max(t for t in tops if t < i))]
            pos = siblings.index(i)
            entry = f"<< /Title ({label}) /Parent {parent} 0 R /Dest [{page_ids[page]} 0 R /XYZ 0 0 0]"
            if pos > 0:
                entry += f" /Prev {ids[siblings[pos - 1]]} 0 R"
            if pos < len(siblings) - 1:
                entry += f" /Next {ids[siblings[pos + 1]]} 0 R"
            children = [j for j in range(i + 1, len(outline)) if outline[j][2] == depth + 1 and not any(outline[k][2] <= depth for k in range(i + 1, j))]
            if children:
                entry += f" /First {ids[children[0]]} 0 R /Last {ids[children[-1]]} 0 R /Count {len(children)}"
            objs[ids[i]] = entry + " >>"
    catalog += " >>"
    objs[1] = catalog
    objs[2] = f"<< /Type /Pages /Kids [ {' '.join(f'{p} 0 R' for p in page_ids)} ] /Count {pages} >>"
    for i, (pid, cid) in enumerate(zip(page_ids, content_ids)):
        objs[pid] = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents {cid} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        line = f"BT /F1 12 Tf 20 250 Td ({text.format(n=i + 1)}) Tj ET"
        objs[cid] = f"<< /Length {len(line)} >>\nstream\n{line}\nendstream"
    objs[font_id] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    info_id = next_id
    objs[info_id] = f"<< /Title ({title}) /Author ({author}) >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n{objs[oid]}\nendobj\n".encode("latin-1")
    xref_at = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for oid in range(1, size):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


AXLER_LABELS = "0 << /S /r >> 2 << /S /D /St 5 >>"
"""Two roman front pages, then printed pages from 5: labels i, ii, 5, 6, …"""
AXLER_OUTLINE = [("Chapter 3 Linear Maps", 2, 1), ("3A Vector Space of Linear Maps", 2, 2), ("3B Null Spaces and Ranges", 6, 2)]


def stage_home(home: Path) -> dict[str, Path]:
    books = home / "Berkeley" / "Books"
    books.mkdir(parents=True)
    paths = {
        "axler": books / "linear-algebra-done-right-4e.pdf",
        "hoffman": books / "hk.pdf",
        "decoy": books / "notes-on-nothing.pdf",
        "plain": books / "plain.pdf",
    }
    paths["axler"].write_bytes(build_pdf(10, labels=AXLER_LABELS, outline=AXLER_OUTLINE, title="Linear Algebra Done Right", author="Sheldon Axler"))
    paths["hoffman"].write_bytes(build_pdf(4, title="Linear Algebra", author="Hoffman and Kunze"))
    paths["decoy"].write_bytes(build_pdf(2, title="Notes", text="axler said on page {n}"))
    paths["plain"].write_bytes(build_pdf(4))
    (books / "not-a-book.pdf").write_bytes(b"%PDF-1.4 but nothing else")
    (books / "essay.txt").write_text("axler axler axler")
    return paths


# ── the owner's words as a place ─────────────────────────────────────────────


def config_checks(root: Path) -> None:
    print("the section loads")
    from ciel.config import load_config

    (root / "config.toml").write_text('[learning]\nenabled = true\nsearch_roots = ["~/Books", "~/Papers"]\npages_per_call = 4\n')
    loaded = load_config(root / "config.toml")
    check("a [learning] section in the config file is read: the switch, a list, and a number",
          loaded.learning.enabled is True and loaded.learning.search_roots == ("~/Books", "~/Papers") and loaded.learning.pages_per_call == 4)
    (root / "bare.toml").write_text("[tasks]\nenabled = true\n")
    check("without the section the module is off", load_config(root / "bare.toml").learning.enabled is False)


def place_checks() -> None:
    print("the owner's words as a place")
    check("'section 3B', a bare '3B', '§3.2', and 'chapter 3' are a section or a chapter in the owner's words",
          parse_place("section 3B")[0] == Place("section", "3B") and parse_place("3b")[0] == Place("section", "3B")
          and parse_place("§3.2")[0] == Place("section", "3.2") and parse_place("chapter 3")[0] == Place("chapter", "3"))
    check("'page 84' is a printed page and 'pdf page 90' a PDF page",
          parse_place("page 84")[0] == Place("page", "84") and parse_place("printed page xii")[0] == Place("page", "xii")
          and parse_place("pdf page 90")[0] == Place("pdf_page", "90"))
    check("'theorem 3.21' is kept as the owner's words with its kind, and 'cor. 3.22' too",
          parse_place("theorem 3.21")[0] == Place("item", "3.21", "theorem") and parse_place("Cor. 3.22")[0] == Place("item", "3.22", "corollary")
          and (parse_place("Theorem 3.21")[0] or Place("", "")).words() == "theorem 3.21")
    place, question = parse_place("84")
    check("a bare number is asked about, never read as a page", place is None and "printed page, a PDF page, or a section" in question)
    place, question = parse_place("somewhere in the middle")
    check("words that are no place are refused with what a place is", place is None and "not a place I can keep" in question and "theorem 3.21" in question)
    labels = ["i", "ii"] + [str(n) for n in range(5, 13)]
    outline = [["Chapter 3 Linear Maps", 3], ["3A Vector Space of Linear Maps", 3], ["3B Null Spaces and Ranges", 7]]
    placed, _ = resolve_place(Place("page", "6"), labelled=True, labels=labels, outline=outline)
    check("a printed page maps through the labels to a PDF page", placed == Place("page", "6", "", 4))
    placed, question = resolve_place(Place("page", "84"), labelled=True, labels=labels, outline=outline)
    check("a printed page the labels do not carry is asked about", placed is None and "No printed page 84" in question)
    placed, question = resolve_place(Place("page", "3"), labelled=False, labels=None, outline=[])
    taken, _ = resolve_place(Place("pdf_page", "3"), labelled=False, labels=None, outline=[])
    check("a label-less PDF refuses a printed page and takes a PDF page or a section",
          placed is None and "no printed page numbers" in question and "pdf page 3" in question and taken == Place("pdf_page", "3")
          and resolve_place(Place("section", "3B"), labelled=False, labels=None, outline=[])[0] == Place("section", "3B"))
    placed, question = resolve_place(Place("page", "6"), labelled=True, labels=None, outline=[])
    check("a labelled book whose labels were too many to keep asks for a PDF page or a section", placed is None and "too many" in question)
    check("a section is placed by the outline when the outline names it, a chapter too, and kept unplaced otherwise",
          resolve_place(Place("section", "3B"), labelled=True, labels=labels, outline=outline)[0] == Place("section", "3B", "", 7)
          and resolve_place(Place("chapter", "3"), labelled=True, labels=labels, outline=outline)[0] == Place("chapter", "3", "", 3)
          and resolve_place(Place("section", "3C"), labelled=True, labels=labels, outline=outline)[0] == Place("section", "3C")
          and resolve_place(Place("section", "3"), labelled=True, labels=labels, outline=outline)[0] == Place("section", "3", "", 3))
    check("a place round-trips through its record shape", Place.from_dict(Place("item", "3.21", "theorem", 9).as_dict()) == Place("item", "3.21", "theorem", 9))


# ── the Mac's side ───────────────────────────────────────────────────────────


async def library_checks(root: Path) -> None:
    print("\nthe books on the Mac")
    try:
        import Quartz  # noqa: F401
    except ImportError:
        print("  (PDFKit is not on this machine; the Mac's side is pinned on a Mac)")
        return
    from ciel.learning import LocalLibrary

    home = (root / "mac-home").resolve()
    paths = stage_home(home)
    state = home / ".ciel"
    state.mkdir()
    (state / "hidden.pdf").write_bytes(build_pdf(1, title="Axler hidden"))
    config = LearningConfig(enabled=True, study_root=home / "Berkeley" / "Math", search_roots=(str(home / "Berkeley"), str(state), "/etc", str(root / "elsewhere")),
                            max_search_results=3, max_search_files=4, max_pdf_bytes=5000)
    library = LocalLibrary(config, state_dir=state, forbidden=frozenset({"id_rsa"}), home=home)
    roots, refused = library.roots()
    check("a search root outside home or under the state directory is refused and named",
          roots == ((home / "Berkeley").resolve(),) and set(refused) == {str(state), "/etc", str(root / "elsewhere")})
    found = await library.find_books("axler")
    check("a book is found by its PDF title and author when the filename says nothing, and never by page text",
          [m["path"] for m in found["matches"]] == [str(paths["axler"])] and found["matches"][0]["title"] == "Linear Algebra Done Right"
          and "stopped after 4 PDFs" in found["note"] and "roots not searched" in found["note"])
    found = await library.find_books("done right")
    check("a book is found by its filename", [m["path"] for m in found["matches"]] == [str(paths["axler"])])
    found = await library.find_books("linear algebra")
    check("several matches are offered in path order, each with title, author, and size",
          [m["name"] for m in found["matches"]] == ["hk.pdf", "linear-algebra-done-right-4e.pdf"] and found["matches"][0]["author"] == "Hoffman and Kunze"
          and all(m["bytes"] > 0 for m in found["matches"]))
    bounded = LocalLibrary(replace(config, max_search_files=2, search_roots=(str(home / "Berkeley"),)), state_dir=state, forbidden=frozenset(), home=home)
    found = await bounded.find_books("plain")
    check("a search stops at its file bound and says so", found["matches"] == [] and "stopped after 2 PDFs" in found["note"])
    check("a search with no words asks for some", "needs a word" in (await library.find_books("  "))["note"])
    info = await library.pdf_info(str(paths["axler"]))
    check("pdf_info reports pages, printed labels, the outline two levels deep, title, and a digest",
          info["pages"] == 10 and info["labelled"] and info["labels"] == ["i", "ii"] + [str(n) for n in range(5, 13)]
          and info["outline"] == [["Chapter 3 Linear Maps", 3], ["3A Vector Space of Linear Maps", 3], ["3B Null Spaces and Ranges", 7]]
          and info["title"] == "Linear Algebra Done Right" and len(info["digest"]) == 64 and info["bytes"] == paths["axler"].stat().st_size)
    info = await library.pdf_info(str(paths["plain"]))
    check("a PDF without printed labels or an outline reports none", info["pages"] == 4 and not info["labelled"] and info["labels"] is None and info["outline"] == [])
    for path, why in ((str(root / "elsewhere.pdf"), "home folder"), (str(state / "hidden.pdf"), "state directory"),
                      (str(home / "Berkeley" / "Books" / "essay.txt"), "not one of .pdf"), ("relative.pdf", "absolute")):
        try:
            await library.pdf_info(path)
            check(f"a book must be under home and a PDF ({why})", False)
        except RuntimeError as exc:
            check(f"a book must be under home and a PDF ({why})", "refused on the Mac" in str(exc) and why in str(exc))
    big = LocalLibrary(replace(config, max_pdf_bytes=100), state_dir=state, forbidden=frozenset(), home=home)
    info = await big.pdf_info(str(paths["axler"]))
    check("a PDF past the byte bound is refused, not partially read", "larger than 100 bytes" in info["error"] and "pages" not in info)
    info = await library.pdf_info(str(home / "Berkeley" / "Books" / "missing.pdf"))
    check("a missing PDF is said", info["error"] == "the file does not exist")
    try:
        await library.pdf_info(str(home / "Berkeley" / "Books" / "not-a-book.pdf"))
        check("a file that is not a PDF is said, in PDFKit's words", False)
    except RuntimeError as exc:
        check("a file that is not a PDF is said, in PDFKit's words", "could not open" in str(exc))
    check("check_root takes a folder under home and nothing else",
          check_root(str(home / "Berkeley"), home=home, state_dir=state) == (home / "Berkeley").resolve()
          and check_root(str(paths["axler"]), home=home, state_dir=state) is None and check_root(str(state), home=home, state_dir=state) is None)


# ── the hub's side: the tools ────────────────────────────────────────────────


class FakeLibrary:
    """The Mac as the hub sees it: answers by path, or raises the wire's words."""

    def __init__(self, books: dict[str, dict[str, Any]], matches: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.books = books
        self.matches = matches or {}
        self.away = False

    async def find_books(self, query: str) -> dict[str, Any]:
        if self.away:
            raise RuntimeError("spoke not connected")
        return {"matches": list(self.matches.get(query.lower(), [])), "note": ""}

    async def pdf_info(self, path: str) -> dict[str, Any]:
        if self.away:
            raise RuntimeError("spoke not connected")
        if path not in self.books:
            return {"error": "the file does not exist"}
        return dict(self.books[path])


AXLER_INFO = {"pages": 10, "labelled": True, "labels": ["i", "ii"] + [str(n) for n in range(5, 13)],
              "outline": [["Chapter 3 Linear Maps", 3], ["3A Vector Space of Linear Maps", 3], ["3B Null Spaces and Ranges", 7]],
              "title": "Linear Algebra Done Right", "author": "Sheldon Axler", "digest": "a" * 64, "bytes": 4000}
PLAIN_INFO = {"pages": 4, "labelled": False, "labels": None, "outline": [], "title": "", "author": "", "digest": "b" * 64, "bytes": 900}


class Study:
    """A temporary hub: projects, the task store behind its controller, the tools bound."""

    def __init__(self, root: Path, library: FakeLibrary) -> None:
        self.root = root
        self.library = library
        self.config = LearningConfig(enabled=True, study_root=root / "home" / "Berkeley" / "Math", search_roots=(str(root / "home" / "Berkeley"),))
        self.projects = ProjectStore(root / "projects")
        self.tasks = TasksConfig(enabled=True, directory=root / "tasks")
        self.controller: TaskController | None = None
        self.binding = TaskBinding(Origin(OWNER, "voice-turn", "voice", ingress_ids=("voice:1",)), 1, 1)

    async def open(self) -> None:
        from ciel.brain.tools import learning as tools
        from ciel.brain.tools import projects as project_tools

        self.controller = TaskController(replace(self.tasks, owner=OWNER), None, namespaces=(NAMESPACE,))
        await self.controller.start()
        assert self.controller.store is not None, self.controller.unavailable
        self.controller.bind_feature(NAMESPACE, frozenset(), controls=controls(self.projects, self.library, self.config, Limits()))
        tools.bind_learning(self.projects, self.library, self.config, Limits())
        tools.bind_learning_tasks(self.controller, lambda: self.binding)
        project_tools.bind_projects(self.projects)
        from ciel.learning import notebook_lines
        project_tools.bind_study(lambda project_id: notebook_lines(self.store, OWNER, project_id))

    @property
    def store(self) -> Any:
        assert self.controller is not None
        return self.controller.store

    async def close(self) -> None:
        if self.controller is not None:
            await self.controller.close()
            self.controller = None


async def study_checks(root: Path) -> None:
    print("\nthe study, from the tools")
    from ciel.brain.tools import learning as tools
    from ciel.brain.tools import projects as project_tools

    home = root / "home"
    books = home / "Berkeley" / "Books"
    books.mkdir(parents=True)
    axler, plain, hoffman = str(books / "ladr4.pdf"), str(books / "plain.pdf"), str(books / "hk.pdf")
    for path in (axler, plain, hoffman):
        Path(path).write_bytes(b"%PDF-1.4 fixture")
    library = FakeLibrary({axler: AXLER_INFO, plain: PLAIN_INFO, hoffman: {**PLAIN_INFO, "digest": "c" * 64}},
                          {"linear algebra": [{"path": axler, "name": "ladr4.pdf", "title": "Linear Algebra Done Right", "author": "Sheldon Axler", "bytes": 4000},
                                              {"path": hoffman, "name": "hk.pdf", "title": "Linear Algebra", "author": "Hoffman and Kunze", "bytes": 900}]})
    study = Study(root, library)
    await study.open()
    study.projects.write("linear algebra", "reading", description="Math 110")
    study.projects.set_aliases("linear algebra", ["110"])
    study.projects.write("analysis", "hw", description="Math H104")

    out = text_of(await tools.find_book.handler({"query": "linear algebra"}))
    check("a search offers the candidates with title, author, and size, and binds nothing",
          out.startswith("2 candidates") and axler in out and "Hoffman and Kunze" in out and "confirm the edition" in out
          and not (study.projects.get("linear algebra") or study.projects.all()[0]).resources)
    out = text_of(await tools.register_book.handler({"project": "110", "path": axler, "title": "Linear Algebra Done Right", "edition": ""}))
    check("registering needs the owner's confirmed edition and binds nothing without it",
          "Confirm the edition" in out and not (study.projects.get("linear algebra") or study.projects.all()[0]).resources)
    out = text_of(await tools.register_book.handler({"project": "110", "path": axler, "title": "Linear Algebra Done Right", "edition": "4th edition",
                                                     "aliases": "Axler, LADR", "subject": "110"}))
    project = study.projects.get("linear algebra")
    assert project is not None
    book = project.resource(key="book-linear-algebra-done-right-4th")
    folder = project.resource(key="study-linear-algebra-done-right-4th")
    records = {r.key: r for r in await every(study.store, OWNER)}
    check("registering binds the PDF and the study folder, keeps the book's facts, and logs a line",
          out.startswith(f"Registered Linear Algebra Done Right (4th edition) on '{project.name}'") and "10 pages" in out and "printed page numbers kept" in out
          and book is not None and book.role == "book" and book.locator == axler and folder is not None and folder.role == "folder"
          and folder.locator == str(root / "home" / "Berkeley" / "Math" / "110" / "Reading" / "linear-algebra-done-right-4th")
          and records[f"book:{project.id}:linear-algebra-done-right-4th"].payload["aliases"] == ["Axler", "LADR"]
          and records[f"labels:{project.id}:linear-algebra-done-right-4th"].payload["labels"][2] == "5"
          and records[f"outline:{project.id}:linear-algebra-done-right-4th"].payload["entries"][2] == ["3B Null Spaces and Ranges", 7]
          and any("Registered Linear Algebra Done Right" in line for line in project.log))
    check("the first book is the project's current one", book is not None and book.current and "current book" in out)
    out = text_of(await tools.register_book.handler({"project": "110", "path": axler, "title": "Linear Algebra Done Right", "edition": "4th edition"}))
    check("registering the same edition again is said, not doubled", "already registered" in out and len(await every(study.store, OWNER, "book:")) == 1)
    out = text_of(await tools.register_book.handler({"project": "110", "path": str(books / "gone.pdf"), "title": "Ghost", "edition": "1st"}))
    check("a missing PDF is said at registration and nothing is bound", "does not exist" in out and len((study.projects.get("linear algebra") or project).resources) == 2)
    library.away = True
    out = text_of(await tools.register_book.handler({"project": "110", "path": plain, "title": "Plain", "edition": "1st"}))
    check("a Mac that cannot be reached is said, not worked around", "could not be reached" in out and len((study.projects.get("linear algebra") or project).resources) == 2)
    out = text_of(await tools.find_book.handler({"query": "plain"}))
    check("a search with the Mac away is said the same way", "could not be reached" in out)
    library.away = False

    out = text_of(await tools.open_study.handler({"book": "axler"}))
    check("a book resolves by its own alias without naming the project, and has no bookmark yet",
          out.startswith("Linear Algebra Done Right (4th edition) on 'linear-algebra'") and "No bookmark yet" in out)
    out = text_of(await tools.set_bookmark.handler({"book": "ladr", "where": "section 3B"}))
    place, payload = await bookmark_of(study.store, OWNER, (await resolve_book(study.projects, study.store, OWNER, book="axler"))[0])
    first_set = payload["set_at"]
    check("a bookmark is kept in the owner's words and placed through the outline",
          out == "Bookmark kept: Linear Algebra Done Right (4th edition) at section 3B, PDF page 7." and payload["words"] == "section 3B"
          and place == Place("section", "3B", "", 7))
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "page 6"}))
    check("a printed page is placed through the labels", out.endswith("at page 6, PDF page 4."))
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "page 84"}))
    check("a printed page the labels do not carry is asked about, and the bookmark stays", "No printed page 84" in out
          and (await bookmark_of(study.store, OWNER, (await resolve_book(study.projects, study.store, OWNER, book="axler"))[0]))[1]["words"] == "page 6")
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "84"}))
    check("a bare number is asked about", "printed page, a PDF page, or a section" in out)
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "theorem 3.21"}))
    _, payload = await bookmark_of(study.store, OWNER, (await resolve_book(study.projects, study.store, OWNER, book="axler"))[0])
    check("'theorem 3.21' is stored as the owner's words with its kind", out.endswith("at theorem 3.21.") and payload["place"] == {"kind": "item", "value": "3.21", "label": "theorem", "pdf_page": None})

    out = text_of(await tools.register_book.handler({"project": "linear algebra", "path": plain, "title": "Plain Book", "edition": "1st", "subject": "110", "aliases": "linear notes"}))
    project = study.projects.get("linear algebra")
    assert project is not None
    check("a second book is registered beside the first and is not current", out.startswith("Registered Plain Book (1st)") and "no printed page numbers kept" in out
          and not (project.resource(key="book-plain-book-1st") or book).current and (project.resource(key="book-linear-algebra-done-right-4th") or book).current)
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "book": "plain book", "where": "page 3"}))
    check("a label-less book refuses a printed page with the question", "no printed page numbers" in out and "pdf page 3" in out)
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "book": "plain book", "where": "pdf page 3"}))
    check("a label-less book takes a PDF page", out == "Bookmark kept: Plain Book (1st) at PDF page 3.")
    out = text_of(await tools.study_status.handler({"book": "axler"}))
    check("two books on one project keep separate bookmarks", "Bookmark: theorem 3.21" in out and "'theorem 3.21'" in out)
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "3A"}))
    check("with several books and none named, the current one is used", out.startswith("Bookmark kept: Linear Algebra Done Right"))
    out = text_of(await tools.open_study.handler({"book": "plain book"}))
    project = study.projects.get("linear algebra")
    assert project is not None
    check("open_study marks the opened book current", (project.resource(key="book-plain-book-1st") or book).current and "Bookmark: PDF page 3" in out)
    study.projects.bind("linear algebra", "book", plain, key="book-plain-book-1st")
    study.projects.bind("linear algebra", "book", axler, key="book-linear-algebra-done-right-4th")
    out = text_of(await tools.set_bookmark.handler({"project": "linear algebra", "where": "3A"}))
    check("with several books, none named and none current, it asks", out.startswith("Which book?") and "Plain Book" in out and "do not guess" in out)
    out = text_of(await tools.open_study.handler({"book": "linear"}))
    check("an ambiguous book name asks", out.startswith("'linear' could mean any of") and "Plain Book (1st)" in out and "do not guess" in out)
    out = text_of(await tools.open_study.handler({"book": "rudin"}))
    check("an unknown book name lists the books", out.startswith("No registered book called 'rudin'") and "Plain Book on linear-algebra" in out)
    out = text_of(await tools.open_study.handler({"project": "analysis"}))
    check("a project with no book says so", "No book is registered on 'analysis'" in out)
    out = text_of(await tools.open_study.handler({"project": "l"}))
    check("an ambiguous project asks the way open_project does", out.startswith("'l' could mean any of: analysis, linear-algebra") and "do not guess" in out)

    located, _ = await resolve_book(study.projects, study.store, OWNER, book="axler")
    assert located is not None
    before = (await bookmark_of(study.store, OWNER, located))[1]
    await tools.open_study.handler({"book": "axler"})
    await tools.study_status.handler({"book": "axler"})
    after = (await bookmark_of(study.store, OWNER, located))[1]
    check("elapsed time and reopening never move the bookmark", before == after and after["words"] == "3A" and after["set_at"] >= first_set)
    out = text_of(await project_tools.open_project.handler({"name": "linear algebra"}))
    check("open_project shows the books with their bookmarks", "## Books" in out and "Reading Linear Algebra Done Right (4th edition), at section 3A, said" in out
          and "Reading Plain Book (1st), at PDF page 3" in out)

    await study.close()
    study = Study(root, library)
    await study.open()
    located, _ = await resolve_book(study.projects, study.store, OWNER, book="axler")
    assert located is not None
    place, payload = await bookmark_of(study.store, OWNER, located)
    check("a bookmark survives a fresh store", place == Place("section", "3A", "", 3) and payload["words"] == "3A")

    study.binding = TaskBinding(Origin(OWNER, "public-turn", "web", private=False, ingress_ids=("dm:1",)), 1, 1)
    outs = [text_of(await handler({"book": "axler", "where": "3B"})) for handler in (tools.open_study.handler, tools.set_bookmark.handler, tools.study_status.handler, tools.close_book.handler)]
    check("a public turn reaches no record: nothing leaves the private lane",
          all("live private owner turn" in o for o in outs) and (await bookmark_of(study.store, OWNER, located))[1]["words"] == "3A")
    study.binding = TaskBinding(Origin(OWNER, "voice-turn-2", "voice", ingress_ids=("voice:2",)), 1, 1)

    print("\nclosing a book")
    project_id, slug = located.project.id, located.slug
    tag = f"learning:{project_id}:{slug}"
    spec = Specification("chapter 3 prepared", Scope(("learning.prepare",), (tag,)), (Criterion("sheets", tag, "written"),), project=tag)
    mine = await study.store.create(Origin(OWNER, "prep-1", "voice", ingress_ids=("voice:3",)), spec, Step("read", "learning.prepare", tag), now=100)
    other_tag = f"learning:{project_id}:plain-book-1st"
    other = await study.store.create(Origin(OWNER, "prep-2", "voice", ingress_ids=("voice:4",)),
                                     replace(spec, scope=Scope(("learning.prepare",), (other_tag,)), criteria=(Criterion("sheets", other_tag, "written"),), project=other_tag),
                                     Step("read", "learning.prepare", other_tag), now=101)
    for start in range(0, 300, 50):
        await study.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, tuple(
            RecordWrite(f"bookmark:{project_id}:{slug}:{n:03d}", {"kind": "bookmark", "project": project_id, "slug": slug, "words": "x", "place": {}, "set_at": 1.0})
            for n in range(start, start + 50))))
    await study.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (
        RecordWrite(record_key("request", project_id, slug), {"kind": "request", "project": project_id, "slug": slug, "operation": "learning.prepare", "requested_at": 1.0}),
        RecordWrite(record_key("request", project_id, "plain-book-1st"), {"kind": "request", "project": project_id, "slug": "plain-book-1st", "operation": "learning.prepare", "requested_at": 1.0}))))
    total_before = len(await every(study.store, OWNER))
    check("a prefix with more records than one page is read to the end", total_before > 256 and len(await every(study.store, OWNER, f"bookmark:{project_id}:{slug}:")) == 300)
    out = text_of(await tools.close_book.handler({"book": "axler"}))
    remaining = await every(study.store, OWNER)
    project = study.projects.get("linear algebra")
    assert project is not None
    check("closing a book cancels its tasks and leaves another book's",
          (await study.store.get(OWNER, mine.id)).status == "cancelled" and (await study.store.get(OWNER, other.id)).status == "queued" and "1 task cancelled" in out)
    check("closing deletes the book's records across more than one page and fences its requests",
          not any(f":{project_id}:{slug}" in r.key for r in remaining) and any(r.key == record_key("request", project_id, "plain-book-1st") for r in remaining)
          and any(r.key == record_key("book", project_id, "plain-book-1st") for r in remaining) and f"{total_before - len(remaining)} records deleted" in out)
    check("closing deletes no file and unbinds the book from the project",
          Path(axler).exists() and project.resource(key="book-linear-algebra-done-right-4th") is None and project.resource(key="study-linear-algebra-done-right-4th") is None
          and project.resource(key="book-plain-book-1st") is not None and any("Closed Linear Algebra Done Right" in line for line in project.log) and "no file touched" in out)
    out = text_of(await tools.open_study.handler({"book": "axler"}))
    check("a closed book is gone from resolution", out.startswith("No registered book called 'axler'"))
    await study.close()


# ── a chapter, prepared ──────────────────────────────────────────────────────

CHAPTER_TEXT = {
    3: "Chapter 3 Linear Maps. Convention: F denotes R or C, and V and W are vector spaces over F. Definition (linear map): a function T from V to W with additivity and homogeneity.",
    4: "Theorem 3.5 The set L(V,W) is a vector space with the operations of addition and scalar multiplication as defined.",
    5: "Some discussion between the results, with nothing to keep.",
    6: "Theorem 3.21 Suppose T in L(V,W). Then null T is a subspace of V (continues)",
    7: "of V, and the range of T is a subspace of W. Definition (null space): the subset of V consisting of the vectors T maps to 0.",
    8: "Lemma 3.22 Suppose T in L(V,W). Then T is injective if and only if null T equals zero.",
    9: "Corollary 3.23 with a symbol the text lost.",
    10: "Theorem 3.24 A linear map to a smaller dimensional space is not injective.",
}


ITEMS_BY_PAGE: dict[int, list[dict[str, Any]]] = {
    3: [{"kind": "definition", "number": "", "title": "linear map", "statement": "a function $T$ from $V$ to $W$ with additivity and homogeneity", "spans": False, "confidence": "confident"}],
    4: [{"kind": "theorem", "number": "3.5", "title": "", "statement": "The set L(V,W) is a vector space with the operations as defined.", "spans": False, "confidence": "confident"}],
    6: [{"kind": "theorem", "number": "3.21", "title": "", "statement": "Suppose $T \\in \\mathcal{L}(V,W)$. Then null $T$ is a subspace of $V$", "spans": True, "confidence": "unsure"}],
    7: [{"kind": "theorem", "number": "3.21", "title": "", "statement": "Suppose $T \\in \\mathcal{L}(V,W)$. Then null $T$ is a subspace of $V$, and the range of T is a subspace of $W$.", "spans": False, "confidence": "confident"},
        {"kind": "definition", "number": "", "title": "null space", "statement": "the subset of V consisting of the vectors T maps to 0", "spans": False, "confidence": "confident"}],
    8: [{"kind": "lemma", "number": "3.22", "title": "", "statement": "Suppose $T \\in \\mathcal{L}(V,W)$. Then $T$ is injective if and only if null $T = \\{0\\}$. Proof: Suppose $T$ is injective; then the null space is trivial.", "spans": False, "confidence": "confident"},
        {"kind": "proposition", "number": "3.22", "title": "", "statement": "Suppose $T \\in \\mathcal{L}(V,W)$. Then $T$ is injective if and only if null $T = \\{0\\}$.", "spans": False, "confidence": "confident"}],
    9: [{"kind": "corollary", "number": "3.23", "title": "", "statement": "with a symbol the text lost", "spans": False, "confidence": "unsure"}],
    10: [{"kind": "theorem", "number": "3.24", "title": "", "statement": "A linear map to a smaller dimensional space is not injective.", "spans": False, "confidence": "confident"}],
}
"""What a careful reader finds on each fixture page; the fake extractor answers from this, never from mathematics."""


class ChapterBackend:
    """A fake extractor that answers from the fixture's ground truth by
    page, so the orchestration is exercised and the mathematics is not
    pretended. A statement marked as spanning is cut short and marked as
    continuing when its page is the window's last; the next window, which
    starts on that page, sees the whole statement from the page after."""

    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.image_calls: list[tuple[str, tuple[str, ...]]] = []
        self.assess_calls: list[str] = []
        self.hint_calls: list[str] = []
        self.compose_calls: list[str] = []
        self.solve_calls: list[str] = []
        self.referee_calls: list[str] = []
        self.grade_calls: list[str] = []
        self.composed: dict[tuple[str, int], int] = {}
        self.fail_once: str | None = None

    async def extract(self, system_prompt: str, payload: str, schema: dict[str, Any], *, limits: Any,
                      images: tuple[tuple[str, bytes], ...] = ()) -> dict[str, Any]:
        import re as _re
        if images:
            self.image_calls.append((payload, tuple(label for label, _ in images)))
            fixed = []
            for line in payload.split("\n"):
                m = _re.match(r"- (\S+) \(PDF page (\d+)\): (.*)", line)
                if m:
                    fixed.append({"id": m.group(1), "statement": m.group(3).strip() + " [checked against the page]", "confidence": "confident"})
            return {"items": fixed}
        self.text_calls.append(payload)
        if self.fail_once and self.fail_once in payload:
            self.fail_once = None
            from ciel.brain.extract import ExtractionError
            raise ExtractionError("the model returned nothing this once")
        if "Write question" in payload:
            m = _re.search(r"Write question (\d+) of (\S+): a (\w+) question worth (\d+) points", payload)
            n, set_name, kind, points = int(m.group(1)), m.group(2), m.group(3), int(m.group(4))
            self.composed[(set_name, n)] = self.composed.get((set_name, n), 0) + 1
            draft = self.composed[(set_name, n)]
            self.compose_calls.append(payload)
            if kind == "definition":
                question = f"State the definition of the null space of $T \\in \\mathcal{{L}}(V,W)$. [q{n} draft{draft} {set_name}]"
            elif kind == "example":
                question = f"Give an example of a linear map with a nonzero null space. [q{n} draft{draft} {set_name}]"
            else:
                question = f"Prove that null $T$ is a subspace of $V$. [q{n} draft{draft} {set_name}]"
            half = points // 2
            return {"question": question, "solution": f"SECRET SOLUTION {set_name} q{n}: closure under addition and scalar multiplication, and zero is in it.",
                    "rubric": [{"points": half, "criterion": "shows closure under addition"}, {"points": points - half, "criterion": "shows closure under scalars and that 0 is in it"}]}
        if "The question, quoted:" in payload and "author's private solution" not in payload:
            m = _re.search(r"\[q(\d+) draft(\d+) (\S+)\]", payload)
            n, draft = int(m.group(1)), int(m.group(2))
            self.solve_calls.append(payload)
            assumptions = ["assumed T is linear, which the question does not say"] if (n == 1 and draft == 1) else []
            return {"solution": "INDEPENDENT: null T contains 0 and is closed under the operations.", "assumptions": assumptions}
        if "author's private solution" in payload:
            m = _re.search(r"\[q(\d+) draft(\d+) (\S+)\]", payload)
            n, set_name = int(m.group(1)), m.group(3)
            self.referee_calls.append(payload)
            if set_name.startswith("midterm") and n == 2:
                return {"consistent": True, "agree": False, "rubric_credits": True, "verdict": "reject", "reason": "the two solutions reach different conclusions"}
            return {"consistent": True, "agree": True, "rubric_credits": True, "verdict": "release", "reason": "consistent and agreed"}
        if "The student's answer as submitted" in payload:
            self.grade_calls.append(payload)
            grades = []
            for ident, points, answer in _re.findall(r"=== (prob-\d+) \((\d+) points\) ===.*?The student's answer as submitted, quoted:\n(.*?)(?=\n=== prob-|\Z)", payload, _re.DOTALL):
                full = "subspace" in answer
                grades.append({"id": ident, "score": int(points) if full else int(points) // 2,
                               "explanation": "Closure under addition was shown; " + ("scalars were handled too." if full else "the scalar case is missing.")})
            return {"grades": grades}
        if "student's attempt so far" in payload:
            self.hint_calls.append(payload)
            if "definition of 'null space'" in payload:
                return {"hint": "Think of the subset of V consisting of the vectors T maps to 0, and check it is closed under addition."}
            return {"hint": "Which two properties make a subset a subspace, and which hypothesis on T gives you each?"}
        if "The student's answer, quoted" in payload:
            self.assess_calls.append(payload)
            answers = []
            for ident in _re.findall(r"=== (\S+) ===", payload):
                if ident.startswith("def-"):
                    answers.append({"id": ident, "verdict": "correct", "findings": ["Equivalent to the book's: the subset of V consisting of the vectors T maps to 0."]})
                elif ident == "thm-3.21":
                    answers.append({"id": ident, "verdict": "needs_revision", "findings": ["The argument never uses that T is additive, so closure under addition is asserted, not shown."]})
                else:
                    answers.append({"id": ident, "verdict": "uncertain", "findings": ["The step from the second line to the third could not be followed."]})
            return {"assessments": answers}
        numbers = [int(n) for n in _re.findall(r"=== PDF page (\d+) ===", payload)]
        last = max(numbers)
        found: dict[tuple[str, str, str], dict[str, Any]] = {}
        unreadable: list[int] = []
        for number in numbers:
            for entry in ITEMS_BY_PAGE.get(number, []):
                if "symbol the text lost" in entry["statement"]:
                    unreadable.append(number)
                    continue
                key = (entry["kind"], entry["number"], entry["title"])
                started = found[key]["page"] if key in found else number  # an item starts where it was first seen, however far it runs
                found[key] = {"kind": entry["kind"], "number": entry["number"], "title": entry["title"], "statement": entry["statement"], "page": started,
                              "continues": bool(entry["spans"]) and number == last, "confidence": entry["confidence"]}
        conventions = ["F denotes R or C", "V and W are vector spaces over F"] if "first pages" in payload else []
        return {"items": list(found.values()), "conventions": conventions, "unreadable_pages": unreadable}


class Room:
    """A temporary hub with a runner: the real store, adapter, library, and workbench over a hand-built book."""

    def __init__(self, root: Path, *, images: str = "notation", reuse: bool = False) -> None:
        from contextlib import asynccontextmanager
        from ciel.config import JournalConfig
        from ciel.journal import ActionJournal
        from ciel.learning import LearningAdapter, LocalLibrary
        from ciel.project_work import LocalWorkbench
        from ciel.task_runner import TaskRunner

        self.root = root
        self.home = (root / "home").resolve()
        self.state = self.home / ".ciel"
        books = self.home / "Berkeley" / "Books"
        self.pdf = books / "ladr4.pdf"
        if not reuse:
            self.state.mkdir(parents=True)
            books.mkdir(parents=True)
            pages = [CHAPTER_TEXT.get(n, f"Front matter page {n}") for n in range(1, 11)]
            self.pdf.write_bytes(build_pdf_pages(pages, labels=AXLER_LABELS, outline=AXLER_OUTLINE + [("Chapter 4 Polynomials", 9, 1)]))
        self.config = LearningConfig(enabled=True, study_root=self.home / "Berkeley" / "Math", search_roots=(str(self.home / "Berkeley"),),
                                     pages_per_call=4, pages_per_image_call=2, images=images, poll_s=30.0, max_image_bytes=50000)
        self.tasks = TasksConfig(enabled=True, runner=True, directory=root / "tasks", owner=OWNER)
        self.projects = ProjectStore(root / "projects")
        self.library = LocalLibrary(self.config, state_dir=self.state, forbidden=frozenset({"id_rsa"}), home=self.home)
        self.bench = LocalWorkbench(home=self.home, state_dir=self.state, forbidden=frozenset({"id_rsa"}))
        self.adapter = LearningAdapter(self.projects, self.library, self.bench, self.config, Limits(16000, 16), clock=lambda: self.now)
        self.backend = ChapterBackend()
        self.journal = ActionJournal(JournalConfig(dir=root / "journal"))
        self.lock = asyncio.Lock()
        self.now = 1000.0
        self.store: Any = None

        @asynccontextmanager
        async def lease():
            async with self.lock:
                yield

        self.runner = TaskRunner(self.tasks, lambda: self.store, (self.adapter,), lease=lease, backend=self.backend, journal=self.journal,
                                 clock=lambda: self.now)
        self.adapter.bind_store(lambda: self.store, OWNER)
        self.turn = Origin(OWNER, "approving-turn", "web", ingress_ids=("chart:1",))

    async def open(self) -> None:
        from ciel.tasks import TaskStore
        store = TaskStore(self.tasks)
        store.register(NAMESPACE)
        await store.start()
        self.store = store

    async def close(self) -> None:
        if self.store is not None:
            await self.store.close()
            self.store = None

    async def run(self, rounds: int = 160, advance: float = 31.0) -> list[str]:
        results: list[str] = []
        idle = 0
        for _ in range(rounds):
            report = await self.runner.step(now=self.now)
            if report is None:
                idle += 1
                self.now += advance
                if idle > 3:
                    break
                continue
            idle = 0
            results.append(f"{report.result}:{report.detail[:40]}")
            self.now += 1.0
        return results


def build_pdf_pages(texts: list[str], *, labels: str = "", outline: list[tuple[str, int, int]] = ()) -> bytes:
    """The hand-built PDF with a different line of text on each page."""
    marker = "PAGETEXT{n}"
    raw = build_pdf(len(texts), labels=labels, outline=outline, title="Linear Algebra Done Right", author="Sheldon Axler", text=marker)
    # Replace each page's marker with its text, then fix the stream lengths and the xref by rebuilding.
    import re as _re
    out = raw
    for index, text in enumerate(texts):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        out = out.replace(f"PAGETEXT{index + 1}".encode(), safe.encode("latin-1"))
    # Lengths and offsets changed: rebuild the xref table and each stream's /Length.
    body = out.split(b"xref\n")[0]
    objects = _re.findall(rb"(\d+) 0 obj\n(.*?)\nendobj\n", body, _re.DOTALL)
    rebuilt = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number, content in objects:
        stream = _re.search(rb"stream\n(.*?)\nendstream", content, _re.DOTALL)
        if stream:
            content = _re.sub(rb"/Length \d+", b"/Length %d" % len(stream.group(1)), content)
        offsets[int(number)] = len(rebuilt)
        rebuilt += b"%s 0 obj\n%s\nendobj\n" % (number, content)
    size = max(offsets) + 1
    xref_at = len(rebuilt)
    rebuilt += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for n in range(1, size):
        rebuilt += b"%010d 00000 n \n" % offsets[n]
    info = _re.search(rb"/Info (\d+) 0 R", raw).group(1)
    rebuilt += b"trailer\n<< /Size %d /Root 1 0 R /Info %s 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, info, xref_at)
    return bytes(rebuilt)


async def estimator_checks() -> None:
    print("\nthe estimator cuts a chapter to fit the allowance")
    from ciel.learning import calls_for, chapter_range, cut_segments, item_id, needs_images, parse_pages, sheet_name, windows

    config = LearningConfig(pages_per_call=6, pages_per_image_call=3, images="notation")
    check("windows overlap by one page and the last is short", windows(63, 80, 6) == [(63, 68), (68, 73), (73, 78), (78, 80)] and windows(5, 5, 6) == [(5, 5)])
    check("a window costs one text call and two image calls, with a quarter in reserve rounded up: three windows are twelve, four fifteen, five nineteen",
          calls_for(63, 78, config) == 12 and calls_for(63, 83, config) == 15 and calls_for(63, 88, config) == 19
          and calls_for(63, 78, LearningConfig(pages_per_call=6, images="never")) == 4)
    sections = [("3A", 63, 72), ("3B", 73, 85), ("3C", 86, 96), ("3D", 97, 112)]
    cut = cut_segments(63, 112, sections, config, 16)
    check("whole sections are taken while they fit and no task exceeds the allowance",
          all(calls_for(a, b, config) <= 16 for a, b, _ in cut) and cut[0][0] == 63 and cut[-1][1] == 112
          and all(cut[i][1] + 1 == cut[i + 1][0] for i in range(len(cut) - 1)))
    long = cut_segments(1, 60, [("3A", 1, 60)], config, 16)
    check("a section too long for one task is split by page range, each piece within the allowance, none lost",
          len(long) > 1 and all(calls_for(a, b, config) <= 16 for a, b, _ in long) and long[0][0] == 1 and long[-1][1] == 60
          and all("pages" in label for _, _, label in long))
    check("no section headings is one section, the chapter", cut_segments(1, 10, [], config, 16) == [(1, 10, "")])
    outline = [["Chapter 3 Linear Maps", 63], ["3A Vector Space of Linear Maps", 63], ["3B Null Spaces and Ranges", 73], ["Chapter 4 Polynomials", 113], ["4A Zeros", 113]]
    found, secs = chapter_range(outline, 3, 400)
    check("a chapter's range runs from its outline entry to the page before the next chapter's, with its sections within",
          found == (63, 112) and secs == [("3A Vector Space of Linear Maps", 63, 72), ("3B Null Spaces and Ranges", 73, 112)])
    check("the last chapter runs to the book's end and an unknown chapter is none", chapter_range(outline, 4, 400)[0] == (113, 400) and chapter_range(outline, 7, 400) == (None, []))
    check("item ids follow the grammar: a numbered theorem, an unnumbered definition by term",
          item_id("theorem", "3.21", "", 3) == "thm-3.21" and item_id("definition", "", "null space", 3) == "def-3-null-space"
          and item_id("corollary", "3.22 ", "", 3) == "cor-3.22")
    check("an item with notation or an unsure reading needs images under the notation rule, and never under never",
          needs_images({"statement": "Then $T$ is injective", "confidence": "confident"}, "notation")
          and not needs_images({"statement": "Then T is injective", "confidence": "confident"}, "notation")
          and needs_images({"statement": "plain", "confidence": "unsure"}, "notation") and not needs_images({"statement": "$x$", "confidence": "unsure"}, "never")
          and needs_images({"statement": "plain", "confidence": "confident"}, "always"))
    check("the owner's pages are printed unless they say pdf", parse_pages("pages 120-171") == ("printed", 120, 171) and parse_pages("pdf pages 130–181") == ("pdf", 130, 181)
          and parse_pages("120 to 171") == ("printed", 120, 171) and parse_pages("chapter three") is None)
    check("a replacement sheet takes the next version's name", sheet_name("theorems", 3, 1) == "chapter-03.tex" and sheet_name("theorems", 3, 2) == "chapter-03.v2.tex")


async def chapter_checks(root: Path) -> None:
    print("\na chapter, prepared under the grant")
    try:
        import Quartz  # noqa: F401
    except ImportError:
        print("  (PDFKit is not on this machine; the chapter run is pinned on a Mac)")
        return
    from ciel.learning import book_target, controls, every, prepare_chapter
    from ciel.readers import answer_text, read_latex
    from ciel.tasks import DerivedOrigin

    room = Room(root / "room")
    await room.open()
    room.projects.write("linear algebra", "reading", description="Math 110")
    library = room.library
    said = await __import__("ciel.learning", fromlist=["register"]).register(
        room.projects, room.store, OWNER, library, room.config, Limits(), project="linear algebra", path=str(room.pdf),
        title="Linear Algebra Done Right", edition="4th edition", aliases="Axler", subject="110")
    check("the fixture book is registered from the real PDF with its outline", said.startswith("Registered") and "4 outline entries" in said)
    doors = controls(room.projects, library, room.config, Limits(16000, 16), room.adapter)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3"}))["said"]
    check("without the grant, prepare says so and queues nothing", "grant is not approved" in said and not await every(room.store, OWNER, "request:"))
    setup = room.adapter.setup
    project = room.projects.get("linear algebra")
    assert project is not None and setup is not None
    target = book_target(project.id, "linear-algebra-done-right-4th")
    check("the grant offers every operation the adapter serves, one target per registered book, the study root, and new files only",
          set(o for o, _ in setup.operations) == set(__import__("ciel.learning", fromlist=["OPERATIONS"]).OPERATIONS) and len(setup.operations) == 12
          and [t for t, _ in setup.targets] == [target] and any("new files only" in v for _, v in setup.bindings) and setup.limits.max_per_window == 32 and setup.limits.max_children == 256)
    draft = await room.store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome,
                                              Scope(tuple(o for o, _ in setup.operations), tuple(t for t, _ in setup.targets)), setup.limits, setup.bindings, now=room.now)
    grant, mandate = await room.store.activate_grant(room.turn, draft.id, draft.revision, draft.digest, "chart:fixture", now=room.now)
    await room.adapter.activated(room.store, room.turn, grant, mandate)
    watch = await room.store.get(OWNER, (await room.store.records(OWNER, NAMESPACE_NAME, (f"watch:{mandate.id}",)))[0].payload["task_id"])
    check("activation starts the watch under the mandate as the approving turn", watch.next_step.operation == "learning.poll" and watch.origin.request_id == room.turn.request_id)
    narrow = Scope(tuple(o for o, _ in setup.operations if o != "learning.images"), tuple(t for t, _ in setup.targets))
    old_draft = await room.store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome, narrow, setup.limits, setup.bindings, now=room.now)
    await room.store.revoke_grant(OWNER, grant.id, now=room.now)
    await room.adapter.mandate_changed(room.store, OWNER, replace(mandate, status="revoked"))  # as the controller does on a revocation
    old_grant, old_mandate = await room.store.activate_grant(room.turn, old_draft.id, old_draft.revision, old_draft.digest, "chart:old-build", now=room.now)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3"}))["said"]
    check("a grant approved before a build that added a step is named as lacking it, with what to do, and nothing is queued under it",
          "does not cover learning.images" in said and "approve it again" in said and not await every(room.store, OWNER, "request:"))
    await room.store.revoke_grant(OWNER, old_grant.id, now=room.now)
    draft = await room.store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome,
                                              Scope(tuple(o for o, _ in setup.operations), tuple(t for t, _ in setup.targets)), setup.limits, setup.bindings, now=room.now)
    second_turn = replace(room.turn, request_id="approving-turn-2", ingress_ids=("chart:2",))
    grant, mandate = await room.store.activate_grant(second_turn, draft.id, draft.revision, draft.digest, "chart:fixture-2", now=room.now)
    await room.adapter.activated(room.store, second_turn, grant, mandate)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3"}))["said"]
    requests = await every(room.store, OWNER, "request:")
    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    check("with the grant, chapter 3 is queued from the outline as one task, its request and chapter records written, nothing read, and the words say it runs beside the conversation",
          "beside the conversation" in said and
          said.startswith("Queued chapter 3 of Linear Algebra Done Right (4th edition): PDF pages 3–9, 7 pages, as 1 task") and len(requests) == 1
          and chapter["status"] == "requested" and chapter["segments"] == [[3, 9]] and not room.backend.text_calls)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3"}))["said"]
    check("asking again while it is queued adds nothing", "already queued" in said and len(await every(room.store, OWNER, "request:")) == 1)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "9"}))["said"]
    check("a chapter the outline does not place asks for its pages", "does not say where chapter 9" in said)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "pages": "pages 5-6"}))["said"]
    check("a page range without its chapter number asks for it", "needs the chapter number" in said)
    room.backend.fail_once = "Window 2"
    results = await room.run()
    tasks = await room.store.list(OWNER)
    children = [t for t in tasks if isinstance(t.origin, DerivedOrigin)]
    reading = next(t for t in children if "learning.window" in t.specification.scope.operations)
    items = {r.payload["id"]: r.payload for r in await every(room.store, OWNER, f"item:{project.id}:linear-algebra-done-right-4th:3:")}
    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    check("the watch derives the reading task under the mandate and the request names it",
          reading.origin.parent_mandate_id == mandate.id and (await every(room.store, OWNER, "request:"))[0].payload["task_id"] == reading.id)
    check("every window is read, in order; each step is one model call, and the abandoned call is the only one redone",
          chapter["windows_done"] == [[3, 6], [6, 9]] and any(r.startswith("abandoned") for r in results)
          and sum(1 for p in room.backend.text_calls if "Window 2" in p) == 2 and sum(1 for p in room.backend.text_calls if "Window 1" in p) == 1
          and reading.model_calls == len([p for p in room.backend.text_calls if "Window" in p]) + len(room.backend.image_calls))
    check("a statement across the window boundary is provisional from the first window and whole from the next, kept once",
          "thm-3.21" in items and not items["thm-3.21"]["provisional"] and "range of T" in items["thm-3.21"]["statement"]
          and sum(1 for i in items if i == "thm-3.21") == 1)
    check("items carry their kind, number, page, and id; a definition keeps the book's words as private reference text",
          items["thm-3.5"]["item_kind"] == "theorem" and items["thm-3.5"]["page"] == 4 and items["def-3-null-space"]["reference"].startswith("the subset of V")
          and items["def-3-linear-map"]["title"] == "linear map")
    check("a proof the model copied after a statement is cut off into the item's private reference, and the same number under another kind is one item",
          "Proof" not in items["lem-3.22"]["statement"] and items["lem-3.22"]["reference"].startswith("Proof:") and "prop-3.22" not in items)
    check("items with notation are re-read from the page images, two pages a call, and the image reading is kept",
          room.backend.image_calls and all(len(labels) <= 2 for _, labels in room.backend.image_calls)
          and items["thm-3.21"]["statement"].endswith("[checked against the page]") and items["thm-3.21"]["confidence"] == "confident")
    check("a page the model could not read is flagged, never invented, and its corollary is not among the items", chapter["flagged"] == [9] and "cor-3.23" not in items)
    check("the chapter's conventions are kept from its first window",
          (await room.store.records(OWNER, NAMESPACE_NAME, (f"convention:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload["lines"][0].startswith("F denotes"))
    check("the reading task completed within its allowance and the chapter was read", reading.status == "done" and reading.model_calls <= 16)
    publishing = next((t for t in children if "learning.publish" in t.specification.scope.operations), None)
    folder = room.home / "Berkeley" / "Math" / "110" / "Reading" / "linear-algebra-done-right-4th"
    definitions, theorems = folder / "Definitions" / "chapter-03.tex", folder / "Theorems" / "chapter-03.tex"
    check("when every window is read the watch derives one publishing task, and both sheets land as new files under the study folder",
          publishing is not None and publishing.status == "done" and definitions.exists() and theorems.exists()
          and chapter["status"] == "prepared" and set(chapter["sheets"]) == {"definitions", "theorems"} and all(v["state"] == "verified" for v in chapter["sheets"].values()))
    theorem_text, definition_text = theorems.read_text(), definitions.read_text()
    check("the theorems sheet has each statement, its page, an empty box, and the conventions; no proof and no definition's text",
          "\\begin{namedquestion}{thm-3.21}" in theorem_text and "{p.~6}" in theorem_text and "F denotes" in theorem_text
          and "the subset of V" not in theorem_text and "\\begin{framed}\\vspace{8\\baselineskip}\\end{framed}" in theorem_text and "Lemma 3.22" in theorem_text)
    check("the definitions sheet names each term and asks for the definition; the book's definition is nowhere on it",
          "\\begin{namedquestion}{def-3-null-space}" in definition_text and "give each in your own words" in definition_text and "the subset of V" not in definition_text
          and "thm-3.21" not in definition_text)
    check("a flagged page is said on the sheet's first lines", "Page 9 could not be read" in theorem_text)
    back = read_latex(theorem_text, "chapter-03.tex")
    ids = [i.id for i in back.items]
    check("the reader reads a sheet back with the ids it was written with, every box not started, and the answer spans isolated",
          ids == sorted(ids, key=ids.index) and "thm-3.21" in ids and "lem-3.22" in ids and all(i.claim == "not started" for i in back.items)
          and all(answer_text(i).strip() == "\\vspace{8\\baselineskip}" for i in back.items) and not back.gaps)
    notices = await room.store.notices(OWNER)
    check("the completion's notice says the chapter is prepared", any("Chapter 3 of Linear Algebra Done Right is prepared" in n.outcome for n in notices))
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3"}))["said"]
    check("a prepared chapter is reopened, never regenerated", "already prepared" in said and str(theorems) in said)
    before = theorem_text
    calls_before = len(room.backend.text_calls) + len(room.backend.image_calls)
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "3", "replace": "true"}))["said"]
    check("a requested replacement is queued as the next version, rendered from what was read, and the old sheets stay",
          said.startswith("Queued new sheets for chapter 3") and "version 2" in said and "no page is read again" in said)
    (folder / "Theorems" / "chapter-03.v2.tex").write_text("% the owner made this name first\n")
    await room.run()
    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    check("the replacement's sheets take the next free number: a name the owner took goes to the one after, nothing is ever replaced, and no page was read again",
          chapter["status"] == "prepared" and chapter["version"] == 2 and chapter["sheets"]["definitions"]["path"].endswith("chapter-03.v2.tex")
          and chapter["sheets"]["theorems"]["path"].endswith("chapter-03.v3.tex") and theorems.read_text() == before
          and (folder / "Theorems" / "chapter-03.v2.tex").read_text() == "% the owner made this name first\n"
          and len(room.backend.text_calls) + len(room.backend.image_calls) == calls_before)
    journal = room.journal.recent(50)
    check("every publication was written down before it was sent, through the journal", sum(1 for e in journal if e.get("tool") == "task_learning.publish") >= 4)

    print("\nthe Mac away mid-chapter")
    said = (await doors["learning_prepare"](room.store, OWNER, {"book": "axler", "chapter": "4"}))["said"]
    check("chapter 4 is queued from the outline too", said.startswith("Queued chapter 4"))

    class Away:
        def __init__(self, inner: Any, misses: int) -> None:
            self.inner, self.misses, self.calls = inner, misses, 0

        async def pdf_pages(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            if self.misses > 0:
                self.misses -= 1
                raise RuntimeError("spoke not connected")
            return await self.inner.pdf_pages(*args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    away = Away(room.library, misses=2)
    room.adapter._library = away
    before_calls = len(room.backend.text_calls)
    results = await room.run()
    tasks = await room.store.list(OWNER)
    ch4 = next(t for t in tasks if (t.specification.project or "").endswith("linear-algebra-done-right-4th") and "chapter 4" in t.specification.outcome.lower())
    check("a step that finds the Mac away checkpoints with a delay, spends no attempt, never parks, and goes on when the Mac is back",
          ch4.status == "done" and ch4.attempts == 0 and away.calls >= 3 and not any(r.startswith("waiting") for r in results)
          and len(room.backend.text_calls) > before_calls)
    from ciel.tasks import Criterion as _Criterion, Scope as _Scope, Specification as _Specification, Step as _Step
    tag4 = f"learning:{project.id}:linear-algebra-done-right-4th"
    parked = await room.store.create(Origin(OWNER, "parked-1", "voice", ingress_ids=("voice:9",)),
                                     _Specification("a parked read", _Scope(("learning.window",), (book_target(project.id, "linear-algebra-done-right-4th"),)),
                                                    (_Criterion("read", book_target(project.id, "linear-algebra-done-right-4th"), "read"),), project=tag4),
                                     _Step("read", "learning.window", book_target(project.id, "linear-algebra-done-right-4th"), (("project", project.id), ("slug", "linear-algebra-done-right-4th"), ("chapter", "9"), ("first", "1"), ("last", "1"))), now=room.now)
    parked = await room.store.wait(OWNER, parked.id, parked.revision, "resource", "The book's pages could not be rendered: the Mac could not be reached: the Mac is not connected", now=room.now)
    old_request_key = f"request:{project.id}:linear-algebra-done-right-4th:prepare:9:1-1:oldbuild"
    await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (RecordWrite(old_request_key, {"kind": "request", "project": project.id, "slug": "linear-algebra-done-right-4th",
                                                                                                  "operation": "learning.prepare", "chapter": 9, "first": 1, "last": 1, "label": "",
                                                                                                  "requested_at": 1.0, "nonce": "oldbuild", "task_id": parked.id}, 0),)))
    room.now += 31
    await room.runner.step(now=room.now)
    old_request = (await room.store.records(OWNER, NAMESPACE_NAME, (old_request_key,)))[0].payload
    check("a reading task an earlier build parked on the Mac's absence, whose scope cannot take the images step, is ended by the watch and its request re-derived afresh",
          (await room.store.get(OWNER, parked.id)).status == "cancelled" and old_request["task_id"] == "" and old_request["nonce"] != "oldbuild")
    await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (RecordWrite(old_request_key, None, None),)))
    room.adapter._library = room.library

    print("\nthe review: a snapshot, an assessment held to the book, a numbered feedback file")
    from ciel.learning import feedback_text, reveal, sheet_progress
    from ciel.readers import answer_hash

    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    theorems_path = Path(chapter["sheets"]["theorems"]["path"])
    definitions_path = Path(chapter["sheets"]["definitions"]["path"])
    doors = controls(room.projects, library, room.config, Limits(16000, 16), room.adapter, room.bench)
    said = (await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "theorems"}))["said"]
    check("with every box empty a review is refused and the emptiness said, nothing queued",
          "Every box named" in said and "empty" in said and not [r for r in await every(room.store, OWNER, "request:") if r.payload.get("operation") == "learning.review"])
    empty_box = "\\begin{framed}\\vspace{8\\baselineskip}\\end{framed}"
    text = theorems_path.read_text()
    head, _, tail = text.partition("\\begin{namedquestion}{thm-3.21}")
    tail = tail.replace(empty_box, "\\begin{framed}\nLet $u, v \\in \\operatorname{null} T$. Then $T(u+v) = 0$, so $u + v \\in \\operatorname{null} T$; scalars likewise. % first attempt\n\\end{framed}", 1)
    theorems_path.write_text(head + "\\begin{namedquestion}{thm-3.21}" + tail)
    text = definitions_path.read_text()
    head, _, tail = text.partition("\\begin{namedquestion}{def-3-null-space}")
    tail = tail.replace(empty_box, "\\begin{framed}\nThe set of vectors sent to zero by $T$.\n\\end{framed}", 1)
    definitions_path.write_text(head + "\\begin{namedquestion}{def-3-null-space}" + tail)
    said = (await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "theorems", "items": "thm-3.21, thm-9.99"}))["said"]
    check("an item not on the sheet is said, with the ids that are", said.startswith("Not on the theorems sheet") and "thm-3.21" in said and "lem-3.22" in said)
    said = (await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "theorems"}))["said"]
    check("with one answer written the review is queued, the empty boxes to be named and not reviewed",
          said.startswith("Queued a review of 1 answer on the theorems sheet of chapter 3") and "empty box" in said)
    said = (await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "theorems"}))["said"]
    check("asking again while it is queued adds nothing", "already queued" in said)
    room.backend.fail_once = "The student's answer, quoted"
    results = await room.run()
    reviews = [r.payload for r in await every(room.store, OWNER, f"review:{project.id}:linear-algebra-done-right-4th:3:theorems:")]
    review = reviews[0]
    items_now = {i.id: i for i in (await __import__("ciel.learning", fromlist=["read_sheet_items"]).read_sheet_items(room.projects, room.bench, project, str(theorems_path)))[0]}
    check("the review snapshotted the one written answer by hash, named the empty boxes, and was assessed after the interrupted call from the same snapshot",
          review["status"] == "published" and review["snapshot"] == {"thm-3.21": answer_hash(items_now["thm-3.21"])} and set(review["empty"]) >= {"lem-3.22", "thm-3.5"}
          and any(r.startswith("abandoned") for r in results) and sum(1 for p in room.backend.text_calls if "The student's answer" in p) == 2
          and len(room.backend.assess_calls) == 1 and review["assessed"]["thm-3.21"]["verdict"] == "needs_revision")
    payload = room.backend.assess_calls[-1]
    check("the assessment saw the statement, the chapter's conventions, and the answer, and nothing of the other sheets",
          "Theorem 3.21" in payload and "F denotes R or C" in payload and "scalars likewise" in payload and "def-3-null-space" not in payload)
    feedback = Path(review["feedback"])
    feedback_body = feedback.read_text()
    check("the feedback is a new numbered file beside the sheet, names the hash it assessed and the verdicts, and supplies no argument",
          feedback.name == "chapter-03.feedback-001.md" and feedback.parent == theorems_path.parent and "needs revision (answer hash" in feedback_body
          and review["snapshot"]["thm-3.21"][:12] in feedback_body and "lem-3.22 — not reviewed: the box is empty" in feedback_body
          and "not a verification" in feedback_body and "closure under addition is asserted" in feedback_body)
    check("the sheet itself was never edited by the review", "first attempt" in theorems_path.read_text() and not any(p.name.endswith(".tmp") for p in theorems_path.parent.iterdir()))
    check("the snapshot records are gone once the feedback is published", not await every(room.store, OWNER, f"snapshot:{project.id}:"))
    progress = {r.payload["item"]: r.payload for r in await every(room.store, OWNER, f"progress:{project.id}:linear-algebra-done-right-4th:3:theorems:")}
    check("the progress record carries the verdict with the hash it assessed and the feedback file",
          progress["thm-3.21"]["verdict"] == "needs_revision" and progress["thm-3.21"]["assessed_hash"] == review["snapshot"]["thm-3.21"]
          and progress["thm-3.21"]["feedback"] == str(feedback) and progress["lem-3.22"]["verdict"] == "")
    standing = await sheet_progress(room.store, OWNER, room.bench, located_room := (await resolve_book(room.projects, room.store, OWNER, book="axler"))[0], chapter, "theorems")
    check("where the sheet stands reads by hash: one needs revision, the rest empty", standing["counts"]["needs_revision"] == 1 and standing["counts"]["empty"] == 2 and standing["counts"]["stale"] == 0)
    theorems_path.write_text(theorems_path.read_text().replace("scalars likewise", "and for $\\lambda u$ use homogeneity"))
    standing = await sheet_progress(room.store, OWNER, room.bench, located_room, chapter, "theorems")
    check("an edit after the review reads as stale, the verdict kept", standing["counts"]["stale"] == 1 and standing["rows"][[r["id"] for r in standing["rows"]].index("thm-3.21")]["state"] == "stale")
    said = await feedback_text(room.projects, room.store, OWNER, room.bench, book="axler", chapter="3", sheet="theorems")
    check("read_feedback returns the file's verdicts and gaps for speaking, and says the sheet moved since", "needs revision" in said and "Stale: thm-3.21" in said and "never argue" in said)
    said = (await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "theorems", "items": "thm-3.21"}))["said"]
    await room.run()
    progress = {r.payload["item"]: r.payload for r in await every(room.store, OWNER, f"progress:{project.id}:linear-algebra-done-right-4th:3:theorems:")}
    check("a second review is a second numbered file and the history keeps the first verdict with its hash",
          (theorems_path.parent / "chapter-03.feedback-002.md").exists() and len(progress["thm-3.21"]["history"]) == 1
          and progress["thm-3.21"]["history"][0]["hash"] == review["snapshot"]["thm-3.21"] and progress["thm-3.21"]["assessed_hash"] != review["snapshot"]["thm-3.21"])
    await doors["learning_review"](room.store, OWNER, {"book": "axler", "chapter": "3", "sheet": "definitions"})
    await room.run()
    definitions_feedback = (definitions_path.parent / "chapter-03.feedback-001.md").read_text()
    check("a review of the definitions saw the book's definition as private reference and its finding that repeated the book's words was withheld",
          "private reference: the subset of V" in room.backend.assess_calls[-1] and "assessed correct" in definitions_feedback
          and "the subset of V consisting" not in definitions_feedback and "withheld" in definitions_feedback)
    said = (await doors["learning_hint"](room.store, OWNER, {"book": "axler", "chapter": "3", "item": "thm-3.21"}))["said"]
    check("a hint is queued only on request and recorded as assistance", said.startswith("Queued a hint on thm-3.21"))
    await doors["learning_hint"](room.store, OWNER, {"book": "axler", "chapter": "3", "item": "def-3-null-space"})
    await room.run()
    hint_files = sorted(theorems_path.parent.glob("chapter-03.hint-*.md")) + sorted(definitions_path.parent.glob("chapter-03.hint-*.md"))
    progress = {r.payload["item"]: r.payload for r in await every(room.store, OWNER, f"progress:{project.id}:linear-algebra-done-right-4th:3:theorems:")}
    definition_hint = (definitions_path.parent / "chapter-03.hint-001.md").read_text()
    check("the hint lands as a numbered file with one nudge and the item records it; a hint that repeated the book's words was replaced",
          len(hint_files) == 2 and "Which two properties" in (theorems_path.parent / "chapter-03.hint-001.md").read_text()
          and progress["thm-3.21"]["hinted"] == [str(theorems_path.parent / "chapter-03.hint-001.md")]
          and "the subset of V consisting" not in definition_hint and "No hint could be given" in definition_hint)
    said = (await doors["learning_reveal"](room.store, OWNER, {"book": "axler", "chapter": "3", "item": "def-3-null-space"}))["said"]
    progress_defs = {r.payload["item"]: r.payload for r in await every(room.store, OWNER, f"progress:{project.id}:linear-algebra-done-right-4th:3:definitions:")}
    check("revealing a definition gives the book's words from the record, at once, and marks the item revealed",
          said.startswith("Revealed, and recorded") and "the subset of V consisting" in said and progress_defs["def-3-null-space"]["revealed"] is True)
    said = (await doors["learning_reveal"](room.store, OWNER, {"book": "axler", "chapter": "3", "item": "thm-3.21"}))["said"]
    check("revealing a theorem points at where the book proves it, and marks the item revealed", "proves theorem 3.21 on PDF page 6" in said
          and (await sheet_progress(room.store, OWNER, room.bench, located_room, chapter, "theorems"))["counts"]["revealed"] == 1)
    out = await status(room.store, OWNER, located_room, bench=room.bench)
    check("study_status says where each sheet stands", "Chapter 3 theorems:" in out and "1 revealed" in out and "Chapter 3 definitions:" in out and "1 assessed correct" in out)

    print("\npractice and a mock midterm: written, solved independently, refereed, submitted, graded")
    from ciel.learning import request_set, sets_of, submit_set
    from ciel.tasks import DerivedOrigin as _Derived

    syllabus = theorems_path.parent.parent / "syllabus.md"
    room.projects.bind("linear algebra", "syllabus", str(syllabus), key="syllabus")
    syllabus.write_text("# Math 110 syllabus\nMidterm covers chapters 1-3; proofs weighted heavily.\n")
    from ciel.learning import set_bookmark as _set_bookmark
    await _set_bookmark(room.projects, room.store, OWNER, located_room, "section 3B")
    said = (await doors["learning_practice"](room.store, OWNER, {"book": "axler", "topic": "null spaces"}))["said"]
    check("a practice question is queued as practice-001, one problem, book-based, from the prepared chapters up to the bookmark and not the one past it",
          said.startswith("Queued a practice question as practice-001") and "chapter 3" in said and "chapters 3, 4" not in said and "Book-based practice" in said
          and "never on the sheet" in said)
    said = (await doors["learning_exam"](room.store, OWNER, {"book": "axler"}))["said"]
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("a mock midterm defaults to five problems, sixty minutes, one hundred points, calibrated to the bound syllabus",
          "midterm-001" in said and sets["midterm-001"]["problems"] == 5 and sets["midterm-001"]["minutes"] == 60 and sets["midterm-001"]["points"] == 100
          and sets["midterm-001"]["calibration"].startswith("Calibrated to the bound syllabus") and "Midterm covers" in sets["midterm-001"]["calibration_text"]
          and sets["midterm-001"]["points_each"] == {"1": 20, "2": 20, "3": 20, "4": 20, "5": 20})
    said = (await doors["learning_exam"](room.store, OWNER, {"book": "axler", "problems": "3", "minutes": "45", "points": "60", "chapters": "chapter 3", "calibration": "none"}))["said"]
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("an explicit change to the duration, points, and count is honoured", sets["midterm-002"]["problems"] == 3 and sets["midterm-002"]["minutes"] == 45 and sets["midterm-002"]["points"] == 60)
    await room.run(rounds=400)
    tasks = await room.store.list(OWNER)
    questions = {(q.payload["set"], q.payload["n"]): q.payload for q in await every(room.store, OWNER, f"question:{project.id}:linear-algebra-done-right-4th:")}
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("each question is one task: composed, solved from the question alone, refereed; a solve that needed an added assumption is rejected and rewritten, the second draft released",
          questions[("practice-001", 1)]["status"] == "released" and questions[("practice-001", 1)]["attempts"] == 2 and "draft2" in questions[("practice-001", 1)]["question"]
          and any("assumed T is linear" in p for p in room.backend.referee_calls) and all("SECRET" not in p for p in room.backend.solve_calls))
    check("a question the referee rejects twice is withdrawn", questions[("midterm-001", 2)]["status"] == "rejected" and questions[("midterm-001", 2)]["attempts"] == 2)
    exam_sheet = Path(sets["midterm-001"]["sheet"]["path"])
    sheet_text = exam_sheet.read_text()
    check("the midterm sheet has the header, the four released problems with empty boxes, the withdrawal, and no solution or rubric anywhere",
          exam_sheet.name == "midterm-001.tex" and "60 minutes, 100 points over 4 problems" in sheet_text and "Calibrated to the bound syllabus" in sheet_text
          and sheet_text.count("\\begin{namedquestion}{prob-") == 4 and "1 problem was withdrawn" in sheet_text and "SECRET" not in sheet_text
          and "closure under addition" not in sheet_text and sets["midterm-001"]["status"] == "released")
    practice_sheet = Path(sets["practice-001"]["sheet"]["path"])
    check("the practice sheet says book-based practice and has its one problem", "Book-based practice" in practice_sheet.read_text() and practice_sheet.read_text().count("namedquestion}{prob-") == 1)
    check("the compose call saw the material, the conventions, and the calibration as quoted data",
          any("Chapter 3" in p and "F denotes" in p and "Midterm covers" in p for p in room.backend.compose_calls))
    notices = await room.store.notices(OWNER)
    evidence_texts = []
    for t in tasks:
        for a in await room.store.attempts(OWNER, t.id):
            evidence_texts.extend(f"{e.criterion_id} {e.observed}" for e in await room.store.observations(OWNER, a.id))
    check("solutions and rubrics appear in no notice, no task evidence, and no notebook state",
          all("SECRET" not in f"{n.outcome} {n.detail}" for n in notices) and all("SECRET" not in e for e in evidence_texts)
          and "SECRET" not in (room.projects.get("linear algebra") or project).state)
    said = (await doors["learning_reveal"](room.store, OWNER, {"book": "axler", "set": "midterm-001", "item": "prob-1"}))["said"]
    check("a solution is not revealed while the exam is open", said.startswith("Not while midterm-001 is open") and "SECRET" not in said)
    said = (await doors["learning_hint"](room.store, OWNER, {"book": "axler", "set": "midterm-001", "item": "prob-3"}))["said"]
    await room.run()
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    hint_file = exam_sheet.parent / "midterm-001.hint-001.md"
    check("a hint during the exam is written beside the sheet, names no step, and is recorded on the set",
          said.startswith("Queued a hint on prob-3 of midterm-001") and hint_file.exists() and "SECRET" not in hint_file.read_text() and sets["midterm-001"]["hints"] == ["prob-3"])
    empty_box = "\\begin{framed}\\vspace{14\\baselineskip}\\end{framed}"
    text = exam_sheet.read_text()
    head, _, tail = text.partition("\\begin{namedquestion}{prob-1}")
    tail = tail.replace(empty_box, "\\begin{framed}\nIt is a subspace: closed under addition and scalars, and contains 0. \\input{scratch}\n\\end{framed}", 1)
    head2, _, tail2 = tail.partition("\\begin{namedquestion}{prob-3}")
    tail2 = tail2.replace(empty_box, "\\begin{framed}\nClosed under addition only.\n\\end{framed}", 1)
    exam_sheet.write_text(head + "\\begin{namedquestion}{prob-1}" + head2 + "\\begin{namedquestion}{prob-3}" + tail2)
    (exam_sheet.parent / "scratch.tex").write_text("% scratch work included\n")
    small = __import__("dataclasses").replace(room.config, max_submission_chars=200)
    said = await submit_set(room.projects, room.store, OWNER, room.bench, small, Limits(16000, 16), room.adapter, book="axler", set_name="midterm-001")
    check("a submission past the cap is refused, never truncated, and nothing is recorded",
          "larger than 200 characters" in said and not await every(room.store, OWNER, f"submission:{project.id}:"))
    said = (await doors["learning_submit"](room.store, OWNER, {"book": "axler", "set": "midterm-001"}))["said"]
    parts = await every(room.store, OWNER, f"submission:{project.id}:linear-algebra-done-right-4th:midterm-001:")
    kept = "".join(e["text"] for part in parts for e in part.payload["files"])
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("submitting keeps the sheet and its include whole on record, with the hash, before it is acknowledged",
          said.startswith("Submitted midterm-001: 2 files kept whole") and "scratch work included" in kept and "Closed under addition only" in kept
          and sets["midterm-001"]["status"] == "submitted" and sets["midterm-001"]["submission_hash"][:12] in said and "Grading runs from the record" in said and "beside the conversation" in said)
    said = (await doors["learning_submit"](room.store, OWNER, {"book": "axler", "set": "midterm-001"}))["said"]
    check("a submission is final", "submitted already" in said)
    watches = [t for t in await room.store.list(OWNER) if t.next_step.operation == "learning.poll"]
    watch_record = (await room.store.records(OWNER, NAMESPACE_NAME, (f"watch:{mandate.id}",)))[0].payload
    check("a watch that nears its polling allowance ends itself and a successor is derived under the mandate, which the mandate's record now names",
          len(watches) >= 2 and any(t.status == "done" for t in watches) and any(t.status == "queued" for t in watches)
          and watch_record["task_id"] == next(t.id for t in watches if t.status == "queued" and dict(t.next_step.arguments).get("mandate") == mandate.id)
          and any(isinstance(t.origin, _Derived) for t in watches))
    exam_sheet.write_text(exam_sheet.read_text().replace("Closed under addition only.", "Closed under addition and scalars, and contains 0."))
    (exam_sheet.parent / "scratch.tex").write_text("% changed after submission\n")
    clock = room.now
    await room.close()
    room = Room(root / "room", reuse=True)
    room.now = clock
    await room.open()
    await room.run()
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    grade_file = exam_sheet.parent / "midterm-001.grade-001.md"
    courtesy = exam_sheet.parent / "midterm-001.submitted.tex"
    grade_text = grade_file.read_text()
    check("after a restart the grading reads the record, not the edited sheet or include: partial credit as submitted, the total, and the assistance",
          sets["midterm-001"]["status"] == "graded" and grade_file.exists() and "prob-3 — 10 / 20" in grade_text and "prob-1 — 20 / 20" in grade_text
          and "Total: 30 / 80" in grade_text and "hint was given on this problem" in grade_text and "the scalar case is missing" in grade_text
          and "SECRET" not in grade_text and any("Closed under addition only" in p for p in room.backend.grade_calls)
          and not any("Closed under addition and scalars, and contains 0" in p and "prob-3" in p.split("prob-3", 1)[0] for p in room.backend.grade_calls))
    check("the courtesy copy is the submission as kept, published under the grant, and editing it changes nothing",
          courtesy.exists() and "Closed under addition only" in courtesy.read_text() and "courtesy copy, not the record" in courtesy.read_text())
    courtesy.write_text("edited by the owner\n")
    said = await __import__("ciel.learning", fromlist=["feedback_text"]).feedback_text(room.projects, room.store, OWNER, room.bench, book="axler", set_name="midterm-001")
    check("read_feedback returns the grade file for speaking", "Grade at" in said and "prob-3 — 10 / 20" in said)
    said = (await doors["learning_reveal"](room.store, OWNER, {"book": "axler", "set": "midterm-001", "item": "prob-1"}))["said"]
    check("after the grade a problem's solution is revealed from the record and marked", "SECRET SOLUTION midterm-001 q1" in said and "Revealed, and recorded" in said)
    out = await status(room.store, OWNER, located_room, bench=room.bench)
    check("study_status lists the sets with their grades", "midterm-001 (midterm" in out and "30 / 80" in out and "hints on prob-3" in out)
    doors = controls(room.projects, room.library, room.config, Limits(16000, 16), room.adapter, room.bench)
    await doors["learning_practice"](room.store, OWNER, {"book": "axler", "topic": "ranges", "problems": "1"})
    await room.run()
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    p2 = Path(sets["practice-002"]["sheet"]["path"])
    p2.write_text(p2.read_text().replace("\\begin{framed}\\vspace{14\\baselineskip}\\end{framed}", "\\begin{framed} a subspace \\end{framed}", 1))
    async def nothing(store: Any, owner: str) -> set[str]:
        return set()
    original = room.adapter.active_targets
    room.adapter.active_targets = nothing  # type: ignore[method-assign]
    said = (await doors["learning_submit"](room.store, OWNER, {"book": "axler", "set": "practice-002"}))["said"]
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("a submission without the grant is kept whole and grading waits, saying why",
          "grading waits for it" in said and sets["practice-002"]["status"] == "submitted" and await every(room.store, OWNER, f"submission:{project.id}:linear-algebra-done-right-4th:practice-002:"))
    room.adapter.active_targets = original  # type: ignore[method-assign]
    room.now += 40 * 86400
    await room.run()
    sets = {e["set"]: e for e in await sets_of(room.store, OWNER, located_room)}
    check("a grant that expired before the grading could be derived loses nothing: the submission stands, still to be graded",
          sets["practice-002"]["status"] == "submitted" and await every(room.store, OWNER, f"submission:{project.id}:linear-algebra-done-right-4th:practice-002:"))
    await room.close()

    print("\nthe same chapter with images off")
    quiet = Room(root / "quiet", images="never")
    await quiet.open()
    quiet.projects.write("linear algebra", "reading", description="Math 110")
    await __import__("ciel.learning", fromlist=["register"]).register(
        quiet.projects, quiet.store, OWNER, quiet.library, quiet.config, Limits(), project="linear algebra", path=str(quiet.pdf),
        title="Linear Algebra Done Right", edition="4th edition", subject="110")
    setup = quiet.adapter.setup
    assert setup is not None
    draft = await quiet.store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome,
                                               Scope(tuple(o for o, _ in setup.operations), tuple(t for t, _ in setup.targets)), setup.limits, setup.bindings, now=quiet.now)
    grant, mandate = await quiet.store.activate_grant(quiet.turn, draft.id, draft.revision, draft.digest, "chart:fixture", now=quiet.now)
    await quiet.adapter.activated(quiet.store, quiet.turn, grant, mandate)
    doors = controls(quiet.projects, quiet.library, quiet.config, Limits(16000, 16), quiet.adapter)
    await doors["learning_prepare"](quiet.store, OWNER, {"book": "linear algebra done right", "chapter": "3"})
    await quiet.run()
    project = quiet.projects.get("linear algebra")
    assert project is not None
    chapter = (await quiet.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    check("with images never, no image call is made and the chapter is still prepared from the text",
          not quiet.backend.image_calls and chapter["status"] == "prepared" and len(quiet.backend.text_calls) == 2)
    await quiet.close()


async def background_checks(root: Path) -> None:
    """The learning steps beside the conversation: a background runner over the same store."""
    print("\nthe learning steps beside the conversation")
    from ciel.task_runner import TaskRunner, private_lease
    from ciel.learning import register, controls

    room = Room(root / "beside", images="never")
    await room.open()
    room.projects.write("linear algebra", "reading", description="Math 110")
    await register(room.projects, room.store, OWNER, room.library, room.config, Limits(), project="linear algebra", path=str(room.pdf),
                   title="Linear Algebra Done Right", edition="4th edition", subject="110")
    setup = room.adapter.setup
    assert setup is not None
    draft = await room.store.save_grant_draft(OWNER, setup.host, setup.namespace, setup.outcome,
                                              Scope(tuple(o for o, _ in setup.operations), tuple(t for t, _ in setup.targets)), setup.limits, setup.bindings, now=room.now)
    grant, mandate = await room.store.activate_grant(room.turn, draft.id, draft.revision, draft.digest, "chart:fixture", now=room.now)
    await room.adapter.activated(room.store, room.turn, grant, mandate)
    doors = controls(room.projects, room.library, room.config, Limits(16000, 16), room.adapter, room.bench)
    await doors["learning_prepare"](room.store, OWNER, {"book": "linear algebra done right", "chapter": "3"})
    beside = TaskRunner(room.tasks, lambda: room.store, (room.adapter,), lease=private_lease(), backend=room.backend, journal=room.journal,
                        clock=lambda: room.now, background=True)
    room.runner.exclude(beside.served)
    room.runner._adapters, room.runner._by_operation = [], {}  # the ladder's runner serves nothing of the learning module's here
    async with room.lock:  # the conversation's lease, held throughout: a turn in progress
        results = []
        for _ in range(240):
            report = await beside.step(now=room.now)
            if report is None:
                room.now += 31
                if results and results[-1] == "idle":
                    break
                results.append("idle")
                continue
            results.append(report.result)
            beside.interrupt()
            room.now += 1
        ladder_saw = await room.runner.step(now=room.now)
    project = room.projects.get("linear algebra")
    assert project is not None
    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (f"chapter:{project.id}:linear-algebra-done-right-4th:3",)))[0].payload
    check("with the conversation's lease held throughout and the owner interrupting after every step, the chapter is read and its sheets written by the background runner alone",
          chapter["status"] == "prepared" and "done" in results and not any(r.startswith("abandoned") for r in results) and ladder_saw is None
          and all(t.attempts == 0 for t in await room.store.list(OWNER)))
    await room.close()


async def review_fix_checks(root: Path) -> None:
    """The seven defects of reports/2026-09-10-learning-code-review.md, each pinned as fixed."""
    print("\nthe 2026-09-10 review's seven defects")
    from types import SimpleNamespace
    from unittest.mock import patch
    from ciel.brain.extract import ExtractionLimits
    from ciel.learning import (book_target, chapter_range, check_publish_path, cut_segments, publish_request, read_with_includes, register, reveal,
                               segment_request, study_folder, submit_set, with_study_root)
    from ciel.task_runner import StepContext

    def context(step: Step, extract: Any) -> StepContext:
        return StepContext(SimpleNamespace(id="review-task", next_step=step), None, (), 1001.0, extract, True, limits=ExtractionLimits(32000, 60.0, 0.5))

    async def no_model(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("this path must not call a model")

    room = Room(root / "review", images="always")
    await room.open()
    room.projects.write("linear algebra", "reading", description="Math 110")
    await register(room.projects, room.store, OWNER, room.library, room.config, Limits(), project="linear algebra", path=str(room.pdf),
                   title="Review Book", edition="1st", aliases="Review", subject="110")
    book = (await every(room.store, OWNER, "book:"))[0].payload
    project_id, slug = book["project"], book["slug"]
    project = room.projects.get("linear algebra")
    assert project is not None
    check("4: the study folder is the Mac's: the book's folder lies under the root the Mac answered, whatever the hub's home",
          str(book["folder"]).startswith(str(room.home / "Berkeley" / "Math")) and with_study_root({"pages": 1}, Path("~/Berkeley/Math"))["study_root"].startswith(str(Path.home())))
    with patch("pathlib.Path.home", return_value=Path("/home/ciel")):
        hub_default = study_folder(LearningConfig(enabled=True), "110", "Review Book", "1st")
        from_mac = study_folder(LearningConfig(enabled=True), "110", "Review Book", "1st", str(room.home / "Berkeley" / "Math"))
    accepted, _ = check_publish_path(str(from_mac / "Definitions" / "chapter-03.tex"), home=room.home, state_dir=room.state, study_root=Path(room.config.study_root), forbidden=frozenset())
    check("4: a hub expanding the tilde in its own home would name a folder the Mac refuses; the Mac's answer names one it accepts",
          str(hub_default).startswith("/home/ciel/") and accepted is not None)

    chapter_key = record_key("chapter", project_id, slug, 3)
    await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (RecordWrite(chapter_key, {"kind": "chapter", "project": project_id, "slug": slug, "chapter": 3,
                                                                                          "first": 3, "last": 4, "status": "reading", "version": 1, "segments": [[3, 4]],
                                                                                          "windows_done": [], "flagged": [], "sheets": {}}),)))

    class ScannedLibrary:
        async def pdf_pages(self, path: str, first: int, last: int, *, images: bool = False) -> dict[str, Any]:
            return {"pages": [{"page": n, "text": "", "image": __import__("base64").b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 30).decode()} for n in range(first, last + 1)]}

    seen: list[int] = []

    async def scanned_extract(system: str, payload: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        seen.append(len(kwargs.get("images", ())))
        if kwargs.get("images"):
            return {"items": [{"kind": "theorem", "number": "3.9", "title": "", "statement": "Read from the page image.", "page": 3, "continues": False, "confidence": "confident"}],
                    "conventions": ["from the image"], "unreadable_pages": []}
        return {"items": [], "conventions": [], "unreadable_pages": []}

    _, step = segment_request(book, 3, 3, 4, "", "")
    room.adapter._library = ScannedLibrary()
    first = await room.adapter._window(context(step, scanned_extract))
    await room.store.write_records(OWNER, first.records)
    second = await room.adapter._images(context(first.next_step, scanned_extract))
    await room.store.write_records(OWNER, second.records)
    room.adapter._library = room.library
    items = [r.payload for r in await every(room.store, OWNER, f"item:{project_id}:{slug}:3:")]
    chapter = (await room.store.records(OWNER, NAMESPACE_NAME, (chapter_key,)))[0].payload
    check("3: a scanned book reaches the image extractor even when the text found nothing, and the images discover the items",
          seen == [0, 2] and [i["id"] for i in items] == ["thm-3.9"] and items[0]["statement"] == "Read from the page image." and chapter["items"] == 1
          and first.next_step.operation == "learning.images" and second.evidence)

    problems = Path(book["folder"]) / "Problems"
    problems.mkdir(parents=True)

    async def released(name: str, text: str, count: int, **extra: Any) -> str:
        path = problems / f"{name}.tex"
        path.write_text(text)
        exam = {"kind": "exam", "project": project_id, "slug": slug, "set": name, "kind_of": "midterm", "status": "released", "minutes": 60, "points": count * 10,
                "problems": count, "coverage": "chapter 3", "calibration": "Book-based practice", "requested_at": 1000, "hints": [], "sheet": {"path": str(path)}, "files": {}}
        await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (RecordWrite(record_key("exam", project_id, slug, name), exam),)))
        return await submit_set(room.projects, room.store, OWNER, room.bench, room.config, Limits(), None, book="Review", set_name=name, now=1001)

    said = await released("midterm-001", "\\input{missing-answers}\n", 1)
    exam = (await room.store.records(OWNER, NAMESPACE_NAME, (record_key("exam", project_id, slug, "midterm-001"),)))[0].payload
    check("2: a submission whose include cannot be read is refused by name before anything is recorded, and the set stays open",
          "missing-answers could not be read" in said and exam["status"] == "released" and not await every(room.store, OWNER, record_key("submission", project_id, slug, "midterm-001", "")))
    (problems / "outside.tex").write_text("")
    files, why = await read_with_includes(project, room.bench, str(problems / "midterm-001.tex"), max_chars=100000)
    (problems / "midterm-001.tex").write_text("% \\input{commented-out}\n\\input{../../../../elsewhere}\n")
    _, why_out = await read_with_includes(project, room.bench, str(problems / "midterm-001.tex"), max_chars=100000)
    (problems / "midterm-001.tex").write_text("% \\input{commented-out}\nAn answer.\n")
    files, why_ok = await read_with_includes(project, room.bench, str(problems / "midterm-001.tex"), max_chars=100000)
    check("2: an include outside the study folder is refused by name, and a commented-out include is no dependency",
          "elsewhere lies outside the study folder" in why_out and why_ok == "" and [n for n, _ in files] == ["midterm-001.tex"])

    def answer(n: int, body: str) -> str:
        return f"\\begin{{namedquestion}}{{prob-{n}}}\nQuestion {n}\n\\begin{{framed}}\n{body}\n\\end{{framed}}\n\\end{{namedquestion}}\n"

    said = await released("midterm-002", answer(1, "A long written proof. " * 1700) + answer(2, "A complete second answer.") + answer(3, "A complete third answer."), 3)
    assert said.startswith("Submitted"), said
    writes = []
    for n in range(1, 4):
        writes.append(RecordWrite(record_key("question", project_id, slug, "midterm-002", n),
                                  {"kind": "question", "project": project_id, "slug": slug, "set": "midterm-002", "n": n, "status": "released", "attempts": 1,
                                   "kind_of": "proof", "points": 10, "question": "Give a proof.", "solution": "Private reference.", "rubric": [{"points": 10, "criterion": "A valid proof"}]}))
    await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, tuple(writes)))
    received: list[str] = []

    async def grade_seen(system: str, payload: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        received.append(payload)
        return {"grades": [{"id": ident, "score": 10, "explanation": "Valid argument."} for ident in __import__("re").findall(r"=== (prob-\d+) ", payload)]}

    grade_step = Step("read", "learning.grade", book_target(project_id, slug), (("project", project_id), ("slug", slug), ("set", "midterm-002")))
    outcome = await room.adapter._grade(context(grade_step, grade_seen))
    await room.store.write_records(OWNER, outcome.records)
    grade = outcome.records.writes[0].payload["grade"]
    check("1: a problem too long for one call is said to be ungraded and not counted, the others reach the model whole, and nothing is sliced",
          grade["problems"]["prob-1"].get("ungraded") and grade["problems"]["prob-2"]["score"] == 10 and grade["problems"]["prob-3"]["score"] == 10
          and len(received) == 1 and "=== prob-2 " in received[0] and "=== prob-3 " in received[0] and "=== prob-1 " not in received[0]
          and grade["total"] == 20 and grade["max"] == 20 and outcome.next_step.kind == "mutation")
    exam_record = (await room.store.records(OWNER, NAMESPACE_NAME, (record_key("exam", project_id, slug, "midterm-002"),)))[0].payload
    questions = [r.payload for r in await every(room.store, OWNER, record_key("question", project_id, slug, "midterm-002", ""))]
    rendered = __import__("ciel.learning", fromlist=["render_grade"]).render_grade(exam_record, questions, book, now=1001)
    check("1: the grade file says which problem was not graded and keeps it out of the total", "prob-1 — not graded" in rendered and "Total: 20 / 20" in rendered and "Not graded and not counted: prob-1" in rendered)
    misses: list[str] = []

    async def grade_missing(system: str, payload: str, schema: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        misses.append(payload)
        return {"grades": []}

    await released("midterm-004", answer(1, "Short.") + answer(2, "Short too."), 2)
    await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, tuple(
        RecordWrite(record_key("question", project_id, slug, "midterm-004", n), {"kind": "question", "project": project_id, "slug": slug, "set": "midterm-004", "n": n, "status": "released",
                                                                                "attempts": 1, "kind_of": "proof", "points": 10, "question": "Q", "solution": "S", "rubric": [{"points": 10, "criterion": "c"}]}) for n in (1, 2))))
    step4 = Step("read", "learning.grade", book_target(project_id, slug), (("project", project_id), ("slug", slug), ("set", "midterm-004")))
    one = await room.adapter._grade(context(step4, grade_missing))
    await room.store.write_records(OWNER, one.records)
    two = await room.adapter._grade(context(step4, grade_missing))
    await room.store.write_records(OWNER, two.records)
    graded4 = two.records.writes[0].payload["grade"]["problems"]
    check("1: a problem the model's answer left out is carried to the next call once, then said to be ungraded, never scored zero in silence",
          one.next_step.operation == "learning.grade" and "prob-1" not in one.records.writes[0].payload["grade"]["problems"]
          and graded4["prob-1"].get("ungraded") and "two grading calls" in graded4["prob-1"]["explanation"] and len(misses) == 2)

    exam = (await room.store.records(OWNER, NAMESPACE_NAME, (record_key("exam", project_id, slug, "midterm-001"),)))[0].payload
    for status in ("generating", "releasing", "released", "submitted", "grading"):
        await room.store.write_records(OWNER, RecordSet(NAMESPACE_NAME, (
            RecordWrite(record_key("exam", project_id, slug, "midterm-003"), {**exam, "set": "midterm-003", "status": status, "sheet": {}}),
            RecordWrite(record_key("question", project_id, slug, "midterm-003", 1), {"kind": "question", "project": project_id, "slug": slug, "set": "midterm-003", "n": 1, "status": "composed",
                                                                                     "attempts": 1, "kind_of": "proof", "points": 10, "question": "Q", "solution": "PRIVATE UNRELEASED SOLUTION", "rubric": []}))))
        said = await reveal(room.projects, room.store, OWNER, book="Review", set_name="midterm-003", item="prob-1", now=1001)
        if "PRIVATE UNRELEASED SOLUTION" in said or not said.startswith("Not while"):
            check(f"5: a set's solution is refused while it is {status}", False)
    check("5: a set's solution is refused in every state but graded", True)

    span, sections = chapter_range([["Chapter 3 Linear Maps", 10], ["3A Maps", 12], ["3B More", 15], ["Chapter 4 Polynomials", 20]], 3, 30)
    segments = cut_segments(*span, sections, room.config, 16)
    covered = sorted({page for a, b, _ in segments for page in range(a, b + 1)})
    check("6: a chapter whose first section starts after its opening is read from its first page, every page once, and gaps between sections too",
          span == (10, 19) and covered == list(range(10, 20)) and segments[0][0] == 10
          and sorted({page for a, b, _ in cut_segments(1, 12, [("3B", 5, 8)], room.config, 16) for page in range(a, b + 1)}) == list(range(1, 13)))

    _, publish_step = publish_request(book, 3, 1)
    plan = await room.adapter.plan(context(publish_step, no_model))
    result = await room.library.publish(plan.payload["path"], plan.payload["content"])
    assert not result.get("error")
    reconciled = await room.adapter.reconcile(context(publish_step, no_model), SimpleNamespace(recipe=plan.recipe))
    verified = await room.adapter._verify(context(plan.verify, no_model))
    kept = next(w.payload for w in verified.records.writes if w.key == chapter_key)
    check("7: a publication interrupted before its record checkpoint is recovered by the verification from the planned path and digest, and the next sheet follows",
          reconciled.verdict == "applied" and not verified.wait and verified.next_step is not None and verified.next_step.kind == "mutation"
          and dict(verified.next_step.arguments)["sheet"] == "theorems" and kept["sheets"]["definitions"]["digest"] == plan.payload["digest"]
          and kept["sheets"]["definitions"]["state"] == "verified" and [e.criterion_id for e in verified.evidence] == ["definitions"])
    await room.close()


def typeset_checks(root: Path) -> None:
    """What the model wrote is made to compile: the shapes the first live sheets failed on."""
    print("\nwhat the model wrote, made to compile")
    import shutil
    import subprocess
    from ciel.learning import render_exam, render_sheet, tex_safe

    check("Unicode math becomes LaTeX commands in math mode, in text and inside existing math alike",
          tex_safe("The symbol \\oplus (⊕) denotes a direct sum.") == "The symbol $\\oplus$ $(\\oplus)$ denotes a direct sum."
          and tex_safe("addition in 𝐅ⁿ") == "addition in $\\mathbf{F}^{n}$" and tex_safe("$α ∈ 𝐅$") == "$\\alpha \\in \\mathbf{F}$")
    check("a math command, a superscript, or a subscript left outside math is set in math, punctuation kept outside",
          tex_safe("If x, y \\in \\mathbf{F}^n, then x + y = y + x.") == "If x, y $\\in$ $\\mathbf{F}^n$, then x + y = y + x."
          and tex_safe("Definition (α^m).") == "Definition $(\\alpha^m)$." and tex_safe("the pair (x_1, y).") == "the pair ($x_1$, y).")
    check("text specials are escaped, dashes and quotes are typeset, an unbalanced dollar is closed, and an unknown character is marked, never dropped",
          tex_safe("a 100% chance & item #1") == "a 100\\% chance \\& item \\#1" and tex_safe("the number −1, 3 – 4, ‘so’") == "the number -1, 3 -- 4, `so'"
          and tex_safe("unbalanced $x") == "unbalanced $x$" and "[U+2603]" in tex_safe("snow ☃ man") and tex_safe("$x$ with {0} playing") == "$x$ with {0} playing")
    book = {"title": "Linear Algebra Done Right", "edition": "4th edition"}
    chapter = {"chapter": 1, "first": 15, "last": 40, "flagged": []}
    items = [{"id": "thm-1.14", "kind": "theorem", "number": "1.14", "title": "commutativity of addition in 𝐅ⁿ",
              "statement": "If x, y \\in \\mathbf{F}^n, then x + y = y + x.", "page": 21, "provisional": False},
             {"id": "thm-1.32", "kind": "theorem", "number": "1.32", "title": "the number −1 times a vector", "statement": "$(-1)v = -v$ for every $v \\in V$; 100% of the time.", "page": 30, "provisional": False},
             {"id": "def-1-m", "kind": "definition", "number": "", "title": "α^m", "statement": "PRIVATE", "page": 18, "provisional": False},
             {"id": "def-1.1", "kind": "definition", "number": "1.1", "title": "complex numbers, C", "statement": "PRIVATE", "page": 16, "provisional": False}]
    conventions = ["The symbol \\oplus (⊕), a plus sign inside a circle, denotes a direct sum of subspaces.",
                   "The symbol \\iff (⟺) means 'if and only if'; it can also be read as 'is equivalent to'.",
                   "Sums of subspaces are analogous to unions (with {0} playing the role of the empty intersection)."]
    theorems = render_sheet("theorems", book, chapter, items, conventions, now=1_800_000_000)
    definitions = render_sheet("definitions", book, chapter, items, conventions, now=1_800_000_000)
    exam = render_exam({"set": "midterm-001", "kind_of": "midterm", "minutes": 60, "points": 20, "coverage": "chapter 1", "calibration": "Book-based practice."},
                       [{"n": 1, "status": "released", "points": 20, "kind_of": "proof", "question": "Prove that 𝐅ⁿ is a vector space and that α^m ∈ 𝐅 for all m ≥ 1."}],
                       book, now=1_800_000_000)
    check("the rendered sheets carry no raw Unicode symbol and no stray math", not any(ord(c) > 127 for c in theorems + definitions + exam)
          and "$\\oplus$" in theorems and "{$\\alpha^m$}" in definitions and "$\\mathbf{F}^{n}$" in exam)
    if shutil.which("latexmk") is None:
        print("  (latexmk is not on this machine; the compile is pinned where it is)")
        return
    for name, text in (("theorems", theorems), ("definitions", definitions), ("exam", exam)):
        folder = root / "typeset" / name
        folder.mkdir(parents=True)
        (folder / "sheet.tex").write_text(text)
        done = subprocess.run(["latexmk", "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "sheet.tex"], cwd=folder,
                              capture_output=True, text=True, timeout=180)
        errors = [line for line in (folder / "sheet.log").read_text(errors="replace").split("\n") if line.startswith("! ")] if (folder / "sheet.log").exists() else []
        check(f"the {name} sheet with the shapes the first live sheets failed on compiles under pdflatex with shell escape off",
              done.returncode == 0 and (folder / "sheet.pdf").exists() and not errors)


async def wire_checks() -> None:
    print("\nthe hub without its Mac")
    from ciel.config import HubConfig, WebConfig
    from ciel.hub.rpc import RemoteLibrary
    from ciel.hub.server import HubServer
    from ciel.learning import find

    server = HubServer(WebConfig(), replace(HubConfig(), speak_timeout_s=1.0))
    out = await find(RemoteLibrary(server), "axler")
    check("a hub with no spoke seated says the Mac could not be reached instead of reading its own disk", "could not be reached" in out)


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ciel-learning-probe-") as tmp:
        config_checks(Path(tmp))
        typeset_checks(Path(tmp))
        place_checks()
        await library_checks(Path(tmp))
        await study_checks(Path(tmp))
        await estimator_checks()
        await chapter_checks(Path(tmp))
        await review_fix_checks(Path(tmp))
        await background_checks(Path(tmp))
        await wire_checks()
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
