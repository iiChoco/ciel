# Ciel can act independently

Foundation plan — 2026-09-08. Proposed, not implemented.

Revised against the supplied critique; see the
[review response](../reports/2026-09-08-independent-action-review-response.md)
for accepted changes and qualifications.

## The idea

Ciel can accept responsibility, carry it forward between conversations, and act
within authority the owner has deliberately given her. She remembers what she
is trying to accomplish, checks what has changed, takes a bounded next step,
waits when appropriate, and returns with evidence of what happened.

The owner should be able to say “take care of this,” leave, and later ask
“What happened?” The answer includes what Ciel did, what remains, and what
needs the owner's decision. Independence includes knowing when to stop.

This is a reusable capability. Its first feature is
[events from the inbox](2026-09-08-email-calendar-plan.md): confirmed email
commitments become verified calendar entries. That feature supplies concrete
source and action adapters; the foundation owns their execution contract.

This plan builds on [durable tasks](2026-09-07-durable-tasks-plan.md) and
[owner controls](2026-09-07-task-controls-plan.md). Their storage, authority,
and recovery decisions remain in force. The owner's 2026-09-08 direction
separates the general capability from its first application and replaces the
earlier PR-check pilot with the inbox feature. PR monitoring is deferred.

The current request authorizes planning. It does not activate a background
service, grant account access or standing action permission, or authorize
implementation, dependency changes, commits, or deployment.

## What belongs here

| Foundation owns | Each feature supplies |
|---|---|
| Owner mandate, limits, lifecycle, and derived tasks | Eligible triggers and intended outcome |
| Broker-owned grants and dispatch authorization | Concrete operation, target, payload, and preconditions |
| Persistent waits, bounded scheduling, and retries | Source cursor and domain-specific polling needs |
| Attempt identity, action journal, and reconciliation state | Provider identity, idempotency mechanism, and reconciliation reads |
| Evidence-gated completion and durable notice intent | Typed completion criteria and authentic observations |
| Private status, pause/cancel/revoke, and questions | Feature-specific preview, wording, and controls |

The runtime must not contain Gmail filters, calendar field rules, or PR check
names. Those belong in adapters. Build only the interfaces needed by the first
feature and a synthetic adapter; a plugin framework or arbitrary workflow
language is not part of this foundation.

## Existing base and missing work

| Existing source | Reuse and remaining work |
|---|---|
| [tasks.py](../src/ciel/tasks.py) | Private records, revisions, attempts, evidence, questions, recovery, and notice intent exist. Runner, grants, derived mandates, and delivery acknowledgements still need work. |
| [task_controls.py](../src/ciel/task_controls.py) and [task_context.py](../src/ciel/task_context.py) | Owner/private admission and authority fencing exist; creation currently validates PR watches. Dispatch feature-specific validation through a small explicit adapter registry. |
| [schedule.py](../src/ciel/schedule.py), [pipeline.py](../src/ciel/pipeline.py), [hub/server.py](../src/ciel/hub/server.py) | Extend the shared arbiter with bounded task opportunities. Keep one brain owner. |
| [confirm.py](../src/ciel/confirm.py) and [brain/toolguard.py](../src/ciel/brain/toolguard.py) | Per-action confirmation exists. Add persisted grants and broker-owned per-dispatch checks. |
| [brain/witness.py](../src/ciel/brain/witness.py) | Reflection and proactive model turns remain read-only outside the notebook. |
| [journal.py](../src/ciel/journal.py) and [brain/recorder.py](../src/ciel/brain/recorder.py) | Reuse action history; add durable dispatch intent and stable task/attempt/action correlation. |
| [proactive/policy.py](../src/ciel/proactive/policy.py) and [remote/chart.html](../src/ciel/remote/chart.html) | Reuse private delivery policy and owner views; add durable acknowledgements and grant controls. |

Read the current files before implementation: other work is uncommitted in this
checkout. One writer owns the checkout. Preserve its existing changes.

## Implementation starts from the landed task controls

The task store, owner controls, and Chart task view landed as commit 45e4fd7
("The owner can see and steer a task") with their reviews and probes. That
commit is the baseline this plan builds on; its schema is the version two the
origin migration below starts from. Other work in the checkout is unrelated
and uncommitted; preserve it, and do not stage or commit it as part of this
plan.

## Responsibility and authority

An explicit owner mandate records outcome, source scope, permitted operations
and targets, constraints, limits, expiry, and completion criteria. Observation
of the world may trigger work only within that mandate. An email, webpage,
model response, or ordinary proactive turn cannot create permission.

Support both a finite task and a standing mandate that can derive finite tasks.
A trusted runtime operation derives a child by inheriting owner and narrowing
scope; it records the parent and consumes the parent's durable allowances.
Do not fabricate an attended Origin or let background model calls create
new owner mandates. A standing mandate remains active until paused, expired,
cancelled, or exhausted; completing a child does not complete the parent.

Separate three decisions:

- Clarification establishes what the owner meant or supplies missing facts.
- Approval authorizes a concrete action or a specific standing scope.
- Verification establishes whether the intended result actually exists.

An answer to a clarification cannot silently widen authority. Bind questions
and answers to their task revision; stale replies after cancellation or a
changed proposal cannot restart work.

### Automatic actions still pass through Proof Obligation

The owner approves a concrete standing scope through the existing confirmation
broker. Persist its principal, scope, approval provenance, revision, expiry,
limits, and revocation. Config can enable the mechanism but cannot mint a grant.
Widening scope needs fresh owner approval. Grant controls require the same
private owner admission as task controls.

Every mutation passes through a broker-owned authorization operation, whether
it uses a live approval or a standing grant. Bind the resulting authorization
to the task, attempt, operation, exact target, payload digest, observed
preconditions, and grant revision. Recheck these immediately before dispatch.
Scope, expiry, and budgets must be enforced by runtime code, not a prompt.

A dedicated runner dispatches approved typed operations through guarded
adapters. Do not add writes to an unattended MCP allowlist, suppress the broker
to obtain permission, or disable Witness. Model-assisted planning/extraction
has only the bounded context and read capabilities its feature needs; it
cannot choose its own authority or call an unguarded writer. Existing SDK
tool paths retain their guards and recorder-owned context binding.

Pause/revoke/cancel stops new dispatches. Already-dispatched work may finish;
retain reconciliation obligations and report the actual outcome.
Undo is a new guarded action against current remote state, never a general
bypass or an automatic promise that every effect can be reversed.

## Minimum records and admissible origins

Milestone 2 introduces a tagged `TaskOrigin = HumanOrigin | DerivedOrigin`.
Migrate existing schema-two Origin JSON to HumanOrigin without changing its
owner, request identity, private admission, or human lane. The existing
`create` path continues to accept attended private HumanOrigin only.

| Proposed record | Required fields and validation |
|---|---|
| HumanOrigin | kind=human, owner, request_id, lane, attended=true, private=true, ingress_ids; same admission checks as current Origin |
| DerivedOrigin | kind=derived, owner, parent_mandate_id, parent_revision, grant_id/revision, adapter_namespace, trigger_key, source_revision, derived_at; no human lane or claimed attendance |
| StandingGrant | id, owner, revision, approved scope and digest, broker approval reference, expiry/revoked_at, operations/targets, policy version, lifetime/window limits |
| GrantDraft | id, owner, revision, execution host/account bindings, normalized scope, digest, validation status; never executable |
| DispatchAuthorization | task/attempt/action IDs, operation/target, payload digest, precondition digest, grant revision or concrete approval reference, deadline |
| FeatureRecord | owner, registered namespace, record_key, record revision, bounded payload; schema version belongs to the namespace registry |
| Proposal | id, owner, revision, event record key, parent mandate ID, proposed operation/target/payload digest, source revision and evidence, status (open, approved, superseded, dismissed); inert, never claimable |
| DeliveryAttempt | notice ID, private destination identity, attempt ID, outcome/acknowledgement, retry time; separate from completion |

Expose a runtime-only `derive_task(parent_id, expected_revision, trigger_key,
source_revision, spec, step)` operation, not an MCP tool accepting DerivedOrigin.
The controller constructs origin from registered adapter context. In one
transaction check the parent is an active standing mandate, owner and grant
match, authority and budgets still permit the scope, and the child narrows it.
Insert the child, parent accounting, and trigger mapping atomically. V1 derives
only from a standing human mandate; nested autonomous mandates are unsupported.

Deduplicate on (owner, parent mandate ID, adapter namespace, trigger_key),
where the adapter builds trigger_key from the event's stable identity, the
source revision, and the operation. A replay of the same message lands on the
same key and spends nothing.

A new source revision may revise a child in place only while that child has
no committed dispatch intent and the revision leaves its required scope
unchanged. The revision invalidates the child's open questions and any
pending approval, and in the same transaction records the superseded
revision as a replay alias of the child, so replaying the older message
neither creates another task nor rolls the candidate back. Once dispatch
intent exists, that attempt's payload, identity, and key are frozen: the
task finishes its read-only reconciliation against the remote state, and
only then does the adapter propose the next operation against what is
actually there. A revision that would need a different scope is never an
in-place revision; it is a proposal.

A terminal child is never reopened. The store makes completion final, and
its evidence and notice keep their original meaning. A later source revision
about the same event derives a new child only when the operation it needs is
inside the parent's grant; derive_task keeps its containment check without
exception. When the needed operation is outside the grant (an update or a
deletion under a create-only grant), the adapter records a Proposal instead:
an inert, revisioned feature record tied to the event and its source
evidence, visible in the owner's view, never claimable by the runner. A
private attended owner turn approves the exact current proposal revision
through the broker, and that approval creates one finite HumanOrigin task
whose scope is the approved operation and whose origin carries the approval
reference; the proposal becomes approved and links the task. A replay, a
stale answer, or a source revision that supersedes the proposal creates no
task and widens no grant. Waiting is execution state, never a source of
authority. The persistent thing across that chain is the feature's event
record, held as a FeatureRecord, which links every proposal and task that
acted on the event. Deserialization and store validation handle both
origin variants. Generic permission
and privacy checks consult inherited owner/parent authority, never pretend the
derived work was a human turn. Existing public/unattended `create` refusals stay.

### Feature records without feature columns

The store owns `feature_namespaces(owner, namespace, schema_version)` and
`feature_records(owner, namespace, record_key, revision, payload_json)`.
It understands owner isolation, revisions, size/record-count limits, and atomic
transactions; it does not interpret a Gmail history ID or calendar tombstone.
Registered adapters supply pure payload validators and versioned payload
migrations. Namespace registration is application code, never a model-selected
string or executable blob in the database.

Use a bounded transaction API to commit a feature write-set together with a
child/checkpoint/outbox transition, including expected record revisions.
An adapter cannot run arbitrary SQL or commit across namespaces. Network/model
calls stay outside the worker transaction. Core schema migration creates the
facility; adapter migrations advance only that namespace's version. Missing or
unsupported adapters disable their tasks and preserve their records; ordinary
conversation and compatible namespaces remain usable. Probe rollback, namespace
isolation, unknown versions, and restart with persisted derived origins.

## Setup and grant approval

Chart is the v1 setup surface. Add a private owner form that gathers feature
scope from adapter-supplied fields, presents resolved account/target identities,
and saves a versioned GrantDraft. Voice or Discord may open this setup and later
pause/revoke it; the yes/no broker is not asked to choose an account or target.

The owner reviews the complete normalized scope in Chart, then requests one
final approval. The controller binds the displayed draft revision/digest to a
broker request and shows its yes/no question on that private Chart session.
The broker owns approval validation/recording behind the surface. Editing the
draft invalidates a pending question. Timeout, reload, or a stale response
leaves a draft, never a grant. A fresh approval request can follow; no broker
call remains alive while a form is being filled out.

Activation commits the approved grant and mandate together after rechecking
account/target identity and draft revision. Store the approval provenance in
the same durable transaction boundary as the grant; journal projection follows
the foundation's repair rules. Setup and all draft fields remain private.

### Preview needs no grant

The owner decides whether to grant anything by looking at a preview, so
preview cannot depend on a grant. A preview is a finite task with a
HumanOrigin, created through the existing `create` path from an attended
private turn, whose scope holds read and extraction operations only and
whose targets name the source and window. It runs on the same runner, spends
its own poll and model allowances, persists candidate records in the
feature's namespace, and derives no children. A candidate the owner picks
for a one-off action becomes a separate task under a concrete approval.
When a standing grant is later activated, the namespace's checkpoints and
dedupe identities carry over, so preview work is not repeated and a
previewed candidate is not imported twice.

## Execution and persistence

The proposed `src/ciel/task_runner.py` claims one bounded step, gathers
current observations, prepares an operation, checks authority, records intent,
dispatches, verifies, and checkpoints/completes or persists a wait.
Use small typed protocols and factories following neighboring code.
The adapter's prepare phase is pure/read-only; mutation exists only in the
authorized dispatch phase. Unsupported adapters cannot claim executable work.

The hub owns execution in split mode. Standalone owns its local runner.
The spoke never executes a duplicate. Extend Source/Snapshot with task
readiness and precomputed eligible-wait age and actual Vigil-candidate urgency.
Keep pick_next pure. The normal order is confirmation, due timers, voice,
typed, web, Discord, Vigil, then one task step. An aged eligible task moves
ahead of nonurgent Vigil only; urgent Vigil stays ahead. Use the existing
policy's min_importance_to_message threshold to classify the actual eligible
candidate's urgency, even when delivery will be local. Default task_aging_s is
300, a proposed TasksConfig field.

BUSY, active speech, held thoughts, and pending human input still exclude task
dispatch. Age runs from persisted eligibility, resets after a served step,
and does not accrue while waiting for a future poll or an owner answer.
Choose oldest eligible tasks fairly; no deadline is guaranteed during
continuous human or urgent work. Waiting occupies no model turn, transaction,
or permanently pending broker call. A model extraction does occupy a bounded
turn as specified below.

Persist retries, next eligible time, attempt limits, model/poll/write budgets,
questions, grants, and delivery state. Resume and restart do not refill limits.
Periodic allowances reset only at persisted, defined window boundaries, never
on process startup; lifetime allowances do not reset. Feature policy sets
values, while runtime code accounts for parent and child use atomically.

Extend the existing task-store ownership boundary with versioned migrations,
not a parallel state engine. Preserve current task records and fail visibly
on unsupported/corrupt schemas. Keep state owner-only, secrets and internal
state forbidden to model file/shell tools, and error logs redacted.
Retention must preserve unresolved actions and the dedupe identities needed
for supported replay windows.

### One model call has one bounded context

Add `src/ciel/brain/extract.py` with a protocol-backed
`extract_json(prompt, payload, schema, limits)` operation. Follow
[the interview backend's standalone structured call](../src/ciel/interview/brain.py)
as a construction pattern, not as a shared session or dependency on interview
state. Construct a fresh ClaudeSDKClient for each extraction: fixed extraction
system prompt, tools=[], allowed_tools=[], mcp_servers={}, setting_sources=[],
resume=None, dontAsk permissions, explicit tool denials, and schema output.
Use a private empty working directory and no application conversation resume.
Do not call Brain.respond, build_system_prompt, or pipeline world-prefix logic.

The call receives only the bounded source payload and declared extraction
context (for example, the approved timezone), with no memory/Atlas/world chips.
Validate structured output again in runtime code. Never fall back to the
ordinary conversational client if this path fails. Verify SDK transcript and
debug-output handling during implementation so bodies do not leak into normal
Trace or reports; disclose any unavoidable provider/SDK retention at setup.

Each extraction consumes a scheduled task step, a model-attempt allowance,
and the same exclusive model-turn lease used by normal Brain calls. Expose a
small Brain lease method rather than a second lock; acquire it once, without
nesting a normal response. Configure a timeout and attempt/cost limits. Human
input cancels a running extraction, closes/drains its fresh client under bounded
cleanup, and fences any late result before yielding the lease. An interrupted
attempt remains spent. Waiting for mail or owner input consumes no model call;
actually extracting an event does. Pin concurrency, isolation options, cleanup,
cost accounting, and late-result rejection using a fake SDK client.

## Recovery and evidence

Before sending a mutation, durably record task/attempt/action identity, target,
payload digest, authorization, preconditions, and reconciliation recipe, and
correlate it with Inverse. Failure to persist intent or required journal state
prevents dispatch. A later result/journal projection can be repaired from that
intent; it must not cause the mutation to be repeated.

If a crash or timeout leaves the outcome uncertain, recovery asks the adapter
what happened. It never blindly repeats a possibly completed effect. Require
each writable adapter to declare and prove its identity/idempotency and
reconciliation behavior. If the provider cannot establish a safe next step,
persist a reconciliation wait and involve the owner.

Read-only recovery can continue for an already-dispatched action after the
mandate is revoked; it cannot issue another mutation under the revoked grant.
Remote owner edits and changed source revisions invalidate stale preconditions.
Use provider version checks where available. A cancelled task records any
effect discovered by recovery without reactivating itself.

Completion requires authentic, fresh evidence matching the current task
criteria and target revision. A successful tool return or a model's “done”
is insufficient. Store completion and its notice intent atomically.
These mechanisms do not promise exactly-once effects across arbitrary services.

## Visibility and reporting

Private owner views show outcome, next step, wait reason, current authority,
allowances, actions taken, evidence age, and unresolved effects.
Pause/resume/cancel/revoke and question answers use revision checks.
Public lanes, shared replay, and the interview room receive no private task
details or action authority.

Reuse Vigil's delivery timing, quiet hours, and private channels. Persist notice
intent separately from delivery attempts/acknowledgements so a crash after
completion cannot lose the result. Channels without idempotent delivery may
duplicate a notice after an uncertain send; document that limit.
Chart remains an authoritative private receipt. Muting notifications and
stopping execution are separate, clearly named controls.

All swappable choices live in documented config dataclasses. Reuse existing
TaskConfig fields where their meaning fits; put shared grant/runner choices
there or in a narrowly justified shared dataclass. Feature defaults belong to
the feature. Effective authority is always the intersection of configured
limits, the stored grant, and the individual task's scope.

## Foundation milestones

1. **A task gets another turn.** Add the adapter contract, bounded runner,
   shared arbitration, restartable reads/waits, the namespace-record
   facility, and the isolated extraction call, using a synthetic adapter.
   Preview-shaped finite tasks run on this milestone alone.
   Gate: human input wins, and a running extraction's late result is
   rejected; restart resumes the next step; stale callbacks fail.
   Landed 2026-09-08 (`task_runner.py`, `brain/extract.py`, schema three in
   `tasks.py`, the TASK rung in `schedule.py`), with `probe_task_runner.py`
   and `probe_extraction.py` pinning the gate; see the changelog entry
   "A task gets another turn".
2. **Permission has a precise scope.** Add the HumanOrigin/DerivedOrigin
   schema migration, runtime-only derive_task validation with
   operation-scoped trigger keys, grants and GrantDraft, the Chart scope
   form, persisted questions, limits, and private owner controls through
   the broker.
   Gate: no grant, wrong owner/target, expiry, revocation, stale approval, or
   exhausted parent budget all prevent dispatch; a completed child is never
   reopened; a revision needing an out-of-grant operation yields an inert
   proposal, and only the owner's approval of that exact proposal yields a
   task; a child with dispatch intent is never revised in place.
   This milestone is larger than the other three together, so it lands as
   two verified changes: first the records (origin migration, derive_task,
   grants, limits) with their probes, then the Chart draft and approval
   surface. The gate applies to both; nothing derives
   work under a grant until the second change exists.
3. **An interrupted action can be understood.** Add durable intent, journal
   correlation, guarded dispatch, reconciliation, and evidence verification.
   Gate: a synthetic external effect survives process-kill scenarios without
   blind repetition; uncertainty waits; owner edits are preserved.
4. **The result reaches the owner.** Add durable delivery acknowledgements,
   private receipts, and authority/status controls.
   Gate: completion survives a crash before notification; public-lane requests
   fail; notification mute does not masquerade as execution pause.

These are implementation milestones inside one foundation plan, not four
separate user-facing features. Keep changes cohesive and individually verified.
The inbox feature can develop read-only preview alongside this work, with
agreed file ownership or separate worktrees if parallel work is later requested.
Its automatic writer is blocked until all four foundation gates pass.

## Acceptance and verification

Prove the foundation with a deterministic fake source and writable fake
resource in temporary directories. The scenario creates an owner mandate,
derives work, interrupts a dispatched effect, restarts, reconciles it, verifies
the result, and delivers its receipt. Repeat with cancellation, revocation,
missing authority, changed resource version, unavailable journal, exhausted
budgets, and a stale question answer. Then complete a child, deliver a new
source revision for its event, and check that the completed child is
untouched, a proposal exists and no executable task does, approving the
exact proposal creates one bounded task, and a replay, a stale answer, or a
superseding revision creates none. Then dispatch a create, lose its
response, deliver a revision that changes the time, and check that the
frozen attempt reconciles first and the change arrives as a proposal against
the reconciled remote state, with the older revision retained as a replay
alias. Then run a
grant-less preview task, restart it mid-extraction, activate a grant, and
check that its checkpoints carry over and no candidate is imported twice.
No Gmail account is needed to establish these invariants.

Add `scripts/probe_task_runner.py`, `scripts/probe_task_authority.py`,
and `scripts/probe_extraction.py` (proposed files). Pin derived-origin
round trips and admission, namespace migration/isolation, draft-edit races,
exact scheduling order, and isolated SDK options/model-turn accounting. Extend/run existing `probe_tasks.py`, `probe_task_tools.py`,
`probe_task_wire.py`, `probe_ladder.py`, `probe_hub_arbiter.py`,
`probe_confirm_wire.py`, `probe_vigil.py`, `probe_turns.py`, and
`probe_hub_imports.py` when their layers change. Follow the repository's probe
style and report measured before/after counts.

Use `uv run --no-sync python scripts/probe_task_runner.py` once created, with
corresponding commands for touched probes. Hub changes also require
`uv run --no-sync python -c "import ciel.hub.server"`.
After source changes, verify spoke readiness as required by AGENTS.md.
Check Chart's narrow layouts, focus, offline/pending states, and private routing.

Each implementation change needs its README/changelog update and applicable
probes. No new dependency or codename is proposed. This documentation-only
change needs link/command checks and `git diff --check`, not runtime probes.

**Foundation done:** a bounded, approved task can proceed, wait, recover, verify,
and report independently using a synthetic adapter.

**First feature done:** the inbox adapter uses that same mechanism to put a real
confirmed commitment on the chosen calendar, under separately activated scope.
