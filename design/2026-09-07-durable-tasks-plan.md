# Ciel can carry a task between conversations

Implementation proposal for review — 2026-09-07. This document proposes work;
it does not authorize implementation, unattended side effects, commits, or
deployment. Existing voice and Barn Door changes remain separate.

Revised after `reports/2026-09-07-durable-tasks-plan-review.md`. Accepted:
aging relative to non-urgent Vigil, explicit GitHub access and evidence rules,
recorder-owned correlation, the mandate mapping, an owner-question pilot,
and restart/delivery invariants. Qualifications and an SDK-context reproduction
are in `reports/2026-09-07-durable-tasks-review-response.md`.

## Outcome and first acceptance scenario

Ciel should turn a user's requested outcome into durable, inspectable work:
remember the next step, wait without occupying the brain, resume after a
restart, ask when authority or information is missing, and complete only when
there is evidence. Conversation/session history must not be required to resume.

First usable slice: “Watch this PR's checks and tell me when they pass.” The
owner supplies the PR. Capture its identity and head SHA; observe checks,
checkpoint, restart the hub, observe again, verify the current head's checks,
and produce one durable completion notice. A new head invalidates evidence
for the old head. Passing checks means exactly that, not that the PR is safe
to merge. No merge, commit, deployment, or external message to another person.

This pilot proves the lifecycle. It does not yet fulfill arbitrary “ship this
feature” requests. Those require the guarded execution phase below. Voice
acceptance testing can continue independently; it should not block the task
store or scheduler work.

### A task implements a mandate

Use one vocabulary, with task as the owner-facing name: desired state is the
requested outcome plus completion criteria; observed facts are evidence with
source, timestamp, target identity, and relevant revision; policy is authority,
scope, constraints, and budget; actions are typed steps and attempts;
verification is the runtime's comparison of evidence with the desired state.
Reference Phase Space fact names where they exist. Do not invent a second
store of world facts or require a new world fact for every task-local detail.
A broad world revision changing is not itself grounds to invalidate unrelated
evidence: compare the specific observations the action depends on.

## What exists and what changes

Paths below are relative to the repository root.

| Existing surface | Reuse and boundary |
|---|---|
| `src/ciel/projects.py`, `src/ciel/brain/tools/projects.py` | Atlas stores Markdown project state and a dated log. Link tasks to projects; keep project summaries human-readable. Do not parse Markdown prose into executable state. |
| `src/ciel/world.py` | Phase Space supplies observations, provenance, age, and revisions. Tasks store the specific observations used as preconditions/evidence; the world receives only derived task summaries. |
| `src/ciel/schedule.py`, `src/ciel/pipeline.py` | One shared arbiter already prioritizes confirmations, timers, human lanes, and Vigil. Add task eligibility and one bounded execution opportunity; do not create a competing infinite agent loop. |
| `src/ciel/brain/agent.py`, `src/ciel/brain/witness.py` | The brain has a turn lock; Witness restricts unattended turns. Keep that boundary. A requested task is not blanket permission for background tools. |
| `src/ciel/confirm.py` | Reuse the broker for concrete actions. Long waits belong in a persisted task, not a permanently active confirmation or SDK call. |
| `src/ciel/brain/recorder.py`, `src/ciel/journal.py` | Inverse records actions and snapshots. Add stable correlation between tasks, attempts, tool calls, and journal entries before resumable mutations are allowed. |
| `src/ciel/pipeline.py::_emit_verify` | Current verification is a Vigil note, capped at three pending checks and expiring after 30 minutes. Useful precedent, insufficient as the authoritative completion gate for a durable task. |
| `src/ciel/proactive/events.py`, `src/ciel/proactive/policy.py` | Reuse the notification policy and delivery path. They decide when to tell the owner; they do not decide whether the work is done. |
| `src/ciel/remote/chart.html`, `src/ciel/hub/server.py` | Show task status and owner controls in Chart using the existing Instrument components and authenticated owner boundary. |

## Proposed architecture

Add `src/ciel/tasks.py` for records, transitions, and storage, plus
`src/ciel/task_runner.py` for claiming eligible work and performing one bounded
step. Add `src/ciel/brain/tools/tasks.py` for the owner's task controls and queries.
Use the existing registry/binding conventions. No new codename or dependency.

The hub owns tasks in split mode. A standalone local runtime can own its own
configured store. The spoke never schedules a second copy of the task, and a
hub outage does not transfer authority to the spoke. Automatic failover,
cross-host replication, and parallel task execution are outside v1.

Use Python's standard-library SQLite for the task store, proposed at
`~/.ciel/tasks/tasks.sqlite3`. Atlas remains Markdown. Task state needs atomic
updates across a task, its attempt, evidence, and a notification outbox;
A single atomically replaced JSON document under one writer can also commit
these records together; SQLite is chosen for per-record updates, transition
history, outbox queries, and standard transaction semantics as the store grows. Keep the directory owner-only, including database sidecars, use explicit
schema versions and migrations, and provide readable export/status tools.
Serialize store access on a dedicated worker thread; transactions must be short and never span awaits,
model calls, or network calls. Corrupt or unsupported data disables execution
with an actionable error rather than being replaced by an empty store.
A schema version ahead of this executable also disables task execution:
rollback must never silently downgrade or overwrite a newer database.
Choose journal mode explicitly, protect rollback/WAL/SHM files under the same
private directory, and use SQLite's backup facilities rather than copying only
a live database file while ignoring its WAL.

A process ownership lock prevents two runtimes from executing against the same
store. Task revisions and attempt IDs reject stale results after interruption.
This is single-owner recovery, not a distributed lease or an exactly-once
execution claim.

### Minimum records

- **Task:** stable ID; owner, originating request reference, lane, and attended status; requested
  outcome; optional Atlas project; explicit scope; typed completion criteria;
  status; next step; wait reason; next eligible time/deadline; revision;
  cumulative resource budget; creation/update timestamps.
- **Step/attempt:** task and step IDs; typed operation and validated target;
  attempt ID; observed preconditions; dispatch/result state; tool-call and
  journal references; started/finished timestamps; retry/reconciliation state.
- **Evidence:** task/step association; criterion; observed value; source and
  target identity; observation time; revision/head/content hash where useful;
  verifier result (`satisfied`, `unsatisfied`, or `unknown`).
- **Transition and notification:** a durable transition history, plus an
  outbox item keyed by task ID and transition revision. Terminal state and
  its notification intent commit together.

Descriptions and plans are data. Owner identity, authorization, task revision,
and execution state come from trusted runtime context, not model-supplied
claims. Source documents, repository text, and tool output cannot grant scope.

### State transitions

Normal progress is `queued → running → verifying → done`. Steps can return to
`queued` after a checkpoint. `waiting` records an external condition, an owner
decision, unavailable capability, or uncertain previous execution. `paused`,
`failed`, and `cancelled` are explicit states; resuming a paused/failed task is
an owner operation that preserves history and revalidates scope and evidence.

The model can propose a step or summarize a result. Validated runtime
transitions own claims, dispatch, retries, and completion. Do not expose an
unrestricted tool that lets the model set `done` or rewrite approval/evidence.

On startup, a previously `running` attempt needs recovery. A read-only attempt
can be rerun under its retry policy. A possibly dispatched mutation must first
be reconciled with the external system. If the outcome cannot be established,
record `waiting/reconciliation` and ask the owner; do not automatically repeat
it. Persist times in UTC; rebuild process-relative deadlines after restart.

## Execution and approval contract

V1 supports a small typed read-only adapter with an explicit eligibility and
verification contract, not arbitrary shell scripts supplied by the model. The
PR pilot needs an authenticated read-only GitHub adapter available on the hub.
There is no GitHub task adapter under `src/ciel` in the reviewed checkout;
a developer's local `gh` login is not a production integration. Budget the
adapter as part of stage three. Use a narrow REST client with installed or
standard-library facilities and a repository-limited fine-grained token with
only the read permissions needed for PR identity, check runs, and commit
statuses. Verify permissions against the selected endpoints. Store the token
owner-only on the hub, name its file in `FORBIDDEN_NAMES`, include configurable
credential paths in the existing secret protections, and never place it in
task data, prompts, logs, fixtures, or reports. Credential provisioning is a
separate explicit setup step; missing/revoked access waits visibly. Handle
pagination, rate limits, and ambiguous API failures without claiming success.

Inspect both check runs and commit statuses; a repository may use either or
both, so do not require both APIs to contain results. Use an explicit monitored
set identified by kind, name/context, and expected producer where available.
If it is unspecified, clarify or propose an observed set to the owner; do not
silently assume the visible checks constitute branch-protection requirements.
Missing expected results and an entirely empty set are unknown. For v1 strict
pass criteria, all monitored entries must succeed on the recorded head SHA;
queued/running/pending and completed neutral/skipped entries remain unknown.
This strict policy is intentionally narrower than GitHub's merge rules,
which can accept neutral/skipped checks. Failed checks stay unsatisfied and
may be polled until a bounded deadline. Reduce reruns/history to the applicable
latest result per identity, paginate fully, and recheck the PR head before
committing completion. Never mix head-SHA evidence with test-merge-SHA checks.
See [GitHub's status-check semantics](https://docs.github.com/en/pull-requests/reference/status-checks).

Correlation belongs in the existing `ActionRecorder.before/after`, keyed by
SDK `tool_use_id`, with task ID, step/attempt ID, and revision recorded beside
the existing journal fields. Keep the recorder last and all guard decisions
unchanged. Correlation metadata never grants authority.

Do not rely on a ContextVar set only in the runner: the installed SDK
0.2.139 spawns hook handlers from a long-lived reader. The offline reproduction
in `reports/2026-09-07-task-hook-context-repro.py` observed no runner context
in that callback. Bind immutable task context explicitly at the serialized
Brain/hook boundary, pin it per tool-use ID before dispatch, and carry that
association through post-hooks and late results. Prove old callbacks cannot
attach to the next task; absent or ambiguous binding must not be guessed.
A ContextVar is usable inside that explicitly bound callback scope, not as
an assumed transport across the SDK boundary.

The journal is currently best-effort and pruned, so correlation alone is not
a recovery ledger. Persist required attempt intent in the task transaction
before a resumable mutation can run, and retain enough reconciliation evidence
there to survive journal pruning. If this durable write fails, do not dispatch
that task action. Keep ordinary non-task recorder behavior unchanged.

Later mutating steps must persist their intent and attempt identity before
dispatch. Bind every permitted action to its exact normalized arguments,
resource identity, current preconditions, task revision, and applicable owner
authority. All existing tool, workspace, confirmation, and journal checks still
apply. A persisted approval record is not permission to bypass the broker.
Changed arguments or scope require a new decision; an already authorized,
unchanged action should not acquire a gratuitous second approval step.

For the first action-capable release, unattended work may prepare an action
but must yield into `waiting/owner` if the current policy requires attendance.
When the owner returns, present the concrete action, revalidate it, and invoke
it through the existing attended path. On restart, a pending question is
reconstructed as a fresh broker interaction; never deserialize a pending Future
or reinterpret an unrelated “yes” as task authorization.

Broader delegated background mutations need a separate, reviewed scoped
execution policy, expressed through task-scoped extensions to the existing
grants surface (`src/ciel/brain/tools/grants.py`), with resource, operation,
revision, duration, budget, and revocation enforced at dispatch. Do not accomplish that by weakening Witness globally or
turning a task lane into an implicitly attended lane.

A successful tool return is execution evidence, not proof of the requested
outcome. Read back the target before `done`. Prefer deterministic verifiers
for supported criteria. A missing response after dispatch is `unknown`, not
failure or success. Provider idempotency keys can prevent duplicates where
supported; elsewhere recovery must inspect external state or stop for review.
No general exactly-once guarantee is possible across the crash boundary.
For example, a sent message may have no authoritative lookup or idempotency
support. A timeout cannot establish whether it was delivered, and absence
from a weak read-back is not proof it was never sent. Such an attempt remains
uncertain for owner review; do not resend automatically or claim the recipient
read it. Readable post-state can reconcile some actions, not every action.

## Scheduling, interruption, and limits

Extend `Snapshot`/`Source` with task readiness in `schedule.py`; keep
`pick_next` pure. Existing human/confirmation/timer priorities stay ahead
of task work. Normally Vigil precedes tasks; after a configurable eligible-wait
bound, a task takes one step ahead of a non-urgent Vigil item. Give the snapshot
precomputed task age/overdue status and urgency of the actual eligible Vigil
candidate, not just whether any event exists. Never let aging bypass BUSY,
speech/held-thought gates, pending human input, or urgent work.

Measure age from persisted eligibility; a scheduled poll not yet due or a task
waiting for owner input is not starved runnable work. Reset the served task's
age after a step, preserve it across restart, and select tasks fairly. Pin
normal, aged, urgent, restart, and continuous-arrival cases in the ladder
probe. This prevents starvation by routine Vigil; it does not promise a
wall-clock bound during continuous foreground or urgent activity. Ensure the
new branch reaches actual dispatch when the maintenance slot is occupied,
rather than repeatedly selecting work the pipeline cannot enact.

Use the same scheduler integration in local and hub roles. Execute at most
one task step at a time. If a step needs the model, acquire the existing brain
turn lock and use a task snapshot assembled from persisted state, relevant
Atlas context, and fresh observations. Do not hold the brain while waiting for
a future condition or an owner response. Do not silently start another Brain
instance to hide contention or inherit ambient guard state across concurrent
turns.

Foreground input preempts background work: cancel safe reads, checkpoint,
release the brain/guards, and return the room. An external mutation already
sent is not undone by cancelling its coroutine; mark it for reconciliation.
Pause/cancel must fence late callbacks and prevent future dispatch. Report any
in-flight uncertainty honestly.

Add a `TasksConfig` in `config.py`, with defaults and README documentation in
the implementing change: enabled flag, store location, allowed task kinds,
active-task limit, step timeout, retry/backoff limits, polling bounds, eligible-wait aging bound, and
per-task/day execution budgets. Persist spend across restart. Bound SDK/model turns, steps, attempts, and wall time; leave dollar budgets
out of v1 rather than pretending estimated spend is a reliable ceiling. A timeout, missed deadline, or exhausted budget produces an explicit
wait/failure reason, never an infinite retry loop.

## Notification and task controls

Owner controls: create, inspect/list, pause, resume, cancel. Creation is tied
to an actual owner request and deduplicated using trusted ingress/tool-call
identity where available. Do not deduplicate by title alone, or infer new
background responsibilities from Atlas notes and reflected memory.

Chart shows outcome, status, next action, blocker, last verified observation,
and the evidence behind completion. Task details stay out of public lanes.
The voice path answers “what are you working on?” and “what is this waiting
for?” from the same records. Clarify an ambiguous pause/cancel target.

Material transitions enqueue notifications transactionally, then hand off to
Vigil with a stable dedupe key. Routine polling is silent. Keep a durable
owner-visible completion record even if an ephemeral Vigil item expires.
Mark delivery separately from work completion, and bound retries/backoff for delivery failures
without re-executing the task. Network delivery can be at-least-once unless
the transport offers durable receipts/idempotency; do not promise exactly-once
external speech or messages.

## Implementation sequence and acceptance gates

Status as of 2026-09-07: stage one is committed as `ecc13c3`, with 140 task
probe checks and the three storage-review acceptance scenarios passing.
[The stage-two plan](2026-09-07-task-controls-plan.md) details owner controls,
trusted request identity, private Chart routing, and the remaining record work.

1. **Records and recovery.** Add store, dataclasses, validated transitions,
   process ownership, schema handling, and a new `probe_tasks.py`. No background
   executor yet. Gate: creation, transitions, restart, competing owner, and
   stale-result rejection are deterministic with temporary state.
2. **Owner controls and visibility.** Register task tools, trusted request
   metadata, owner/private routing, and minimal Chart status and controls.
   Gate: the same task is inspectable and pausable across sessions; a public
   or unattended caller cannot create delegated responsibility or widen scope.
3. **One scheduler-driven read-only task.** Add the PR-check adapter, bounded
   runner, persisted polling/budgets, and local/hub arbitration. Gate: human
   input wins; waiting consumes no model turn; restart resumes the right task
   without requiring chat history. Include an owner-decision path: on a bounded
   monitoring deadline, ask whether to extend it. Restart while waiting, then
   bind the answer to the reconstructed question and task revision. A late yes
   after cancellation cannot resume it. PR disambiguation tests the separate
   clarification path; it is not a substitute for the broker's yes/no test.
4. **Verification and reliable reporting.** Add typed verifiers and a durable
   notification outbox. Gate: changed PR heads invalidate old evidence;
   pending/failed/unknown checks cannot count as passed; completion and its
   notification survive a crash independently of delivery.
5. **Guarded action recovery.** Add task/attempt/journal correlation and one
   supported mutation through existing guards. Start with an explicitly
   scoped file edit using expected content/hash, a snapshot, and read-back.
   Gate: kill the process before dispatch, after external success but before
   recording, and before notification. Recovery never blindly repeats a
   possibly completed action. Only then broaden supported workflows.

Each stage is one cohesive change with its own README/changelog update and
review. Do not bundle the current keyboard/diagnostic work into these changes.
No branch, commit, push, production config change, or deployment is implied
by this plan. The existing source autoreloader still applies during future
implementation; use disabled task defaults until the pilot is deliberately
activated. Source edits cause re-exec when each runtime's reload conditions
permit; the hub is only affected when its source changes (for example on a
requested sync), not by every unsynced Mac edit. Neither loop necessarily
restarts in the middle of every attempt. Test graceful reload separately from
forced process death. Polling must be idempotent and notification dedupe keys
must remain durable through repeated reloads.

## Probe and review requirements

New `probe_tasks.py` covers state/store, crash recovery, stale claims, resource
budgets, schema errors, owner scope, and adapter/verifier contracts. Use
subprocess termination around transaction/dispatch boundaries, not just clean
exceptions. All fixtures use temporary state with no live accounts or runtime
files. Scripted adapters record externally observed calls for replay checks.

Extend and run the probes for touched integration layers: `probe_ladder.py`,
`probe_hub_arbiter.py`, `probe_turns.py`, `probe_confirm_wire.py`,
`probe_shellguard.py`, `probe_vigil.py`, `probe_web.py`, and
`probe_hub_imports.py` as applicable. Inspect `probe_closure.py` if Atlas or
rotation ownership changes. Run `uv run --no-sync python -c "import ciel.hub.server"` after hub changes. Record actual probe counts, not targets.
For Chart changes, verify narrow layout, focus, and each changed state. For
source edits, verify the running spoke returns to `spoke ready`.

Pilot demonstration: create the PR task, interrupt it, restart the hub, change
the PR head, observe new check results, verify that completion cites the new
head, and see its persisted completion notice. Then repeat with failed checks,
missing credentials, an offline spoke, owner cancellation, and exhausted
budget. Mocks prove the contract; an explicitly authorized live pilot must
prove actual connector behavior before calling it operational.

## Remaining questions after cross-review

1. SQLite is the selected proposal; verify the schema/backup/ownership details
   remain smaller than a custom store rather than reopening the storage debate.
2. Does the proposed task slot preserve foreground responsiveness and give
   enough progress under sustained Vigil traffic?
3. Prove explicit task-context binding through the actual SDK dispatcher,
   especially late callbacks; runner-only ContextVar propagation failed.
4. Is the owner-attended action boundary sufficient for the first useful
   release? What precise policy would later delegated mutations require?
5. Can the proposed adapter and evidence schema reconcile a crash after an
   action without falsely claiming exactly-once execution?
6. Are task completion and notification delivery separated strongly enough
   that a missed notification cannot repeat the underlying work?
7. Does the PR pilot exercise enough of the intended lifecycle before we
   attempt repository editing and a complete “ship this feature” workflow?
