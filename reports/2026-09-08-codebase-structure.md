# 2026-09-08 — Too much meets in the pipeline

A bounded structural assessment of the working checkout. The checkout already
contained substantial uncommitted feature work; none was changed. These findings
concern maintenance cost, not reproduced production failures. Source line numbers
refer to the inspected working tree and can move as that work continues.

## 1. The orchestrator owns too many responsibilities

**Evidence.** `src/ciel/pipeline.py:792` begins a constructor that builds audio,
web/hub services, the world table, tools, the brain, task controls, and watchers.
The file is 3,856 lines. Turn execution (`:2218`), proactive behavior (`:2719`),
audio interruption (`:3490`), and startup/fallback (`:3728`) share the same class.
`_VoiceSink` (`:383`) receives the whole pipeline and writes private conversation
flags (`:427`); the delivery boundary therefore depends on orchestration internals.

**What was reproduced.** Source inspection establishes the responsibilities and
private-state access; the adjacent script measures constructor and class size.
Size alone is not the finding: unrelated operations share ownership and lifecycle.

**Proposed fix.** Start with speech factory/fallback ownership in `tts/`, following
`stt/__init__.py:1`. Next separate service construction from turn execution with
explicit dependencies. Keep `turn.py:263`'s sink contract and `schedule.py:107`'s
pure arbitration. Moving sinks into another file while still handing each the whole
pipeline would leave the central problem intact. Preserve local and hub behavior.

## 2. Turn verification depends on reconstructing private state

**Evidence.** `scripts/probe_turns.py:211` and
`scripts/probe_hub_arbiter.py:153` bypass `Pipeline.__init__` and assign its private
fields individually. This makes the probes depend on the implementation's state
layout and leaves their setup separate from normal construction.

**What was reproduced.** Both constructor bypasses and the following manual field
assignments were inspected. This does not establish that a current probe is wrong.

**Proposed fix.** Give the turn executor a small explicit construction interface
that accepts fake collaborators. Keep the existing behavioral checks, especially
confirmation origin, public/private lanes, cancellation, and delivery accounting.
A shared fake fixture can ease transition but does not itself fix ownership.

## 3. An extraction left unreachable implementation behind

**Evidence.** `src/ciel/pipeline.py:137` returns the result from `ciel.endpointing`.
Lines 138–146 retain the old implementation, including references to `text` that
cannot execute. The current pipeline diff does not introduce this block.

**What was reproduced.** AST inspection finds statements following that
unconditional return. No runtime failure is claimed.

**Proposed fix.** Remove the dead block as a small cleanup, retaining the wrapper
and its callers. Run the endpointing behavior probes when making that change.

## What to keep and what to defer

The code already has useful boundaries: speech protocols, the pure scheduler,
and turn request/sink contracts. Central configuration is a repository convention;
its 2,440 lines alone do not justify splitting it. Module-level tool bindings
(e.g. `brain/tools/memory.py:25`, `:154`, `:160`) make service dependencies implicit,
but replacing them broadly needs a separate lifecycle assessment. No concurrent
binding failure was reproduced here.

Suggested sequence: remove the dead block; move speech construction/fallback to
its owning package; separate runtime assembly; then narrow turn and delivery state.
Each implementation should preserve the existing probes and document its own
change. Coordinate with the current writer before editing their in-flight files.

## Verification

Run `uv run --no-sync python reports/2026-09-08-codebase-structure-repro.py`.
The script uses only the standard library and source text, imports no application
modules, and accesses no runtime state. The static reproduction and
`git diff --check` were run. Application probes and live services were not run or
inspected; this assessment changes no behavior. Only this report and its adjacent
reproduction script were created.
