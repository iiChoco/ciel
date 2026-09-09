# The revised plans still need two lifecycle decisions

Review — 2026-09-08. Scope: the independent-action foundation and its
email-to-calendar feature, as present in this checkout during review.

**Assessment.** The earlier revisions made origin records, setup, isolated
extraction, host authorization, and feature storage much more concrete.
Three actionable findings remain. Resolve the two lifecycle contracts before
implementing the affected flows. They are plan gaps, not a claim that an
unimplemented runner already has these runtime bugs.

Only this review and its reproduction script were written. The plans and
production code were left unchanged.

## 1. [P1] Separate a persistent event identity from its completed action task

**Plan location.** [Foundation:151](../design/2026-09-08-independent-action-plan.md#L151)
requires a new source revision to revise the same candidate without creating
a second child. [Inbox:259](../design/2026-09-08-email-calendar-plan.md#L259)
calls those candidate tasks finite, and
[Inbox:386](../design/2026-09-08-email-calendar-plan.md#L386) requires a later
reschedule/cancellation to be presented and acted on with approval.

**Reproduced.** A synthetic calendar observation completes an actual TaskStore
task. A subsequent reschedule cannot ask an owner question, resume, claim,
or retarget that same task; each operation raises TaskConflict. Reusing its
request ID with a changed desired time also fails. The original completion
and criteria survive reopening. This follows
[tasks.py:651](../src/ciel/tasks.py#L651),
[tasks.py:726](../src/ciel/tasks.py#L726),
[tasks.py:822](../src/ciel/tasks.py#L822), and
[tasks.py:846](../src/ciel/tasks.py#L846).

**Impact.** After the first successful calendar insertion, the plan gives no
admissible lifecycle for the next email about that event. “Revise the same
candidate” leaves the implementer to reopen a terminal task, overwrite what
its original completion meant, or violate the no-second-child rule. The
create-only parent also cannot silently delegate an update/delete operation.

**Proposed fix.** Keep one persistent feature-level event/candidate record and
its source-to-calendar identity, but give distinct finite operations their own
task identities keyed by event, source revision, and operation. Deduplicate
replays of one operation without confusing them with a later reschedule.
Preserve each completed task, its evidence, and its notice. A fresh concrete
owner approval authorizes a separately scoped update/delete task; it must not
widen the original create-only grant. Define the links and transition/accounting
rules in milestone 2. An explicit, audited terminal-task revision mechanism
would be another design, but its rules must be specified rather than inferred.

**Acceptance scenario.** Import a confirmation; verify and complete it;
restart; receive a reschedule; obtain approval and update the existing event;
replay both emails. One calendar event remains, and both action receipts keep
their original meaning. Repeat with a cancellation and an in-flight older
operation whose outcome still needs reconciliation.

## 2. [P2] Give preview a task path that does not require the write grant

**Plan location.** [Inbox:90](../design/2026-09-08-email-calendar-plan.md#L90)
saves preview choices without granting writes.
[Foundation:135](../design/2026-09-08-independent-action-plan.md#L135) requires
a grant reference for DerivedOrigin, and
[Foundation:146](../design/2026-09-08-independent-action-plan.md#L146) admits
children only under a matching active standing mandate/grant.
[Foundation:223](../design/2026-09-08-independent-action-plan.md#L223) creates
the mandate with the approved grant. Meanwhile
[Inbox:254](../design/2026-09-08-email-calendar-plan.md#L254) commits derived
tasks as part of inbox page progress.

**Established by document inspection.** Those requirements form a bootstrap
gap for live preview and the first per-event approval: before granting automatic
creation there is no specified parent/grant under which candidate tasks run.
The reproduction checks those clauses, not a nonexistent grant implementation.
The sender-enrollment flow explicitly expects preview to supply the candidates
from which the owner builds the initially empty allowlist.

**Impact.** An implementation must either ask for automatic authority before
showing the preview that supports that decision, introduce an undocumented
grant exception, or build preview outside the stated execution model.

**Proposed fix.** Define an explicit read-only preview request using a
HumanOrigin finite task, scoped to a mailbox/window, with bounded extraction
and no mutation operations. It can persist feature candidate records without
deriving executable write children. Convert a selected candidate to a separately
approved action task, or derive under a standing grant only after activation.
If persistent read-only mandates are preferred, define their approval, grant
record, and scope explicitly; do not make write authority an implicit condition
of reading. Pin how preview checkpoints/dedupe survive later activation.

**Acceptance scenario.** With no calendar-write grant and an empty sender list,
request live preview, restart during extraction, inspect a saved candidate,
and approve one event or enroll a sender. Before that approval there are no
calendar writes and no fabricated grant/attendance.

## 3. [P2] Reference the dataclass that actually owns Gmail credentials

**Plan location.** [Inbox:362](../design/2026-09-08-email-calendar-plan.md#L362)
says the reader borrows MailConfig.gmail_oauth_keys / gmail_token_file.

**Reproduced.** Dataclass field inspection finds neither field on MailConfig
and both on SectionsConfig. The definitions are at
[config.py:1196](../src/ciel/config.py#L1196) and
[config.py:1201](../src/ciel/config.py#L1201);
[MailConfig](../src/ciel/config.py#L1218) configures the separate SMTP sender.

**Impact.** Following the plan literally produces an attribute error or causes
the writer to improvise a second credential location. This undoes the plan's
intention to reuse one existing configuration source.

**Proposed fix.** Either name SectionsConfig explicitly as the temporary source
of the existing Gmail paths, or specify a shared Google/Gmail credential config
migration with backward compatibility for the section alarm. Do not quietly add
the fields to the unrelated SMTP MailConfig. Keep the execution-host readiness
checks already in the plan.

**Acceptance scenario.** The configured inbox reader and existing section alarm
resolve the intended Gmail paths without requiring SMTP to be enabled, and the
existing calendar adapter continues using its documented Google credentials.

## Verification and scope

Run [the reproduction](2026-09-08-independent-action-second-review-repro.py):

```sh
uv run --no-sync python reports/2026-09-08-independent-action-second-review-repro.py
```

All 14 review checks reproduced: seven source/config contract checks and seven
TaskStore lifecycle checks in a private temporary directory. No mic, model,
network, real emails, credentials, or ~/.ciel state were used.
The static preview checks establish a specification gap; they do not simulate
or prove the behavior of the future runner.

The existing task-controls change is now present in git history as 45e4fd7;
its previous baseline prerequisite is not a new finding. Its full probe suite
was not rerun for this focused review. Live Gmail/Calendar, model isolation,
and planned migrations were not tested.

All 17 local report references resolved, and `git diff --check` passed.
