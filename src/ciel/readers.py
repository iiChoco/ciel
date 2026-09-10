"""Readers — what a document says about where the work stands, read as data.

A project's working document is the evidence of its state: which questions
have an answer written, which sections are still a heading, where a TODO
was left. Nothing here compiles, executes, or interprets: a reader is a
deterministic pass over text that builds a *roster* of the document's parts
and, for each, a *claim* of record — ``not started``, ``in progress``,
``answer written``, or ``unclear`` — with the line it rests on and an
excerpt as evidence. What the model may later add on top of a reading is a
separate, isolated step; policy decides, never confidence.

**Read, never run.** LaTeX is parsed for a handful of environments the
owner's template uses (``numedquestion``, ``namedquestion``, ``alphaparts``,
``arabicparts``, ``enumerate``, ``framed``, ``\\item``, ``\\answerbox``),
with ``\\begin {x}`` and ``\\begin{x}`` both spellings, comments dropped
(an escaped ``\\%`` is not a comment), verbatim left opaque, and no macro
expanded. Markdown is read by headings. Anything else is located and named,
not understood.

**An include is followed within the roots the caller sets.** ``\\input``
and ``\\include`` are resolved through a loader the caller supplies — the
loader enforces the registered folder, the depth and count limits, and
returns None for what it will not or cannot read — and a reference that
does not resolve is a *gap* in coverage, named in the reading, never a
silent absence. A cycle is a gap too.

**Counts describe written coverage, not correctness or effort.** An answer
that merely repeats the statement is not started; an empty box or the
template's ``\\answerbox`` is not started; a TODO, a ``??``, or a ``\\todo``
is in progress; a part the parser could not follow is unclear. Whether the
roster is complete — every question the assignment set — is unknown to a
reader that only saw the working file, and the reading says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

CLAIMS = ("not started", "in progress", "answer written", "unclear")

_BEGIN = re.compile(r"\\begin\s*\{\s*([A-Za-z*]+)\s*\}(?:\s*\{([^}]*)\})?")
_END = re.compile(r"\\end\s*\{\s*([A-Za-z*]+)\s*\}")
_ITEM = re.compile(r"\\item\b")
_INCLUDE = re.compile(r"\\(?:input|include)\s*\{\s*([^}]*?)\s*\}")
_ANSWERBOX = re.compile(r"\\answerbox\s*\{[^}]*\}")
_TODO = re.compile(r"(?:\\todo\b|\bTODO\b|\bFIXME\b|\bTBD\b|\?\?|\\ldots\s*$|\.\.\.\s*$)", re.IGNORECASE)
_NOISE = re.compile(r"\\(?:vspace|hspace|vfill|hfill|newpage|noindent|smallskip|medskip|bigskip)\*?(?:\s*\{[^}]*\})?|\\blacksquare|\\qed\b|\$\s*\$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_TODO = re.compile(r"(?:\bTODO\b|\bTBD\b|\bFIXME\b|\[ \]|\?\?)", re.IGNORECASE)

QUESTION_ENVS = ("numedquestion", "namedquestion")
PARTS_ENVS = ("alphaparts", "arabicparts", "enumerate")
ANSWER_ENV = "framed"

Loader = Callable[[str], "str | None"]
"""Given the reference as written in the document, the included text, or
None when it is missing, out of bounds, or past the limits — the caller's
rule, and the reader reports a gap either way."""


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    """A question number ("3"), a part ("3a"), or a heading path ("2.1")."""
    kind: str  # "question" | "part" | "section"
    title: str
    """The statement's or heading's first words, for the owner to recognise it."""
    claim: str
    evidence: str
    """The words the claim rests on, short and quoted from the document."""
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class Reading:
    reader: str  # "latex" | "markdown"
    version: int
    items: tuple[Item, ...]
    files: tuple[str, ...]
    """Every file the reading covered, the main one first."""
    gaps: tuple[str, ...] = ()
    """What the reader could not follow or understand, in words."""
    complete: bool | None = None
    """Whether the roster is the whole assignment: None means unknown."""

    def counts(self) -> dict[str, int]:
        leaves = [i for i in self.items if not any(o.id.startswith(i.id) and o.id != i.id and o.kind == "part" for o in self.items)]
        result = {claim: 0 for claim in CLAIMS}
        for item in leaves:
            result[item.claim] += 1
        result["total"] = len(leaves)
        return result


def reader_for(path: str) -> str | None:
    """Which reader a file gets, by its suffix; None is unsupported."""
    lower = path.lower()
    if lower.endswith((".tex", ".ltx", ".latex")):
        return "latex"
    if lower.endswith((".md", ".markdown", ".mdown")):
        return "markdown"
    return None


def read(text: str, path: str, loader: Loader | None = None) -> Reading | None:
    reader = reader_for(path)
    if reader == "latex":
        return read_latex(text, path, loader)
    if reader == "markdown":
        return read_markdown(text, path)
    return None


def describe(reading: Reading) -> str:
    """The reading in the owner's words: counts, then what is still open."""
    counts = reading.counts()
    total = counts["total"]
    unit = "part" if any(i.kind == "part" for i in reading.items) else ("question" if reading.reader == "latex" else "section")
    parts = [f"{total} {unit}{'s' if total != 1 else ''}: {counts['answer written']} written, "
             f"{counts['in progress']} in progress, {counts['not started']} not started"
             + (f", {counts['unclear']} unclear" if counts["unclear"] else "")]
    open_items = [i for i in reading.items if i.claim != "answer written"
                  and not any(o.id.startswith(i.id) and o.id != i.id and o.kind == "part" for o in reading.items)]
    if open_items:
        parts.append("Still open: " + "; ".join(f"{i.id} ({i.claim}, line {i.line}" + (f", {i.title!r}" if i.title else "") + ")" for i in open_items[:12])
                     + ("; …" if len(open_items) > 12 else ""))
    if reading.gaps:
        parts.append("Gaps: " + "; ".join(reading.gaps[:6]))
    parts.append("Roster completeness is " + ("known" if reading.complete else "unknown: only the working file was read" if reading.complete is None else "incomplete"))
    return ". ".join(parts) + "."


# ── LaTeX ────────────────────────────────────────────────────────────────────

def _strip_comments(text: str) -> str:
    """Drop ``%`` comments line by line, keeping line count; ``\\%`` stays."""
    out = []
    for line in text.split("\n"):
        cut = None
        i = 0
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def _blank_verbatim(text: str) -> str:
    """Verbatim content is opaque: replaced by spaces, newlines kept."""
    def blank(match: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    return re.sub(r"\\begin\s*\{verbatim\*?\}.*?\\end\s*\{verbatim\*?\}", blank, text, flags=re.DOTALL)


@dataclass
class _Node:
    kind: str  # "question" | "part"
    id: str
    file: str
    line: int
    title_source: list[str] = field(default_factory=list)
    answers: list[tuple[str, int, str]] = field(default_factory=list)  # (text, line, file)
    parts: list["_Node"] = field(default_factory=list)
    unclear: str = ""

    def title(self) -> str:
        words = " ".join(" ".join(self.title_source).split())
        words = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", words)
        words = " ".join(words.replace("{", " ").replace("}", " ").split())
        return words[:100]


_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def read_latex(text: str, path: str, loader: Loader | None = None) -> Reading:
    files: list[str] = [path]
    gaps: list[str] = []
    questions: list[_Node] = []
    counter = 0
    stack: list[tuple[str, str, int]] = []  # (env, file, line)
    node_stack: list[_Node] = []
    capture: list[str] | None = None
    capture_at: tuple[str, int] | None = None
    seen_files: set[str] = {path}

    def current() -> _Node | None:
        return node_stack[-1] if node_stack else None

    def walk(source: str, file: str, depth: int) -> None:
        nonlocal counter, capture, capture_at
        cleaned = _blank_verbatim(_strip_comments(source))
        pos = 0
        line = 1
        pattern = re.compile(r"\\begin\s*\{|\\end\s*\{|\\item\b|\\(?:input|include)\s*\{|\\answerbox\s*\{")
        while True:
            match = pattern.search(cleaned, pos)
            chunk = cleaned[pos: match.start() if match else len(cleaned)]
            if chunk.strip():
                if capture is not None:
                    capture.append(chunk)
                elif current() is not None:
                    current().title_source.append(chunk) if not current().answers else None
            line += chunk.count("\n")
            if match is None:
                break
            token = match.group(0)
            pos = match.start()
            if token.startswith("\\answerbox"):
                m = _ANSWERBOX.match(cleaned, pos)
                end = m.end() if m else match.end()
                target = current()
                if capture is not None:
                    capture.append("\\answerbox")
                elif target is not None:
                    target.answers.append(("\\answerbox", line, file))
                pos = end
                continue
            if token.startswith("\\item"):
                target = current()
                if capture is None and target is not None and stack and stack[-1][0] in PARTS_ENVS:
                    owner = target if target.kind == "question" else (node_stack[-2] if len(node_stack) > 1 else target)
                    if target.kind == "part":
                        node_stack.pop()
                        owner = current() or owner
                    index = len(owner.parts)
                    part_id = owner.id + (_LETTERS[index] if index < 26 else str(index + 1)) if stack[-1][0] != "arabicparts" else f"{owner.id}.{index + 1}"
                    part = _Node("part", part_id, file, line)
                    owner.parts.append(part)
                    node_stack.append(part)
                elif capture is not None:
                    capture.append("\\item")
                pos = match.end()
                continue
            if token.startswith("\\input") or token.startswith("\\include"):
                m = _INCLUDE.match(cleaned, pos)
                pos = m.end() if m else match.end()
                if m is None:
                    continue
                reference = m.group(1)
                if capture is not None:
                    capture.append(f"\\input{{{reference}}}")
                    continue
                if loader is None:
                    gaps.append(f"{reference}: includes are not followed here")
                    continue
                key = reference if reference.endswith(".tex") else reference + ".tex"
                if key in seen_files or depth >= 8:
                    gaps.append(f"{reference}: included again (a cycle), not followed")
                    continue
                included = loader(reference)
                if included is None:
                    gaps.append(f"{reference}: included but not readable within the project's folder or limits")
                    continue
                seen_files.add(key)
                files.append(key)
                walk(included, key, depth + 1)
                continue
            if token.startswith("\\begin"):
                m = _BEGIN.match(cleaned, pos)
                if m is None:
                    pos = match.end()
                    continue
                env, arg = m.group(1), m.group(2)
                pos = m.end()
                if capture is not None:
                    capture.append(m.group(0))
                    stack.append((env, file, line))
                    continue
                stack.append((env, file, line))
                if env in QUESTION_ENVS:
                    counter += 1
                    node = _Node("question", str(counter), file, line)
                    if env == "namedquestion" and arg:
                        node.title_source.append(arg)
                    questions.append(node)
                    node_stack.append(node)
                elif env == ANSWER_ENV:
                    capture = []
                    capture_at = (file, line)
                continue
            if token.startswith("\\end"):
                m = _END.match(cleaned, pos)
                if m is None:
                    pos = match.end()
                    continue
                env = m.group(1)
                pos = m.end()
                if not stack or stack[-1][0] != env:
                    # A close that does not match its open: what the parser
                    # holds is not to be trusted past here.
                    target = current()
                    if target is not None and not target.unclear:
                        target.unclear = f"unbalanced \\end{{{env}}} at {file}:{line}"
                    gaps.append(f"{file}:{line}: \\end{{{env}}} does not close an open environment")
                    if capture is not None and env == ANSWER_ENV:
                        capture = None
                    continue
                stack.pop()
                if capture is not None:
                    if env == ANSWER_ENV and not any(s[0] == ANSWER_ENV for s in stack):
                        target = current()
                        if target is not None:
                            target.answers.append(("".join(capture), capture_at[1] if capture_at else line, capture_at[0] if capture_at else file))
                        capture = None
                    else:
                        capture.append(m.group(0))
                    continue
                if env in PARTS_ENVS:
                    if node_stack and node_stack[-1].kind == "part":
                        node_stack.pop()
                elif env in QUESTION_ENVS:
                    while node_stack and node_stack[-1].kind == "part":
                        node_stack.pop()
                    if node_stack:
                        node_stack.pop()
                continue
        return None

    walk(text, path, 0)
    if capture is not None:
        gaps.append(f"a framed answer is never closed (from {capture_at[0]}:{capture_at[1]})" if capture_at else "a framed answer is never closed")
        target = current()
        if target is not None and not target.unclear:
            target.unclear = "an answer box is never closed"
    for env, file, line in stack:
        if env in QUESTION_ENVS or env in PARTS_ENVS:
            gaps.append(f"{file}:{line}: \\begin{{{env}}} is never closed")

    items: list[Item] = []
    for q in questions:
        q_claim, q_evidence, q_line, q_file = _judge(q.answers, q.title(), q.unclear)
        items.append(Item(q.id, "question", q.title(), q_claim, q_evidence, q_file or q.file, q_line or q.line))
        if not q.parts:
            continue
        shared = _split_shared_answer(q.answers, len(q.parts)) if q.answers else None
        for index, part in enumerate(q.parts):
            answers = part.answers
            if not answers and shared is not None:
                answers = [shared[index]] if index < len(shared) else []
            elif not answers and q.answers:
                answers = q.answers  # one answer under the question covers its parts
            claim, evidence, line, file = _judge(answers, part.title(), part.unclear or q.unclear)
            items.append(Item(part.id, "part", part.title(), claim, evidence, file or part.file, line or part.line))
    return Reading("latex", 1, tuple(items), tuple(files), tuple(gaps), None)


def _split_shared_answer(answers: list[tuple[str, int, str]], parts: int) -> list[tuple[str, int, str]] | None:
    """One box under a question whose enumerate has one item per part is
    one answer per part; anything else stays one answer for all."""
    if len(answers) != 1:
        return None
    text, line, file = answers[0]
    pieces = _ITEM.split(text)
    if len(pieces) - 1 != parts:
        return None
    return [(piece, line, file) for piece in pieces[1:]]


def _judge(answers: list[tuple[str, int, str]], statement: str, unclear: str) -> tuple[str, str, int, str]:
    if unclear:
        return "unclear", unclear, 0, ""
    if not answers:
        return "not started", "no answer box", 0, ""
    text = " ".join(a[0] for a in answers)
    line, file = answers[0][1], answers[0][2]
    if "\\answerbox" in text:
        return "not started", "an empty \\answerbox", line, file
    body = _NOISE.sub(" ", text)
    body = re.sub(r"\\(?:begin|end)\s*\{[^}]*\}", " ", body)
    collapsed = " ".join(body.replace("{", " ").replace("}", " ").split())
    if not collapsed:
        return "not started", "an empty answer box", line, file
    if _TODO.search(text):
        return "in progress", _excerpt(text, _TODO), line, file
    if statement and _same_words(collapsed, statement):
        return "not started", "the box repeats the statement", line, file
    return "answer written", collapsed[:80], line, file


def _same_words(answer: str, statement: str) -> bool:
    a = set(re.findall(r"[a-z0-9]+", answer.lower()))
    s = set(re.findall(r"[a-z0-9]+", statement.lower()))
    if not a or not s:
        return False
    return len(a & s) / len(a | s) >= 0.8


def _excerpt(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    if match is None:
        return " ".join(text.split())[:80]
    start = max(0, match.start() - 30)
    return " ".join(text[start: match.end() + 30].split())[:80]


# ── Markdown ─────────────────────────────────────────────────────────────────

def read_markdown(text: str, path: str) -> Reading:
    items: list[Item] = []
    lines = text.split("\n")
    sections: list[tuple[list[int], str, int, list[str]]] = []
    counters: list[int] = []
    fenced = False
    for number, raw in enumerate(lines, 1):
        if raw.strip().startswith("```"):
            fenced = not fenced
            if sections:
                sections[-1][3].append(raw)
            continue
        heading = None if fenced else _HEADING.match(raw)
        if heading is None:
            if sections:
                sections[-1][3].append(raw)
            continue
        level = len(heading.group(1))
        if level > len(counters):
            counters.extend([0] * (level - len(counters)))
        else:
            counters = counters[:level]
        counters[level - 1] += 1
        sections.append((list(counters), heading.group(2).strip(), number, []))
    for path_ids, title, line, body in sections:
        content = "\n".join(body).strip()
        section_id = ".".join(str(n) for n in path_ids)
        if not content:
            claim, evidence = "not started", "a heading with nothing under it"
        elif _MD_TODO.search(content):
            claim, evidence = "in progress", _excerpt(content, _MD_TODO)
        else:
            claim, evidence = "answer written", " ".join(content.split())[:80]
        items.append(Item(section_id, "section", title[:100], claim, evidence, path, line))
    gaps = ("an unclosed code fence: what followed it was read as code",) if fenced else ()
    return Reading("markdown", 1, tuple(items), (path,), gaps, None)


__all__ = ["CLAIMS", "Item", "Loader", "Reading", "describe", "read", "read_latex", "read_markdown", "reader_for"]
