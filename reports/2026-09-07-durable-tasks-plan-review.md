# Review: "Ciel can carry a task between conversations" — 2026-09-07

Reviewed the proposal in `design/2026-09-07-durable-tasks-plan.md` against
the code it leans on: `schedule.py` (the arbiter), `pipeline.py::_emit_verify`,
`brain/agent.py::_hooks`, `brain/recorder.py`, `journal.py`, and the grants
module. No code was changed. Findings are ordered by how much they would
change the plan; the seven cross-review questions are answered at the end.

## What the plan gets right

- **The model proposes, the runtime owns the transition.** This is the
  endgame's own sentence — the LLM interprets, deterministic code owns
  state transitions and verification — applied to the one place it is most
  tempting to cheat. "Do not expose a tool that lets the model set `done`"
  is the single most important line in the document.
- **Execution is not evidence.** A tool return proves a call happened; a
  read-back proves the outcome. Tying evidence to the PR head SHA, and
  letting a new head invalidate old evidence, is exactly how Phase Space
  already treats an observation from a stale source.
- **Recovery is classified before it is attempted.** Read-only attempts
  rerun; possibly-dispatched mutations reconcile or stop and ask. Refusing
  a general exactly-once claim across the crash boundary is honest and
  rare.
- **Terminal state and its notification commit together**, with delivery
  tracked separately so a lost message never re-runs the work. This is the
  right split and it is stated crisply.
- **Witness is not weakened**, a pending broker question is rebuilt rather
  than a Future deserialized, and an unrelated "yes" cannot become task
  authority. Each of those is a mistake this project has already spent an
  evening on elsewhere, and the plan pre-empts all three.
- **The pilot is small and honest.** "Watch this PR's checks" exercises
  create, wait without the brain, restart, re-observe, verify against the
  current head, notify — the whole lifecycle — with no mutation and no
  outward message. "Passing checks is not safe to merge" is the kind of
  sentence that keeps a future tool from over-claiming.
- **Disabled defaults, staged gates, one change per stage**, and an
  explicit list of which probes must grow. This matches how the repository
  actually works.

## Findings

### 1. The task slot will starve under Vigil, and the plan does not say how it recovers

`pick_next` today tries, in order: confirm, timers, voice, typed, web,
remote, then Vigil — and Vigil already fires only from `WAITING`. Putting
tasks *after* Vigil in the same quiet slot means a task step runs only when
nothing at all is queued. Vigil's policy paces proactive turns, so in the
common case this is fine; under a busy watcher (the sections watcher on a
bad day, or the calendar around a meeting) a task can sit eligible for a
long time, and the plan's "expose prolonged queue waiting" makes the
starvation visible without ending it.

Proposal: keep the order, but give `Snapshot` a `task_waiting_s` and let
`pick_next` prefer the task once it has waited longer than a configured
bound (say ten minutes) *and* the Vigil item is not urgent. That is one
pure branch, testable in `probe_ladder.py`, and it keeps the plan's own rule
that priority changes need a scheduling test. Do not alternate blindly:
Vigil's urgency tiers exist precisely so that a meeting-in-eight-minutes
beats a poll.

Also note the hub's `state` in the snapshot is the *spoke's* reported state.
A task step on the hub therefore waits for the room to be quiet, which is
right, and runs freely when the spoke is disconnected, which is also right
but worth a sentence in the README.

### 2. Nothing on the hub can read GitHub today

`grep` finds no GitHub access anywhere under `src/ciel`. The hub is an Azure
VM without a developer's `gh` login, as the plan suspects. The pilot needs a
narrow read-only adapter: `urllib` against the REST API with a fine-grained
personal access token holding only checks and statuses read, stored
owner-only beside `spotify.json` and added to `FORBIDDEN_NAMES`. Two API
facts the verifier must decide explicitly, because "the checks pass" is
not one endpoint:

- Check runs (`/commits/{sha}/check-runs`) and commit statuses
  (`/commits/{sha}/status`) are separate systems; a repository may use
  either or both. "Passing" means every check run concluded `success`
  *and* the combined status is `success`, for the head SHA the task holds.
- `queued`, `in_progress`, and a conclusion of `neutral` or `skipped` are
  `unknown`, not `satisfied`. Required-check configuration is a branch
  protection detail the adapter should not pretend to know.

### 3. Correlation belongs in the recorder, keyed by what it already keys on

`ActionRecorder.before/after` are already keyed by the SDK's `tool_use_id`
and are the last PreToolUse hook and the only PostToolUse hook. That is the
seam: set a task context (task id, attempt id, task revision) for the
duration of a step — a `contextvars.ContextVar` on the Brain, entered by the
runner, never by the model — and have the recorder write it into
`journal.record` as a structured field beside `tool`, `args`, `response`,
`snapshot`, `note`. The journal then carries the correlation the plan asks
for before any resumable mutation is allowed, with no new hook and no
change to the guards. The guards must *not* read the task context to widen
anything; the plan says so and the order of the chain (Witness → workspace
→ shell → Mac shell → confirm gate → recorder) already keeps the recorder
last and powerless.

### 4. "Task" is a mandate; say so and shape the record accordingly

The endgame names the primitive: a mandate is desired state, observed
facts, policy, actions, verification. The task record has requested
outcome, evidence, completion criteria, steps, verifier — the same five
things under other names. Naming the mapping now costs nothing and stops
the store from growing a second, parallel notion later when a reconciler
arrives. Concretely: type completion criteria against world-fact names
where one exists (a PR head is a fact a watcher could publish), and let
evidence rows reference an observation's source and revision the way
`world.py` already records them.

### 5. SQLite is justified, with two cautions

A single atomic JSON document can be written safely — `journal.record`
already does write-then-rename — but it cannot update one task while
another is mid-step without rewriting the whole file, and an outbox that
must commit *with* a transition is exactly the multi-record atomicity the
plan describes. Standard-library SQLite is the smallest thing that gives
it. Two cautions: run every access in a thread with a short transaction, as
the plan says, and either disable WAL or treat `-wal` and `-shm` sidecars as
part of the owner-only directory, since a rollback journal is one file and
WAL is three. The "corrupt store disables execution" rule is right; add
that a schema version *ahead* of the code also disables execution, so a
rolled-back hub never silently downgrades a newer store.

### 6. The autoreloader will exercise recovery constantly

Both processes re-exec on every source edit. During development a running
attempt will be interrupted many times an hour. That is a gift for stage 1
and a hazard for stages 3 and 4: polling must be idempotent across
re-execs, and the notification dedupe key must survive them, or a day of
editing produces a day of duplicate completion notices. Say this in the
plan's limits section and pin it in `probe_tasks.py` with a real process
kill, which the plan already requires.

### 7. Smaller points

- Creation dedupe by `tool_use_id` is sound; also record the lane and
  whether the turn was attended, so a public or unattended caller is
  refused by data, not by the tool description.
- "Bound model steps and attempts; monetary ceiling only with reliable cost
  reporting" — count SDK turns, which are observable, and leave dollars out
  of v1 entirely.
- The plan's reading of `_emit_verify` is exact: a three-deep backlog and a
  thirty-minute expiry make it a hint, not a gate. Keep it for what it is.
- "Clarify an ambiguous pause/cancel target" should also cover a task the
  user names by outcome rather than by id; the voice path will get "stop
  watching that PR", not a UUID.

## The seven questions

1. **SQLite or one JSON document?** SQLite, for the reason in finding 5:
   the outbox-with-transition atomicity and per-task updates. A JSON
   document is simpler only until two records must change together.
2. **Does the slot preserve responsiveness and make progress?**
   Responsiveness yes — tasks run only from quiet `WAITING`, after
   everything human. Progress under sustained Vigil, no; see finding 1 for
   the one-branch fix.
3. **Where does task context enter the hooks?** In the recorder, via a
   context variable set by the runner, written into the journal beside the
   existing fields; finding 3. Not in the guards.
4. **Is owner-attended sufficient for v1, and what later?** Sufficient,
   and correct: unattended work prepares, the owner returns, the action is
   revalidated and runs through the attended path. For delegated mutation
   later, the vehicle already exists: the grants module. A task-scoped grant
   naming the exact tool, an argument pattern, a resource identity, and an
   expiry, recorded in the journal and read by the Witness, is a policy the
   project can review; a global relaxation is not.
5. **Can the schema reconcile a crash after an action without claiming
   exactly-once?** Yes, for actions whose result can be read back: the
   attempt id persisted before dispatch plus a post-state read (the file
   hash in stage 5) tells "done", "not done", or "unknown". For actions
   with no readable post-state — a message sent to a person — no schema
   can, and those must stay attended. Say that boundary out loud.
6. **Are completion and delivery separated strongly enough?** Yes, if the
   outbox row is keyed by task id and transition revision and delivery
   retries are bounded and never invoke the verifier. Add the bound.
7. **Does the PR pilot exercise enough before "ship this feature"?** It
   exercises everything except mutation, approval, and the "ask when
   information is missing" path beyond a missing credential. Add one
   deliberate owner question to the demonstration — an unspecified PR, or
   two open PRs with the same title — so `waiting/owner` and its rebuilt
   broker interaction are proven before stage 5 depends on them.

## Verdict

Build it as staged. The architecture is the endgame's loop made concrete,
the boundaries are the right ones, and the pilot is honest about what it
proves. The changes I would make before stage 1 are small: the starvation
bound in the arbiter, the GitHub adapter's two-system verifier decided up
front, the recorder as the correlation seam, and the mandate vocabulary
named so the store does not have to be renamed when the reconciler comes.
