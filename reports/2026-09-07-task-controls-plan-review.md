# Review: "The owner can see and steer a task" — 2026-09-07

Reviewed `design/2026-09-07-task-controls-plan.md` (stage two) against the
code it names: `tasks.py` as committed in `ecc13c3`, `turn.py`,
`brain/agent.py`, `brain/tools/__init__.py`, `brain/recorder.py`,
`brain/witness.py`, `confirm.py`, `remote/web.py`, `remote/discord.py`,
`hub/server.py`, `wire.py`, `remote/chart.html`, and the installed SDK
(`claude-agent-sdk 0.2.139`, `_internal/query.py`). Three findings are
reproduced by `reports/2026-09-07-task-controls-plan-review-repro.py`
against the real SDK dispatcher over an in-memory transport and against
the real links with fixture messages; nothing under `~/.ciel` is read or
written. No code was changed. Findings are ordered by how much they would
change the plan.

## What the plan gets right

- **Identity is carried, never reconstructed.** "Preserve identity before
  it reaches the brain; do not reconstruct it from the model's arguments"
  is the sentence the whole stage rests on, and it is the right one. A
  model that names its owner is a model that can be talked into naming one.
- **The owner tools are narrow.** Create, list, inspect, pause, resume,
  answer, cancel — and nothing that claims, dispatches, observes,
  retargets, or completes. That keeps stage one's rule ("the runtime owns
  the transition") intact while giving the owner something to hold.
- **Saved is not started.** "Ciel says it saved the request, never that it
  has started watching" prevents the most likely lie a stage without a
  runner could tell. Creating straight into a durable resource wait makes
  the Chart honest by data rather than by prose.
- **Refuse, never migrate** survives into version two, including the
  explicit refusal to reset a version-one store on the assumption that it
  is disposable.
- **Private frames for private data.** Keeping task payloads off the
  shared replay ring and out of the generic hello is exactly the
  distinction `wire.py` already draws between broadcast and addressed
  frames; the plan applies it correctly.
- **The review reproduction stays.** Keeping the negative ContextVar
  demonstration and adding positive checks beside it is how the project
  keeps a hard-won fact from being un-learned.

## Findings

### 1. Gate 2 cannot pass as written: no tool-use ID reaches an in-process tool

The plan's first integration gate requires binding request context to
"the actual tool-use ID" and proving "how that ID reaches an in-process
task tool through the installed SDK dispatcher", adding that "matching
tool names or arguments is not a substitute". The installed dispatcher
offers no such channel.

`query.py:685` builds the call as
`CallToolRequestParams(name=params.get("name"), arguments=params.get("arguments", {}))`
and invokes the server's `CallToolRequest` handler directly. Everything
else in the request — a `_meta` block, where the MCP protocol would carry
a tool-use or progress token — is discarded before the handler runs. The
SDK bypasses the MCP server's own request path, so `request_ctx` (the
`ContextVar` the MCP library sets for handlers) is never set.
`create_sdk_mcp_server` (`__init__.py:461`) then calls
`tool_def.handler(arguments)` with the arguments alone. Separately,
`query.py:302` spawns each control request as its own task
(`_spawn_control_request_handler`), so a PreToolUse hook — which *does*
receive `tool_use_id` — and the `tools/call` it precedes are not ordered
by the SDK; the CLI's sequencing is the only ordering, and two parallel
calls to the same tool with the same arguments are indistinguishable to
anything that matches on name and input. I found no evidence in the
bundled CLI's strings that it sends a tool-use id on the in-process call
at all.

Reproduced (repro §1): with the real `Query` dispatcher and a
`tools/call` carrying `_meta: {claudecode/toolUseId: toolu_A}`, the
handler received `{'text': 'A'}` and an unset request context, and its
response was written before its own PreToolUse hook had returned.

Proposal: move the binding one level up, to the boundary the code already
serializes. `Brain.ask` holds `_turn_lock` for the turn's whole lifetime
(`agent.py:659`–`783`); install an immutable request context on the Brain
under that lock, before `query()`, and clear it in the same `finally` that
sets `_needs_drain`, on `interrupt()`, on `_evict_client()`, and on
`rotate()`. A task tool reads the context *once, at entry*, together with
a generation counter the Brain bumps on every connect, eviction, rotation,
and turn end; the controller rechecks the generation before it commits. A
handler still running when its turn was interrupted then acts under the
principal it entered with, and a handler that enters after the turn ended
finds no context and refuses. That is the plan's "late callbacks must
never inherit the next request's authority", stated in terms the SDK can
actually keep. Tool-use IDs remain valuable where they do arrive — the
recorder's hooks — for *recording* the correlation the plan reserves in
step 1, not for deriving authority. The gate's wording should change to
match: prove the turn-boundary binding through the real dispatcher (the
repro's harness does this without a model), including the interrupted
in-flight call and the late entrant, and drop the requirement to bind on
a tool-use ID the tool never sees. If a later SDK carries `_meta` through,
the binding can be tightened without changing the store.

### 2. "Attended" means three things, and the plan needs one

Stage one's `Origin.attended` must be `True` to create a task
(`tasks.py:401`). The lane registry says a Discord turn is "deliberately
evidence of the opposite" of presence (`turn.py:14`, `user-remote`). The
Witness rule's "unattended" means a turn with nobody listening at all —
reflection and Vigil (`witness.py:3`). The plan then maps "the configured
Discord owner's private messages to the same stable local owner" and
lists "public/unattended denial" among the gate's cases. Read with stage
one's meaning, a Discord DM is unattended and can never save a request;
read with the plan's intent, it can.

Proposal: define `attended` for tasks as *a live owner turn* — any lane,
private, not Witness-engaged — and keep room presence where it lives, in
the transcript labels. Write that sentence into `Origin.attended`'s
docstring and the README, and pin both halves: "the owner's Discord DM
can save a request" and "a reflection turn cannot, even with an owner in
the store". The second half falls out of finding 1 for free: `reflect()`
and Vigil's turns call `ask()` with no request context, so the tools find
nothing to act under. Do not add the task tools to `_CIEL_OBSERVERS`
(`witness.py:50`); the plan says reads need private owner context too,
and an unattended list would be exactly the leak.

### 3. Identity has to survive the batch, and the plan does not say how

`WebLink.pop_batch` (`web.py:359`) and `DiscordLink.pop_batch` join a run
of messages into one turn's text; `HubServer` keeps `say_id` only long
enough to dedupe a resend (`server.py:63`). By the time `_handle_web_turn`
builds its `TurnRequest` (`pipeline.py:2130`), the Chart's `seq` and the
Discord message ids are gone. The plan says "preserve identities across
queueing and batching" and "one new task per explicit owner request", but
a batched turn is one request with several ingress identities.

Reproduced (repro §2): two Chart says with seq 41 and 42 became one turn
`"watch PR 12\nand PR 13"`; two Discord DMs with ids 1001 and 1002 became
the same.

Proposal: make the queue element carry identity — the `Lane` protocol's
`(arrival, text, channel)` grows an ingress record — and define a batched
turn's request identity as the principal, the lane, and the ordered
ingress ids of the run, hashed. Creation dedupes on that. Two consequences
worth stating in the plan: a Chart say resent after its original was
already consumed (the documented at-least-once double, `web.py:91`)
becomes a second turn whose `create` returns the *same* task rather than a
second one, which is the behaviour the plan wants and today's link cannot
give; and a batch that contains two requests ("watch 12 and 13") is one
request identity, so the clarification path the plan describes must
happen before creation, not by splitting the identity after. The shape
change touches `probe_ladder.py`, `probe_hub_arbiter.py`,
`probe_discord.py`, and `probe_spoke.py` as well as the four probes the
plan lists; add them to the run list.

### 4. The public/private partition is a reconnect, and the plan should price it

"Partition public and private SDK session history while retaining the
single Brain/turn lock" names the right requirement — a guild mention
must not be able to quote a task the owner inspected in a DM — but the
Brain has one client and one session, chosen at connect time
(`agent.py:490`, `resume=previous.session_id`). `ClaudeSDKClient.query`
takes a `session_id` argument, but I could not verify that it opens a
second history rather than labelling messages, and the plan should not
lean on it unverified. The honest mechanisms are a second, resume-less
client for public turns, or a reconnect with `resume=None` whenever the
lane flips — and a reconnect is the cold start `_last_connect_s` measures,
the SDK subprocess plus every MCP server, paid inside the turn.

Two things the plan should say. First, the exposure is not new: the
shared session already carries recalled memories, read messages, and
held notes from private turns, and a public turn can already quote them;
the plan's boundary is the right one, but it is a Brain-level change with
its own probe, not a task-tool detail. Second, choose the mechanism. I
would take the second client, created lazily on the first public turn,
sharing `_turn_lock`, with no `resume` and no session persistence — the
cost is one more subprocess only while someone mentions Ciel in a server
— and pin in `probe_turns.py` that a public turn's prompt carries no task
tools and that the fake brain saw a distinct client. If that is judged
too large for stage two, say so and gate the Chart surface on the smaller
guarantee the tools can give (no task tools, no task text in a public
prompt) with the session partition recorded as owed before stage three.

### 5. Creation-into-wait needs a store operation, and resume without a runner must not queue

`create` writes `queued` at revision 1 (`tasks.py:553`); `wait` is a
second transaction that requires `queued` or `verifying` (`tasks.py:700`).
"Creation and this wait must commit atomically" therefore needs a new
store method (`create` taking an optional wait, writing both history rows
in one transaction), which the schema bump is the moment to add.

The larger gap is `resume`: it returns any `waiting` task to `queued`
(`tasks.py:750`). With no runner bound, a queued task is a task the Chart
would show as ready to run, which is the claim the plan forbids ("a
resume cannot turn that disabled capability into a claim that work is
running"). Proposal: the controller, not the model, knows whether
execution is bound; while it is not, a resume of a `waiting/resource`
task re-enters the same resource wait in the same transaction with a
history row that says so, and the Chart shows "saved, execution
unavailable" unchanged. Pin it: "resuming while execution is unavailable
leaves the task waiting on the resource".

### 6. Name the confirm-tier operations, or take the broker out of stage two

Step 3 says to use the confirmation broker "under the repository's action
policy" and to "bind any confirmation to the concrete operation, task,
arguments, and revision; recheck after the answer". None of the seven
owner operations acts on the world: each is the owner's own explicit
request, and the plan itself says a direct authorization needs no
invented second approval. `ConfirmToolGuard` keys on tool name alone
(`toolguard.py:188`) and has no notion of revision, so the recheck the
plan describes would be new machinery with no customer until stage five.

Proposal: stage two journals every control through the controller — the
recorder's hooks only see SDK tool calls (`recorder.py:99`), so a Chart
pause would otherwise go unrecorded — and uses no broker. State that the
gate arrives with the first mutation in stage five, and that
`describe_call` gets task formatters then. Note that `[journal]` can be
off (`tools/__init__.py:149`); the controller must accept `None` and
still work.

### 7. An expected revision from the model is a guess; from the Chart it is a fact

The tools take "permitted task/control arguments" and "expected task
revisions are checked again at the transition boundary". A model can pass
a task id it just listed; a revision it types is either copied from the
listing (stale the moment the Chart moves) or invented. The store's
revision check exists to fence *stale callbacks*, and a spoken "pause it"
is not a callback — it is against the present.

Proposal: the tool path takes the task id only, and the controller applies
the control against the record it reads inside the store's transaction;
the Chart path carries the revision it rendered, because there the
mismatch is real information ("someone paused it from the other tab").
Both go through the same controller, one with a supplied revision and one
with the read one, so validation cannot diverge.

### 8. Owner eligibility on the wire is admission; say it and stop there

`admit` (`web.py:256`) takes the role from the client's hello and, on
loopback without `require_token`, checks no token at all. "Reject commands
from unadmitted or ineligible peer roles" reads as a role check, and a
role check would be theatre: the role is a string the client chose.

Reproduced (repro §3): a loopback hello claiming `role: "owner"` is
admitted with that role and no token examined.

What the wire actually has is the single-owner model the README already
states — on loopback, reach is identity; off loopback, the hub token is —
and it is sufficient, because anyone the door admits is the one user. The
plan should say that owner-eligible means *admitted on the Chart path*,
and enforce the two exclusions that matter: the spoke seat, whose frames
are routed by `ws is self._spoke` (`server.py:261`) and should log and
drop task frames explicitly rather than fall through the catalog; and the
interview room, which never reaches `/ws`. Off loopback, the plan should
also require the token (`require_token`) for any hub that serves task
frames, since the loopback shortcut is the one place a browser page on
the same machine could otherwise speak as the owner.

### 9. Smaller points

- **The bindings table has no writer in stage two.** No runner means no
  attempts and no tool calls to bind. Adding the table in the version-two
  schema is right — refuse-not-migrate makes a later bump expensive — but
  the plan should say no row is written in this stage and pin that an
  empty table validates. The "SDK client generation" it reserves does not
  exist yet; finding 1 adds it to the Brain.
- **Guard the store by name.** `forbidden_names` (`permissions.py:138`)
  is name-based, so list `tasks.sqlite3`, `tasks.sqlite3-journal`, and
  `owner.lock` — the sidecar by its full name, or a workspace of `~/` lets
  `Read` open the journal file. Pin in `probe_files.py` and
  `probe_shellguard.py`.
- **Advertise tasks in the hello.** The page is served from disk and can
  be newer than the process (`web.py:40`). A `tasks: true` capability flag
  in the server hello, the `acks` pattern, lets a newer page hide the
  section against an older hub instead of showing a permanent error.
- **`TurnRequest` is constructed by keyword in five places**
  (`pipeline.py:1824`, `2014`, `2071`, `2103`, `2131`) and in
  `probe_turns.py`; new fields need defaults, and the voice and typed
  lanes mint their identities at those sites.
- **The acceptance demonstration's cross-view case should end in a
  refresh.** "A pause from one view makes an old resume from another fail
  visibly" — add that the failing view then shows the paused state, since
  step 4 promises a fresh view after every conflict.

## Summary

The stage is well shaped and most of it can be built as written. Two
things must change before implementation starts: the step-2 gate has to
be restated around the turn boundary, because the SDK gives an in-process
tool no tool-use ID to bind on (finding 1); and the plan has to say what
"attended" means and how a batched turn keeps its identities (findings 2
and 3). The session partition (finding 4) is a real cost that deserves a
decision rather than a verb. The rest — creation-into-wait, resume without
a runner, the broker's role, revisions from the model, and wire
eligibility — are sentences to add so the gates test the right thing.

## Revision check — 2026-09-07, later the same day

The revised plan settles findings 1–3 and 5–9 as proposed. Two of its own
additions need to change, and one is confirmed.

- **Drop the per-turn private reconnect and per-turn handler closures
  (step 2).** A connect measured 5–20 s on the Mac and 1–3 s on the hub
  (`brain connected in …` in the process logs), paid on every private
  owner turn; it also restarts the SDK's per-session cumulative cost, so
  `_last_turn_cost` breaks on each reconnect, and it abandons the
  registry's module-level binding convention. The hazard is narrower than
  stated: an abandoned turn's `tools/call` control requests are spawned by
  the reader before that turn's result line, and a spawned handler enters
  at the next loop yield — inside `_drain_stale`, before the next context
  exists. A turn-N call can first enter during turn N+1 only after a
  drain that timed out, the state `_needs_drain` already names. Install
  the context after the drain and immediately before `query()`; withhold
  task authority for a turn that starts with drain debt still owed. In-flight
  calls at an interrupt are cancelled by the CLI's `control_cancel_request`
  (`query.py:305`) and keep the binding they captured at entry; the
  transactional recheck decides the commit. Pin both in the harness.
- **Mint the Chart request ID (step 2).** `clientId` is localStorage
  (`chart.html:534`), stable across tabs; `saySeq` is sessionStorage
  (`chart.html:888`), restarting at 1 per tab. `(client_id, seq)` repeats
  across tabs and, with persisted ingress IDs, would resolve a fresh
  request to an old task or a conflict. Mint a random per-message ID in the
  ledger, the spoke's `say_id` pattern; keep `seq` for acks.
- **The `probe_hub_imports` audit is warranted (step 5).** Memory is
  enabled by default with `dir` defaulting to `~/.ciel/memory`
  independently of `state_dir` (`config.py:852`–`854`), so construction
  already reaches the real home; the fixture must redirect every such path.
- A batch overlapping a consumed ID conflicts as a whole, so its genuinely
  new message cannot save a task that turn; acceptable, and the reply should
  ask the owner to repeat the new part alone.
