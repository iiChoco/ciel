# The owner can see and steer a task

Stage-two implementation plan — 2026-09-07. Builds on
[the durable-task plan](2026-09-07-durable-tasks-plan.md).
Stage one is committed as `ecc13c3`, “A task keeps its place”: 140 probe
checks and all three storage-review acceptance scenarios pass. This document
plans the next change; it does not start implementation or activate tasks.

Revised against the [stage-two review](../reports/2026-09-07-task-controls-plan-review.md)
and its [reproduction](../reports/2026-09-07-task-controls-plan-review-repro.py).
The installed SDK drops call metadata before in-process handlers, so the first
integration gate now proves authority at the turn boundary. Tool-use IDs remain
recording metadata. This revision also settles attendance, batch identity,
session separation, resource waits, and the two control paths' revision rules.
The appended revision check replaces per-turn private reconnects with authority
installed only after a successful drain. It preserves the Brain's warm client,
cost accounting, and module-level service bindings. Chart ingress IDs are minted
per message; a batch hash alone cannot recognize a retry whose membership changed.

## What the owner gets

An explicit request can become a saved task. The owner can ask what is waiting,
inspect the evidence, pause it, resume it, answer its question, or cancel it.
The same task and controls appear in Chart after a conversation or process
restart. Task details are available only through a private owner route.

Stage two has no runner. A newly saved task visibly waits for execution support;
Ciel says it saved the request, never that it has started watching. The first
supported request shape is a PR-check watch, prepared without contacting GitHub.
Scheduling, GitHub credentials and observation, automatic notifications, and
external actions remain in later stages. Arbiter aging remains stage-three work.

## Boundaries found in the current code

- [`TurnRequest`](../src/ciel/turn.py) carries lane and privacy, but no stable
  owner or request identity. Web and spoke queues currently discard much of
  their ingress identity when they enqueue text. Preserve identity before it
  reaches the brain; do not reconstruct it from the model's arguments.
- [`build_tool_server`](../src/ciel/brain/tools/__init__.py) is synchronous.
  [`Pipeline`](../src/ciel/pipeline.py) owns both local and hub lifecycles.
  Open and close the asynchronous task store in that lifecycle, then bind it
  to tools and Chart. Do not open SQLite in a tool-registry import.
- [`WebLink`](../src/ciel/remote/web.py) shares its hello/replay/broadcast path
  across clients. A hello's role and client ID are not independent credentials.
  Private task responses need an admitted owner connection, with an explicit
  audience; putting them in the shared replay ring is insufficient.
- [`Brain._hooks`](../src/ciel/brain/agent.py) puts Witness and the other guards
  ahead of [`ActionRecorder`](../src/ciel/brain/recorder.py). Keep that order.
  The [SDK reproduction](../reports/2026-09-07-task-hook-context-repro.py) shows
  that runner-only ContextVar propagation does not establish hook identity.
- [`ActionJournal.record`](../src/ciel/journal.py) is best-effort and returns
  no stable entry ID. Reserve a future journal reference in the task schema,
  but do not pretend current journal rows provide a durable recovery ledger.

## 1. Finish the records before enabling a store

Extend [`TaskStore`](../src/ciel/tasks.py) with a structured owner question and
answer. A question has an identity and task revision. Answering checks the
owner, question identity, expected revision, and waiting state in one
transaction. It records the answer and, where needed, a validated next step.
Scope, outcome, criteria, and existing budgets cannot be widened or refilled.
Answer text remains quoted data rather than execution instructions.

Add an atomic creation-with-resource-wait operation to the store. The current
`create()` writes `queued`, and `wait()` is a separate transaction; calling both
is not atomic. Write creation and wait history together, with no observable
queued interval. Reserve the ingress-to-request association described in step 2
in this schema change, and commit it with creation or return the existing task.

Keep plain resume for paused tasks. A task waiting on a question needs its
answer; an unrelated yes or a stale answer after cancellation cannot resume it.
An ambiguous answer leaves the question waiting. Store any proposed deadline
extension as data for the later bounded runner to validate; do not invent a
scheduler deadline contract in this stage. Broker reconstruction for the
stage-three monitoring-deadline demonstration remains a separate acceptance gate.

The controller supplies execution availability; the model cannot. Without a
runner, resuming a paused task, resuming a resource wait, or accepting an answer
that would otherwise queue work must finish in `waiting/resource`, atomically,
with history explaining that execution remains unavailable. Preserve question,
reconciliation, and exhausted-budget restrictions. Resume does not answer a
question, erase uncertainty, or refill allowances.

Reserve correlation as a one-to-many association between an attempt and its
tool calls: attempt ID, task revision at binding, SDK client generation,
tool-use ID, and optional journal reference. One attempt may make several calls.
Bindings cannot be reassigned to another attempt. Persist only necessary IDs,
not credentials or full tool responses. Wiring resumable external mutations to
these records stays in stage five. No runtime bindings are written in stage two:
there is no runner or attempt to bind. Fixtures exercise multiple bindings and
conflicts; an empty bindings table must validate on reopen. Hooks may record
tool-use IDs where the SDK supplies them, but these IDs never confer authority.

Use a new schema version for the changed committed schema. Preserve the
refuse-not-migrate behavior: an older or newer store is refused without changing
its bytes. Do not quietly reset a version-one store on the assumption that it
is disposable. Keep tasks disabled by default; fixtures create fresh stores.
Document the compatibility boundary before anyone enables stage two.

**Gate:** answer/reopen, duplicate answer, stale answer, cancellation, scope,
budget, atomic creation into wait, resume/answer without execution, ingress retry
associations, multiple tool IDs, conflicting bindings, empty bindings on reopen,
and schema refusal all have temporary-store checks. Keep the held-lock,
long-poll, and retarget regressions.

## 2. Carry trusted owner requests through the real tool path

Extend the lane queue record and `TurnRequest` with immutable owner principal,
request identity, ordered ingress identities, lane, attendance, and privacy. New
`TurnRequest` fields default to absent authority so existing keyword callers
remain valid and cannot accidentally gain task access. Map accepted local owner
input, admitted Chart/spoke input, and the configured Discord owner's private
messages to the same stable local owner. Neither channel names, client-supplied
roles, nor text claiming to be the owner establishes identity. Missing or
ambiguous authority disables task controls. Preserve the existing speaker
policy; a diagnostic bypass is not verified identity.

For tasks, `Origin.attended` means a live owner turn on any lane, private and
not Witness-engaged. It does not mean physical room presence. Document that
meaning on `Origin` and in README without changing transcript presence labels.
An owner's Discord DM qualifies; reflection and Vigil do not acquire authority
from an owner stored in config. Keep every task tool, including list/inspect,
out of Witness's `_CIEL_OBSERVERS`.

Retain spoke `say_id`; mint a random per-message Chart request ID and persist it
with the message in the resend ledger before sending. Reuse that exact ID on
resend and refresh. Keep `seq` for acknowledgements only: `clientId` is shared
across tabs through localStorage, while the sessionStorage-backed `saySeq` can
restart at 1 in each tab, so `(client_id, seq)` is not a request identity. Hash
the minted message IDs, never the ack sequence. Retain Discord message IDs and
mint voice/typed IDs when input is accepted. Change the `Lane` queue contract
and its producers/consumers together. Scope IDs by
admitted principal and a stable sender namespace, not an ephemeral socket, and
bound their size. Batch only a contiguous run with matching principal, lane,
sender namespace, privacy, and reply route. Define its request ID as a hash of
a canonical encoding of the principal, lane, sender namespace, and ordered
ingress IDs. Carry the original IDs alongside that hash; text is never the
identity.

Persist consumed ingress IDs with the originating task. An identical request
retry returns the same task or a specification conflict, including after reopen.
A resend after consumption must resolve its original request even if it arrives
alone after having belonged to a larger batch. Deduplicate repeats before
batching where possible; a partial overlap or a combination of IDs from different
saved requests must return a conflict/clarification, never mint another task.
That conflict applies to the whole batch: any genuinely new message in it cannot
save a task in that turn. Tell the owner to repeat the new part on its own.
Stage two supports one task per explicit request. A batch asking to watch two
PRs must clarify into separate new requests before creation; the model cannot
split a batch identity to manufacture two mandates.

Keep request context absent while `_drain_stale()` runs under
`Brain._turn_lock`. After the drain, inspect `_needs_drain`; only when no debt
remains install immutable request context, immediately before `query()`. A task
handler captures its binding once at entry, before any await; it contains principal,
request identity, privacy/attendance, client generation, and turn generation.
The controller validates it again at the transition boundary. Missing, revoked,
stale, or ambiguous bindings fail closed for reads and writes. Clear authority
and advance the generation at turn end, interruption, eviction, close, and
rotation, including exceptions and abandoned generators. A queued SQLite write
must not slip past invalidation: synchronize revocation and the transactional
authorization check so a control either commits under still-valid authority or
is refused. Cancelling its awaiting coroutine alone is insufficient.

Retain the private SDK client and the registry's module-level service bindings
across turns. Do not create per-turn handler closures or reconnect to establish
authority. The revision check reports cold connects of 5–20 seconds on the Mac
and 1–3 seconds on the hub; paying that per private turn would undermine voice
latency and reset the SDK's cumulative cost accounting.

Pin the reviewed ordering in the installed SDK harness: old control requests
are spawned before the abandoned turn's result is read, and their handlers enter
during the drain, while request context is absent. If the drain times out or
otherwise leaves `_needs_drain` true, the following turn receives no task
authority for its entire lifetime, even if the old result arrives during it.
Task tools are unavailable that turn and must return an explicit unavailable
result without reading or changing private records. Enforce that at tool entry
as well as exposure/guards so a cached tool definition cannot bypass it. Do not
clear debt or grant authority merely because a new query is being sent.

An in-flight handler retains the immutable binding captured at entry after an
interrupt. The SDK's `control_cancel_request` cancels its handler task; the
transactional recheck above still decides whether a submitted write may commit.
A stale handler that first enters after a drain timeout finds no authority,
including during the following turn. Subsequent turns may regain authority only
after their drain leaves no debt. Turns without trusted context likewise have
no task authority. The separate public client in step 3 remains required.

Do not match calls by name/arguments, depend on `_meta`, presume ContextVar
propagation through SDK reader tasks, or require a tool-use ID the in-process
handler never receives. This turn-boundary bridge is the first integration gate;
all task tools remain unavailable until it passes.

**Gate:** offline tests through the actual SDK reader and in-process MCP path
cover normal calls, parallel identical calls, retries, reconnect, interruption,
and an in-flight call awaiting storage, including SDK handler cancellation.
Prove that a stale call spawned before the next query enters with no context
during the drain; a drain-timeout turn exposes no usable task tools and rejects
calls through cached definitions for its whole lifetime. Pin recovery after a
successful drain, revocation at the transaction boundary, and consecutive clean
private turns reusing the same client with intact cost accounting. Pin an owner
DM's admission, reflection/Vigil denial, distinct minted IDs from two Chart tabs
with the same client ID and ack seq, resend/refresh identity preservation, exact
and partial batch retries across reopen, and clarification asking the owner to
repeat the new part alone. No account or model call.

## 3. Bind one service and expose narrow owner tools

Create `src/ciel/brain/tools/tasks.py` (new), following existing tool and binding
conventions. Offer create, list, inspect, pause, resume, answer, and cancel.
The model supplies only the request description and permitted task/control
arguments. Owner identity, attendance, privacy, and request identity come from
step 2. The model does not supply an expected revision: ordinary controls take
a task ID, and the controller reads and validates the current record inside the
same store transaction that applies the control. Chart supplies the revision it
rendered, which the same controller checks exactly. Question answers also name
the question they answer; their stored question identity/revision must match,
so a spoken stale answer cannot apply to a replacement question. Trusted callback
paths retain explicit revision fences. Do not implement tool controls as a
separate `get()` followed by an unchecked write or silently retry Chart conflicts.

For creation, accept only the supported PR-watch shape: a canonical repository
and PR reference plus an explicit set of monitored checks. Parse and validate
that shape offline into the runtime's scope, read step, and exact criteria.
If the target or check set is unspecified, ask for clarification; do not infer
branch-protection requirements or accept arbitrary operations or mutations.
External identity and the initial head are verified by the stage-three adapter.
Create directly into a durable resource wait explaining that execution is not
available; creation and this wait must commit atomically. A resume cannot turn
that disabled capability into a claim that work is running.

No owner tool exposes claim, dispatch, observe, retarget, complete, or arbitrary
state writes. Task creation requires an explicit owner request; Atlas entries,
reflection, quoted messages, or ordinary conversation cannot create a mandate.
Require private owner context for reads as well as writes. Withhold private task
summaries from public prompts and responses, not merely from the write tools.
Use a distinct public SDK client, created lazily under the same `_turn_lock`,
with `resume=None` and no session persistence. Never select histories through
`query(session_id=...)`: that argument has not been shown to isolate them.
The public client receives no task tools, private system context, recalled
memories, held notes, or private transcript backfill. Keep public histories
separate across different channel audiences too; recreate the public client on
an audience change. Close both clients on shutdown and revoke active authority
on interruption/rotation. Public results must not overwrite the private session
resume record. Reuse the warm private client with step 2's drain and authority
rules; retain existing reconnects for transport recovery and session rotation.

This is a Brain-level privacy fix: the shared history already risks exposing
memories and messages as well as future task results. Stage two includes it;
Chart is not exposed with the partition still owed. The cost is another SDK
subprocess and its permitted MCP servers once the public lane is used, plus
cold start on first use or an audience change. Pin distinct clients, absent
public task tools/context, private-task-result → public-turn isolation, audience
switches, reconnect, and private resume in `probe_turns.py`. A system instruction
to keep secrets is not the boundary.

Stage-two create/pause/resume/answer/cancel implement the owner's explicit
request to change a saved record; none dispatches an external action or starts
execution. They need no second broker approval. Journal these controls through
the shared controller so Chart and SDK paths are both covered, with one entry
per applied control and no duplicate recorder entry. Accept a `None` journal
when journaling is disabled; durable task history remains authoritative. Journal
failure cannot turn an already committed control into an apparent rollback.
List/inspect stay read-only. Do not weaken Witness or make the recorder an
authorization guard.

Keep confirmation requirements for regrettable external actions intact. The
first external mutation in stage five must bind broker approval to the concrete
operation, task, arguments, and revision and recheck before dispatch; add its
`describe_call` formatters then. This stage introduces no generic bypass for
future actions. The separate stage-three deadline gate remains as stated above.

`Pipeline` opens one store when configured, binds a shared controller to tools
and Chart, and drains it on shutdown. The hub owns it in split mode; standalone
owns its local store; the spoke owns neither. An open failure disables the task
surface with a useful unavailable status while normal conversation continues.
Keep all swappable choices in `TasksConfig`, including any stable owner mapping
needed by the existing single-owner model, and document their precise meanings.

**Gate:** cross-session create/list/pause/resume/cancel, durable resource wait,
request retry without duplication, unsupported requests, owner-only reads,
public/unattended denial, tool-current/Chart-stale revision behavior, question
identity, journal disabled/failing without double recording, disabled/open-failed
store, and clean shutdown all pass with temporary stores and no runner.

## 4. Add the Chart surface on the existing wire

Extend [`wire.py`](../src/ciel/wire.py), `WebLink`, and
[`HubServer`](../src/ciel/hub/server.py) with bounded task list/detail requests
and owner control commands. Commands carry a request identity and expected
revision. Return explicit results to the requesting admitted owner connection.
Keep task payloads out of the generic hello and shared replay ring; reconnect
fetches a fresh owner-filtered snapshot. Advertise protocol support with a
`tasks: true` hello capability, carrying no task data; an older server lacking
it makes the newer page hide the section. Report disabled/open-failed storage
separately as unavailable.

Owner eligibility means admission on the Chart path in the existing single-owner
model: local reach under the loopback/Origin policy, or the hub token remotely.
A hello role or frame owner field grants nothing. A remotely bound hub serving
task frames must require its token, including on loopback connections to that
hub, using `require_token`. Explicitly log and drop task commands from the
server's bound spoke seat (`ws is self._spoke`); the interview room never reaches
`/ws`. Spoke-originated owner conversation still enters through the validated
turn path in step 2. Preserve admission and Origin checks; do not invent a
second role-based credential.

Use one shared controller for tools and sockets so authorization, validation,
and journaling cannot diverge. Async store calls must not block the socket
handler. Bound and track pending handlers, cancel/drain them on shutdown, and handle a disconnected
caller without treating cancellation as proof that a transaction rolled back.
A repeated command returns its persisted result where available or an explicit
revision conflict followed by a fresh view; it never silently repeats a change.

In [`chart.html`](../src/ciel/remote/chart.html), add a compact Tasks section
separate from the ephemeral working-agent roster. Show outcome, status, next
step, blocker/question, last observation with age/source/head, and completion
evidence. Offer applicable pause/resume/answer/cancel controls. Use text-safe DOM
rendering for all task and evidence strings. Refresh after conflicts and reconnect;
show unavailable/offline/pending/error states without claiming success early.

Reuse the Instrument tokens and components without changing the base palette.
Verify keyboard navigation and focus, narrow layout, empty/populated states,
long descriptions, waiting questions, cancelled/completed tasks, stale controls,
reconnect, and storage failure. No optimistic completion display.

**Gate:** two admitted owner views agree after a control action and reconnect;
unadmitted, spoke-seat, and interview callers receive no task detail; a claimed
role cannot bypass admission, and remote token requirements hold even for a
loopback peer of a remotely bound hub; public conversation stays
private-data-free; malicious task text renders literally; disconnected retries
and stale controls cannot repeat or redirect an action.

## 5. Review and finish stage two as one change

Implementation order is 1 → 2 → 3 → 4. Each gate passes before the next surface
is exposed. One writer owns the checkout. Preserve the existing speaker and
shortcut changes; re-read shared files before editing and stage only stage-two
hunks. No parallel arbiter work or separate worktree is started by this plan.

Extend the relevant existing probes and add a dedicated task-tool/context probe
if that keeps `probe_turns.py` readable. At minimum, run:

```sh
uv run --no-sync python scripts/probe_tasks.py
uv run --no-sync python scripts/probe_turns.py
uv run --no-sync python scripts/probe_ladder.py
uv run --no-sync python scripts/probe_hub_arbiter.py
uv run --no-sync python scripts/probe_discord.py
uv run --no-sync python scripts/probe_spoke.py
uv run --no-sync python scripts/probe_web.py
uv run --no-sync python scripts/probe_wire.py
uv run --no-sync python scripts/probe_confirm_wire.py
uv run --no-sync python scripts/probe_files.py
uv run --no-sync python scripts/probe_shellguard.py
uv run --no-sync python scripts/probe_hub_imports.py
uv run --no-sync python -c "import ciel.hub.server"
uv run --no-sync python reports/2026-09-07-task-store-review-repro.py --accept
git diff --check
```

Run any new probe and every other touched-layer probe too. All fixtures use
temporary state; do not read production config, tokens, recordings, or stores.
Preserve the offline SDK reproduction and add positive binding/stale-callback
checks rather than replacing its demonstrated negative case. Preserve the review
reproduction as historical evidence; its batching assertions describe the old
behavior, so pin the corrected identity behavior in layer probes rather than
expecting those old assertions to pass after implementation.

Before running `probe_hub_imports.py` for this change, fix its fixture config:
it currently constructs `Config()` with a temporary `state_dir`, proactive
paths, and file workspace, but leaves tasks disabled and their directory at its
default. Explicitly use `TasksConfig(enabled=True, directory=tmp / "tasks")` in
an enabled fixture and retain a disabled case. Check construction opens no store,
then exercise asynchronous hub store open/close without a socket or SDK process.
Audit all other state/token paths reached by construction and lifecycle and
redirect them to temporary fixtures or disable those features. A temporary
`state_dir` alone does not relocate independently defaulted paths: memory is
enabled by default and `MemoryConfig.dir` independently defaults to
`~/.ciel/memory`, which construction already reaches. Redirect that directory
explicitly along with the other paths. Preserve the probe's refusal of Mac/audio
imports.

Add `tasks.sqlite3`, `tasks.sqlite3-journal`, and `owner.lock` to `FORBIDDEN_NAMES`
in `brain/permissions.py`, including the journal's full basename. Pin all three
in `probe_files.py` and `probe_shellguard.py`, including a broad home workspace
represented entirely by fixtures.

Update README with supported requests, owner controls, configuration, privacy,
reconnect/error behavior, and the explicit absence of execution. Add one stage-two
changelog entry with actual probe counts and references to the review findings.
Check the running spoke returns to `spoke ready` after the last source edit.
Review the exact proposed change independently of the other uncommitted work;
commit stage two separately only when requested. No dependency, production
activation, push, deployment, or live GitHub pilot is part of this plan.

## Acceptance demonstration

Using temporary fixtures, an owner saves one PR-watch request and is told that
execution is unavailable. A second private session and Chart find the same ID.
A pause from one view makes an old resume from another fail visibly, then that
view refreshes to show the paused state. A fresh authorized resume leaves the
task waiting for execution support. A resend after consumption and after a
changed batch boundary resolves the saved task or a visible conflict, never a
second task. A mixed old/new batch conflict asks the owner to repeat the new
part alone. Two Chart tabs sharing a client ID and ack seq can save distinct
requests because each message has its own minted ID. An owner's Discord DM can
save a request. Reopening the store retains the task and its history. A seeded
owner question survives reopen, accepts only its matching answer once, and rejects that answer after
cancellation. An accepted answer cannot queue execution without a runner.
Public and unattended callers see no private task data and cannot create or
control responsibility, including after a private SDK result. A stale call
enters during the drain without authority; after a drain timeout, task tools
remain unavailable throughout the following turn with a visible explanation.
The next successfully drained owner turn can use them on the same private
client. The demo performs no GitHub calls and starts no background execution.
Passing this gate makes stage three ready to implement.
