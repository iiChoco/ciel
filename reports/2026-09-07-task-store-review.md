# Review: the task store (stage one) — 2026-09-07

Reviewed `src/ciel/tasks.py` (710 lines) and `scripts/probe_tasks.py`
(92 checks, all passing here), with the `[tasks]` config, README section,
and changelog entry in the working tree. Three findings were reproduced by
`reports/2026-09-07-task-store-review-repro.py` against temporary stores.
No code was changed. Findings are ordered by impact.

## What the store gets right

This is a careful piece of work, and most of what the plan promised is
already true in code rather than in prose:

- **One owner, one thread, short transactions.** A single worker thread,
  `BEGIN IMMEDIATE` around every operation, `asyncio.shield` so a cancelled
  caller cannot leave a half-run transaction, and a rollback journal with
  `synchronous=EXTRA`. The probe kills real subprocesses with SIGKILL
  before dispatch, after an external effect, before commit, and after
  commit, and every one of those checks says the right thing.
- **Refuse, never reset.** A schema ahead of the code, a foreign
  application id, a missing table, a corrupt row, a symlinked database, a
  shared directory: each is refused with the bytes on disk untouched, and
  the probe proves the bytes. `_validate_rows` re-derives the invariants
  from the tables at every open rather than trusting them.
- **Authority comes from the runtime.** Only an attended, private owner
  request creates a task; a step cannot leave the task's scope; the same
  request id returns the same task and a changed request under that id is
  a conflict; another owner sees nothing, not even notices.
- **Uncertainty is a state, not a guess.** A dispatched mutation
  interrupted by a crash becomes `waiting/reconciliation` with the
  attempt marked `unknown`, and neither pause, resume, nor cancel can
  launder it back into something runnable. A dispatched read is simply
  retried.
- **Done has evidence, and the notice commits with it.** Completion
  demands fresh, exact-value evidence for every criterion on the current
  attempt, and the outbox row is written in the same transaction as the
  `done` transition. A torn completion rolls back both.
- **The words are the endgame's.** "A task is a mandate" is the first
  line of the module, and the record carries desired outcome, scope,
  criteria, evidence, and verification under those names.

## Findings

### 1. A concurrent reader poisons the store until the process restarts

`tasks.py:287` opens SQLite with `timeout=0`, and `tasks.py:362` marks the
store poisoned on *any* `sqlite3.Error` inside a transaction, after which
every call raises "task store is not available" until a new `TaskStore`
is opened. `SQLITE_BUSY` is one of those errors. So the moment anything
else holds a read lock at the instant of `BEGIN IMMEDIATE` — the
"readable export/status tools" the plan itself asks for, a human with
`sqlite3` open, a backup — the runtime's task work stops for good.

Reproduced: a second connection holding a read transaction made a write
fail, and after the reader left, `list()` still raised.

Proposal: a `busy_timeout` of a few seconds (`timeout=5.0` at connect),
and treat `OperationalError` whose message is "database is locked" or
"database is busy" as a transient refusal of *that* operation rather than
as poison. Poison should be reserved for corruption and I/O errors. Pin it
in the probe with a held read lock that is released within the timeout,
and with one that is not.

### 2. Every poll is an attempt, so the pilot's watch ends after eight looks

`tasks.py:544` increments `attempts` on every `claim`, and `TasksConfig`
defaults `max_attempts` to 8. The PR pilot polls: claim, dispatch, observe
"pending", checkpoint with a later `eligible_at`, repeat. On the ninth
look the store raises `TaskLimit` and the task can never resume.

Reproduced: a task watching a pending criterion at five-minute intervals
died after eight polls, forty minutes into a job the plan expects to run
for hours.

The plan separates "polling bounds" from "retry/backoff limits", and the
record conflates them. Proposal: count an attempt only when it ends
without a clean checkpoint — an interruption, a failure, a reconciliation
— and keep a separate, larger `max_polls` (or a per-day poll budget, as the
plan's "per-task/day execution budgets" intended) for the ordinary case. A
poll that observes and checkpoints is progress, not a retry. Whichever
counter is chosen, its meaning should be in the field's docstring, because
"attempt allowance counts claims rather than successful replies" is a
probe check today and it pins the wrong thing for the pilot.

### 3. A criterion's target revision is fixed at creation; a new PR head can never complete the task

`Criterion.target_revision` is set when the task is created and
`tasks.py:594` requires evidence to carry exactly that revision. The plan
says a new head invalidates the old evidence, which the store does
correctly, but there is no operation that updates the criterion to the new
head. `checkpoint` changes only the step. So a PR that gets one more commit
after the task was created can pass every check on the new head and the
task will still refuse to complete.

Reproduced: evidence "passed" on `head-2` against a criterion pinned to
`head-1` is refused, and the store's public surface has no method that
re-targets a criterion.

Proposal: let the runtime, not the model, move the target revision through
a revision-checked operation — the observing step reports the head it saw,
and a `retarget` (or a `checkpoint` that carries new criteria) records
the change as a transition so history shows when the target moved. The
invariant to keep is that evidence and criterion must agree; the mistake to
avoid is treating the target as immutable when the plan says it changes.

### 4. An owner's answer has nowhere to land

`wait(reason='owner')` stores the question durably, which the probe pins
nicely. `resume` at `tasks.py:653` returns the task to `queued` with the
*same* next step. The owner's answer — which PR, extend the window — has no
field, so the runner would re-ask or would have to smuggle the answer
through an out-of-band channel. Proposal: `resume` accepts an optional
step (validated against scope like `checkpoint`) and records the answer's
text in the transition detail, so the question and its answer sit together
in history.

### 5. The "repeated startup" check tests a short-circuit, not recovery

`probe_tasks.py:164` calls `start()` on a store that is already open, and
`tasks.py:264` returns `()` for an open store without running recovery.
The property the check names — recovery notices are not repeated — is
true, because recovered tasks are no longer `running`, but the check does
not exercise it. Close and reopen instead.

### 6. Smaller points

- **The `-journal` sidecar is pre-created empty** (`tasks.py:283`). A
  zero-length journal is not a hot journal, so SQLite ignores it, and the
  probe passes; but creating rollback-journal and WAL files that SQLite
  did not ask for is the kind of thing a future SQLite version could read
  differently. Checking their permissions when present, and creating none,
  keeps the same guarantee.
- **Retention.** Transitions, evidence, and outbox rows are never pruned.
  Fine for stage one; a poll-driven pilot writes a transition and an
  evidence row per look, so a retention rule belongs in stage three.
- **The correlation columns the plan names** — tool-call and journal
  references on an attempt — are not in the schema yet. The author's
  response to the plan review is right that a runner-side ContextVar does
  not cross the SDK's reader thread, and that the identity must be bound
  at the hook boundary per tool-use id. When that arrives it needs a column
  here, and adding one is a schema bump under the refuse-not-migrate rule,
  so it is worth adding now while the schema is fresh.
- **Style.** The module is written in the project's voice and its
  docstring carries the invariants in bold. The code itself packs
  arguments without spaces (`(OWNER,task.id,task.revision,now=101)`)
  where the rest of the tree spaces them; the guide says match the
  neighbours.

## Verdict

Stage one is accepted on its own terms: the crash and authority
invariants it claims are real, and the probe is the most convincing one in
the repository because it kills processes rather than raising exceptions.
Findings 1 through 3 should be fixed before the executor lands, because
each would stop the pilot in its first hour: a status tool would poison
the store, the watch would exhaust its allowance, and a second commit on
the PR would make completion impossible. None of them is large.
