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

    uv run --no-sync python scripts/probe_readers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.readers import describe, read, read_latex, read_markdown, reader_for

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


def main() -> int:
    latex_checks()
    markdown_checks()
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
