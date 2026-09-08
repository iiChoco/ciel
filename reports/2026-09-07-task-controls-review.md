# Review: the stage-two implementation, "The owner can see and steer a task" — 2026-09-07

Reviewed the uncommitted stage-two hunks on `ciel-upgrade` against
`design/2026-09-07-task-controls-plan.md` and the two earlier reviews:
`tasks.py`, `task_context.py` (new), `task_controls.py` (new),
`brain/tools/tasks.py` (new), `turn.py`, `wire.py`, `brain/agent.py`,
`pipeline.py`, `hub/server.py`, `remote/web.py`, `remote/discord.py`,
`remote/lane.py`, `remote/chart.html`, `spoke/client.py`, `spoke/frontend.py`,
`config.py`, `brain/permissions.py`, and the README and changelog entries.
The speaker-diagnostic, shortcuts, and gesture hunks that share these files
were read only where stage two touches them.

Every probe the plan lists was run here and passes with the counts the
changelog reports (`probe_tasks.py` 166, `probe_task_tools.py` 24,
`probe_task_wire.py` 20, `probe_turns.py` 78, `probe_ladder.py` 19,
`probe_hub_arbiter.py` 54, `probe_discord.py` 39, `probe_spoke.py` 56,
`probe_web.py` 40, `probe_wire.py` 75, `probe_confirm_wire.py` 13,
`probe_files.py` 30, `probe_shellguard.py` 165, `probe_hub_imports.py` 6),
as do `import ciel.hub.server`, the three storage-review acceptance
scenarios, and `git diff --check`. Two findings are reproduced by
`reports/2026-09-07-task-controls-review-repro.py` against temporary
stores and a temporary journal; nothing under `~/.ciel` is read or
written. No code was changed. Findings are ordered by impact.

## What the implementation gets right

- **Authority is a turn, captured once, fenced through commit.** The
  finding that the SDK hands an in-process tool nothing but its arguments
  became `TaskAuthority`: installed under `_turn_lock` only after a drain
  that left no debt, snapshotted by the handler at entry, and revoked by a
  lease whose lock the store's transaction holds from `BEGIN IMMEDIATE`
  through `COMMIT`. Revocation waits off the loop for a commit already
  under way and wins against one still queued. The probe drives the real
  `Query` dispatcher over a memory transport and pins the stale call
  entering during the drain, the drain-timeout turn with no authority for
  its whole lifetime, `_owed_results` refusing to let one result discharge
  two abandoned queries, and the SDK's own handler cancellation failing to
  slip a queued write past the fence. This is the hard part of the stage
  and it is done as the revised plan asked, on the warm client.
- **Identity survives the batch.** `Ingress` and `TurnBatch` carry the
  origin through every lane; `pop_turn_batch` coalesces only a run with
  matching principal, lane, namespace, privacy, and channel, drops a
  resend already in the run, and hashes the ordered message IDs. Chart
  mints a per-message ID into its ledger instead of hashing the per-tab
  ack counter. The store keeps consumed IDs, returns the saved task for an
  exact retry, and refuses a partial overlap with the sentence the plan
  asked for.
- **Two doors, one controller, and the revision rule is right.** Tools
  read the current record inside the transaction; Chart sends the
  revision it rendered and gets a conflict then a refresh. Owner
  eligibility on the wire is admission, the spoke seat is logged and
  dropped, task frames never touch the replay ring, and the hello carries
  `tasks: true` and no data.
- **Saved is not started, in data.** Creation commits straight into
  `waiting/resource`; resume and an accepted answer land there too; the
  Chart says "saved · execution unavailable" because the record does.
- **The public partition is a second client.** No `session_id` trick: a
  separate `Brain(public=True)` with `resume=None`, an empty MCP set, no
  private prompt context, recreated on an audience change, and its results
  never touch the private session file.

## Findings

### 1. The Chart's task list hides the live task behind recently cancelled ones

`owner_view` lists `ORDER BY updated_at DESC ... LIMIT max_active`
(`tasks.py`, `owner_view`). Terminal records are never pruned and a cancel
touches `updated_at`, so once `max_active` records have been cancelled
more recently than a waiting task was touched, that task is not in the
list. The Chart and `list_tasks` both read this view; the owner's one
saved responsibility becomes invisible while every cancelled one shows.

Reproduced (repro §1): with `max_active = 2`, one saved watch and two
later create-then-cancel pairs leave the list holding
`['cancelled', 'cancelled']` and the live task missing.

Fix: list non-terminal records first, then the most recent terminal ones
within the bound — `ORDER BY status IN ('done','failed','cancelled'),
updated_at DESC` — or bound the two groups separately. Pin: "a saved task
stays listed however many were cancelled after it".

### 2. The journal is now written from two threads, and it loses entries

`ActionJournal.record` is load, append, rewrite to one shared temp name,
rename (`journal.py:128–160`); it was only ever called from the loop
thread. `TaskStore.create` and `owner_control` now call the controller's
`record` inside `execute()` on the store's worker thread, while the
recorder's PostToolUse hook still records on the loop thread. A Chart
pause arriving while the model's `write_file` completes, or a `pause_task`
called in parallel with any journaled tool, races two rewrites: one loses
its entry, and the shared `.jsonl.tmp` name makes the second rename fail
with `FileNotFoundError`, caught as `OSError` and logged as "could not
write the action journal". The lost entry can be the *other* tool's — a
file write's snapshot reference — not just the task control's.

Reproduced (repro §2): two threads recording 200 entries each keep 200
of 400.

Fix: keep the "journal even if the caller disconnects" property but move
the write to the loop — in `_run`, attach the record to the shielded
future with `add_done_callback` (which runs on the loop thread whether or
not the awaiting coroutine was cancelled) — or give `ActionJournal` a
`threading.Lock` around load-rewrite and a per-call temp name. Pin: "a
task control and a recorded tool journaling at once lose nothing".

### 3. An enabled but unenrolled voice gate grants the room task authority

The README written for this change says: "Diagnostic speaker bypass and
an enabled voice gate with no profile confer no task authority." The code
does not do the second half. Both sites test only for the gate's
existence — `pipeline.py` (`_handle_voice_turn`):

```python
origin=None if self._config.voice.diagnostic or (self._config.voice.enabled and self._speaker is None) else owner_origin(...)
```

and the spoke's `owner_input=... (not self._config.voice.enabled or self._speaker is not None)`.
`build_speaker_gate` returns a `SpeakerGate` whenever sherpa-onnx is
installed, and that gate fails open with no profile or with the model
unavailable (`speaker.py:308–331`, `enrolled` is `False`). So with
`[voice].enabled = true` and no enrolment — the documented state before
`scripts/enroll_voice.py` is run — every voice in the room can save and
cancel the owner's tasks, which is exactly the case the README promises
is closed. Failing open is right for conversation; task authority should
fail closed.

Fix: `self._speaker is None or not self._speaker.enrolled` at both sites
(the property covers the model-unavailable path too, since it drops the
references). Pin in `probe_spoke.py` and `probe_turns.py`: "an enabled
gate with nobody enrolled sends no owner input".

### 4. Public turns lose the persona and every conversational tool

The public client's options are a fixed sentence ("You are Ciel, answering
in a public channel…"), `tools=['WebSearch', 'WebFetch']`, `mcp_servers={}`,
and no `hooks` (`agent.py:322–331`), where the private client carries
`build_system_prompt(personality=…)`, the ciel MCP server, and the hook
chain. A guild mention therefore gets no `jarvis` voice, no timers, no
world readings, no Spotify status, and WebFetch runs unrecorded. The
plan's words were "public tools", and the README says "public web tools",
so this is documented — but it is a product change to every server reply,
not a task-tool detail, and it deserves a deliberate yes. Two cheaper
shapes if the answer is no: keep the persona prompt (it is not private
context) and give the public client the ciel server filtered to the
Witness observer set minus recall; or keep it as built and say so in the
Discord section of the README, which currently still describes public
mentions as the typed lane's remote image.

### 5. Enabling tasks on the Azure hub puts the tunnelled Chart behind the token

`task_token_required` (`web.py:358–360`) turns on `require_token` for the
whole `/ws` whenever tasks are enabled and the hub binds off loopback. The
hub binds off loopback so the spoke can reach it, and the Cloudflare
tunnel reaches it as a loopback peer, admitted today by reach behind
Access. The day `[tasks].enabled = true` lands on the hub, ciel.yunhan.me
will answer 4401 and show the token form once per browser. That is the
plan's rule and the right one; it just needs a sentence in the hub
section of the README and in the deploy note, so the first "the Chart
asks for a token now" is not read as a regression.

### 6. Smaller points

- **Two utterances, two identical mandates.** Identity is the request,
  not the specification, so "watch PR 12" said twice saves two watches on
  the same checks (repro §3, recorded as a design note). Within the plan
  ("one task per explicit request"), and the tool description tells the
  model to list first; worth deciding whether creation should return the
  existing non-terminal task with the same specification instead.
- **The Tasks section appears on every Chart, tasks off or on.** The hello
  advertises `tasks: true` unconditionally (`web.py:766`), so the default
  Chart grows a collapsed "Tasks · saved responsibilities" row that opens
  to "Tasks are disabled." Advertising only when a controller is bound and
  enabled keeps the default page as it was; the plan's "older server hides
  the section" survives either way.
- **Barge-in now waits for a task commit.** `interrupt()` awaits
  `TaskAuthority.clear()`, which blocks a thread on the lease lock until an
  in-flight task transaction commits — up to `busy_timeout_s` (5 s) plus
  the write. Rare and bounded, but the interrupt path was previously free
  of storage; noting it so the latency, if ever seen, has a name.
- **`showTask` hardcodes the next step.** "Next: read the selected checks"
  is printed for any `next_step` (`chart.html`, `showTask`); render
  `next_step.kind` and `capability` so a seeded or future task reads true.
- **`_same_channel` is dead** (`discord.py:95`): `pop_turn_batch` compares
  channels with `!=`. Delete it.
- **`Ingress` is unhashable and `pop_turn_batch` indexes `ingress_ids[0]`.**
  Defining `__eq__` on the frozen dataclass sets `__hash__` to `None`, and
  `authority()` raises `IndexError` for an `Origin` with no ingress IDs.
  Nothing in the tree does either today; a guard on the second costs one
  line.
- **`bindings.client_generation` is `TEXT`** while `TaskAuthority` counts
  an `int`; no writer yet, so fix the column now under refuse-not-migrate
  or plan to `str()` it later.
- **Style.** `turn.py` places `from ciel.tasks import Origin` between the
  stdlib imports and `from typing import …`; `tasks.py` keeps the
  space-less argument packing the stage-one review noted.

## Verdict

Stage two does what the revised plan asked, and the part that mattered
most — proving authority at the turn boundary through the real SDK
dispatcher, with revocation meeting the transaction — is built and pinned
rather than described. Findings 1 through 3 should be fixed before the
flag is enabled anywhere: the first hides the owner's live task, the
second silently drops journal entries the moment two doors write at once,
and the third contradicts the README's own privacy claim in the exact
configuration a new install has. Finding 4 is a decision to make on
purpose and finding 5 a sentence to write. None is large.
