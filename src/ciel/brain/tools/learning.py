"""Learning tools — the study workspace, from the private brain.

Same principle as the project tools: the descriptions are the training.
Nothing else tells the model that a book is registered only on the owner's
confirmed edition, that a bookmark moves only on the owner's words, or that
closing a book is the one destructive act here and is asked about.

**Every write goes through the task controller's door.** Registering,
bookmarking, and closing are feature controls (``learning_register``,
``learning_bookmark``, ``learning_close``): the controller admits the live
private owner turn, runs the control, and journals it, exactly as it does
the calendar feature's. Reads take the store through the same admission
and journal nothing. These tools exist only on the private brain; the
public channel's client never has them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from claude_agent_sdk import tool

from ciel.config import LearningConfig
from ciel.learning import Library, Limits, feedback_text, find, resolve_book, status
from ciel.projects import ProjectStore
from ciel.task_context import TaskBinding
from ciel.task_controls import TaskController
from ciel.tasks import TaskConflict, TaskStoreError

log = logging.getLogger(__name__)

_projects: ProjectStore | None = None
_library: Library | None = None
_config: LearningConfig | None = None
_limits = Limits()
_bench: Any = None
_controller: TaskController | None = None
_context: Callable[[], TaskBinding | None] = lambda: None


def bind_learning(projects: ProjectStore | None, library: Library | None, config: LearningConfig | None, limits: Limits | None = None,
                  bench: Any = None) -> None:
    """The notebook, the Mac's books, the section, and the workbench a sheet
    is read through; None withholds the tools' effects."""
    global _projects, _library, _config, _limits, _bench
    _projects, _library, _config, _bench = projects, library, config, bench
    if limits is not None:
        _limits = limits


def bind_learning_tasks(controller: TaskController | None, context: Callable[[], TaskBinding | None]) -> None:
    """The controller whose door the writes go through, and the turn's authority."""
    global _controller, _context
    _controller, _context = controller, context


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def _ready() -> tuple[Any, Any, str]:
    if _projects is None or _library is None or _config is None:
        return None, None, "The learning module is not available right now."
    if _controller is None:
        return None, None, "The learning module's records are not available right now."
    binding = _context()
    try:
        store = _controller.store_for(binding)
    except (TaskConflict, TaskStoreError) as exc:
        return None, None, str(exc)
    return binding, store, ""


async def _control(operation: str, args: dict[str, Any]) -> str:
    binding, _, why = _ready()
    if why:
        return why
    assert _controller is not None
    try:
        result = await _controller.apply(binding, operation, args)
    except (ValueError, RuntimeError) as exc:
        return str(exc)
    return str(result.get("said") or result)


@tool(
    "find_book",
    (
        "Look for a book's PDF on the Mac by a few words of its title or filename — 'find Axler' — under the study "
        "roots only, by filename and PDF metadata, never page text. Offers candidates; it binds nothing and never "
        "chooses. Show the owner the candidates, confirm which one and the edition (from the title page), then register_book."
    ),
    {"query": str},
)
async def find_book(args: dict[str, Any]) -> dict[str, Any]:
    if _library is None or _config is None:
        return _text("The learning module is not available right now.")
    query = " ".join(str(args.get("query") or "").split())
    if not query:
        return _text("A word or two of the title or filename is needed.")
    return _text(await find(_library, query))


@tool(
    "register_book",
    (
        "Register a book for reading on a course's project, only once the owner has confirmed the PDF and its edition in "
        "their own words. `project` is the course (its exact name or alias); `path` the PDF on the Mac (from find_book or the "
        "owner); `title` the book as the owner calls it; `edition` as confirmed ('4th edition', '2015'); `aliases` other "
        "names the owner uses, comma-separated ('Axler, LADR'); `subject` the class or subject folder for its worksheets "
        "(defaults to the project's name). Binds the PDF and a study folder to the project and keeps the book's facts. "
        "Never register a book the owner did not name or an edition they did not confirm."
    ),
    {"project": str, "path": str, "title": str, "edition": str, "aliases": str, "subject": str},
)
async def register_book(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_register", {k: str(args.get(k) or "") for k in ("project", "path", "title", "edition", "aliases", "subject")}))


@tool(
    "open_study",
    (
        "Resume a book — 'let's read Axler', 'let's work on linear algebra'. `book` is the book's name or alias, `project` "
        "the course; either alone is enough when it is unambiguous. Answers with the book, its bookmark in the owner's own "
        "words, and where that is in the PDF, and marks the book the project's current one so a bare set_bookmark lands on it. "
        "Several possible books are a question, never a guess. open_document with role 'book' opens the PDF itself."
    ),
    {"project": str, "book": str},
)
async def open_study(args: dict[str, Any]) -> dict[str, Any]:
    _, store, why = _ready()
    if why:
        return _text(why)
    assert _projects is not None and _controller is not None
    located, question = await resolve_book(_projects, store, _controller.config.owner,
                                          project=str(args.get("project") or ""), book=str(args.get("book") or ""))
    if located is None:
        return _text(question)
    if not located.resource.current:
        try:
            _projects.select(located.project.name, located.resource.key)
        except ValueError:
            log.info("the opened book could not be marked current on %s", located.project.name)
    return _text(await status(store, _controller.config.owner, located, bench=_bench))


@tool(
    "set_bookmark",
    (
        "Keep where the owner is in a book, in their own words, only when they say so — 'I'm at 3B', 'page 84', "
        "'theorem 3.21', 'pdf page 90', 'chapter 3'. `where` is their words; `book` and `project` as in open_study "
        "(the project's current book when neither is given). A printed page is placed through the PDF's own page numbers; "
        "a PDF without them asks for a PDF page or a section instead; a bare number is asked about. The bookmark never "
        "moves on its own: not with time, not with worksheets, only with the owner's words."
    ),
    {"project": str, "book": str, "where": str},
)
async def set_bookmark(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_bookmark", {k: str(args.get(k) or "") for k in ("project", "book", "where")}))


@tool(
    "study_status",
    (
        "Where the owner stands with a book, without changing anything: the book, its edition and pages, the bookmark and "
        "when it was said, the PDF and the study folder. `book` and `project` as in open_study."
    ),
    {"project": str, "book": str},
)
async def study_status(args: dict[str, Any]) -> dict[str, Any]:
    _, store, why = _ready()
    if why:
        return _text(why)
    assert _projects is not None and _controller is not None
    located, question = await resolve_book(_projects, store, _controller.config.owner,
                                          project=str(args.get("project") or ""), book=str(args.get("book") or ""))
    if located is None:
        return _text(question)
    return _text(await status(store, _controller.config.owner, located, bench=_bench))


@tool(
    "close_book",
    (
        "Close a registered book at the owner's explicit request: its bookmark and every record of the work on it are "
        "deleted, its tasks cancelled, and its bindings removed from the project. Every file stays — the PDF and the study "
        "folder are untouched. This is asked about before it runs; never call it to tidy up, only on the owner's words. "
        "`book` and `project` as in open_study."
    ),
    {"project": str, "book": str},
)
async def close_book(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_close", {k: str(args.get(k) or "") for k in ("project", "book")}))


@tool(
    "prepare_chapter",
    (
        "Queue the worksheets for one chapter of a registered book, at the owner's request — 'prepare chapter 3', 'let's work on "
        "chapter 3'. `chapter` is its number; `pages` is optional ('pages 120-171' as printed, or 'pdf pages 130-181') when the "
        "book's outline does not say where the chapter is; `book` and `project` as in open_study; `replace` 'true' only when the "
        "owner asks for new sheets over ones already prepared (the old ones stay). Nothing is read or written in this turn: the "
        "chapter is queued under the worksheets standing grant, read from the book in windows in the background, and a "
        "definitions sheet and a theorems sheet land under the study folder as new files; a notice says when. Without the grant "
        "approved for this book, this says so and queues nothing. A prepared chapter is reopened, never regenerated."
    ),
    {"project": str, "book": str, "chapter": str, "pages": str, "replace": str},
)
async def prepare_chapter(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_prepare", {k: str(args.get(k) or "") for k in ("project", "book", "chapter", "pages", "replace")}))


@tool(
    "check_sheet",
    (
        "Queue a review of the owner's written answers on a prepared chapter's sheet, at their request — 'check my definitions', "
        "'check theorem 3.21'. `chapter` is the number; `sheet` is 'definitions' or 'theorems'; `items` optionally names ids to review "
        "('thm-3.21, lem-3.22'), else every written box on the sheet. The sheet is read now only to see there is something written: an "
        "empty box is not reviewed and is said. The review runs in the background, assesses the sheet as saved then — held to the "
        "book's statement or definition and the chapter's conventions, accepting equivalent definitions and alternative proofs — and "
        "writes a numbered feedback file beside the sheet that names gaps and never supplies the argument; a notice says when. The "
        "sheet itself is never edited. `book` and `project` as in open_study."
    ),
    {"project": str, "book": str, "chapter": str, "sheet": str, "items": str},
)
async def check_sheet(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_review", {k: str(args.get(k) or "") for k in ("project", "book", "chapter", "sheet", "items")}))


@tool(
    "study_hint",
    (
        "Queue one hint on one item, only when the owner asks for a hint outright — 'give me a hint on 3.21'. `item` is the id on the "
        "sheet (thm-3.21, def-3-null-space); `chapter` its number. The hint is one nudge that names no step, no object, and none of the "
        "book's words, written as a numbered hint file beside the sheet in the background and recorded as assistance on the item; "
        "a notice says when. Never call this to be helpful on your own; a review's feedback is where gaps are named."
    ),
    {"project": str, "book": str, "chapter": str, "item": str, "set": str},
)
async def study_hint(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_hint", {k: str(args.get(k) or "") for k in ("project", "book", "chapter", "item", "set")}))


@tool(
    "reveal_answer",
    (
        "Give the answer to one item, only when the owner asks for the answer outright — 'just tell me the definition of null space', "
        "'I give up on 3.21'. A definition is the book's own words from the record; a theorem is where the book proves it; a problem of a "
        "graded set (`set`, `item` prob-2) is its solution from the record, and never while the set is open. The item is "
        "recorded as revealed and no longer counts as the owner's own work. Never call this for a hint, a check, or on your own judgement."
    ),
    {"project": str, "book": str, "chapter": str, "item": str, "set": str},
)
async def reveal_answer(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_reveal", {k: str(args.get(k) or "") for k in ("project", "book", "chapter", "item", "set")}))


@tool(
    "read_feedback",
    (
        "Read the latest feedback on a chapter's sheet, to speak it when the owner asks — 'what did you think of my proofs', 'read me the "
        "feedback'. Returns the feedback file's text with each item's verdict and named gaps, and where the sheet stands now (edited since "
        "assessed reads as stale). Speak the verdicts and the gaps in the owner's words; never argue the mathematics, never supply a step. "
        "`chapter` and `sheet` as in check_sheet; or `set` ('midterm-001') for a graded set's grade file."
    ),
    {"project": str, "book": str, "chapter": str, "sheet": str, "set": str},
)
async def read_feedback(args: dict[str, Any]) -> dict[str, Any]:
    _, store, why = _ready()
    if why:
        return _text(why)
    assert _projects is not None and _controller is not None
    if _bench is None:
        return _text("The owner's machine is not reachable right now, so the feedback cannot be read.")
    return _text(await feedback_text(_projects, store, _controller.config.owner, _bench, project=str(args.get("project") or ""),
                                     book=str(args.get("book") or ""), chapter=str(args.get("chapter") or ""), sheet=str(args.get("sheet") or ""),
                                     set_name=str(args.get("set") or "")))


@tool(
    "practice",
    (
        "Queue a practice question at the owner's request — 'give me a practice problem on null spaces', 'a harder one'. `topic` in the "
        "owner's words; `difficulty` a word ('easy', 'exam', 'hard', or a kind: 'definition', 'example', 'proof'); `chapters` to draw on "
        "('chapters 2-3'), else the prepared chapters up to the bookmark; `problems` how many, one by default. Each question is written from "
        "the book's material, solved independently from the question alone, and refereed before release; the sheet lands under Problems as "
        "a new file (practice-001.tex) and a notice says. Solutions and rubrics stay on record and never on the sheet. Grading is by "
        "submit_exam on the set, like a midterm."
    ),
    {"project": str, "book": str, "topic": str, "difficulty": str, "chapters": str, "problems": str},
)
async def practice(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_practice", {k: str(args.get(k) or "") for k in ("project", "book", "topic", "difficulty", "chapters", "problems")}))


@tool(
    "mock_exam",
    (
        "Queue a mock midterm at the owner's request — 'set me a midterm on chapters 1 to 3'. Defaults: five problems mixing definitions, "
        "examples or counterexamples, and proofs, sixty minutes, one hundred points; `minutes`, `points`, `problems`, and `chapters` change "
        "them at the owner's word. `calibration` names a bound syllabus, assignment set, or sample exam resource to calibrate to; without one "
        "the sheet says book-based practice and never claims to match an instructor. Each problem is written, solved independently, and "
        "refereed before release; the sheet lands under Problems (midterm-001.tex) and a notice says. Solutions and rubrics never reach the "
        "sheet; a hint during the exam is recorded on the grade; grading follows submit_exam."
    ),
    {"project": str, "book": str, "chapters": str, "minutes": str, "points": str, "problems": str, "calibration": str},
)
async def mock_exam(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_exam", {k: str(args.get(k) or "") for k in ("project", "book", "chapters", "minutes", "points", "problems", "calibration")}))


@tool(
    "submit_exam",
    (
        "Submit a released practice or midterm set at the owner's explicit word — 'submit the midterm', 'grade my practice'. `set` is its "
        "name (midterm-001, practice-002). The sheet and every include are read now and kept whole on record before anything is "
        "acknowledged; past the size cap the submission is refused, never truncated. Grading runs in the background from that record, "
        "never from the sheet again: partial credit per rubric criterion, an explanation per problem, hints recorded as assistance, a "
        "courtesy copy of the submission and a numbered grade file under Problems; a notice says. A submission is final."
    ),
    {"project": str, "book": str, "set": str},
)
async def submit_exam(args: dict[str, Any]) -> dict[str, Any]:
    return _text(await _control("learning_submit", {k: str(args.get(k) or "") for k in ("project", "book", "set")}))


LEARNING_TOOLS = [find_book, register_book, open_study, set_bookmark, study_status, close_book, prepare_chapter, check_sheet, study_hint, reveal_answer,
                  read_feedback, practice, mock_exam, submit_exam]

__all__ = ["LEARNING_TOOLS", "bind_learning", "bind_learning_tasks", "find_book", "register_book", "open_study", "set_bookmark",
           "study_status", "close_book", "prepare_chapter", "check_sheet", "study_hint", "reveal_answer", "read_feedback", "practice", "mock_exam", "submit_exam"]
