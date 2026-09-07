# Codebase review — September 4, 2026

Reviewed the working checkout, including existing uncommitted changes. Application source was left untouched. This was a focused review of the assistant's guards, hub/spoke protocol, timers, world state, and interview authentication/storage/lifecycle, with lighter inspection of the UI and speech code. It is not an exhaustive audit of every integration.

The highest-value next work is fixing the six defects below. The code has useful separation between protocol, policy, and hardware, and substantial scripted coverage; failures are concentrated where these pieces interact.

## Findings

### 1. P1 — Recursive search bypasses the forbidden-file boundary

Location: `src/ciel/brain/permissions.py:170–188`; file tools are allowed in `src/ciel/brain/agent.py:383–386`.

The guard checks only the supplied search root. A `Grep` rooted at an allowed workspace is approved even when it recursively searches forbidden descendants such as `credentials.json`. Omitting `path` skips the check entirely. Blocking a subsequent `Read` does not help when Grep's content output already contains the file's contents.

Reproduced with a temporary workspace containing a **fake** `credentials.json`: direct Read was denied, recursive Grep was approved, and ripgrep found the fixture. This verifies the guard gap and recursive matching; it did not invoke the live Claude tool runtime or read real credentials.

Suggested fix: expose a search implementation that checks every candidate's resolved path before reading it, including forbidden components and symlink targets. Apply the equivalent boundary to Glob, including patterns that traverse outside the search root. Do not depend on model-provided exclusion globs.

### 2. P1 — Concurrent account writes can undo a disable or deletion

Location: `src/ciel/interview/accounts.py:205–229`; HTTP handlers run create/reset in worker threads in `src/ciel/interview/app.py`.

Account mutations load the entire JSON file, change their local copy, and replace the file. A password reset can load an enabled account, spend time in scrypt while an admin disables it, and then save its stale copy with `disabled: false`. Atomic replacement prevents partial JSON but does not prevent lost updates. The shared `.tmp` filename adds another collision risk for overlapping saves.

Reproduced deterministically by pausing reset's hash operation, disabling the account, then completing reset: the account was enabled again. Parallel creates and a login's `touch()` can also overwrite unrelated changes.

Suggested fix: make the complete read/modify/write operation transactional. SQLite is a good fit here. If retaining JSON, coordinate all writers, including CLI processes, with a file lock and use unique temporary files. Hash outside the critical section, then reload the current account before applying the password update.

### 3. P1 — Old cookies authenticate as a recreated account

Location: `src/ciel/interview/app.py:180–186`; cookie signing and verification in `src/ciel/interview/accounts.py:271–299`.

Cookies identify only a username and expiration. Resetting a password does not revoke them. More seriously, deleting an account and recreating the same username makes that original account's cookie valid for the new account and its current role. The default cookie lifetime is 30 days.

Reproduced: issue an Alice cookie, reset Alice's password, delete Alice, then recreate Alice as an admin. The original cookie remains accepted after reset and resolves to the recreated admin account.

Suggested fix: include an immutable account ID and revocation version in the signed cookie, verify both on each request, and rotate the version on password reset. New accounts must receive a new ID even when their username is reused. Invalidation should also close existing authenticated sockets.

### 4. P1 — A connecting spoke can miss the authoritative timer snapshot

Location: `src/ciel/hub/server.py:108–114` and `208–226`; hello payload in `src/ciel/remote/web.py:711–733`.

`note_timers()` deduplicates globally. If the hub broadcasts a change while the spoke is offline, it records that set as already sent. When the spoke reconnects, its hello contains no timer snapshot, its client does not request replay, and subsequent unchanged polls return early. The mirror remains stale until another timer change occurs.

Reproduced: publish an armed timer with no clients, welcome a fresh spoke, and poll the same timer set again. Its queue contains only `hello`, with no timer snapshot. This can leave offline fallback unaware of newly armed timers or still holding timers canceled while it was disconnected.

Suggested fix: send the current authoritative timer set directly to every newly seated spoke, independent of broadcast deduplication. Test both new timers and cancellations made during a disconnect.

### 5. P1 — Offline timer fallback consumes timers while muted (corrected September 5)

Location: `src/ciel/spoke/frontend.py:275–281` and `_ring_locally()` at line 740.

The mirror's due-timer poll checks state and whether playback is in progress, but never `_muted`. `_ring_locally()` marks the timer rung before invoking playback. The real Player already receives `muted=lambda: self._muted` and suppresses audio, so the actual defect is silently consuming a due timer while muted: it will no longer ring after unmute.

Correction: the original reproduction used a fake player without the real player's mute callback and incorrectly reported audible playback. Inspection on September 5 confirmed that the callback was already present in HEAD. The new poll and playback checks fix the actual timer-consumption defect by leaving muted timers pending.

Suggested fix: hold due timers while muted and recheck mute immediately before playback. Preserve them for the existing delayed-announcement behavior on unmute rather than marking them delivered.

### 6. P2 — Daily interview usage limits are bypassable

Location: `src/ciel/interview/app.py:385–403`; deletion in `src/ciel/interview/store.py:134–138`.

Creation checks usage, awaits potentially expensive brief generation, and only then saves a session. Multiple concurrent requests can all pass the same check. Usage is calculated from session directories, so deleting a session removes its usage charge. Regeneration also performs model work without consuming this counter.

Reproduced with a cap of one: two concurrent requests both returned HTTP 200 and stored two sessions. Deleting those sessions made the usage check report capacity again.

Suggested fix: reserve quota transactionally before starting generation, keep usage independently of deletable session history, and define a separate budget/rate limit for regeneration. Release reservations on qualifying failures.

## Verification and test issue

Seven existing suites passed **468 checks**:

| Probe | Checks |
|---|---:|
| `probe_shellguard.py` | 148 |
| `probe_wire.py` | 70 |
| `probe_turns.py` | 63 |
| `probe_interview.py auth` | 57 |
| `probe_spoke.py` | 50 |
| `probe_hub_arbiter.py` | 48 |
| `probe_tool_rpc.py` | 32 |

The auth and wire probes initially could not bind their temporary loopback servers in the sandbox; both passed when rerun with the required execution permission.

`probe_world.py` fails at line 682 because `make_pipeline()` fixes the world's clock to September 4 at 15:42, while `Pipeline._presence_now()` evaluates freshness using the actual clock. The supposed fresh observation is already stale. This is a deterministic-test defect, not a demonstrated live presence bug. With only the temporary test harness's clock aligned to real time, the five presence checks and three remaining agenda checks passed. Use a shared injectable clock for both observation and freshness evaluation.

The companion `2026-09-04-review-repros.py` reproduces the six findings with temporary accounts/data, mocked brief generation, and fake audio. Run from the repository root:

```sh
PYTHONPATH=src .venv/bin/python reports/2026-09-04-review-repros.py
```

No live model, microphone, external messaging, or native speech quality was tested. The search finding's verification boundary is stated above.

## Improvements worth making

1. **Provide one regression command and run it in CI.** Preserve the useful existing probes, but give them an aggregate runner with machine-readable results. Separate pure tests, loopback integration tests, and optional hardware/live checks. Prioritize reconnects, overlapping requests, mute transitions, and cancellation over more isolated happy-path examples.
2. **Extract delivery and lifecycle ownership from the pipeline.** `pipeline.py` is 3,665 lines with 115 method definitions. Move timer delivery, world refresh, and lane orchestration into components with explicit ownership of tasks and cleanup. A shared delivery policy should decide mute, interruption, success, and retries for both hub and local operation.
3. **Strengthen persistence selectively.** Keep Markdown for memories and projects, where human editing matters. Use transactions for accounts and usage. Give file writes unique temporary names and explicit permissions; the shared `atomic_write()` currently replaces files with the temporary file's permissions. Audit privacy-sensitive callers separately from plain notebook files.
4. **Make reconnect a state reconciliation step.** Classify wire data as either current state that must be resent or events with IDs and delivery receipts. Timer snapshots, pending confirmations, and interrupted operations should have documented reconnect behavior and dedicated tests.

## Feature ideas

1. **A “why did that happen?” panel in the Chart.** Explain why a nudge was held, why an action required confirmation, which device owns playback, and whether a source is stale. Build on the world table, source health, journal, and policy decisions already present. This has the most immediate value for diagnosing assistant behavior.
2. **Inspect and correct memory in the Chart.** Search facts, see their conversation/proactive provenance, edit or forget them, and resolve contradictory entries. The Markdown store and provenance field already provide much of the foundation.
3. **A visible outbox and recovery view.** Show “queued,” “delivered,” “failed,” and “needs your decision” for commands and announcements. Let users cancel queued requests before a reconnect and distinguish safe retries from actions whose outcome is uncertain.
4. **Interview practice driven by previous debriefs.** Surface score trends by interview mode, recurring weak areas, and a short drill based on a prior question. Link feedback back to the corresponding transcript/recording. Existing per-question scorecards make this a smaller addition than another interview mode; avoid presenting scores across different modes as directly comparable.

Recommended order: repair the access and account boundaries, fix timer reconciliation/mute behavior, make the regression suite reliable, then build the explanation panel and targeted interview practice.
