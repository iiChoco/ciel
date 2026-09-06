# Follow-up code review — September 5, 2026

Reviewed the current working checkout against HEAD `1b52531`, concentrating on changes since the previous review: file-search replacement, account transactions and cookie generations, usage reservations, timer reconciliation/mute, and interview interruption/retry handling. These changes are still uncommitted in the local checkout. No application source was modified during this review.

The previous fixes address the originally demonstrated paths, and the updated probes pass. Five remaining findings follow, ordered by impact. Four affect Ciel Interview.

## Findings

### 1. P1 — A recreated username inherits the original account's interview data

`src/ciel/interview/app.py:454–466`, `src/ciel/interview/store.py:48–54`

Cookies now distinguish immutable account IDs, but session ownership and storage still use only the username. Delete Alice, recreate Alice for another person, and the new account can list and read the old account's interviews, transcripts, code, debriefs, and recordings. `_owned()` loads by `account.username` and does not compare an owner ID; listing exposes the old session IDs as well.

Reproduced with two accounts having different IDs: a session created under the first account, containing a private fixture answer, was returned successfully by `_session_get()` authenticated as the second account. This is a remaining isolation defect, not a claim that the fixed old cookie itself still works.

Fix: store and check an immutable owner ID in session metadata and use it consistently for listing, individual resources, live sessions, and storage. Migrate existing sessions to their current owner before accepting replacements; retain old data separately rather than assigning it to whoever next acquires the username.

### 2. P1 — Credential revocation does not revoke existing interview sockets

`src/ciel/interview/app.py:208–214`, `677–699`; receive loop at `588–607`; `src/ciel/interview/session.py:169–185`

A self-service password change rotates cookie authentication but never evicts existing sockets, so another browser already connected as that user continues its interview. Admin reset/disable/delete calls `_evict()`, but that only pauses the session: neither `pause()` nor `_finish()` closes its WebSocket. The receive loop keeps accepting frames without rechecking account credentials, and `on_audio()` appends bytes even after the session ended.

Verified through a real aiohttp loopback WebSocket using temporary accounts and a scripted backend:

- After changing the password, the old connection submitted another answer and received `turn.end` for turn 2.
- After disabling the account and awaiting `_evict()`, that connection remained open and successfully appended `POST_DISABLE_AUDIO` to the recording.

Fix: associate sockets with account ID/auth generation, invalidate old sockets on every credential change, close them on eviction, and reject all incoming frames for ended or revoked sessions. A paused client state is not server-side access revocation. Track every attached connection, not just the latest `_ws` pointer.

### 3. P1 — The new search tool can block the entire assistant event loop

`src/ciel/brain/tools/files.py:164–175`

`search_files()` is async in name but its directory walk, file reads, and Python regex matching contain no await. The entry/file-size limits do not bound regex execution time. A valid backtracking pattern can monopolize the same event loop that handles the assistant, sockets, timers, and interview turns.

Reproduced in an isolated child process with a 30-byte fixture: `^(a+)+$` against 29 `a` characters followed by `!`. After two seconds the search was still executing, and a concurrent 50 ms heartbeat had never run. The parent terminated the child at the deadline.

Fix: perform search in a separately killable process with a hard execution deadline, or use a regex engine with bounded execution plus off-loop traversal. A coroutine timeout cannot interrupt synchronous regex execution, and merely moving Python regex matching to a thread does not reliably isolate GIL-bound work.

### 4. P2 — Drain timeout reintroduces the interview's stale-response race

`src/ciel/interview/brain.py:260–271`

The new drain correctly discards an aborted turn when its ResultMessage arrives promptly. On timeout or receive failure, however, `_drain()` unconditionally clears `_in_flight` and allows the same stream to be reused without establishing that the old result was consumed. A result arriving after the 15-second deadline can therefore be treated as the next query's response, recreating the failure this change intends to fix. Retries can further advance the mismatch.

Reproduced using the existing fake SDK client with delayed interrupt completion and a shortened test deadline: the timeout cleared the debt, a late old ResultMessage was queued, and `ask('new answer')` raised the old turn's error.

Fix: clear the debt only after consuming the expected terminal result. On timeout or broken transport, retire the connection and fail/recover explicitly; do not issue a new query on an unsynchronized stream. Add delayed-result and failed-drain cases to the new brain probe.

### 5. P2 — Failed generation across midnight refunds the wrong day's quota

`src/ciel/interview/app.py:428–431`, `438–448`

Reservation and release independently call `_today()`. If a request reserves just before midnight and fails afterward, release decrements the new day's usage rather than the reservation's original day. If another request has already consumed a new-day slot, the late failure refunds that unrelated request and permits additional interviews or regenerations.

Reproduced with a controlled date: reserve on September 5, reserve on September 6, then release the September 5 request after the date changes. September 6 usage becomes zero.

Fix: return a reservation object/ID containing the original account, day, and kind; release that exact reservation. Use the same mechanism for session creation and regeneration.

## Verification

All 13 selected probe suites passed, totaling **678 checks**:

| Probe | Checks |
|---|---:|
| files | 17 |
| world | 121 |
| spoke | 52 |
| hub arbiter | 50 |
| turns | 63 |
| vigil | 173 |
| interview auth | 70 |
| interview brief | 50 |
| interview brain | 12 |
| interview session | 28 |
| interview endpoint | 17 |
| interview wire | 11 |
| interview cases | 14 |

Interview probes used default config, temporary data, and a disabled speaker warm-up to avoid live shared accounts and hardware. Loopback integration checks ran with the required execution permission. `git diff --check` passed. No live model calls, production accounts, microphone, or actual speech quality were tested.

The world's previous clock-related failure is fixed. The earlier search-root, account lost-update, old-cookie, timer-snapshot, and parallel/deletion quota scenarios have new regression coverage. The additional findings above expose cases that coverage does not yet exercise.

Reproduction scripts are in `reports/2026-09-05-review/`: `repros.py` exercises storage ownership, socket/session state, midnight accounting, and delayed results; `ws_repro.py` verifies revocation over a real temporary loopback connection; `regex_repro.py` exercises the bounded child-process search reproduction. Run with `PYTHONPATH=src .venv/bin/python <script>` from the repository root.

## Correction to the first review

The earlier statement that muted fallback timers could audibly ring was too strong. The production Player already enforces mute; the fake player used in that reproduction did not. The actual bug was marking a muted timer rung, losing its later announcement. The new checks fix that behavior. The earlier report has been corrected accordingly.

Prioritize account data isolation and socket revocation for Interview, then bound file-search execution and make drain failure terminal or explicitly recoverable. The midnight quota fix is small but should preserve reservation identity rather than add another date check.
