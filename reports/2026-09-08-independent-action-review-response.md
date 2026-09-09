# The plans say who may act and where

Review response — 2026-09-08.

The owner supplied a critique of the independent-action foundation and its
email-to-calendar feature. This response checks its code claims and records
plan revisions, ordered by impact. These are design fixes, not implemented
runtime behavior. Earlier review text is user-supplied assessment, not evidence
that a live account was tested.

[Reproduction](2026-09-08-independent-action-review-repro.py):
run `python3 reports/2026-09-08-independent-action-review-repro.py` from the
checkout. It reads source text/AST and plan links only. It does not import Ciel,
read credentials/runtime state, contact providers, or test model isolation.

## 1. Derived tasks need their own admissible origin — accepted

**Evidence.** [src/ciel/tasks.py:420](../src/ciel/tasks.py#L420) requires
attended private input on a human lane. The existing Origin has no parent
mandate identity. Static reproduction confirms both.

**Change.** [design/2026-09-08-independent-action-plan.md:125](../design/2026-09-08-independent-action-plan.md#L125) defines HumanOrigin and
DerivedOrigin, schema-two migration, a runtime-only derive_task operation,
atomic parent checks/accounting, dedupe, and revision handling. Milestone 2
now owns this change. Ordinary human creation validation remains strict.

## 2. A calendar choice is not a yes/no answer — accepted

**Evidence.** [src/ciel/confirm.py:343](../src/ciel/confirm.py#L343)
takes an already formed question and returns a boolean.

**Change.** [design/2026-09-08-independent-action-plan.md:208](../design/2026-09-08-independent-action-plan.md#L208) names Chart as the
form surface and the broker as the approval/recording mechanism. Versioned
drafts hold resolved identities and the complete scope; edits invalidate pending
approval. The [design/2026-09-08-email-calendar-plan.md:83](../design/2026-09-08-email-calendar-plan.md#L83) section lists actual inbox fields.
No grant exists while the owner is merely filling out a form.

## 3. Isolated extraction needs an explicit construction and scheduling path — accepted

**Evidence.** [src/ciel/pipeline.py:2209](../src/ciel/pipeline.py#L2209)
adds world context to ordinary responses.
[interview/brain.py:54](../src/ciel/interview/brain.py#L54)
already constructs a fresh structured-call client, but inbox extraction does
not exist.

**Change.** [design/2026-09-08-independent-action-plan.md:282](../design/2026-09-08-independent-action-plan.md#L282) specifies a new brain/extract.py
operation patterned on that construction, tools/settings/context isolation,
schema validation, no conversational fallback, the shared Brain model-turn
lease, accounting, cancellation and cleanup. This is a proposed shape, not a
claim that those isolation guarantees have been tested.

## 4. Execution-host account readiness must be explicit — accepted with correction

**Evidence.** [src/ciel/config.py:1181](../src/ciel/config.py#L1181)
and [src/ciel/config.py:1985](../src/ciel/config.py#L1985) use Path.home().
These are process-user defaults, not intrinsically Mac paths. A login on one
machine is no evidence that the hub service user has it. No actual token file
or deployed configuration was inspected.

**Change.** The inbox setup now requires independent authorization/readiness
on the execution host. Missing Gmail means offline/synthetic preview only;
missing Calendar allows email preview with incomplete checks clearly marked,
and blocks automatic writes. It explicitly forbids silently copying Mac tokens
or transferring execution to the spoke.

## 5. Provider records need a boundary inside the shared store — accepted

**Evidence.** [src/ciel/tasks.py:334](../src/ciel/tasks.py#L334) defines the
generic schema; it has no namespace facility. The earlier plans said both
“no Gmail in the runtime” and “feature records through store migrations.”

**Change.** [design/2026-09-08-independent-action-plan.md:189](../design/2026-09-08-independent-action-plan.md#L189) specifies generic namespace
and record tables, adapter-owned payload schemas/migrations, owner/revision
checks, and a bounded atomic write-set API. The inbox registers email_calendar
version 1; Gmail cursor fields stay inside its validated payloads.
Unknown versions preserve records and disable only affected work.

## 6. Deleted entries must not be treated as retryable insertion conflicts — accepted with qualification

**Evidence.** Google's [event resource documentation](https://developers.google.com/workspace/calendar/api/v3/reference/events)
says non-recurring cancelled events represent deletions, may expose only an ID,
and eventually disappear. This was checked against documentation, not a live
create/delete experiment. It does not establish an indefinite tombstone or a
particular conflict response for every later insertion.

**Change.** [design/2026-09-08-email-calendar-plan.md:300](../design/2026-09-08-email-calendar-plan.md#L300) makes a bound
cancelled resource locally suppressed/deleted, preserves its tombstone, and
prevents retries/new IDs. A later missing resource cannot erase that fact.
The wording does not attribute the deletion to the owner without evidence;
a move or another actor may also explain it.

## 7. Source authentication needs an honest first-version policy — revised

**Evidence.** No sender-authentication adapter was demonstrated. A top header
alone is not an established trust boundary in this code. Google's
[authentication guidance](https://support.google.com/mail/answer/180707)
also does not equate authentication with safe content.

**Change.** [design/2026-09-08-email-calendar-plan.md:189](../design/2026-09-08-email-calendar-plan.md#L189) explicitly defers independent
sender authentication. The proposed v1 scope is an owner-enrolled exact sender
list, preview by default, review on suspicious/unapproved cases, a private
receipt, and guarded undo. A convincing forgery can still pass that scope;
the Chart grant review must state this before automatic activation. If the
owner does not accept that limit, use per-event approval. This is a policy
proposal, not a grant supplied by this review.

## 8. Calendar adapter naming — accepted for clarity

**Evidence.** The old proposal used ciel/calendar.py. An absolute package import
does not itself shadow the top-level stdlib calendar module, so this is a naming
concern rather than a demonstrated runtime defect.

**Change.** [design/2026-09-08-email-calendar-plan.md:160](../design/2026-09-08-email-calendar-plan.md#L160) uses gcal_adapter.py.

## Remaining review points — accepted

- [design/2026-09-08-independent-action-plan.md:125](../design/2026-09-08-independent-action-plan.md#L125) makes grant/draft/origin records reviewable.
- [design/2026-09-08-independent-action-plan.md:254](../design/2026-09-08-independent-action-plan.md#L254) fixes the ladder position and how an aged
  task moves ahead of nonurgent Vigil while respecting human/urgent work.
- [design/2026-09-08-independent-action-plan.md:66](../design/2026-09-08-independent-action-plan.md#L66) requires an accepted task-controls
  baseline before overlapping runtime work. A commit remains owner-authorized;
  the plan does not stage or commit another writer's files.

## Verification and limits

The static reproduction passed all 10 checks. `git diff --check` passed, and
46 relative links/line anchors plus README/changelog references resolved.
Existing command targets were checked against the checkout. New runner/extraction
modules and probes remain explicitly proposed files. README and changelog
reference these revisions.

No runtime behavior, dependency, live account, process, branch, or deployment
was changed. Runtime probes, SDK isolation tests, schema migration tests, and
live provider behavior remain implementation gates, not completed checks.
