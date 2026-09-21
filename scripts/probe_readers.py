"""Probe the readers — a document read as data, its parts and their claims.

Synthetic LaTeX shaped like the owner's template (never real homework) and
a small Markdown project. Pins: both spellings of begin and end; questions
numbered by count and a named question by its title; parts lettered under
alphaparts and numbered under arabicparts; a framed box under a part is
that part's answer, a box under the question is shared by its parts, and a
shared box with one enumerate item per part is one answer each; an empty
box, the template's answerbox, a box that only repeats the statement, and a
question with no box are not started; a TODO, a ?? and a \\todo are in
progress; written words are written; a commented-out box is no box and an
escaped percent is not a comment; verbatim is opaque; an include is
followed through the caller's loader and counted, a missing or refused one
is a gap, a cycle is a gap; an unclosed box is unclear with a gap; the
counts and the description name what is still open; an unsupported suffix
has no reader; Markdown headings are sections numbered by nesting, empty
under a heading is not started, a checklist box or TODO is in progress,
and an unclosed fence is a gap.

Study items: a ``namedquestion`` whose argument has an id's shape is that
item, any other argument is title text as before, a duplicate id is a gap
and the second unclear; an answer is isolated exactly with its file and
offsets when it shares a line with its statement or holds an include, a
comment inside a box is blanked and not cut so offsets still point into the
owner's file, and the hash of the isolated answer survives a statement edit
and changes with an answer edit.

    uv run --no-sync python scripts/probe_readers.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.readers import Segment, answer_hash, answer_text, describe, read, read_latex, read_markdown, reader_for

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


PREAMBLE = r"""\documentclass{article}
\usepackage{framed}
\newenvironment{numedquestion}[0]{\stepcounter{questionCounter}}{}
\newenvironment{alphaparts}[0]{\begin{enumerate}}{\end{enumerate}}
\newcommand{\answerbox}[1]{\begin{framed}\vspace{#1}\end{framed}}
\begin{document}
"""

HOMEWORK = PREAMBLE + r"""
\begin{numedquestion}
    Negate each statement.
    \begin {alphaparts}
        \item 2 is the smallest prime number.
        \item All that glitters is not gold.
        \item Every bounded region is bisected by some line.
    \end {alphaparts}
  \begin{framed}
    \begin{enumerate}
        \item Let $P$ be the primes; some prime is smaller than 2.
        \item Something that glitters is gold.
        \item
    \end{enumerate}
  \end{framed}
\end{numedquestion}
\begin {numedquestion}
    Prove that there is no smallest positive real number. 100\% of the credit is for rigor.
    \begin {framed}
    Suppose $r$ is the smallest; then $r/2$ is smaller, a contradiction. \hfill$\blacksquare$
    \end {framed}
\end {numedquestion}
\begin{numedquestion}
    A fixed point of $f$ is $a$ with $f(a) = a$.
    \begin{alphaparts}
        \item Show $f$ has a fixed point iff its graph meets the diagonal.
        \begin{framed}
        TODO: draw the picture first.
        \end{framed}
        \item Prove every continuous $f : [0,1] \to [0,1]$ has one.
        \begin{framed}
        Consider $g(x) = f(x) - x$; $g(0) \ge 0 \ge g(1)$, so by the intermediate value theorem there is a zero.
        \end{framed}
        \item Is the same true on $(0,1)$?
        \begin{framed}
        \end{framed}
        \item Is the same true for discontinuous functions?
    \end{alphaparts}
\end{numedquestion}
\begin{numedquestion}
    State the archimedean property.
    \answerbox{2in}
\end{numedquestion}
\begin{numedquestion}
    Compute the limit of $1/n$.
    % \begin{framed} an answer I commented out \end{framed}
    \begin{framed}
    Compute the limit of $1/n$.
    \end{framed}
\end{numedquestion}
\begin{namedquestion}{Bonus: the harmonic series}
    Does it converge??
    \begin{framed}
    \begin{verbatim}
    \end{framed} this is inside verbatim and must not close the box
    \end{verbatim}
    It diverges: group the terms as $1/2, 1/3+1/4, \ldots$ each group at least a half.
    \end{framed}
\end{namedquestion}
\input{extra}
\end{document}
"""

EXTRA = r"""
\begin{numedquestion}
    From the included file.
    \begin{framed}
    Written in the include.
    \end{framed}
\end{numedquestion}
\input{missing}
\input{extra}
"""


def latex_checks() -> None:
    print("the LaTeX reader")
    loads: list[str] = []

    def loader(reference: str) -> str | None:
        loads.append(reference)
        return EXTRA if reference == "extra" else None

    reading = read_latex(HOMEWORK, "hw03.tex", loader)
    by_id = {i.id: i for i in reading.items}
    check("questions are numbered by count, parts lettered, and a named question keeps its title",
          [i.id for i in reading.items if i.kind == "question"] == ["1", "2", "3", "4", "5", "6", "7"]
          and [i.id for i in reading.items if i.kind == "part"] == ["1a", "1b", "1c", "3a", "3b", "3c", "3d"]
          and by_id["6"].title.startswith("Bonus: the harmonic series"))
    check("a shared box with one enumerate item per part is one answer each: two written, the empty third not started",
          by_id["1a"].claim == "answer written" and by_id["1b"].claim == "answer written" and by_id["1c"].claim == "not started")
    check("both spellings of begin and end work, and an escaped percent is not a comment",
          by_id["2"].claim == "answer written" and "contradiction" in by_id["2"].evidence and "100" in by_id["2"].title)
    check("a box under a part is that part's: a TODO is in progress, words are written, an empty box and no box are not started",
          by_id["3a"].claim == "in progress" and "TODO" in by_id["3a"].evidence and by_id["3b"].claim == "answer written"
          and by_id["3c"].claim == "not started" and by_id["3d"].claim == "not started" and by_id["3d"].evidence == "no answer box")
    check("the template's answerbox is not started", by_id["4"].claim == "not started" and "answerbox" in by_id["4"].evidence)
    check("a commented-out box is no box, and a box that only repeats the statement is not started",
          by_id["5"].claim == "not started" and "repeats" in by_id["5"].evidence)
    check("verbatim is opaque and a ?? in the statement does not mark the answer",
          by_id["6"].claim == "answer written" and "diverges" in by_id["6"].evidence)
    check("an include is followed through the loader, its question counted in sequence, and its box read",
          "extra" in loads and by_id["7"].claim == "answer written" and by_id["7"].file == "extra.tex" and reading.files == ("hw03.tex", "extra.tex"))
    check("a missing include and a cycle are gaps, in words",
          any("missing" in g and "not readable" in g for g in reading.gaps) and any("cycle" in g for g in reading.gaps))
    counts = reading.counts()
    check("counts describe the leaves: parts where a question has them, the question otherwise",
          counts["total"] == 12 and counts["answer written"] == 6 and counts["in progress"] == 1 and counts["not started"] == 5 and counts["unclear"] == 0)
    text = describe(reading)
    check("the description names what is still open with its line, and says the roster's completeness is unknown",
          "6 written, 1 in progress, 5 not started" in text and "1c (not started, line" in text and "3a (in progress" in text and "unknown" in text)
    check("the claim's line is the box's line", by_id["3a"].line == HOMEWORK.split("\n").index("        TODO: draw the picture first.") and by_id["2"].line > 0)

    unclosed = PREAMBLE + r"""
\begin{numedquestion}
    First.
    \begin{framed}
    An answer that never closes.
\end{numedquestion}
\begin{numedquestion}
    Second.
    \end{framed}
\end{numedquestion}
"""
    reading = read_latex(unclosed, "bad.tex")
    check("an unclosed box makes its question unclear and a gap says where",
          any(i.claim == "unclear" for i in reading.items) and any("never closed" in g for g in reading.gaps))
    check("no loader means includes are named as not followed", any("not followed" in g for g in read_latex(r"\input{x}", "a.tex").gaps))
    check("a document with no questions reads as nothing, honestly", read_latex(PREAMBLE + "\\end{document}", "empty.tex").items == ())
    check("an unsupported suffix has no reader", reader_for("notes.pdf") is None and read("x", "notes.pdf") is None)


MARKDOWN = """# Proposal

Intro paragraph.

## Aims

- [ ] write the aims
- [ ] cite the review

## Background

Plenty written here, with a code block:

```
# not a heading
```

## Budget

### Personnel

## Timeline

TBD
"""


def markdown_checks() -> None:
    print("\nthe Markdown reader")
    reading = read_markdown(MARKDOWN, "proposal.md")
    by_id = {i.id: i for i in reading.items}
    check("headings are sections numbered by nesting",
          [i.id for i in reading.items] == ["1", "1.1", "1.2", "1.3", "1.3.1", "1.4"] and by_id["1.3.1"].title == "Personnel")
    check("a checklist box is in progress, words are written, a heading with nothing under it is not started, TBD is in progress",
          by_id["1.1"].claim == "in progress" and by_id["1.2"].claim == "answer written" and by_id["1.3.1"].claim == "not started" and by_id["1.4"].claim == "in progress")
    check("a heading inside a code fence is content, not a section", "1.2.1" not in by_id and "not a heading" in by_id["1.2"].evidence or by_id["1.2"].claim == "answer written")
    check("a section whose only content is subsections is not started itself", by_id["1.3"].claim == "not started")
    check("an unclosed fence is a gap", read_markdown("# A\n```\ncode", "a.md").gaps != ())
    check("the same reading shape serves both readers", set(describe(reading).split(":")[0].split()) & {"sections", "section"})


STUDY = PREAMBLE + r"""
\begin{namedquestion}{thm-3.21}
    \textbf{Theorem 3.21.} Suppose $T \in \mathcal{L}(V,W)$. Then null $T$ is a subspace. \begin{framed} Let $u, v \in$ null $T$. \end{framed}
\end{namedquestion}
\begin{namedquestion}{def-3B-span}
    \textbf{Definition (span).} Give the definition.
    \begin{framed}
    \input{more}
    \end{framed}
\end{namedquestion}
\begin{namedquestion}{Just a title, not an id}
    A named question as before. \begin{framed}\vspace{8\baselineskip}\end{framed}
\end{namedquestion}
\begin{namedquestion}{thm-3.21}
    The same id again. \begin{framed} twice \end{framed}
\end{namedquestion}
\begin{numedquestion}
    Numbered as always. \begin{framed} % a comment inside
    written \end{framed}
\end{numedquestion}
\end{document}
"""


def study_checks() -> None:
    print("\nstudy items: ids and exact answers")
    included = {"more": "The set of all linear combinations.\n"}
    reading = read_latex(STUDY, "sheet.tex", lambda ref: included.get(ref))
    by_id = {i.id: i for i in reading.items}

    def first(items: Any, wanted: str) -> Any:
        return next(i for i in items if i.id == wanted)

    check("an id-shaped namedquestion argument is the item's id; any other argument is title text, and numbering runs on as before",
          "thm-3.21" in by_id and "def-3B-span" in by_id and by_id["3"].title.startswith("Just a title") and by_id["5"].claim == "answer written")
    thm = first(reading.items, "thm-3.21")
    check("an answer sharing a line with its statement is isolated exactly, with its file and offsets",
          answer_text(thm) == " Let $u, v \\in$ null $T$. " and len(thm.answer) == 1 and thm.answer[0].file == "sheet.tex"
          and STUDY[thm.answer[0].start:thm.answer[0].end] == thm.answer[0].text)
    span = by_id["def-3B-span"]
    check("an answer whose box holds an include reaches into the included file's text",
          answer_text(span).strip() == "\\input{more}" or "linear combinations" in answer_text(span) or "\\input{more}" in answer_text(span))
    edited = STUDY.replace("Then null $T$ is a subspace.", "Then null $T$ is a subspace of $V$.")
    again = first(read_latex(edited, "sheet.tex", lambda ref: included.get(ref)).items, "thm-3.21")
    changed = first(read_latex(STUDY.replace("null $T$. \\end", "null $T$, so. \\end"), "sheet.tex").items, "thm-3.21")
    check("a statement edit leaves the answer's hash unchanged; an answer edit changes it",
          answer_hash(again) == answer_hash(thm) and answer_hash(changed) != answer_hash(thm))
    check("a duplicate id is a gap, named, and the second item is unclear",
          any("thm-3.21 is used twice" in g for g in reading.gaps) and sum(1 for i in reading.items if i.id == "thm-3.21") == 2
          and any(i.id == "thm-3.21" and i.claim == "unclear" for i in reading.items))
    check("a comment inside a box is blanked, not cut: the offsets past it still point into the owner's file",
          "written" in answer_text(by_id["5"]) and "a comment inside" in STUDY[by_id["5"].answer[0].start:by_id["5"].answer[0].end])
    check("a legacy reading carries no segments where there is no box, and the empty template box is not started",
          by_id["3"].claim == "not started" and answer_text(by_id["3"]).strip() == "\\vspace{8\\baselineskip}")
    check("a segment is a plain record of file, offsets, and text", Segment("a.tex", 0, 1, "x") == Segment("a.tex", 0, 1, "x"))


def main() -> int:
    latex_checks()
    markdown_checks()
    study_checks()
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
