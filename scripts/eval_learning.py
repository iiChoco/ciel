"""Evaluate the learning module's review, hint, and referee prompts against the real model.

A fake extractor proves the orchestration and nothing about mathematics.
This script runs the real isolated extraction call — the same prompts and
schemas the review, the hint, and the referee use — on a small set of
cases kept here, each with the verdict expected and the spoiler that must
not appear in the answer: a phrase, an object, or a step the feedback or
the hint must not hand over. It fails on any case whose verdict differs or
whose spoiler appears, and prints the rest as agreement.

It costs money, so it is run by hand before milestones 3 and 4 are called
done, and its result is recorded in the changelog entry; it is never in
the probe list. It reads none of the owner's files. The cases were written
by the module's author and await an independent check before they become
the yardstick, as the plan asks.

    uv run --no-sync python scripts/eval_learning.py [--model claude-sonnet-5]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.brain.extract import AgentSdkExtractor, ExtractionLimits, extract_json
from ciel.learning import ASSESS_PROMPT, ASSESS_SCHEMA, HINT_PROMPT, HINT_SCHEMA, REFEREE_PROMPT, REFEREE_SCHEMA, _leaks

CONVENTIONS = "F denotes R or C; V and W denote vector spaces over F; L(V, W) is the set of linear maps from V to W"

CASES: list[dict[str, Any]] = [
    {
        "name": "a correct proof by a route the book does not take is assessed correct",
        "kind": "assess",
        "item": ("thm-3.21", "Theorem 3.21. Suppose $T \\in \\mathcal{L}(V,W)$. Then null $T$ is a subspace of $V$."),
        "answer": (r"Consider the map $T$ as a group homomorphism of the additive groups. Its kernel is a subgroup, so null $T$ is closed under "
                   r"addition and contains $0$. For $\lambda \in \mathbf{F}$ and $v \in \operatorname{null} T$, $T(\lambda v) = \lambda T v = 0$, "
                   r"so it is closed under scalar multiplication. Hence null $T$ is a subspace."),
        "expect": "correct",
        "spoilers": [],
    },
    {
        "name": "a proof missing a hypothesis needs revision and the finding does not supply the step",
        "kind": "assess",
        "item": ("thm-3.21", "Theorem 3.21. Suppose $T \\in \\mathcal{L}(V,W)$. Then null $T$ is a subspace of $V$."),
        "answer": (r"Let $u, v \in \operatorname{null} T$. Then $u + v \in \operatorname{null} T$. Also $0 \in \operatorname{null} T$. "
                   r"So null $T$ is a subspace."),
        "expect": "needs_revision",
        "spoilers": ["T(u+v) = Tu + Tv", "T(u + v) = T u + T v", "lambda v", "homogeneity gives"],
    },
    {
        "name": "a definition equivalent to the book's in other words is assessed correct",
        "kind": "assess",
        "item": ("def-3-null-space", "definition of 'null space'. The book's definition, private reference: for $T \\in \\mathcal{L}(V,W)$, "
                                     "the null space of $T$, denoted null $T$, is the subset of $V$ consisting of those vectors that $T$ maps to $0$."),
        "answer": r"The null space of $T$ is $\{v \in V : Tv = 0\}$, the set of solutions of $Tv = 0$.",
        "expect": "correct",
        "spoilers": ["subset of $V$ consisting of those vectors"],
    },
    {
        "name": "a definition wrong in one quantifier needs revision and the finding does not quote the book",
        "kind": "assess",
        "item": ("def-3-injective", "definition of 'injective'. The book's definition, private reference: a function $T: V \\to W$ is called injective "
                                    "if $Tu = Tv$ implies $u = v$."),
        "answer": r"$T$ is injective if there exist $u, v$ with $Tu = Tv$ and $u = v$.",
        "expect": "needs_revision",
        "spoilers": ["if $Tu = Tv$ implies $u = v$", "Tu = Tv implies u = v"],
    },
    {
        "name": "a question with inconsistent assumptions is rejected by the referee",
        "kind": "referee",
        "question": r"Let $T \in \mathcal{L}(V, W)$ be injective with $\dim V = 3$ and $\dim \operatorname{null} T = 1$. Prove that $\dim \operatorname{range} T = 2$.",
        "solution": r"By the fundamental theorem of linear maps, $\dim \operatorname{range} T = 3 - 1 = 2$.",
        "rubric": "- (10) applies the fundamental theorem of linear maps",
        "independent": r"An injective map has $\operatorname{null} T = \{0\}$, so $\dim \operatorname{null} T = 0$, contradicting the hypothesis; the question cannot be settled as stated.",
        "assumptions": ["the hypotheses are inconsistent: injective forces the null space to be zero"],
        "expect": "reject",
        "spoilers": [],
    },
    {
        "name": "a hint on a stalled proof names no step",
        "kind": "hint",
        "item": ("thm-3.21", "Theorem 3.21. Suppose $T \\in \\mathcal{L}(V,W)$. Then null $T$ is a subspace of $V$."),
        "attempt": r"Let $u, v \in \operatorname{null} T$. I want $u + v \in \operatorname{null} T$ but I do not see why.",
        "expect": "",
        "spoilers": ["T(u+v) = Tu + Tv", "T(u + v) = Tu + Tv", "= 0 + 0"],
    },
]
"""A scanned page with a lost subscript is the seventh case the plan names;
it needs a page image and a rendered fixture, and is left for the live
acceptance rather than faked here."""


@asynccontextmanager
async def lease() -> AsyncIterator[None]:
    yield


async def run_case(extractor: AgentSdkExtractor, case: dict[str, Any], limits: ExtractionLimits) -> tuple[bool, str]:
    if case["kind"] == "assess":
        ident, described = case["item"]
        payload = (f"Chapter conventions, quoted: {CONVENTIONS}\n\n=== {ident} ===\n{ident}: {described}\nThe student's answer, quoted:\n{case['answer']}\n")
        result = await extract_json(extractor, lease, ASSESS_PROMPT, payload, ASSESS_SCHEMA, limits)
        entry = next((a for a in result.get("assessments", []) if a.get("id") == ident), None)
        if entry is None:
            return False, "no assessment for the item"
        text = " ".join(str(f) for f in entry.get("findings", []))
        leaked = [s for s in case["spoilers"] if s.lower().replace(" ", "") in text.lower().replace(" ", "")]
        ok = entry.get("verdict") == case["expect"] and not leaked
        return ok, f"verdict {entry.get('verdict')} (expected {case['expect']}); findings: {text[:300]}" + (f"; SPOILED: {leaked}" if leaked else "")
    if case["kind"] == "referee":
        payload = ("The question, quoted:\n" + case["question"] + "\n\nThe author's private solution, quoted:\n" + case["solution"]
                   + "\n\nThe rubric, quoted:\n" + case["rubric"] + "\n\nThe independent solution, quoted:\n" + case["independent"]
                   + "\n\nAssumptions the independent solver needed, quoted:\n" + "\n".join(f"- {a}" for a in case["assumptions"]))
        result = await extract_json(extractor, lease, REFEREE_PROMPT, payload, REFEREE_SCHEMA, limits)
        ok = result.get("verdict") == case["expect"]
        return ok, f"verdict {result.get('verdict')} (expected {case['expect']}); reason: {str(result.get('reason'))[:200]}"
    ident, described = case["item"]
    payload = f"Chapter conventions, quoted: {CONVENTIONS}\n\n{ident}: {described}\n\nThe student's attempt so far, quoted:\n{case['attempt']}"
    result = await extract_json(extractor, lease, HINT_PROMPT, payload, HINT_SCHEMA, limits)
    hint = str(result.get("hint") or "")
    leaked = [s for s in case["spoilers"] if s.lower().replace(" ", "") in hint.lower().replace(" ", "")]
    ok = bool(hint.strip()) and not leaked and not _leaks(described, hint)
    return ok, f"hint: {hint[:300]}" + (f"; SPOILED: {leaked}" if leaked else "")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="claude-sonnet-5", help="the model the isolated call uses")
    parser.add_argument("--budget", type=float, default=0.25, help="the spend ceiling per call, in USD")
    args = parser.parse_args()
    extractor = AgentSdkExtractor(args.model)
    limits = ExtractionLimits(max_chars=32000, timeout_s=120.0, max_budget_usd=args.budget)
    failed = 0
    print(f"evaluating {len(CASES)} cases against {args.model}; each is one real model call\n")
    for case in CASES:
        try:
            ok, detail = await run_case(extractor, case, limits)
        except Exception as exc:  # noqa: BLE001 - one failed call is one failed case
            ok, detail = False, f"the call failed: {exc}"
        print(f"  {'ok  ' if ok else 'FAIL'} {case['name']}\n        {detail}")
        failed += 0 if ok else 1
    print(f"\n{len(CASES) - failed} of {len(CASES)} cases agree" + (f"; {failed} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
