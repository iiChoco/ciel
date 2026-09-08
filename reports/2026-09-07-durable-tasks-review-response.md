# Response to the durable-task plan review — 2026-09-07

The staged architecture stands. The plan incorporates the review with the
qualifications below. No runtime implementation changed.

## 1. Correlation seam accepted; ContextVar transport does not work as proposed

Evidence: installed `claude_agent_sdk/_internal/query.py:253` starts the
reader; line 302 dispatches hook control requests from that reader; line 498
invokes the registered callback. `src/ciel/brain/recorder.py:99` and `:127`
are the appropriate existing pre/post seams, but their tool IDs do not
implicitly transport the caller's Python context.

Reproduction: `uv run --no-sync python reports/2026-09-07-task-hook-context-repro.py`. With SDK
0.2.139 and its real Query dispatcher over an in-memory transport, a variable
set to `task-1/attempt-1` by the runner was `None` inside the hook. No model,
network, user account, or runtime state was used.

Proposed fix: explicitly bind immutable execution identity at the serialized
Brain/hook boundary and pin it per tool-use ID. A callback-local ContextVar
may carry that explicitly supplied identity, but a runner-only ContextVar is
insufficient. Regression tests must reject stale callbacks from old attempts.
The best-effort recorder/journal also cannot replace mandatory pre-dispatch
attempt persistence in the task store (`src/ciel/journal.py:129`).

## 2. GitHub adapter accepted; define the monitored set before success

There is no task-oriented GitHub client in the reviewed `src/ciel` tree.
A repository-restricted token and narrow adapter must be planned explicitly;
PR identity lookup needs appropriate read access as well as checks/statuses.

Inspect both status systems, without demanding nonempty success from each:
GitHub Actions creates checks, not commit statuses. Missing an expected result
is different from a repository not using that system. Track explicit monitored
identities, pagination, reruns, and the exact head; do not infer required-check
configuration from the results visible so far.

Neutral/skipped as unknown is a legitimate strict Ciel policy, not GitHub's
universal rule. GitHub can treat those conclusions as successful for required
checks. The revised plan names the strict policy and keeps mergeability out
of scope. Sources: [status checks](https://docs.github.com/en/pull-requests/reference/status-checks)
and [required checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).

## 3. Aging accepted, with an eligibility and dispatch contract

`src/ciel/schedule.py:118` gives Vigil the final current slot. Add a pure aging
branch ahead of non-urgent Vigil, retaining human, confirmation, timer, and
room-quiet guards. Persist eligibility age and reset it after service. Aging
only bounds interference from routine Vigil; continuous human/urgent work
can still defer a task. The actual `_enact` maintenance-slot rules must let
an aged choice run, or a correct priority branch alone does not create progress.

## 4. Remaining changes accepted with bounded claims

Map the owner-facing task to the mandate vocabulary without adding a second
state store. Require schema-ahead refusal and protected SQLite sidecars. A
single atomic JSON document could also commit task plus outbox; SQLite remains
the chosen proposal for transaction/history/query ergonomics, not because
JSON makes cross-record atomicity impossible.

Add a genuine owner question to the pilot (extend an expired monitoring
window), restart it, and test the broker's identity/revision handling. PR
name disambiguation separately tests clarification, not a yes/no approval.
Keep future delegated authority task-scoped in the grants surface.

Reconcile mutations only where the provider offers sufficient evidence.
Uncertain message sends must not automatically retry; delivery and reading
are distinct claims. Re-exec is a normal recovery case, but source reloads
are gated by runtime activity and remote source deployment. Test forced
crashes as well as clean reloads, without implying every Mac edit restarts
the remote hub immediately.

Validation: the SDK reproduction passed and confirmed the missing context;
plan/report references and whitespace were checked. No runtime behavior,
configuration, service, or existing review was modified.
