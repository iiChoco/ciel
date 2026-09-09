# The revisions close preview and completion gaps, but need one authority boundary

Review — 2026-09-08. This reviews the changes made after the
[second review](2026-09-08-independent-action-second-review.md).
The plans and production code were not edited.

**Verdict.** Agree with the main direction and with the explicit preview path,
persistent event record, and corrected credential configuration. I would start
the read-only foundation work. I would resolve the authority contradiction
below before implementing reschedule/cancellation tasks, and tighten the
non-terminal revision rule before mutation recovery is implemented.

## 1. [P1] Out-of-grant proposals need a different admission path from derived tasks

**Location.** [Foundation:146](../design/2026-09-08-independent-action-plan.md#L146)
requires derive_task to validate that the child narrows an active parent/grant.
[Foundation:173](../design/2026-09-08-independent-action-plan.md#L173) then
creates an update/delete child outside that grant, merely placing it in waiting.

**Why I disagree with this part.** Saving a proposed update is appropriate,
but the same admission operation cannot both reject widened child scope and
admit that widened scope because it is waiting. The plan says the later action
gets concrete approval; it does not define a distinct proposal record or a
record/validation transition that replaces inherited authority with that
approval. Waiting is execution state, not a source of authority.

**Evidence.** Document inspection confirms the conflicting rules. In the real
TaskStore, create with resource_wait=True still refuses an update/delete step
against a create-only task scope. The proposed derive_task does not exist;
this reproduction establishes the current invariant, not a runtime defect in
unwritten code.

**Proposed fix.** Record the desired change as an inert, revisioned feature
proposal tied to the event and source evidence. Upon a private owner's concrete
approval, create a new finite HumanOrigin action task with that approved scope
and approval reference; preserve the event/previous-task links for history.
Ordinary derive_task continues to require scope containment. Alternatively,
specify a separate pending-proposal type and an atomic approval-to-task
conversion, with explicit validation and revocation rules. Do not silently
weaken derive_task's existing contract.

**Acceptance gate.** A cancellation email under a create-only grant records
a proposal but cannot create or claim an executable delete task. An owner
approval of the exact current proposal creates one bounded delete task;
replay, a stale answer, or source change cannot create another or widen the
standing grant.

## 2. [P2] Limit in-place revision to work that has not dispatched a mutation

**Location.** [Foundation:151](../design/2026-09-08-independent-action-plan.md#L151)
includes source revision in the dedupe key, then permits any non-terminal child
to be revised in place when that revision changes.

**Why I would tighten it.** Non-terminal includes a mutation already sent,
one being verified, and one waiting for reconciliation. Example: create from
mail revision 1 is sent; its response is lost; revision 2 changes the time.
Fencing late callbacks does not undo the first request. The plan's general
recovery rules are sound, but this specific instruction does not say whether
the task's operation identity/payload/key can change before that earlier
request is reconciled. An implementer must guess how the two rules interact.

The changed revision is also part of the dedupe key. If an undispatched task
is updated in place, both old and new source keys need durable handling so
replaying revision 1 neither creates another task nor rolls the candidate back.

**Evidence boundary.** Static checks reproduce the broad non-terminal clause
and revision-bearing key. The network sequence above is a design counterexample,
not a live Calendar experiment or an observed bug in a future runner.

**Proposed fix.** Permit revision in place only for an operation with no
committed mutation-dispatch intent, and only while its required scope is
unchanged. Invalidate old questions/approvals and atomically retain replay
aliases/processed revisions. Once dispatch intent exists, freeze that attempt's
payload and identity, finish its read-only reconciliation, and then propose
the next operation against the resulting remote state. State this in the
derivation contract and test revision changes at each crash boundary.

## Changes I agree with

- **Preview no longer needs automatic-write authority.**
  [Foundation:228](../design/2026-09-08-independent-action-plan.md#L228) defines
  a finite HumanOrigin read/extraction task, persisted candidates, and no
  derived children. This closes the previous bootstrap gap. Milestone 1 now
  includes the namespace facility and isolated extraction needed to run it.
- **The event outlives any one action.**
  [Foundation:168](../design/2026-09-08-independent-action-plan.md#L168) preserves
  completed tasks and their receipts, while a FeatureRecord links later work.
  This is the right correction to the former “reopen the completed child”
  problem. The admission and in-flight cases above are the remaining edges.
- **The credential owner is corrected.** The inbox plan now names
  SectionsConfig.gmail_oauth_keys / gmail_token_file, matching the current
  dataclass. Deferring shared credential refactoring keeps this change focused.
- **The baseline is concrete.** 45e4fd7 is present in repository history and
  names the landed task-controls change. Keeping unrelated edits outside this
  implementation is appropriate.

The sender-authenticity limitation remains an explicitly disclosed policy
choice, not a newly discovered correctness defect or a grant from this review.

## Verification and limits

[Reproduction](2026-09-08-independent-action-third-review-repro.py):

```sh
uv run --no-sync python reports/2026-09-08-independent-action-third-review-repro.py
```

All nine checks reproduced: seven document/config assertions and two actual
scope refusals in a private temporary TaskStore. No model, mic, network,
credentials, real email, or ~/.ciel state was accessed. The complete runtime
probe suite and live integrations were not tested.

The previous reproduction scripts intentionally pin older plan wording; they
were not rerun as acceptance tests for the new text. They remain evidence for
their dated review snapshots.
