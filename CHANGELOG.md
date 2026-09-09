# Changelog

Notable changes to Ciel. Newest first.

## 2026-09-09 — A change is a proposal until the owner says so

**Why.** An event added from one message is later moved or cancelled by
another. A create-only grant must not act on that, and an owner who edited
the event by hand must never be overwritten. The plan's last milestone: the
change as an inert proposal, one task from the owner's approval of exactly
it, sent at the event's version.

**What.**

- *A proposal on the event record.* The extraction schema's commitment
  gains `cancelled`; a later message from the sender of an added event,
  about it by title, becomes an inert `proposal` record naming the update or
  the removal, its message, and its excerpts. Nothing is derived; the
  create's receipt stands; a newer message supersedes the open proposal,
  kept under its source's key. A moved commitment with no original on
  record is review, never a fresh add.
- *One task from one approval.* `approve_proposal` and the controller's
  request door make the task and mark the proposal approved at its revision
  in one transaction, the task's origin carrying the approval as its
  authority; a stale or repeated approval makes no task.
- *Sent at the version, only what was proposed.* `calendar.update` and
  `calendar.delete` join the adapter as plan, send, and reconcile: the plan
  reads the event's version, the send carries it, Google's 412 is a failed
  precondition that is unsent and planned again, and the change touches
  only the proposed fields, so the owner's edits to others stand. The real
  client sends conditional PATCH and DELETE with If-Match. The read-back
  completes on the proposed fields, or on the event being gone.

**Probes.** `probe_email_calendar.py 77 → 89: the original on the calendar,
a move as an open update proposal with the receipt untouched, a moved
commitment without an original as review, a newer message superseding the
open proposal, the approval's one task with the update alone and the
proposal marked at its revision, a second approval refused, the update sent
with no question and read back, an edit between plan and send as a failed
precondition with the edit standing, and a cancellation as a removal sent at
the version and read back as gone.` Rerun unchanged: probe_task_runner.py
25, probe_task_dispatch.py 37, probe_task_authority.py 93,
probe_task_wire.py 28, probe_task_tools.py 24, probe_turns.py 88,
probe_hub_arbiter.py 61, probe_hub_imports.py 6; hub import clean; the
spoke reloaded to ready.

## 2026-09-09 — Automatic means the same, without the question

**Why.** A candidate could be added by hand, one approval each. The plan's
fourth milestone is the standing grant: the owner approves a bounded scope
once in Chart, and confirmed commitments from approved senders land on the
calendar as they arrive, through exactly the path a per-action add takes,
minus the question.

**What.**

- *The feature offers its grant.* With a destination calendar configured
  the adapter carries a `GrantSetup`: the operations, the calendar and
  mailbox as targets, the approved senders and the caution that a matching
  address proves nothing, and limits from `max_creates_per_day` and
  `grant_lifetime_s`, each capped by `[tasks]`.
- *Activation starts the watch.* The controller lets a feature act when a
  grant for its namespace is activated and when one of its mandates moves;
  the inbox's first move is the watch task, created as the turn that
  approved and remembered by mandate. Pausing the mandate pauses it,
  resuming resumes it, revoking the grant ends it.
- *A read may propose children.* `Outcome.derive` carries derivations; the
  runner asks the store for each after the outcome is committed and logs a
  refusal rather than failing the step. The watch proposes an add task for
  every ready candidate, keyed by candidate and operation with the
  message's digest as its source revision, so a replay derives nothing
  twice and the store's allowances hold.
- *No question under a grant.* The derived child's authority is the grant
  at its activated revision; the dispatch that a per-action add would ask
  about is sent as it is, read back, and completed the same way.

**Probes.** `probe_email_calendar.py 66 → 77: no setup without a calendar,
the setup's fields and limits, the watch started at activation as the
approving turn under the mandate, a ready candidate derived and finished in
the same round with one event and no question, a stranger's candidate kept
for review, the same message deriving nothing twice, the day's allowance
refusing a third, and pause, resume, and revoke following the mandate.`
Rerun unchanged: probe_task_runner.py 25, probe_task_dispatch.py 37,
probe_task_authority.py 93, probe_task_wire.py 28, probe_task_tools.py 24,
probe_turns.py 88, probe_hub_arbiter.py 61, probe_hub_imports.py 6; hub
import clean; the spoke reloaded to ready.

## 2026-09-09 — A page is queued before the cursor moves

**Why.** A preview reads a window once; watching an inbox for a standing
mandate means reading its history without ever losing a page or reading one
twice, across crashes and across Gmail forgetting its past. And an owner who
says no to a candidate must be able to count on never seeing it again.

**What.**

- *History, one page a step.* The Gmail reader takes the mailbox's history
  anchor and lists messages added since an id, a page at a time, with the
  404 of a forgotten history returned as expired rather than raised. The
  adapter's `inbox.poll` records a page's messages as queued and the page
  token in the same write-set, moves the history id only with the last
  page, extracts what it queued, and goes round again after `poll_s`. A
  replayed page records nothing twice; a first poll anchors and reads
  nothing before it.
- *Forgetting is handled once.* An expired history lists the window since
  the anchor, bounded by `max_messages_per_poll`, takes what is new, and
  anchors again with the resync counted on the cursor record.
- *A watch is a task.* `watch_request` builds it: read operations only,
  ending only with its mandate; the grant setup that starts one is the next
  milestone. A watch's evidence names its own criterion, as a preview's does.
- *A dismissal is a tombstone.* The controller gains feature controls over
  a feature's own records, journaled; `dismiss_candidate` marks a candidate
  dismissed, `add_event_from_mail` refuses it, and a second preview of the
  same window does not read the message again.

**Probes.** `probe_email_calendar.py 54 → 66: the watch's shape, the anchor
with nothing before it, three arrivals as two pages with the token saved
before the id moves, extraction and the next round after the interval, a
crash between pages leaving the token and the id, a restart resuming from
the page with nothing twice, the resync when the source forgets, and a
dismissal that refuses an add, answers twice the same, and stands after a
restart and a second preview.` Rerun unchanged: probe_task_authority.py 93,
probe_task_tools.py 24, probe_task_wire.py 28, probe_turns.py 88,
probe_hub_imports.py 6, probe_sections.py 78; hub import clean; the spoke
reloaded to ready.

## 2026-09-09 — An event is added once, under a name only Ciel would choose

**Why.** The preview could say what the inbox held and nothing could act on
it. The plan's third milestone is one approved event on the calendar,
through the foundation's guarded dispatch: check first, send once, read
back, and never guess about an answer that never came.

**What.**

- *A calendar the writer can read and insert into.* A `CalendarSource`
  protocol and `GoogleCalendar` over the same login plumbing the sender
  uses, borrowing the calendar watcher's token files read-only: get by id,
  find in a window, insert under a client-chosen id; a 404 is None, a
  deletion is a cancelled event, a 409 is a conflict.
- *A name only Ciel would choose.* The event id is derived from the mailbox,
  the message, the candidate, and the calendar, so a retry is safe and a
  duplicate is visible; the body carries title, place, and times in the
  candidate's zone, Ciel's ownership in the private properties, and no mail
  text. `add_event_from_mail` makes the finite task from a preview's
  candidate; an unresolved candidate is refused with what to settle.
- *Check, create, verify.* The check finds Ciel's own event by id, an
  independent match by start and title across the destination and
  `check_calendars` (present, done, nothing sent), a foreign event under the
  id (a recorded conflict that waits), or a deletion since (suppressed,
  never recreated). The create plans one insertion, the foundation asks the
  owner to approve that payload once, and the send treats a conflict as
  success only when what is there is this event. The read-back completes
  the task, or records missing, deleted, conflicting, or edited and waits.
- *Reconciled by the id.* A lost answer resolves applied or not applied by
  reading the id; an event that landed and was since deleted is applied and
  then suppressed; a foreign event under the id is an unknown the owner is
  asked about. Nothing is resent blind.

**Probes.** `probe_email_calendar.py 34 → 54: the id's shape and constancy,
the body's fields and ownership, the add task's scope, the check that sends
nothing and the approval it asks for, one send with ownership read back and
recorded, a second ask finding the event by id, an owner's edit left as it
is, a deletion suppressed and a later 404 still suppressed, an independent
match present without a send, a foreign event under the id as a conflict, a
lost answer reconciled without resending, a send that never landed planned
again under fresh approval, a declined approval, a calendar that is not
connected, and an approval that survives a restart.` Rerun unchanged:
probe_task_authority.py 93, probe_task_notices.py 23, probe_task_wire.py 28,
probe_task_tools.py 24, probe_task_dispatch.py 37, probe_turns.py 88,
probe_hub_arbiter.py 61, probe_hub_imports.py 6; hub import clean; the
spoke reloaded to ready. The real Google calendar is not exercised by the
probes; a live run against a test calendar is separate acceptance.

## 2026-09-09 — A file can come with the words

**Why.** The Chart could carry words and nothing else. A screenshot, a
PDF, a CSV: each had to be described instead of shown, and "look at this"
meant the screen tool on the Mac or nothing at all from a phone.

**What.**

- *The page sends the file ahead of the say.* A FILE button, a paste, or a
  drop makes chips beside the composer; SEND uploads each as its own
  `file.put` frame, answered by id, and the say names the ids. The ledger
  keeps them with the words, so a resend after a reconnect still points at
  what was stored. Images are scaled to 1568 pixels on the long edge as JPEG
  on the page; the hub never resizes. The hello says whether files are
  taken and how large, and an older server hides the button.
- *The server keeps nothing it was not told.* The name is reduced to a safe
  basename, the type is what the first bytes say it is, the size is bounded
  by `[web].max_upload_bytes`, and the file is written owner-only with an
  exclusive create under `[files].workspace/uploads`, where the brain's file
  tools can reach it. A resend of an id answers with the record already
  made; an unadmitted socket stores nothing; an id the server does not hold
  is dropped and the page told in a row.
- *The model is shown what fits and told about the rest.* Every attachment
  is named with type, size, and path under a note that its contents are
  data, never instructions. A text file under `max_inline_chars` is quoted;
  an image within `image_prompt_chars` of base64 rides beside the words as
  an image block in one user message, the SDK's streaming shape; anything
  larger is named for the model to open. The transcript row names what was
  attached and never its contents; a public turn never carries a file.

**Probes.** `probe_web.py 45 → 59: the hello before and after binding, an
owner-only file under a safe name with its size answered, a resend that
rewrites nothing, sniffed images and octet streams, bad base64, empty and
oversized files, an unminted id, an unadmitted socket, a say with files,
files alone, an id not held, a burst keeping its files, and the per-turn
bound. probe_turns.py 80 → 88: the attachment note, quoting, budgets, the
image message's shape, a web turn handing the brain the picture, and the
transcript row. probe_wire.py 80 → 83: the file frames and the say's file
list.` Rerun unchanged: probe_hub_arbiter.py 61, probe_hub_imports.py 6,
probe_task_wire.py 28, probe_confirm_wire.py 17; hub import clean; the
spoke reloaded to ready. Visual: the live echo page served with an uploads
folder; the button, chips, a sent image and text file echoed with their
types, and the narrow layout were checked in the browser.

## 2026-09-09 — The whole thought can be selected

**Why.** Command–A did nothing in the floating note. The accessory process
has no Edit menu, and its text view did not handle the command shortcut path.

**What.**

- *The editor receives its familiar commands.* Command–A selects all;
  Command–X/C/V reach the native cut, copy, and paste actions. Dispatch is
  limited to the focused, editable editor, respects text composition, and
  leaves extra modifier combinations to AppKit. The reproduction is in
  `reports/2026-09-09-note-edit-shortcuts.md`.

**Probes.** `probe_note_window.py 41 → 48: window-level Select All, Unicode
and Caps Lock, selection replacement persisted to the draft, clipboard action
dispatch, extra modifiers, button focus, and editing blocked during a save.`
The Select All check failed before the fix. Clipboard dispatch uses spies
on the temporary editor, without reading or changing the owner's clipboard.

## 2026-09-09 — The dark ground keeps its shade

**Why.** The native quick note looked lighter and bluer than the supplied
design. Its hex tokens were being interpreted as macOS calibrated RGB:
the intended sRGB background `#0a1620` became approximately `#091d2b`.

**What.**

- *The same numbers mean the same colour.* The note's shared colour helper
  now explicitly uses sRGB for text, layers, and controls, preserving the
  prototype's palette and opacity. The reproduction and measured values
  are in `reports/2026-09-09-note-colours.md`.

**Probes.** `probe_note_window.py 37 → 41: background, editor and hint,
error red, and receipt greens match the prototype in sRGB.`
The new background check failed before the fix. Native hover, saved, and
narrow error renders were reviewed; the spoke reloaded to ready.

## 2026-09-09 — A thought needs only a line

**Why.** The first quick-note window gave a passing thought a whole sheet.
The owner's Quick Note Prototype puts the blank line within reach and reveals
more room and controls only when they are useful.

**What.**

- *A bar that grows with the thought.* The native window follows the supplied
  prototype: 560 points wide, cut corners, a gold dot, 17-point system text,
  and an inline save hint. Paragraphs expand the editor to 180 points before
  it scrolls. The Chart's Instrument palette remains unchanged.
- *Controls arrive with attention.* Hover reveals the count, Tuck away, and
  Save. Tab reveals and focuses the controls too, retaining them while they
  have keyboard focus. Narrow windows give the count its own row. The top
  edge stays still as the bar grows, and reopening keeps its dragged position.
- *Memory still owes the receipt.* Saving folds the bar to one line with a
  breathing gold dot. A successful receipt becomes a green serif line before
  tucking away; a failed save restores the editor with a red border, wrapped
  error, and Retry that stays visible without hover. Draft retention, private
  memory writes, and the global shortcuts keep their existing paths.

**Probes.** `probe_note_window.py 19 → 37: compact height, paragraph growth,
scrolling, hover and keyboard disclosure, focus retention, compact progress
and success, pinned retry, reopening during a receipt, narrow error wrapping,
length limits without truncation, and position preservation. probe_notes.py
34 and probe_shortcuts.py 66 pass unchanged.` Native renders reviewed at
560 and 400 points: empty, multiline, hover, keyboard focus, saving, saved,
error, narrow error, and long-note states. The spoke reloaded to ready.

## 2026-09-09 — The keyboard is asked only where there is one

**Why.** Every hub restart since 2026-09-06 logged a `PermissionError`
traceback from asyncio: under systemd the process's stdin is `/dev/null`,
which the selector cannot watch, and `connect_read_pipe` accepts the
descriptor anyway and refuses it later, in a loop callback, past the try
that was meant to catch a stdin that is not readable. Fourteen tracebacks
in three days, none of them a fault, all of them noise in the journal.

**What.**

- *Look before attaching.* The typed lane looks at fd 0 first and attaches
  only to a terminal, a pipe, or a socket; anything else is declined with one
  info line and voice carries on. A service manager's `/dev/null` and a
  closed terminal now read the same way, quietly.

**Probes.** `probe_turns.py 78 → 80: a /dev/null stdin is declined before
anything is attached with no callback traceback; a pipe is still a chatbox
whose line is queued and whose EOF ends the reader quietly.` Rerun unchanged:
probe_hub_arbiter.py 61; hub import clean; the spoke reloaded to ready.

## 2026-09-09 — The inbox is read as data, and a preview writes nothing

**Why.** The foundation's four gates hold against a synthetic adapter and
nothing real uses them. The email-to-calendar plan's first milestone is the
preview: before anything is granted, the owner sees what the inbox holds
and how it would be read.

**What.**

- *The inbox is a source.* `gmail.py` gains a read-only reader beside the
  sender, over the same connector login and plumbing, listing and fetching
  raw messages and changing nothing. `email_calendar.py` parses a message
  with the standard library into bounded text and the few headers policy
  needs, HTML reduced to its text with nothing active kept.
- *The model is held to the message.* The isolated extraction call gets a
  fixed prompt that names the message untrusted data and a schema for
  candidates; every excerpt it cites must be in the message word for word,
  every time must be a real wall time in a named zone or the owner's, and
  what the message does not settle stays unresolved. Policy decides ready,
  review, or ignored; a sender match filters scope and the ready reason
  says it is not proof.
- *A preview is a finite task from an owner turn.* `preview_inbox` asks
  through the controller's new feature door; the adapter lists the window
  once, decides bulk mail without a model call, spends the task's own
  calls on the rest, records candidates and a roster in its namespace, and
  completes with evidence. Out of calls, no backend, or no mailbox are
  said, not guessed at. The task's detail carries the feature's own words.
- *Config, not authority.* `[email_calendar]` registers the adapter and
  holds the owner's zone, sender list, and bounds; no grant is offered yet.

**Probes.** `probe_email_calendar.py 34 (new): normalization, every
interpretation rule, the preview request's bounds, a preview through the
runner with replay, exhausted calls, no backend, an absent mailbox, and a
restart, and the owner's door with a public lane refused and the summary
quoted. Rerun unchanged: probe_sections.py 78, probe_task_runner.py 25,
probe_task_tools.py 24, probe_task_wire.py 28, probe_hub_imports.py 6; hub
import clean; the spoke reloaded to ready.`

## 2026-09-09 — The result reaches the owner

**Why.** A task could finish, fail, or ask, and write its notice; nothing
carried the notice anywhere, and nothing knew whether the owner had seen
it. This is the independent-action plan's fourth milestone, and with it the
foundation's four gates have been built: a bounded, approved task can
proceed, wait, recover, verify, and report independently, against a
synthetic adapter. No real adapter exists yet.

**What.**

- *What is owed is a fact in the store.* Schema seven adds deliveries and a
  settings table. A completion, a failure, or the question a task waits on
  now is owed until the owner looks at the task on a private lane; the look,
  through Chart or `inspect_task`, is the receipt, recorded per notice.
  Every delivery attempt is recorded after the fact with the lane that
  carried it; a sent notice is not offered again, a failed one is offered
  again after `notice_retry_s`.
- *Vigil says it.* `TaskNotifier` hands each owed notice to Vigil's queue
  once per attempt as an ordinary event, importance timely or urgent for a
  failure, in one spoken line with only identifiers in its payload. The
  pipeline records a spoken nudge, a text, or a held note read into the next
  conversation as sent. Presence, quiet hours, and budgets stay Vigil's.
- *The notice switch is not a pause.* `notices_mute` and `notices_unmute`
  are owner controls with the same admission as every other, persisted in
  the store, journaled as the notice switch, reachable from Chart and as
  `mute_task_notices` by voice. Muted, nothing is offered and every task
  keeps running.
- *The view says where things stand.* The list carries the switch and how
  many notices are owed; a task's detail carries its current authority, its
  unresolved effects, and its notices with their deliveries.

**Probes.** `probe_task_notices.py 23 (new): what is owed after a reopen
and what is not, the failed and sent attempts, the receipt once, the switch
persisted and refused to a public lane, the notifier's one offer per attempt
and the retry, the receipt through the controller, the runner stepping under
mute, unmute offering what was owed, and the version-six lift.
probe_wire.py +1: the switch needs no record and no revision. Rerun
unchanged: probe_tasks.py 202, probe_task_authority.py 93,
probe_task_dispatch.py 37, probe_task_runner.py 25, probe_task_wire.py 28,
probe_task_tools.py 24, probe_hub_arbiter.py 61, probe_vigil.py 173,
probe_turns.py 78, probe_hub_imports.py 6, probe_ladder.py 25; hub import
clean; the spoke reloaded to ready. Visual: the live Chart fixture showed
the switch and the owed count in the list and a notice's state in a task's
detail.`

## 2026-09-09 — An interrupted action can be understood

**Why.** The runner could read and the store could hold a grant, but no
mutation had ever been sent: the dispatch phase the runner waited for did not
exist, and neither did the record that would let a crash mid-send be
understood rather than repeated. This is the independent-action plan's third
milestone. With it, a synthetic external effect survives every way a process
can die around it with exactly one effect on the far side. No adapter can
write yet; the probes supply one.

**What.**

- *Nothing is sent that was not first written down.* Schema six adds
  intents and approvals. A `WritingAdapter` plans the payload against what
  it reads of the target; the runner digests payload and preconditions,
  journals the intent (a journal entry now returns its reference, and none
  means no send), and the store's `authorize` finds the authority at that
  moment, the grant at its activated revision or the owner's approval of
  this exact payload, and commits the intent with the attempt's dispatch
  in one write. A moved target or a passed deadline is an unsent attempt,
  closed as not applied and planned again. A human task with no standing
  authority asks the owner to approve this action, through a question
  bound to the payload digest.
- *An outcome nobody knows is reconciled, never resent.* Uncertain
  attempts come before any new step. The adapter's `reconcile` is
  read-only; applied returns the retry allowance and queues the verifying
  read the plan named, not applied reopens the mutation, and a read that
  cannot tell is counted on the intent until `max_reconcile_reads`, when
  the owner is asked in two exact words. A cancelled task records what
  recovery finds and stays cancelled; a revoked grant reconciles but does
  not resend; an owner's edit after the effect is read, not overwritten.
- *A runner queues what the owner answers.* With a runner present, a
  resumed or answered task is queued for it rather than parked in a
  resource wait; without one, nothing changes.
- *Config.* `dispatch_deadline_s`, `mutation_timeout_s`, `max_reconcile_reads`.

**Probes.** `probe_task_dispatch.py 37 (new): the grant path end to end with
its journal correlation, a lost response reconciled and never resent, a moved
precondition planned again, a timed-out send found not applied and sent once
more, reads that cannot tell and the owner's word either way, a task
cancelled while uncertain, a grant revoked after the send, no journal, a
passed deadline, the owner's approval of an exact payload and its refusal, an
owner's edit preserved, and a process killed after the send and before it,
each recovering to one effect and validating after reopening.
probe_task_runner.py 25: the mutation wait now names an adapter that cannot
dispatch. probe_tasks.py 202 and probe_task_authority.py 93: the lifts land
on six. Rerun unchanged: probe_task_wire.py 28, probe_task_tools.py 24,
probe_hub_imports.py 6, probe_turns.py 78, probe_hub_arbiter.py 61,
probe_ladder.py 25, probe_wire.py 79, probe_confirm_wire.py 17,
probe_shellguard.py 165, probe_vigil.py 173; hub import clean; the spoke
reloaded to ready.`

## 2026-09-09 — The form is in Chart; the yes is the broker's

**Why.** The records could hold a grant, but nothing could ask for one: a
draft had no surface, and the owner's yes had no path from a page to the
broker. This is the second half of the independent-action plan's second
milestone. With it, a feature that offers a grant can be approved end to end,
and still nothing derives work until an adapter offers one.

**What.**

- *A draft holds nothing the owner did not see.* Schema five gives a draft
  its adapter, outcome, and limits, all under the digest; an adapter offers
  a `GrantSetup` with labelled operations and targets, resolved accounts,
  host, and limits, and the runner exposes them. The controller's
  `grant_draft_save` lets the owner narrow operations and targets only; the
  rest is the setup's. A version-four store is lifted with its open draft
  discarded rather than guessed at.
- *The yes comes through the broker, on one session.* `grant_approve`
  refuses before any question when the revision and digest are not the
  current draft, then asks through the pipeline's broker. The web link
  shows the question as a private confirm frame to the socket that pressed
  Approve, never a broadcast, and the answer rides back as any web answer.
  The broker's new `ask_through` takes a question with no turn behind it
  only when idle and no turn has a channel installed, for exactly the ask's
  duration. A no, a timeout, or an edit while the question is open leaves a
  draft; a matching yes activates, journaled with its approval reference.
- *The Chart shows it.* A *Standing grants* section under Tasks lists
  mandates with pause, resume, and revoke, open drafts with approve, edit,
  and discard, and the form for every offered feature. The wire catalog
  names each new operation and the record it must carry.
- *Voice opens the door and closes it.* `pause_mandate`, `resume_mandate`,
  and `revoke_grant` join the task tools; `list_tasks` says where the form
  lives. Nothing spoken fills a form or answers for the page.

**Probes.** `probe_task_authority.py 71 → 93: the draft's adapter, outcome,
and limits under its digest, a cap lowered after saving, the form's offered
operations and targets, approval refused before any question for a stale
draft or a runtime with no broker, a no that keeps the draft, an edit while
the question is open that leaves a draft even after a yes, the matching yes
and its journal line, discard, and the version-four lift. probe_task_wire.py
20 → 28: setups on the list view, a draft saved from one view and seen from
another, approval without a broker refused, a stale approval refused before
the question, the question as a private confirm frame on the asking socket
and a task.changed on the other, a yes that activates for every view, and a
mandate paused from the Chart. probe_confirm_wire.py 14 → 17: the
channel-scoped ask approves and clears its channel, waits behind a turn's
channel and gives up rather than clobbering it, and treats no answer as no.
probe_wire.py 75 → 79: the grant operations' required fields. probe_tasks.py
202: the version-two lift now lands on five. Rerun unchanged:
probe_task_runner.py 25, probe_task_tools.py 24, probe_hub_imports.py 6,
probe_turns.py 78, probe_hub_arbiter.py 61, probe_ladder.py 25; hub import
clean. Visual: the live fixture served the real Chart with a setup, a draft,
and an active mandate; the section, its form, keyboard focus, and the narrow
layout were checked in the browser.`

## 2026-09-09 — Permission has a precise scope

**Why.** The runner could give a task a turn, but every task still began in
a human turn, and nothing could hold a standing responsibility or say where
derived work came from. The [independent-action plan](design/2026-09-08-independent-action-plan.md)'s
second milestone lands as two changes, records first; this is the first. It
makes derived work possible and bounded without yet giving anything the
surface to ask for it: the Chart grant form and the broker's approval come
next, and until an adapter derives under a grant, nothing does.

**What.**

- *An origin says what it is.* `HumanOrigin` is the live private owner turn
  every task has had, and `Origin` still names it; `DerivedOrigin` names a
  mandate, grant, adapter, event, and source revision and nothing that claims
  attendance or a human lane. Schema four rewrites every stored origin as a
  human one, byte for byte as a fresh create would, so a repeated request
  still finds its task; version two lifts through three. `create` accepts
  only a human origin, and can now carry an approval reference and the
  write-set that must land with it, which is how an approved proposal becomes
  exactly one task.
- *A grant is the owner's yes, digest and all.* A draft in `grant_drafts` is
  what the owner looks at, never executable; editing it moves its revision
  and digest together. Activation takes an attended private owner turn, the
  exact draft revision and digest, a registered namespace, and limits within
  the new config caps, and commits the `StandingGrant` and the `Mandate`
  under it in one transaction. Revoking a grant ends its mandates at once; an
  expired grant is marked so on the record the moment it is asked to derive.
- *Derived work inherits, it never invents.* The runtime-only `derive_task`
  admits a child only under an active mandate and an active, unexpired grant
  at the revision it was activated with, only inside the grant's operations
  and targets, and only within lifetime and window allowances counted from
  the persisted window start. The same event at the same source revision
  returns the child it already made and spends nothing; a newer revision
  revises an unfinished child in place while no dispatch intent exists and
  the scope stands, voids its open question, and keeps the older revision as
  a replay alias; a sent mutation freezes the child; a finished child is
  never reopened; a change of scope is refused so the adapter proposes.
- *Controls share the owner's door.* The controller derives only through a
  `Namespace` it registered and journals it; mandate pause, resume, and
  revoke and grant revoke go through the same admission as every other
  control, from the same Chart frames. The owner view lists mandates and
  grants beside tasks.

**Probes.** `probe_task_authority.py 71 (new): drafts edited and never
executable, activation's refusals and its one transaction, every refusal of
derive_task, the child's origin, replay, in-place revision and its alias, the
voided question, the frozen child, the finished child's successor, window and
lifetime allowances across a reopen and a lowered cap, pause and resume,
revocation, expiry on the record, the approval path and its stale revision,
the controller's registered-namespace rule and journal, and the version-three
lift. probe_tasks.py 200 → 202: the version-two store lifted through three to
four with its origin saying it was human and its request still found; a
lifted store lists no mandates or grants. Rerun unchanged: probe_task_runner.py
25, probe_task_wire.py 20, probe_task_tools.py 24, probe_hub_imports.py 6,
probe_turns.py 78; hub import clean; the spoke reloaded to ready.`

## 2026-09-08 — Shift belongs to the keyboard

**Why.** Keyboard clicks were being heard as snaps again. Investigation found
that the keyboard veto watched ordinary key-down and key-up events but omitted
modifier changes: a lone Shift or Command could sound like a snap without the
keyboard owning up to it. `reports/2026-09-08-modifier-snap-veto.md` reproduces
that hole; it does not attribute every reported false wake to a modifier.

**What.**

- *A modifier is a key too.* The existing timestamp-only veto includes modifier
  and status-key changes on press and release. Its 300 ms window, mouse checks,
  and gesture thresholds stay the same. Echo cancellation remains enabled.
- *An expired veto leaves a reason to investigate.* The existing
  `log_candidates` switch also shows key/click event ages and the veto window.
  It records no key identity and opens no event tap.

**Probes.** `probe_gestures.py 138 → 146`: modifier-only clicks cannot wake or
complete a clap action; releases refresh the veto, expiry admits a snap again,
and candidate timing diagnostics follow the existing switch.

## 2026-09-08 — The room keeps its volume

**Why.** The split pair fixed Ciel's lisp but left Apple's voice processing
running between turns. Nearby speech could turn the Mac's music down even when
Ciel never woke. `reports/2026-09-08-audio-ducking.md` traced that behavior to
advanced ducking and showed why Stop and mute could not release it.

**What.**

- *The reference listens without touching the speaker.* An opt-in `webrtc`
  backend uses a private, nonmuting Core Audio tap and the approved, pinned
  `pywebrtc-audio` AEC3 dependency. Ciel retains the ordinary PortAudio speaker.
  Apple's voice-processing unit and its ducking are absent from this route.
- *One clock for what played and what returned.* A native aggregate synchronizes
  the mic and stereo reference with drift compensation, resamples them together,
  and detects lost frames. A configurable 40 ms capture hold covers late device
  references. A zero-filled ordinary output stream starts the reference clock
  even in an idle room. The adaptive filter survives turn boundaries and Stop; only
  processed microphone frames leave the audio boundary.
- *A missed callback does not restart the room.* Live use exposed a regression:
  timing gaps and callback lock contention killed the helper, and launchd kept
  relaunching the spoke. Paired drops now reset the resampler and echo filter,
  clear delayed and queued mic audio, and resume in the same process. The ring
  copies outside its lock while retaining ownership of the occupied slot.
  Echo protection stays enabled; adaptation restarts and a word can be interrupted.
- *A missing reference closes the room.* Startup, permissions, malformed frames,
  stalls, helper death, and route changes fail visibly. Private builds are
  atomic and retain the last working helper. The infrastructure launcher's
  purpose string now includes system-audio capture: macOS checks the responsible
  app as well as the helper. Startup timeouts name the permission to check.
  Audio stays in memory. The existing
  Apple and raw backends remain selectable; there is no silent fallback.
- *The measurement has a boundary.* The live Mac comparison found +0.21 dB of
  playback amplitude change and 18.5 dB echo reduction. Synthetic double talk
  preserved voiced speech above the echo; whispered speech under loud media
  and the owner's gestures still need room checks. Barge-in stays off.

**Probes.** `probe_webrtc_audio.py 0 → 47`: actual AEC3, speech retention,
paired resampling, framing, private builds, protocol failures, and cleanup.
The recovery correction adds 7 checks (40 → 47), including forced native
contention/overflow and clearing pre-gap audio without restarting capture.
`probe_apple_audio.py 81 → 81` and `probe_input.py 9 → 9` passed. The explicit
initial live comparison passed 4 checks. The expanded recovery comparison
passed 6 on repeat (+0.38 dB playback change, 16.9 dB echo reduction); its first
run failed the volume tolerance at −2.88 dB, retained in the report. Neither
run retained recordings. `probe_hub_imports.py` passed 6 checks; the infrastructure launcher
suite passed 9 tests.

## 2026-09-08 — A place to catch a thought

**Why.** A passing idea needed a whole conversation to reach Ciel's memory.
By the time there was room to say it, the thought could be gone.

**What.**

- *A blank page at a key.* Command–backslash and a quick backslash pair open
  a native floating Instrument window. Enter makes a paragraph; Shift–Enter
  saves; Escape tucks the draft away. Both gestures are configurable under
  `[notes]`, independent of the voice controls, using the same passive Mac
  listener and its Input Monitoring permission. The pair still reaches the
  foreground app. The hub starts no keyboard listener.
- *The receipt belongs to memory.* A note enters Invariant as a reference,
  with its wording and note provenance preserved, without a model turn.
  The split spoke sends a private `note.save` and waits for `note.result`;
  the local process uses the same writer. Only the seated spoke can use
  this path, and nothing enters the conversation queue or the broadcast ring.
- *A draft survives the interruption.* Owner-only drafts reopen after Escape
  or restart. A failed save keeps the text and offers retry; a stable id
  absorbs a lost receipt, while a second note with the same opening gets
  its own file. The note and human index are owner-only, and memory recall
  and prompt summaries quote notes as data, never instructions to execute.

**Probes.** `probe_notes.py 34 (new): draft recovery and permissions, wording,
provenance, idempotency, bounds, disk failure, quoted recall, offline and timed
out saves, private seated-spoke routing, and local/spoke integration.
probe_note_window.py 19 (new): native focus, Enter, one Shift–Enter save,
in-flight editing, retry identity, visible errors and success, Escape,
restart, Tab focus, a 400-point window, and native process start/reuse/exit. probe_shortcuts.py 55 → 66:
command chord, timed pair, repeats, intervening keys, independent settings,
conflicts, and configuration. probe_wire.py 75 → 76: note frames round-trip
and stay out of the replay ring.` Existing spoke (56), hub arbiter (61),
hub imports (6), turns (78), Vigil (173), and Closure (56) probes also pass.
Native renders reviewed at 620 and 400 points, including draft, error,
saving, and saved states. No new dependency or hub deployment.

## 2026-09-08 — A task gets another turn

**Why.** The store remembered what the owner asked for and the controller let
them steer it, but nothing ever moved a task forward: every saved
responsibility sat in a resource wait by design. The
[independent-action plan](design/2026-09-08-independent-action-plan.md)'s
first milestone is the executor that plan deferred, built against a synthetic
adapter so the mechanism is proven before any inbox touches it.

**What.**

- *One bounded step, at the bottom of the ladder.* `task_runner.py` takes the
  oldest eligible task and gives it one step: prepare (pure), claim, dispatch,
  observe, then complete, checkpoint, wait, or ask. The ladder in `schedule.py`
  gains a TASK source after Vigil, from WAITING only; a task that has waited
  past `task_aging_s` passes a nonurgent nudge and never an urgent one or a
  human lane. Human input interrupts a step holding the model turn; a plain
  read is left to finish. Mutation steps and unserved operations wait visibly.
  Off by default: `[tasks].runner`.
- *Giving up is recorded.* The store gains `eligible`, `abandon`, and
  `note_model_call`: an abandoned attempt is spent, a read is requeued with
  `retry_backoff_s`, a sent mutation waits for reconciliation, and an exhausted
  allowance fails the task with the reason in its detail. A model-call
  allowance is captured at creation like the others.
- *A feature's records have a namespace, not a column.* Schema three adds
  `feature_namespaces` and `feature_records`; adapters register a namespace
  with a validator and a migration through the controller before the store
  opens, and write-sets ride the checkpoint, wait, or completion they belong
  to. Unknown and newer namespaces are preserved untouched and reported
  unsupported. A version-two store is lifted in place; a newer one is refused.
- *One model call has one bounded context.* `brain/extract.py` runs a fresh
  SDK client per call with no tools, no MCP servers, no settings, one turn, a
  private empty working directory, and the schema as its output format, checked
  again in runtime code. It holds the Brain's new turn lease and never falls
  back to the conversational client.

**Probes.** `probe_tasks.py 166 → 200: eligibility in age order, the
model-call allowance, abandonment in each of its three endings, validated
write-sets and expected revisions, write-sets riding a checkpoint, adapter
migration at open with a failed one rolled back, unknown and newer namespaces
preserved, and the version-two lift. probe_task_runner.py 25 (new): one step
observed and saved, restart resumes, a late result after cancellation writes
nothing, mutation and unserved steps wait, exceptions and timeouts spend the
attempt, the owner's voice cancels an extraction and frees the lease, a
question and an external wait, a refused outcome abandoned, unsupported
records, no fallback without a backend. probe_extraction.py 27 (new): bounds,
the lease held and released on timeout and cancellation, the schema check's
six refusals, and the client's options against a fake SDK. probe_ladder.py
19 → 25: the task step's rank and its one exception. probe_hub_arbiter.py
54 → 61: the snapshot's task fields, enacting a step, the interrupt on a human
pick. Rerun unchanged: probe_turns.py 78, probe_task_tools.py 24,
probe_task_wire.py 20, probe_hub_imports.py 6; hub import clean; the spoke
reloaded to ready.`

## 2026-09-08 — Nobody spoke, so nothing was heard

**Why.** An afternoon of typing produced a run of turns nobody had spoken,
answered in full. Three layers let each one through. The snap ear woke on
impulses a fiftieth as loud as a snap — bright, solitary, and owned up to by
no keystroke, the signature of a mouse button. The endpointer, whose
webrtcvad calls a run of keys speech, handed the clatter to Whisper. And
Whisper, which never says "nothing", invented a sentence; its own no-speech
probability could not be asked, since on large-v3-turbo it reads 0.000 on
pure silence. The stock-phrase filter caught none of it, because the
sentences invented over a keyboard are not stock. The investigation and
the calibration are in
[reports/2026-09-08-sentences-nobody-said.md](reports/2026-09-08-sentences-nobody-said.md).

**What.**

- *A sentence needs a voice behind it.* Before Whisper is asked, Silero VAD
  — the speech model openWakeWord already carries — scores the utterance
  frame by frame, and its most confident 30 ms frame must reach
  `speech_threshold` (0.5). Measured on synthesized speech and synthetic
  rooms: silence, noise, clatter, and breath never peaked above 0.23; speech
  peaked at 0.65 and up at every level down to a whisper. One frame is
  enough, so "Yes." survives. The gate wraps the transcriber, not the turn,
  so turns, held thoughts, and confirmation answers are gated alike on either
  engine, and the runtime fallback to faster-whisper stays behind it. It
  fails open without the model and says so. `stt/gate.py`.
- *The keyboard's own word covers the mouse.* macOS reports the time since a
  mouse button went down or up as readily as a key, so a snap or a clap
  inside `keyboard_veto_ms` of either is rejected as that keystroke or that
  click, and the log names which. A fixture that supplies one clock is never
  answered by the Mac's other one.

**Probes.** `probe_stt.py 21 → 33: the gate keeps silence and noise from the
engine and speech untouched, the threshold edge, off at zero and on by
default, warm-up and close pass through, fail-open without the model, and
Silero's own word on silence and hiss. probe_gestures.py 134 → 138: the click
veto, the more recent of key and click named, and a fixture's clock alone.`

## 2026-09-08 — The plans say who may act and where

**Why.** The independent-action and inbox plans left child origins, setup,
model isolation, account placement, and feature storage to implementation.
The [review response](reports/2026-09-08-independent-action-review-response.md)
records the supplied critique and the resulting decisions.

**What.**

- *Responsibility has a record and a surface.* The foundation now names derived
  origins, minimum records, a Chart grant flow, namespaced feature migrations,
  the task's ladder position, and an isolated extraction call. Existing task
  controls must reach an accepted baseline before runtime implementation.
- *The inbox names its limits.* The feature requires execution-host account
  readiness, remembers deleted calendar entries locally, uses gcal_adapter.py,
  and defines a sender-scoped policy without claiming sender authentication.
  The full source-policy limitation must be reviewed before automatic activation.
- *A second pass keeps config in one place.* The feature borrows the existing
  Gmail and Calendar connector-path fields instead of adding four more;
  enrolling a sender from preview is a scope widening through the draft, never
  a one-click allow; foundation milestone 2 lands as records first, Chart
  surface second.
- *A finished task stays finished.* The [second review](reports/2026-09-08-independent-action-second-review.md)
  showed a completed task cannot be revised and that preview had no path
  without a grant. The event is now a persistent feature record with one task
  per operation, keyed by event, source revision, and operation.
  Preview is a grant-less finite task with a human origin. The isolated
  extraction call moves into foundation milestone 1, which live preview needs.
  The Gmail credential paths are named on the config that owns them, the
  section alarm's, and the task-controls precondition now points at 45e4fd7.
- *Waiting is not authority.* The [third review](reports/2026-09-08-independent-action-third-review.md)
  caught that a child outside the grant was being admitted by parking it in a
  wait. A change the grant does not cover is now an inert proposal on the
  event record; only the owner's approval of that exact proposal creates a
  task, and derive_task keeps its containment check. In-place revision is
  limited to children with no dispatch intent and unchanged scope, with the
  older revision kept as a replay alias; once intent exists the attempt is
  frozen and reconciled before anything new is proposed.

**Probes.** Documentation only; the review reproduction checks static source
contracts and plan links without importing Ciel or reading runtime data.
Referenced commands and local links checked; `git diff --check` run.
No runtime probes or account tests were run.

## 2026-09-08 — Independence has a foundation and a first use

**Why.** Email events are the first useful application of Ciel acting
independently. Permissions, durable work, recovery, and verified reporting need
a shared home so the next feature can use them too.

**What.**

- *Responsibility belongs to the runtime.* The
  [independent-action plan](design/2026-09-08-independent-action-plan.md) owns
  mandates, bounded scheduling, broker-owned grants, journal correlation,
  reconciliation, and private receipts. A synthetic adapter proves its gates.
- *The inbox supplies the first real work.* The
  [email-to-calendar feature plan](design/2026-09-08-email-calendar-plan.md)
  owns mail interpretation and calendar behavior, with explicit dependencies
  on the foundation. Preview can come first; automatic writes wait for all
  foundation gates. PR monitoring remains deferred.

**Probes.** Documentation only; plan links and referenced commands checked,
and `git diff --check` run. No runtime behavior, account access, or service
configuration changed.

## 2026-09-08 — The canceller hears; the old speaker speaks

**Why.** Ciel lisped whenever her voice went through Apple's engine, and the
lisp survived every code fix: the rates were 48 kHz all the way through, a
Python stall no longer cut a word, and synthesis and capture faults were
isolated. A standalone listen (`reports/2026-09-07-split-pair-experiment.py`)
played one Piper sentence through PortAudio alone, through PortAudio while the
helper captured, and inside the engine; only the engine lisped. Apple's far end
treats what the engine plays as a phone call, and nothing in the helper chooses
that.

**What.**

- *The speaker leaves the engine.* With `backend = "apple"`, capture still
  comes from Apple's voice processing, but Ciel's voice plays through the
  PortAudio speaker on the same default output. The echo reference is taken at
  the device rather than at the engine, and the listen found the split route
  left no more of Ciel's voice in processed capture than the engine route did.
  A speaker failure can no longer close the microphone.
- *The engine route is kept, named, and not default.* `audio.apple_playback`
  chooses `portaudio` or `engine`; the latter keeps its receipts and underrun
  accounting so the two can be compared in a room.
- *The one-owner rule is relaxed on purpose.* The pair is still decided in one
  place; what changed is which speaker an Apple microphone may be paired with,
  and why. Both Apple speakers keep the processed-speech gate for barge-in; the
  README now says that on the tested MacBook the detector calls Apple's comfort
  noise speech, so the loudness check is what gates.

**Probes.** `probe_apple_audio.py 73 → 81`: the default pair, the engine route
by choice, an invalid choice refused, the split speaker owning no helper and
sending it nothing, capture without a player node, and the helper reaped on
close. `--live` now plays its tone through both routes. Existing input, spoke,
and turn probes pass. The lisp itself was judged by ear on 2026-09-07 and
2026-09-08, not by a probe.

## 2026-09-07 — The room reports its playback gaps

**Why.** The remaining findings in
`reports/2026-09-07-apple-audio-review.md` needed observable playback failures,
repeatable builds, and an accurate account of the Mac's lifecycle. A working
microphone and a played receipt could not explain why a consonant sounded wrong.

**What.**

- *Starvation leaves a reading.* Explicit utterance boundaries let the helper
  report an empty-queue refill without counting a normal ending or Stop. The
  warning carries a count and lower bound on the gap; capture stays protected.
- *A rebuild keeps its address.* Source and compiler identity govern the cache.
  Locked atomic builds publish one owner-only executable with a stable signing
  identifier, preserve the last working build on failure, and remove only old
  Ciel audio hash-named files. Authorization can be checked without opening the
  microphone. Ad-hoc signing still leaves final permission decisions to macOS.
- *Failures explain the next step.* Missing VAD names the spoke dependency and
  its repair command. The README states that nearby speech can duck other apps
  between turns, minimum is not off, and launchd route recovery includes the
  normal greeting. Echo cancellation stays enabled and the effect stays off.

**Probes.** `probe_apple_audio.py 61 → 73` pins native underrun accounting,
telemetry validation, source-error recovery, compiler changes, stable signing,
cache cleanup, failed-build preservation, and missing VAD. Eight live smoke
checks pass. The live stall reproduction reports one gap for the former window
and none for the current window. Identical generated speech completed through
PortAudio and Apple with processing off/on, with no Apple queue starvation.
`reports/2026-09-07-apple-audio-review-response.md` records every review item and
the remaining listening and macOS permission limitations.

## 2026-09-07 — A short pause in Python need not cut a word

**Why.** Ciel sounded wrong after Apple echo cancellation was enabled. The
investigation in `reports/2026-09-07-apple-playback-investigation.md` reproduced
a 203–214 ms gap when Python stalled for 300 ms. All three measured device
rates were 48 kHz; a low microphone rate was not the cause on this Mac. The
review in `reports/2026-09-07-apple-audio-review.md` also exposed two independent
failure-boundary defects.

**What.**

- *The speaker has time to continue.* Twenty 50 ms buffers replace three.
  The native queue remains bounded, starts playing immediately, and is
  discarded on Stop. The same injected stall now leaves no internal silence.
- *A synthesizer is not a microphone.* Source errors propagate to the caller
  without closing healthy capture. Device transport and receipt failures still
  end the audio session visibly. Capture lock contention now signals the worker
  to report failure instead of silently losing a frame.
- *The speaker names its own rate.* Core Audio supplies the default output
  device's nominal rate independently of capture. The startup handshake checks
  and logs negotiated input, output, and mixer rates. An unconnected output
  node's zero-rate format is not used as the hardware rate.

**Probes.** `probe_apple_audio.py 49 → 61`: the deeper queue, unchanged stop and
receipt behavior, three synthesis-error types and subsequent recovery, device
rates, and compiled capture-contention failure. The live probe passes seven
checks; the separate live reproduction measures a 213.5 ms gap with the former
window and none with the new one. Spoke and turn probes pass 56 and 78 checks.
Echo cancellation remains enabled and the TTS effect remains off. A steady
sibilant change is not diagnosed by this gap test; speech fidelity, snaps,
real-room music rejection, and simultaneous user speech remain unqualified.

## 2026-09-07 — The microphone can leave the speakers out

**Why.** Ciel could hear its own answers or music as the next speaker, then
open another follow-up window after answering that. Discarding buffered audio
could not remove sound still playing in the room. The investigation in
`reports/2026-09-07-echo-handling.md` traced the missing echo reference and the
loudness-only interruption check.

**What.**

- *One engine hears and speaks.* The opt-in Apple backend pairs AVAudioEngine
  voice processing with all of Ciel's playback. A small Swift helper, built
  with the existing system toolchain, returns processed 16 kHz mono capture.
  Both the local loop and the spoke use the pair; the hub stays audio-free.
- *Finished means played.* Bounded scheduling waits for Apple's audible
  receipts, including partial final chunks. Stop drops queued audio, cancels
  stalled synthesis, and keeps old receipts out of the next utterance. Mute
  schedules nothing. Apple barge-in requires speech as well as sustained energy.
- *Failure cannot quietly remove protection.* Denied microphone access, bad
  frames, helper death, and changed routes stop capture instead of falling back
  to raw audio. Processed silence is allowed; missing-frame detection remains.
  PortAudio remains the default while the room's acoustic behavior is checked.
- *The room chooses the settings.* `audio.backend`, `apple_ducking`, and
  `apple_agc` are documented together. Apple uses system default devices and
  macOS 14+; minimum ducking is not zero, and gain control defaults off.

**Probes.** `probe_apple_audio.py 0 → 49` covers native compilation and
44.1/48 kHz conversion, bounded capture and playback, framing, audible receipts,
mute, cancellation, stalled synthesis, late receipts, malformed data, helper
failures, and speech-gated interruption in both loops. Existing input, spoke,
turn, shortcut, and hub-import probes pass (9, 56, 78, 55, and 6 checks), as
do endpointing and the 18 mid-thought cases with isolated configuration.
The separate `--live` mode passes six checks on the Mac, including processed
capture and an audible playback receipt, at both 16 and 22.05 kHz playback.
It also exposed and resolved mismatched native I/O formats: both sides now use
the same mono format instead of inheriting aggregate reference channels.
No microphone audio was saved. The subsequent room trial reported lisp-like
speech, so the previous playback backend was restored. The transport and tone
checks did not establish speech fidelity. Apple audio remains experimental;
speech quality, acoustic rejection, gesture recognition, and user speech over
music are not qualified.

## 2026-09-07 — The owner can see and steer a task

**Why.** A durable responsibility needs a way for its owner to inspect and
change it without pretending execution has started. The stage-two review in
`reports/2026-09-07-task-controls-plan-review.md` also found that SDK tools do
not receive call identity, lane batches lose message identity, and a shared
public session can retain private context.

**What.**

- *A saved request keeps its place.* Private owner tools and Chart share one
  controller for listing, inspection, pause, resume, answers, and cancellation.
  Explicit PR-check requests are validated offline and created atomically in
  resource wait. Resume and answers also keep waiting: there is no runner or
  GitHub observation. Questions preserve their identity, choices, and scope.
- *Authority lasts one admitted turn.* Tools capture immutable owner context
  after all owed results drain. A turn with drain debt has no task authority;
  revocation meets the SQLite transaction before a queued operation can commit.
  The private client stays warm. Public audiences use a separate client with
  public tools and no private context or persisted resume.
- *A retry remembers its messages.* Ordered ingress IDs survive lane batching
  and store reopen. Chart mints an ID in its resend ledger rather than deriving
  identity from its per-tab ack counter. Partially consumed batches ask the
  owner to repeat the new part alone.
- *Both doors see one record.* Chart sends rendered revisions; tools use the
  current record inside the transaction. Private task frames never enter shared
  replay, spoke-seat commands are refused, and remotely bound task hubs require
  their token. The optional journal records applied controls after commit;
  task history remains authoritative if journaling fails.
- *Storage belongs to the runtime.* Pipeline opens and closes the store, and
  cancelled startup releases its worker and ownership lock. Tasks remain
  disabled by default. Schema two refuses older and newer stores without reset;
  future attempt-to-tool bindings are reserved but have no runtime writer yet.

**Probes.** `probe_tasks.py 140 → 166`, `probe_task_tools.py 0 → 24`, and
`probe_task_wire.py 0 → 20` cover the real in-memory SDK dispatcher, stale calls,
interrupt cancellation and commit fencing, repeated drain debt, two Chart views,
conflicts, resource waits, questions, ingress retries, storage failures, and
startup/shutdown. `probe_turns.py 71 → 78`, `probe_ladder.py 17 → 19`,
`probe_hub_arbiter.py 51 → 54`, `probe_discord.py 38 → 39`,
`probe_spoke.py 53 → 56`, `probe_web.py 37 → 40`, and `probe_wire.py 70 → 75`
pin lane identity, session separation, and addressed admission.
`probe_files.py 26 → 30` and `probe_shellguard.py 162 → 165` protect task files.
`probe_hub_imports.py 2 → 6` redirects every fixture path, including memory,
away from the real home. The existing closure, Witness, grants, confirmation
wire, speaker, and shortcut probes pass, as do the hub import and all three
storage-review acceptance scenarios. Chart was checked in Chrome at desktop
and 390-pixel widths, including keyboard focus, long text, literal question
text, evidence, terminal records, empty/error/offline states, and two tabs with
matching ack counters but distinct message IDs. All probe state is temporary;
no task execution, account access, or deployment was performed.

## 2026-09-07 — The controls plan follows the turn

**Why.** The stage-two review found that the installed SDK gives an in-process
tool no tool-use ID to establish authority. Attendance, batched request identity,
and session privacy also needed concrete contracts before implementation.

**What.**

- *Authority belongs to one turn.* The revised
  [plan](design/2026-09-07-task-controls-plan.md) follows
  `reports/2026-09-07-task-controls-plan-review.md`, with immutable
  turn bindings installed after the drain, authority withheld while drain debt
  remains, a separate public client, and minted Chart message IDs. The appended
  revision check keeps the private client warm and adds gates for stale calls,
  cross-tab identity, and retries whose batch membership changes.
- *Saved requests keep waiting.* The plan specifies atomic resource waits,
  current-record controls from tools, rendered revisions from Chart, admission
  on the wire, controller journaling, and the expanded probes and temporary
  fixtures needed to verify them. Runtime behavior remains stage one.

**Probes.** Documentation only: local links and command paths checked against
current files; `git diff --check`. No runtime probe or service check required.

## 2026-09-07 — A task keeps its place

**Why.** Atlas remembers a project's working state, but a remembered plan
cannot distinguish an action never sent from one that happened just before a
crash. Stage one of durable tasks gives the runtime that distinction before
it gains an executor, following the plan and its review in
`reports/2026-09-07-durable-tasks-plan-review.md` and
`reports/2026-09-07-durable-tasks-review-response.md`. The storage review in
`reports/2026-09-07-task-store-review.md` also caught three ways a watch could
stop: a concurrent reader, eight ordinary polls, and a second commit on the PR.
Those findings are addressed before the executor arrives.

**What.**

- *One responsibility, one durable record.* Typed task, scope, criterion,
  step, attempt, observation, history, and notice records live in a private
  SQLite store with one worker thread and one process owner. Task changes,
  evidence, and notification intent commit together. Originating requests
  deduplicate; revisions reject stale callbacks; allowances survive restart.
- *Recovery says what is known.* Prepared attempts and dispatched reads can
  return to the queue. Possibly dispatched mutations wait for reconciliation;
  pause, cancellation, and resume cannot quietly turn uncertainty into a
  second action. Recorded observations resume verification without dispatch.
- *Completion has a gate.* Fresh exact-value evidence for every criterion
  and target revision is required for `done`, which commits with its notice.
  Corrupt, incomplete, foreign, and newer stores are refused without reset;
  cancelled callers and failed writes cannot masquerade as clean rollbacks.
- *A watch can keep watching.* A bounded SQLite lock wait refuses one operation
  without poisoning a certainly rolled-back store. Clean checkpoints leave the
  retry allowance alone; a separate durable polling bound counts every round.
  A trusted read can retarget a changed head, with a revision-checked history
  entry that preserves scope, desired values, and the originating request.
- *A foundation, not a new background agent.* `[tasks]` documents the storage
  bounds. No runtime store, model tools, scheduler, GitHub adapter, delivery,
  or external action is enabled in this stage. Existing guards stay intact.

**Probes.** `probe_tasks.py 0 → 140` (92 → 140 after review): identity and owner
scope, transitions and stale results, evidence and budgets, private storage and competing processes,
schema refusal, real process kills around dispatch/commit, cancellation,
shutdown, and injected storage failures. The review regressions hold read and
write locks, refuse a failed rollback, poll across a reopen for over three hours,
exhaust each allowance independently, and complete a retargeted head while
rejecting stale callbacks and old-head evidence. Repeated recovery now closes
and reopens the store. The review reproduction's `--accept` mode verifies all
three findings against the fix. All state and external effects are fixtures in
temporary directories.

## 2026-09-07 — Ciel asks to hear the keys

**Why.** Enabled shortcuts only logged that Input Monitoring was missing,
leaving the user to find and register Ciel's Python in System Settings.

**What.**

- *The request belongs to the room.* Ciel now makes the native macOS Input
  Monitoring request when enabled shortcuts lack permission. The listener's
  thread handles it, so the audio loop remains available. Existing grants
  are reused; disabled or invalid bindings never prompt. A grant that is
  available immediately starts the listener, while denial or failure leaves
  voice running and the keyboard untouched.

**Probes.** `probe_shortcuts.py 48 → 55`: requesting missing permission,
reusing grants, avoiding repeated requests, no prompts when disabled or
invalid, starting after a grant, and surviving a failed system request.

## 2026-09-07 — The room answers to three keys

**Why.** Talking, interrupting, and muting should remain within reach when
another app has focus and the wake word is not the right way to ask.

**What.**

- *A hand on the conversation.* Configurable global Mac shortcuts open
  listening, stop the current response, and toggle the persisted mute switch.
  Both voice paths use their existing capture, interruption, and confirmation
  machinery. Talk respects mute; Stop denies a pending confirmation, clears
  held words, and prevents late speech from restarting the stopped response.
- *The keyboard stays a keyboard.* An opt-in passive event tap matches only
  physical chords, fires once per press, and sends action names to asyncio.
  No new dependency, character capture, or hub keyboard listener. Invalid
  bindings and missing Input Monitoring permission leave voice available.

**Probes.** `probe_shortcuts.py 0 → 48`: exact chords and repeats, configuration,
passive native handoff and teardown, denied permission, both real voice
controls, mute persistence, stopped timer rings, late sentences, and confirmation
cancellation.

## 2026-09-07 — Barn Door can measure without turning you away

**Why.** The previous attempt both missed the user's voice and admitted
other voices. Calibration alone could not explain the room's failures:
`reports/2026-09-06-barn-door-evaluation.md` showed a gap between its base
threshold and the gate's duration and conversation rules.

**What.**

- *One judgement, two uses.* `[voice] diagnostic = true`, with voice enabled,
  runs the existing gate while every captured utterance continues. Only a
  would-accept embedding earns grace; passing through for measurement does
  not. The same gate serves the local pipeline and the Mac spoke.
- *Numbers that can be compared with what happened.* Owner-only bounded
  JSONL readings record scores, thresholds, reasons, grace, and capture
  measurements. Unavailable results stay explicit, diagnostic failures
  cannot silence a turn, and diagnostic rejections save no audio or change
  enrollment. The ordinary process log receives each reading too.

**Probes.** `probe_speaker.py 0 → 39`: enforcing/diagnostic parity, short and
recent speech, bypass without trust, unavailable measurements, cancellation,
strict JSON, bounded private storage, failed writes, and both real voice
handlers with fake speech and a fake encoder. These are policy checks;
real-room speaker accuracy remains to be measured.

## 2026-09-06 — a playback request is enough

**Why.** Asking to play or pause Spotify already says what the user wants.
The user chose to remove the second yes from these playback requests.

**What.**

- *Music follows the request.* Spotify controls run without confirmation by
  default; `[spotify] confirm_controls = true` can restore the voice gate.
  Inverse records and verifies controls independently of that choice.
  Public and unattended turns still cannot control the player, and other
  actions keep their own confirmation requirements.

**Probes.** `probe_spotify.py 93 → 102`: direct controls pass the brain's
hooks without asking, still reach the journal and read-back, and stay
allowed without a confirmer; another connector still asks and respects
refusal, while Spotify's optional confirmation still works.

## 2026-09-06 — Spotify can follow the room

**Why.** A double clap could start a known URI on the Mac, but Ciel could
not find a recording, read the player, or choose a Spotify Connect device.

**What.**

- *The account behind the speakers.* An opt-in Spotify Web API client and
  four tools bring search, playback status, device discovery and controls
  to either brain host, using the dependencies already here. Browser PKCE
  needs no client secret; owner-only atomic tokens and a shared refresh
  lock keep authorization intact across overlapping processes.
- *Looking is different from changing the room.* Controls go through
  Proof Obligation and Inverse; public turns cannot use the account tools,
  and unattended turns can only inspect playback and devices. Playback
  requests are sent once, with explicit uncertainty after network failure;
  rate limits hold later calls. Library and playlist editing are outside
  this first connection's scopes.

**Probes.** `probe_spotify.py 0 → 93`: PKCE state and refusal, refresh
rotation and concurrent clients, token permissions, API shapes and bounds,
empty playback, rate limiting, quoted names, public refusal, opt-in tools,
confirmation, journaling, the Witness rule and credential guards.
`probe_world.py 121 → 123`: public and private turns set the account scope.

## 2026-09-06 — the Mac's files can be put back

**Why.** A Mac overwrite reached the action journal without its previous
contents. The fourth finding in `reports/2026-09-06-codebase-review.md`
reproduced a record that could explain the write but could not undo it.

**What.**

- *Read where the file lives.* Before the write, the recorder asks the
  spoke for bounded previous contents and saves an owner-only snapshot
  beside the hub's journal. Ordinary Read and mac_write_file perform the
  inverse; missing, oversized, or unreachable originals leave a note.

**Probes.** `probe_tool_rpc.py 32 → 59` (the combined review regressions):
complete snapshots beyond the tool-output limit, their permissions and
read-only carve-out, restoration and its own undo record, empty/missing/
oversized originals, forbidden paths, and an unreachable Mac.

## 2026-09-06 — a canceled command stops working

**Why.** Canceling a Mac tool call erased its coroutine while the process
kept changing files. The third finding in
`reports/2026-09-06-codebase-review.md` reproduced a write after cancel.

**What.**

- *Keep ownership until the shell exits.* Commands run in their own
  process group. Cancellation, either deadline, and executor shutdown
  stop that group and reap the shell, including during process startup.
  The hub forwards cancellation, and shutdown awaits cleanup.

**Probes.** `probe_tool_rpc.py 32 → 59` (the combined review regressions):
real delayed child writes stay absent after explicit cancel, both
deadlines, shutdown, hub cancellation, and cancellation during startup;
a call canceled before starting leaves no ledger entry.

## 2026-09-06 — the signup cookie stays behind the door

**Why.** The signup watcher kept a login credential in an owner-only file,
but the brain ran as that same owner and could read it. The second finding
in `reports/2026-09-06-codebase-review.md` reproduced the missing boundary.

**What.**

- *One credential list for both gates.* `sections-cookie` joins
  `FORBIDDEN_NAMES`; a configured cookie filename joins it when the file
  and shell guards are built, on the hub and the spoke.

**Probes.** `probe_files.py 21 → 26`: direct reads, search, listing, and
the shell refuse both default and configured cookie names.
`probe_shellguard.py 148 → 162` and `probe_tool_rpc.py 32 → 59` include
refusals for the standard cookie through the shell and spoke.

## 2026-09-06 — a quiet prefix is not permission to write

**Why.** `git diff --output` could write outside the workspace without
asking, because the shell gate recognized only its prefix. The first
finding in `reports/2026-09-06-codebase-review.md` reproduced that escape.

**What.**

- *The arguments owe a proof too.* Git status and a small display-only
  vocabulary for log stay quiet. Git's diff/show/branch/blame commands,
  file inspection, and unfamiliar date or hostname arguments ask first.
  A configured prefix cannot bypass the argument check.

**Probes.** `probe_shellguard.py 148 → 162`: mutating Git options and
machine setters ask, the display-only forms stay quiet, and a declined
output write is refused. `probe_tool_rpc.py 32 → 59` includes a real
unconfirmed Git output write whose destination remains absent.

## 2026-09-07 — Ciel can see and change your playlists

**Why.** "Play my playlist called Morning Run" had nowhere to go: the
connector searched the public catalogue and never the user's own
library, and could not make or change a playlist at all. Spotify's
current API still allows all of that for a development-mode app, on the
renamed `/items` endpoints and only for playlists the user owns or
collaborates on.

**What.**

- *Six calls, in the connector's own shape.* `playlists` (a page of the
  user's own), `playlist_named` (one by name, case aside, from the
  library and never the catalogue), `playlist_items`, `playlist_create`
  (private unless asked, so a spoken request never publishes to the
  profile), `playlist_add` and `playlist_remove` (one to a hundred track
  or episode URIs, by body, returning the snapshot id the journal keeps).
  Every argument is bounded before a request is made; someone else's
  playlist is refused in plain words rather than as a credential problem.
- *Scopes, remembered.* The login now asks for the three playlist scopes
  and records what it was granted. A login made before this still works
  for playback and is told, by name, to connect again the moment a
  playlist is touched.
- *Reads and actions, tiered as before.* The three reads join the
  Witness's observers; the three changes are journaled, read back, and
  behind the voice gate only when `confirm_controls` says so, and are
  withheld without a journal like playback control.

**Probes.** `probe_spotify.py 102 → 145`: the browser asks for playlist
scopes; a saved login remembers its scopes and an older one is
playback-only; every playlist call reaches its documented endpoint with
its documented body; bad pages, names, URIs and positions never reach
Spotify; another's playlist is refused plainly; a login without the
scopes is told to connect again and makes no request while playback
still works; the reads are tools, the changes are actions, and the
Witness denies the changes.

## 2026-09-07 — two claps never bring Spotify to the front

**Why.** When the API had no active device the gesture fell back to an
Apple event, and an Apple event to an app that is not running launches
it — in front of whatever the user was doing. A gesture that steals the
screen is worse than no gesture.

**What.**

- *Aim, don't wake.* With no active device the API is asked again, aimed
  at this Mac by its Connect device id (by computer name, else any
  computer), which starts the desktop app's player without touching its
  window. If the app is not among the devices it is launched hidden and
  in the background (`open -g -j`), given eight seconds to appear, and
  then aimed at. The AppleScript surface is now only for a connector
  with no login.
- *Every play gives the screen straight back.* Spotify ignores a hidden,
  background launch and activates itself, so when it has to be launched
  the door remembers what was in front, launches by bundle identifier
  (the bundle on disk is "Spotify (old).app" here, which a launch by
  name cannot find), and hands the front back the moment the process is
  up — a flash, not a switch. An app already running is never activated.

**Probes.** `probe_gestures.py 128 → 134`: no active device aims at this
Mac by id without launching; any computer will do; no device at all
launches hidden, waits, and aims; an app that never appears gives up and
says so; an active device is asked nothing else; every play launches the
app hidden before any door.

## 2026-09-06 — the keyboard's own word

**Why.** Every acoustic gate had been tried against keystrokes — four
rules, a model that names typing, a solitude window — and a lone key in
a quiet second still woke Ciel as a snap. It is a snap, acoustically.
What it is not is a hand: the sound of a key is made by a key, and the
Mac knows when its keys are pressed.

**What.**

- *Ask the keyboard.* `audio/keys.py` asks macOS, at the moment the ear
  judges an impulse, how long since a key went down or up — a session-wide
  number any process may read, timestamps only, no event tap, no
  permission dialog, a tenth of a millisecond. A snap or a clap inside
  `gestures.keyboard_veto_ms` (300) of a key event is rejected as that
  keystroke, by name.
- *Opinions in order.* The ear's vetoes compose: the keyboard first,
  since it is certain and free, then the AudioSet model. `first_of` in
  `audio/gestures.py`.

**Probes.** `probe_gestures.py 117 → 128`: a snap 50 or 299 ms after a
key is that key, 300 ms or a minute after it is not; without Quartz the
veto is inert; with a key just pressed a snap and both claps are
keystrokes and say so; with the keyboard quiet they are gestures again;
the keyboard is asked first and a certain answer spares the model; no
opinions is no veto; zero builds an ear without the keyboard.

## 2026-09-06 — a snap is solitary

**Why.** With the AudioSet veto in place, keystrokes still woke Ciel as
snaps: a single key in a quiet second does not sound like *typing* to a
model trained on runs of it, and by shape a mechanical key is a snap.
What a keystroke has that a snap does not is company.

**What.**

- *One number.* `gestures.snap_quiet_ms` (400): a would-be snap that
  follows any other gated impulse inside the window is rejected as
  typing cadence, whatever its shape or the model's opinion. Claps are
  not held to it, so a double clap is untouched. Zero switches it off.

**Probes.** `probe_gestures.py 110 → 116`: a lone snap stands; a snap
200 ms after a keystroke-shaped tick is typing cadence and says so; at
500 ms it is solitary again; a run of keystrokes never becomes a snap;
claps are not held to it; zero switches it off.

## 2026-09-06 — two claps reach whichever device is playing

**Why.** The double clap could only start the desktop app on this Mac.
The Spotify connector that arrived the same day talks to the account
itself, so the gesture should too: two claps in the kitchen should reach
the phone that is playing there.

**What.**

- *Two doors, one order.* `music.play` takes the connector's Web API as
  the first door — `play` on the active device, then a status read for
  the device's name — and keeps the AppleScript surface as the second,
  taken when the API has no player to talk to or no login to talk with.
  The login is checked at play time, so authorizing after the spoke
  started needs no restart. The same URI check guards both doors.
- *The connector must be authorized on the spoke's host.* The gesture
  runs where the microphone is, so the Mac keeps its own token file; the
  README says so beside the gesture.

**Probes.** `probe_gestures.py 104 → 110`: with the connector, two claps
play through the API and the desktop app is not scripted; without a
player, or before authorization, the desktop app answers; a connector
that is off or has no app leaves the desktop app as the only door; the
spoke builds the pair action with the connector beside it.

## 2026-09-06 — a second opinion that only says no

**Why.** Snaps were, in the user's words, too accurate: keyboard clicks
woke Ciel. Four hand-set numbers cannot tell a keystroke from a snap on
a lid microphone, and neither can more of them. What can is a model
trained on two million labelled clips — but tried as the judge, Google's
AudioSet classifier missed half of a set of real snaps and called a loud
clap a snap, while naming typing and speech every time.

**What.**

- *The rules judge; the model may veto.* `audio/audioset.py` wraps YAMNet
  (a tf2onnx export, 16 MB, Apache 2.0) under the onnxruntime the wake
  word already uses, fetched once into the models directory and refused
  unless its SHA-256 matches. The ear keeps a second of raw audio and,
  for each snap or clap the rules accept, asks whether that second was
  really typing, a keyboard, speech, conversation, or music; above
  `veto_threshold` the gesture becomes a rejection that names what was
  heard. A vetoed second clap breaks the pair. Nothing the rules did not
  find is ever found by the model.
- *Two fields.* `gestures.veto_model` — empty, `yamnet`, or a path to a
  model with the same interface — and `gestures.veto_threshold`.

**Probes.** `probe_gestures.py 90 → 103`: vetoed gestures are rejections
that say why; the veto is asked only about what the rules accepted;
it is shown one AudioSet frame ending just after the impulse; a silent
veto changes nothing; a vetoed second clap breaks the pair; the wrapper
vetoes on room classes only, by name and score, never on snapping or
clicking; a bogus or missing custom model is refused; the pretrained
model is pinned by hash.

## 2026-09-06 — the Chart says whether you snapped or spoke

**Why.** With two ways to wake Ciel, the Chart's chip said only
"listening", and in hub-and-spoke mode it did not even say that: the
room's window belongs to the spoke, and the hub only ever set the chip
from its own turns.

**What.**

- *The detector says how it was addressed.* Every wake detector carries a
  `source` — spoken, hotkey, snap, clap twice — and the composite takes
  the firing member's.
- *The window's source rides `voice.state`.* The spoke sends it while the
  window it opened is open, and nothing for a follow-up window; the hub
  keeps it, lights the Chart with it, and returns the chip to idle when
  the window closes and no turn is running.
- *The chip reads it.* `listening · snap` beside `listening · spoken`;
  the hello carries it for a page opened mid-window.

**Probes.** `probe_gestures.py 88 → 90`: the composite names what
addressed it. `probe_spoke.py 52 → 53`: a snap-opened window says so, a
follow-up says nothing, idle says nothing. `probe_hub_arbiter.py +1`:
the source is kept while open, dropped when closed, reported each time.
`probe_web.py +2`: one state frame per change with the source, idle
without, and the hello holds it.

## 2026-09-06 — the ear can narrate what it hears

**Why.** The spoke logged only the gestures it acted on. A clap that was
heard but fell short of a boundary left no trace, so tuning meant
running the tester in a second window and matching its clock to the
spoke's by eye.

**What.**

- *One switch, every impulse.* `gestures.log_candidates`, off by
  default, makes the ear log each gated impulse with its four numbers
  and, for a miss, the cue it failed — the tester's candidate rows, in
  the spoke's own log, in the spoke's own time.

**Probes.** `probe_gestures.py 83 → 88`: silent by default; with the
switch on every gated impulse is a line with its numbers, a miss names
its cue, a lone clap says it is waiting, and the switch reaches the ear
the spoke builds from config.

## 2026-09-06 — the log says which microphone, and when it hears only zeros

**Why.** The spoke sat "ready" all evening and heard nothing — not a
snap, not its name — while a tester opened in a terminal heard every
sound. Nothing in the log could say why: the microphone's name was a
debug line, and the stall watchdog only notices frames that stop
arriving. Frames of pure zeros, which is what macOS delivers to a
process it has not allowed the microphone or from a device with nothing
behind it, looked exactly like a quiet room.

**What.**

- *The microphone is named at INFO.* `microphone open: <name>` on every
  start, with `(default)` when it was the system's choice, because the
  default shuffles when a phone or a headset appears.
- *Pure silence is reported once.* `SilenceWatch` in `audio/input.py`
  counts frames whose every sample is zero and says so after five
  seconds, naming both causes, then says when the room is back. A real
  room never produces exact zeros, so a quiet night does not trip it.

**Probes.** `probe_input.py` new, 9 checks: hiss is not silence, zeros
speak once and only after the window, the message names the permission,
the room's return is reported, a second stretch is reported again, the
window is counted in frames.

## 2026-09-06 — two claps can start the music

**Why.** With the gesture ear in place, the first thing the user wanted
two claps to do was not to wake Ciel but to start Bruno Mars. There was
no way for Ciel to start music at all: the shell guard denies
`osascript` outright, and rightly, since scripting other applications
erases every boundary the tool configs draw.

**What.**

- *A gesture can act instead of waking.* `GestureWake` now carries
  actions beside its wake set: a gesture with an action runs it (scheduled
  on the loop, never awaited by the frame loop) and answers no, so the
  listening window stays shut. `wake.double_clap` is a three-way switch —
  `off`, `wake`, `play` — and `wake.double_clap_plays` names what plays.
- *One narrow door.* `music.py` starts Spotify playing a URI through its
  AppleScript surface and nothing else: the URI must match the exact shape
  "Copy Spotify URI" produces, is refused at build time otherwise, and
  reaches the script as an argument, never spliced in. What played is
  logged. It does not go through the confirmation broker, on purpose: two
  claps are the deliberate act, mute gates them, and a wrong song is undone
  with one tap. The journal lives with the brain, so the log line is the
  record here.

**Probes.** `probe_gestures.py 71 → 83`: in play mode two claps run the
action once and never wake, the snap still wakes beside them, the ready
line says what two claps do, the build refuses anything but a Spotify URI,
the URI reaches AppleScript as an argument, a bad URI never starts a
process, and Spotify's refusal is reported rather than raised.

## 2026-09-06 — a snap or two claps can stand in for the name

**Why.** The wake phrase was the only way to get Ciel's attention without
a keyboard, and there are moments when a phrase is the wrong instrument:
a room where speaking feels odd, a mouth full, a doorway too far for a
whisper. An afternoon at the microphone showed that a finger snap is
reliably detectable on a laptop's lid microphone at the pipeline's
16 kHz, that a clap is too, and that a single clap is indistinguishable
from a knuckle on the desk — so the second gesture is a pair.

**What.**

- *An ear beside the wake word.* `audio/gestures.py` measures every
  impulse that clears an onset gate four ways — peak, width, tilt, fall —
  and sorts it into snap, clap, or neither, in audio time, with filter
  state carried across frames. `GestureWake` wraps it in the detector
  shape and `AnyWake` seats it beside the phrase (or the hotkey), so the
  frame loop, the acknowledgement, and the listening window cannot tell
  a snap from "hey jarvis". The wake-word model still sees every frame,
  and a turn's reset forgets a half-made pair without restarting the
  warm-up.
- *Two switches and a table.* `wake.snap` and `wake.double_clap`, both
  off by default; every boundary the ear uses is a documented field in
  `[gestures]`, read off one MacBook on 2026-09-06 and expected to move
  in another room. The ready line names what is switched on.
- *The tester hears with Ciel's ear.* `scripts/listen_gestures.py` now
  imports the detector and thresholds from the package rather than
  carrying its own copy, replays recordings, and turns every `[gestures]`
  field into a flag so a boundary can be tried before it is written down.

**Probes.** `probe_gestures.py 52 → 71`: a snap and a double clap wake
through the ear and a lone clap never does; each switch admits only its
own gesture; a pair fires once; reset forgets a pair without going deaf;
the composite feeds every member every frame and keeps the hotkey's
`arm`; `always` mode builds no ear; the ready line names the gestures in
order; the `[gestures]` table loads from TOML and the environment.
`probe_spoke.py` and `probe_audio.py vad` unchanged and passing.

## 2026-09-06 — two hands, one set of conventions

**Why.** Two agents wrote to the same checkout and could undo one another's
work even while following the same code style.

**What.**

- *One writer, a separate reviewer.* Each repository's guide now contains the
  five coordination rules, so a fresh clone needs no sibling policy file.
  Local conventions govern style; Google is an optional reference.
- *The Instrument keeps its shape.* Ciel's guide names the token owner,
  offline constraint, and checks for interface changes. Documentation-only
  work has its own verification path.

**Probes.** No runtime behavior changed. Guide links, Claude entry points,
original Claude notes, and deployment flag descriptions were checked;
whitespace checks passed in all three repositories.

## 2026-09-06 — one name everywhere, and a guide any hand can follow

**Why.** The code has been Ciel for weeks; everything around it still
said jarvis — the Mac checkout, the GitHub remote, the server's home
directory. Codex moved the Mac checkout to `~/Projects/ciel` and split
the service definitions into a sibling `infrastructure` repository, but
two things came loose in the move: its forwarding script previewed
instead of deploying, so the old `push_hub.sh --sync` did nothing; and
its launchers no longer created the log directory launchd must open
before the program runs. And with two agents now editing one tree, the
conventions that lived only in the code's own example needed a page.

**What.**

- *The remote is `iiChoco/ciel`; the server runs `/home/ciel/ciel`.*
  GitHub redirects the old name. On the box the unit is patched, the
  venv reinstalled at its new path (a plain sync saw the old path still
  resolving through the symlink and left the editable install pointing
  at it), and `~/jarvis` stays as a symlink for a stale push. The three
  "jarvis" values — the pretrained wake model, the persona, the speaker
  effect — are features and keep their names.
- *`push_hub.sh` deploys again.* It forwards to the infrastructure
  repository's deploy script and deploys by default; `--sync` re-syncs
  the server's locked dependencies, `--dry-run` compares, `--preview`
  prints. The launchers (in `infrastructure/services/launchd`) run
  `sh -c 'mkdir -p ~/.ciel/log && exec .venv/bin/python -m ciel …'`.
- *`AGENTS.md`, read by Codex and imported by `CLAUDE.md`.* How the
  project is actually built: probes as tests and the check-sentence,
  the module-docstring essay, commit and changelog shape, the codenames,
  what is never touched, and what "done" means.

**Probes.** None changed. `probe_spoke` 52, `probe_world` 121,
`probe_turns` 71 re-run after the move and pass.

## 2026-09-05 — the screenshot that took the hub down

**Why.** "Take a look at my screen and put these things on the calendar"
ended the hub twice in a row. Three faults in one line of the journal:
the screen tool's two displays came back as one tool result the CLI
echoed as a single JSON line past the Agent SDK's one-megabyte buffer
(`JSON message exceeded maximum buffer size`); the SDK's reader gave up
with a bare `Exception` the brain did not recognise as a transport death,
so the dead client stayed installed; and the apology after the failed
turn hit `contextlib.suppress` in a module that only imported
`aclosing` — a NameError inside the failure handling, which the hub loop
re-raised from the turn task and died of. systemd brought the hub back
with no memory of the conversation ("this is the start of our
conversation, sir").

**What.**

- *The line buffer fits a screen.* `ClaudeAgentOptions(max_buffer_size=
  64 MiB)` for the brain: a bound on memory per line, not a target.
  Screenshots are re-encoded at a fixed JPEG quality (75) so the payload
  has a known size rather than sips' unstated default.
- *A dead reader is a dead client.* `Brain.ask` reads the SDK stream by
  hand; an exception raised by the stream itself (never by the turn's
  own body) becomes `StreamDied`, a `ConnectionError`, and the eviction
  that follows a transport death follows it — the next turn reconnects
  instead of ending at once with nothing said.
- *One turn's crash is one turn's crash.* `import contextlib` in
  `pipeline.py`; and both loops surface a finished turn task's exception
  through `_turn_crashed()` — logged, marked on the indicator, recorded
  as an event — rather than re-raising it out of the loop.

**Probes.** `probe_turns.py` 63 → 71: the wire apology on a dead spoke,
a crashed turn task as an event, and the stream dying under a turn (what
was heard is kept, the client is evicted, the lock is released, the
buffer is raised).

## 2026-09-05 — the follow-up review's five (ownership, seats, the deadline)

**Why.** The follow-up review (`reports/2026-09-05-followup-review.md`)
found five defects left standing after the review's six: the room still
tied a session to a *username*, so a deleted-and-recreated name inherited
the old person's interviews; a password change or a disable retired the
cookie but not the socket already open under it, which went on
interviewing and recording; the new search ran the model's regex on the
assistant's own event loop, where one backtracking pattern held every
socket and timer; a drain that timed out simply forgot its debt, so the
late result answered the next question anyway; and a slot reserved
before midnight was released against the new day's ledger. Each was
reproduced with temporary data (`reports/2026-09-05-review/`); all five
are fixed here, and the repro scripts now print the fixed values.

**What.**

- *A session belongs to the account.* `meta.json` carries an
  `owner_id`, the account's immutable id; every read that answers a
  request — the lobby, a session, its recording, the socket — checks
  it, and a session under another id in the same directory is a 404.
  Sessions from before ids existed are claimed once at startup by the
  account holding the username then. Deleting an account (panel or
  `ciel interview delete`) moves its directory to
  `users/.retired/<username>.<id>`, ledger included, so the next holder
  of the name starts empty.
- *The socket goes with the sign-in.* Every attached connection is a
  `Seat`; a newer tab supersedes the older (closed, 4409); the end of
  the interview closes what is left after the debrief is announced; an
  eviction — a self-service password change now included, beside admin
  reset, disable, and delete — closes them at once with 4401, before the
  debrief is written, and the page does not retry on either code. An
  ended interview drops every later frame, audio included. A reset made
  at the terminal, which no eviction sees, is caught by the socket
  handler re-checking the cookie against the file every fifteen
  seconds. The close is done by the socket's own handler task, asked
  through the seat: aiohttp closes a socket from any other task by
  dropping the transport the moment the close frame is written, and the
  browser then saw 1006 as often as the code.
- *The search has a deadline.* `search_files` and `find_files` run
  their walk and matching in a spawned child with the guard handed
  over; the parent waits off the loop and kills a child still running
  at twenty seconds. A coroutine timeout cannot interrupt a match and a
  thread holds the GIL; a process can be killed. `^(a+)+$` over thirty
  characters now costs one second and a message, not the room.
- *A failed drain is the end of the connection.* The in-flight debt is
  cleared only by reading the result. A drain that times out or finds
  the stream broken retires the client (bounded disconnect) and every
  later `ask()` fails with the reason; the room's one retry fails the
  same way and the interview ends with an `interviewer` error, debriefed
  from what was said — never answered by the stale result.
- *A reservation names its day.* `SessionStore.reserve` returns a
  `Reservation(username, day, kind)` and `release` takes it back
  against that day; one taken before midnight and released after
  touches nothing in the new day.

**Probes.** `probe_interview.py auth` 70 → 85 (ownership through the
API, the claim at startup, the retired directory), `brief` 50 → 63
(reservations across midnight, ownership and retirement in the store),
`session` 28 → 44 (a superseded tab, a self-change, a disable, and a
terminal reset, each over a real loopback socket, with the code the
browser sees), `brain` 12 → 19 (the drain timing out, the stream
breaking mid-drain, the stale result never read), `probe_files.py` 17 →
21 (the runaway pattern killed at the deadline while a heartbeat keeps
ticking, no child left behind). The repro scripts read: old transcript
accessible False; socket closed True, audio appended False; midnight
frees the current day False; connection retired True.

## 2026-09-05 — the interview that ended on its own (the cut-off race)

**Why.** Two interviews on the hub ended in `error` three and four
questions in, each right after the candidate went on talking while the
interviewer was still thinking. The room cancels the interviewer's turn
and sends the SDK an interrupt — but the CLI still posts a ResultMessage
for the aborted turn (an `error_during_execution` one carrying its
`[ede_diagnostic]` line when no word had been said yet), and the reader
that would have taken it was the cancelled turn. So it sat in the
stream, and the next question's `ask()` read it as its own reply: an
error result ended the interview; a stale text (a cut-off mid-sentence)
would have had the interviewer answer the previous question.

**What.**

- *The interrupt reads out the aborted turn.* `AgentSdkBackend` keeps
  an in-flight flag per query; `interrupt()` drains the stream to the
  pending ResultMessage (bounded at fifteen seconds), and `ask()` does
  the same before a query if a debt is still owed. A broken stream
  surfaces as `BackendError` rather than a crash.
- *One bad turn is not the end.* A turn that fails before it has said a
  word is asked once more; a second failure ends the interview as
  before. `_speak_turn` closes the `ask()` generator on the way out
  (`aclosing`), so a cancellation landing in `_say` cannot leave the
  backend's lock held until the collector gets to it.
- *The page says why.* An `interviewer`/`crash` error frame is no longer
  a `console.warn`: the closing view says the interviewer hit an error
  and the debrief covers what was said; the review header shows
  `end_reason` (now in the session row) for `error` and `idle`.
- *A probe for the race.* `probe_interview.py brain` drives the real
  `AgentSdkBackend` against a fake CLI that posts the aborted turn's
  result the way the real one does (cut-off while thinking, cut-off
  mid-sentence), and runs a whole scripted interview whose second turn
  fails once. 12 checks; the same race was reproduced against the real
  CLI on Haiku before and after.

## 2026-09-04 — the review's six (boundaries, reconciliation, mute)

**Why.** A focused review of the working checkout
(`reports/2026-09-04-codebase-review.md`) found six defects where the
protocol, policy, and hardware pieces meet, each reproduced with
temporary data (`reports/2026-09-04-review-repros.py`). All six are
fixed here, plus the deterministic-test defect it found in
`probe_world.py`.

**What.**

- *The search boundary.* `Grep` and `Glob` took a root the guard
  approved and then walked it themselves — into `credentials.json`,
  through a symlink out, into `.ssh` — and their output carried the
  contents before any `Read` could be refused. Both built-ins are now
  refused outright (hook and `disallowed_tools`, path or no path) and
  replaced by Ciel's own `search_files` / `find_files`
  (`brain/tools/files.py`, codename **Sieve**): the same
  `WorkspaceGuard`, applied to every directory entered and every file
  opened, symlinks resolved first, no model-supplied exclusions.
  `WorkspaceGuard.from_config` so the hook and the sieve are built the
  same way; the Witness lists the two as observers; the prompt names
  them.
- *Account writes are transactional.* Every mutation of
  `interview-accounts.json` holds a lock across the whole
  read-modify-write — a `threading.Lock` for the room's worker threads
  and an `flock` on `<file>.lock` for the CLI — with scrypt computed
  outside it and the account reloaded inside it. A reset that paused
  in scrypt while an admin disabled the account no longer re-enables
  it. `atomic_write` (shared by every file-backed store) now uses a
  unique temp file per call and keeps the target's mode, so the
  owner-only files stay owner-only and two writers cannot replace each
  other's half-written temp.
- *Cookies name the account, not the username.* Each account carries
  an immutable `id` minted at creation and an `auth` generation that
  every reset bumps; the cookie is
  `username|id|auth|expiry|hmac` and is refused unless both still
  match the live account. A reset logs the old cookies out; a username
  deleted and recreated is a stranger to them; a friend changing their
  own password is handed a fresh cookie in the same response and keeps
  their seat. Accounts from before ids existed are upgraded once, under
  the lock. Admin reset, disable, and delete also end the user's live
  interviews (`pause`, with the reason). Every existing cookie is
  three-part and expires: everyone signs in again once.
- *A seated spoke gets the timer set.* `note_timers` dedupes against the
  last set *sent*, so a change broadcast to an empty seat was recorded
  as delivered and the next spoke never heard it. `_welcome` now sends
  the authoritative set privately (unstamped: state, not an event) to
  every spoke it seats, whatever the resume claim did — new timers and
  cancellations made during a disconnect both arrive.
- *The mirror holds its tongue while muted.* The offline fallback's poll
  never looked at the switch, and `_ring_locally` played regardless.
  Both check now — the poll before scheduling, the ring at the moment
  of playback and between timers. A due timer in a muted room is held,
  not marked rung, and rings on the first poll after the unmute.
- *The daily cap is a ledger.* A session's slot is reserved before the
  brief is generated (`users/<u>/usage.json`, under the same lock
  helper), so two requests in the same instant get one slot between
  them; deleting a session keeps its charge; a failed or cancelled
  generation hands the slot back. Regeneration has a budget of its own
  (`[interview] daily_regenerations_per_user`, default 8). `me`
  reports `used_today`. The dev room is no longer exempt from the cap —
  `probe_interview.py brief` always expected the 429.
- *One clock.* `Pipeline._presence_now` judged freshness on the wall
  clock while the world's facts were stamped by the table's own
  (injectable) clock; `World.now()` is the one it uses now, and the
  probe ages a reading by advancing that clock.

**Probes.** New `probe_files.py` (18: the sieve over a fixture with a
fake secret, a forbidden subtree, a symlink out, the walkers refused).
`probe_interview.py auth` 57 → 70 (the race, ids, retired cookies, the
recreated username, the fresh cookie on a self-change), `brief` 47 → 50
(two at once, a deleted session's slot, the regeneration budget).
`probe_hub_arbiter.py` 48 → 50, `probe_spoke.py` 50 → 52,
`probe_vigil.py` observers updated, `probe_world.py` passes again (121).
The repro script now reads False / [200, 429] / snapshot present /
nothing played.

## 2026-09-04 — speak back (typed replies, spoken)

**Why.** The new voice can only be judged by ear, and the only lane that
speaks is the one that needs the user to talk — useless in a lecture
hall. The Chart's turns were text by design (the page is the delivery),
with no way to ask for the room as well.

**What.** A speak-back switch: `speakback.set` from a Chart, `speakback`
broadcast to every client (and in the hello), the **VOICE** chip on the
page, and the typed/spoken command "speak back on|off" (also "voice",
"talk back", "read back", "speak your replies"). On, a web-lane turn
runs through `_SpokenTextSink`: the transcript tap still feeds the page
and each reply sentence is spoken — on the hub as a `turn.begin` with
`lane: "web"`, sentences, `turn.end` down the wire; locally through the
player. The spoke accepts web-lane turns as playback only: no ack
filler, no BUSY, no follow-up window, the room held (`_delivering`) for
each sentence so the wake word does not hear the voice. Muted wins; no
spoke or no player means text alone; a sentence that will not play
silences the rest of that reply without cutting the text. `[web]
speak_back` (default off) is the starting state; the switch is not
persisted.

**Probes.** `probe_turns.py` 51 → 63 (the sink, the command both ways,
muted, no player, a stopped sentence, the hub's framing); `probe_spoke.py`
44 → 50 (a web-lane turn end to end); `probe_wire.py` two samples;
`probe_web.py --live` round-trips the chip.

## 2026-09-04 — Apple's voice, streamed (native TTS)

**Why.** Piper became the default by beating `say` on first-audio latency
and on sound — but `say` had only ever been heard with a compact voice.
macOS ships neural Premium voices (free, ~1 GB, under Accessibility →
Spoken Content) that `say -o` renders no faster than the compact ones,
because it writes a whole file before the first byte. Driven through
AVSpeechSynthesizer's buffer callback instead, the same voices stream.

**What.** `tts/native.py` and `tts/native/CielVoice.swift`: a small Swift
helper, compiled once with `swiftc` (command-line tools suffice), cached
under `~/.ciel/bin` by source hash, kept alive as a subprocess and driven
over pipes — JSON lines in, framed PCM out, with begin/pcm/end/error
frames per utterance and a cancel op for barge-in that drains to the
utterance's end so the next sentence starts in sync. A voice is named
(the best quality installed under that name wins) or given by identifier;
`rate` in words per minute paces it like `say`. `engine = "native"`,
`native_voice = "Jamie"`; the chain is now native → piper → `say`, and both
warm-up fallbacks (pipeline and spoke) walk it rather than jumping to
`say`. The hub and the interview room keep piper.

**Measured** (`probe_native_voice.py --ab`, Jamie Premium vs
`en_GB-alan-medium`, M-series Mac, warm): first audio 25–49 ms vs 41–100
ms; a 5.5 s sentence in 117 ms vs 155 ms; the cold first utterance ~480 ms
(the voice loading), paid once at warm-up. Sound is the user's call; the
four A/B pairs are the probe's output.

**Probes.** `probe_native_voice.py` (13): the build and its hash reuse,
the voice and rate handshake, chunking and first-chunk latency, a barge-in
followed by a clean sentence, the rate mapping, a missing voice as one
clear error with the installed voices named. The spoke was switched to
`native` and came back ready with Jamie.

## 2026-09-04 — the spine under the point (Phase Space, second pass)

**Why.** The first Phase Space was a latest-readings cache: `observe`
replaced whatever was there, so a reading from the past could overwrite
one from the present; a spoke could name any fact, including the hub's
own; the ring's activity-only fetch erased the morning's readiness; a
calendar read that failed became "nothing more today"; the block —
place, presence, calendar, the ring — opened every turn, public
channels included, with meeting titles written by whoever sent the
invite sitting inside a block the prompt called the system's own; the
file was rewritten every second and world-readable; and a write that
failed was never retried. A review at `b6c3298` found all of it. This
is the fix, and the spine the review asked for.

**What.**

- *Observations per source, reducers, ordering.* The table keeps the
  latest observation from every source that has reported a name and a
  reducer per name resolves the fact: newest wins by default (a tie
  keeps the incumbent), the ring merges the day's numbers across reads,
  an observation older than the one held from its source is refused,
  and a candidate a day older than the fact is let go. `revision` —
  changes only, persisted — and `observations(name)` are exposed.
- *The door.* `RELAYED` names what a spoke may send up (place, sections,
  the ring); the hub refuses a `fact` frame for anything else, stamps
  `received_at`, and clamps an `observed_at` that runs ahead of it.
- *Projections.* `render(public=…)` / `snapshot(public=…)` leave the
  `PRIVATE` readings (presence, place, agenda, the ring) out of a public
  channel's turn; `world_now` is scoped per turn. Outside strings
  (`EXTERNAL`: agenda lines, section ids) render in “quotes” and the
  block's rule says quotes mean reported, not instructed; the static
  prompt section says the same and that what a public turn leaves out
  is not to be repeated there from memory.
- *Sources apart from facts.* `agenda_today()` answers None on a failed
  read (EventKit, Google, the RPC, the executor); the refresher marks
  the calendar source failed and leaves the reading standing, with a
  ttl of two refreshes; `note_source`/`sources()` ride the `world` frame
  and the file, and the Chart dims the NEXT chip and says why.
- *The file and the history.* `world.json` is owner-only, written on a
  change and at most every five minutes for a steady re-observation
  (was: every second), carries the revision and the observations, and
  stays dirty when a write fails. `world-history.jsonl` (owner-only,
  bounded by `history_max_bytes`) gets one line per change.
- *Vigil on the table.* Presence is observed once a second locally
  (10 s ttl) and per heartbeat on the hub; the policy decides on the
  table's resolved presence when fresh, the probe otherwise — the one
  place a second device's reading will ever be folded in.
- *The user's clock.* A top-level `timezone` sets the process zone at
  startup, and the hub's unit sets `TZ`; the Azure box is UTC, and
  every clock the hub spoke — the block, alarms, the brief's quiet
  hours — was the box's.

**Probes.** `probe_world.py` 72 → 121: ordering, the ring's reducer,
projections and quoting, the file's mode and write cadence and failed
write, the history and its bound, source health, the hub's ownership
and clamp, a public Discord turn's block and tool scope, Vigil's
presence, the calendar's failures. Every other probe unchanged and
green (`probe_interview.py brief`'s "fifth session → 429" fails on the
previous commit too). Owed: revision-named preconditions on actions
(nothing consumes `revision` yet); the deployed hub needs the unit's
`TZ` (daemon-reload + restart) and `timezone` in its config.

## 2026-09-04 — one point that says where everything is (Phase Space)

**Why.** What Ciel knew about the world was scattered by producer, each
reading owned by its one consumer: presence by the interruption policy,
the place by the location tool, the ring's numbers by nobody once the
nudge was filed, the mute switch by the pipeline, the timers by their
service. The brain saw none of it unless it called a tool, and the only
world facts glued onto a turn were the lane notes' fixed strings — it
never knew the time of day. "What time is it", "am I at home", "how
long is left on the timer", "is anyone around" each cost a tool call or
a guess, and nothing could say *how old* an answer was.

**What.** `world.py`, codename **Phase Space**: one table of facts, each
a value with who reported it, when it was observed, and how long it
stays trustworthy.

- *The table.* `World.observe` records a reading; an unchanged
  re-observation is not a change but refreshes the age; a fact past its
  `ttl_s` is kept and rendered "last known", never dropped. Thread-safe,
  no asyncio: producers on worker threads (the locator) write under a
  lock, and the pipeline polls `version` once a second — the agent
  roster's rule — to flush `~/.ciel/world.json` and hand the Chart the
  snapshot. The file carries facts across the autoreloader's re-execs
  at their true age.
- *The opening block.* Every user turn and every unattended turn now
  opens "(Now — It is 3:42 PM on Friday 4 September 2026 ...)": the
  Mac's seat (hub), presence, place, mute and hold when set, timers and
  watches, the rest of today's calendar, the ring — one sentence each,
  in a fixed room-first order, with ages in the runtime's words ("4
  minutes ago", "as of 11:42 AM", "last known, at 3:42 PM — no newer
  reading"). Lane note first (where the reply lands), then the block
  (what is true), then the held notes, then the words; the transcript
  keeps only the words. A static prompt section says how to read it;
  the `world_now` tool re-reads it mid-turn. `[world] in_prompt = false`
  keeps the table and drops the block.
- *The producers.* The pipeline writes mute, hold, the spoke's seat,
  timers and watches (once a second), and presence (per turn locally,
  per heartbeat on the hub, with `presence_stale_s` as the ttl). The
  locator writes the place on every fix, whichever path read it. The
  ring watcher and the `oura_summary` tool write the day's numbers
  (`today_readings`, only the fields fetched). The sections watcher
  writes the open-spot counts with a four-poll ttl. A refresher reads
  today's remaining calendar every `agenda_refresh_s` (15 min), kicked
  on a spoke connect and a wake from sleep, and skipped on the hub
  while the Mac is away.
- *The wire.* Two frames: `fact` (spoke → hub, last-value, no ack) and
  `world` (hub → every client, whole table, on the replay ring); the
  hello carries the table. The spoke's `WorldRelay` has the table's
  `observe` signature, keeps one frame per name, is flushed from the
  frame loop (never a producer's thread), and resends the set after a
  reconnect. The hub stamps a fact's source with the node
  (`sections@mac`).
- *The Chart.* A readings strip under the header: MAC linked/away, YOU
  here/idle/locked, PLACE, TIMERS, WATCHES, NEXT, RING, SECTIONS, VIGIL
  held — each chip with its age, gold when it wants attention, dimmed
  once stale, redrawn every half minute. `probe_web.py --live` plants a
  sample table; typing "world" toggles the Mac away and a section open.

**Probes.** `probe_world.py` (72): versions and the notify clock,
freshness and "last known", every renderer's wording, the file mirror
across a restart and against a bad file, the relay's flush and resend,
the hub's door and its source stamp, and the turn's composition order.
`probe_wire.py` grows two samples. The pipelines the other probes build
by `__new__` run with no table (class defaults), so `probe_turns`'
prompt contract is untouched. Owed: a live look at the block from a
real turn, and the strip on the deployed hub.

## 2026-09-03 — the interview room (Adjoint)

**Why.** Ciel is one person's assistant, and a mock interviewer is a thing
friends ask for. The same hub can hold both only if the second surface
shares nothing with the first: not the Chart's queue, not the hub token,
not the brain with the owner's memory and Mac. And a mock interviewer has
one job Ciel's own pipeline gets wrong on purpose — waiting. A candidate
thinks between sentences; half a second of silence is not an ending.

**What.** A new package, `ciel/interview/`, served at `/interview` on the
hub's existing aiohttp application (`WebLink.start()` mounts it when
`[interview] enabled = true`).

- *Accounts* (`accounts.py`): the owner creates every account — from an
  admin panel on the page or `ciel interview add-user` — and passwords
  are generated, shown once, and stored as scrypt hashes in
  `interview-accounts.json`, which joins `interview.secret` on the
  personal brain's forbidden list. Logins are signed cookies scoped to
  `/interview`; five failures in fifteen minutes is a 429. Passwords can
  also be chosen (eight characters or more) — by the owner at creation or
  reset, and by any signed-in user for themselves, proving the old one.
- *Three modes.* A **company** interview invents a fictional company and
  role from the candidate's request and shows a brief with likely
  questions; a **case** interview runs a consulting case from a file
  library (three ship in `interview/cases/`, `ciel interview seed-cases`
  asks the model for more) or generates one; a **technical** interview
  poses a coding problem beside an editor (Python, C, C++, Java, Rust,
  JavaScript, TypeScript, Go) — the interviewer *reads* the code, nothing
  is executed. Briefs and debriefs are structured calls against JSON
  schemas (`prompt.py`); the interviewer's speech is a conversation.
- *The interviewer's mind* (`brain.py`): one Claude Agent SDK session
  per interview, no tools, `setting_sources=[]`, a per-session budget,
  behind a `Backend` protocol so an API-key backend is one class away.
  `ScriptedBackend` runs the whole room with no model, for the dev
  server and the probes.
- *Voice.* The candidate speaks through the browser's speech recogniser;
  the interviewer speaks through piper on the hub, one WAV per sentence
  inside the `say` frame (`speaker.py`), falling back to the browser's
  own voice when piper cannot load. The page mixes the microphone and the
  interviewer's audio into one webm recording, uploaded in chunks over
  the same socket.
- *Patience* (`endpoint.py`, `session.py`): the hub owns the clock. Two
  seconds of silence, read with `trails_off` — lifted from the pipeline
  into `ciel/endpointing.py` so both rooms judge a pause the same way —
  and extended once by three more when the words trail off. When the
  room misjudges and the candidate goes on while the interviewer is
  answering, the turn is cancelled, the interviewer says "Sorry, go on",
  and the continuation is merged onto the answer. The interviewer
  reaches the page through two directives on lines of their own,
  `[[exhibit: e1]]` and `[[problem: p1]]`, lifted out of the speech
  stream before the sentence splitter (now `brain/sentences.py`, shared
  with Ciel's own brain) sees them.
- *The record* (`store.py`): one directory per session under
  `<interview dir>/users/<name>/sessions/` — brief, transcript, code,
  recording, and a debrief written after the interview (a case debrief
  compares the recommendation with what really happened). The page
  replays a recording with a click-to-seek transcript.
- *The room and the reload.* A pending source reload waits for live
  interviews and, past `reload_max_wait_s`, pauses them gracefully
  (debrief and all) rather than vanishing mid-question.
- *The page* (`remote/interview.html`, `remote/interview.js`): a stub
  page carrying the DOM contract — ids, data attributes, templates — and
  one script that owns all behaviour. The designed page from Claude
  Design replaces the markup and keeps the hooks.

**Probes.** `probe_interview.py` with `auth` (49), `brief` (47),
`endpoint` (17), `wire` (11), `session` (28, a whole scripted interview
over the socket including a cut-off and a reconnect), `speaker` (4),
`cases` (14). Live: the dev server (`ciel interview serve --dev`, the
`interview-dev` preview) walked through a case and a technical interview
in the browser with piper audio; the designed page walked through every
view at desktop and phone widths. Deployed 2026-09-04: the Cloudflare
Access bypass on `/interview` (the Chart stays behind the owner's PIN),
piper on the arm64 hub, `[interview] enabled = true`. Owed: a spoken
interview from a phone.

## 2026-09-03 — a flapping seat is one line on the Chart

**Why.** Every time the spoke takes or leaves the seat the hub records an
event row, and a spoke that reconnects in a loop wrote "spoke connected"
/ "spoke disconnected" down the whole page.

**What.** `chart.html`'s `addRow` stacks an event onto the previous event
row when the two say the same thing, or when both are seat notices: the
row reads the latest text with the run's count — "spoke disconnected
(8×)". Same rule for a repeated "turn failed". The history replayed on a
fresh hello goes through the same door, so a reload shows the stack, not
the spam. Browser-checked against the echo server.

## 2026-09-03 — the pill wears the Chart's chip

**Why.** Two status lights, two looks: the HUD in the corner was a rounded
grey pill with a coloured dot, the Chart's header a cut-corner chip in the
"Instrument" palette. They report the same six states and should read as
one instrument.

**What.** `ui/hud.py` redrawn in Core Animation from the page's CSS: the
cut-corner polygon (a `CAShapeLayer` mask for the fill, a second stroking
the same path for the border), the near-black ground tinted with the state
colour, the six-point dot with a soft glow that breathes on the page's
timings (3.4 s, 1.6 s while speaking), and a semibold monospace label in
capitals at .2em tracking, in the page's hand-picked text colour per state.
The chip sizes itself to its label and re-anchors to its corner; the window
shadow is gone, the border is the edge. Colours, cuts, and timings are
copied from `chart.html`, not approximated. Two PyObjC notes for next time:
a method that takes an argument and has no trailing underscore is taken
for a selector unless marked `@objc.python_method`; and an attributed
string's `size()` under-reports kerned text, so the label is measured
through its cell, or the last glyph is clipped.

## 2026-09-03 — what survives a hub that is away (phase 5, part one)

**Why.** The split deferred the resilience it needed: a timer must ring
whether or not a server is reachable, a reconnect must not double a
turn, and the away text must find the user when the Mac is asleep.

**What.**

- *The timer mirror* (`spoke/timers.py`): the hub broadcasts
  ``timers.sync`` (deduped at the server, once a second alongside the
  agents roster); the spoke mirrors it, persisted, and rings a timer
  itself only when the hub can't — the link down at the due moment, or
  the hub silent past ``grace_s`` (a wedged hub). What the spoke rang is
  remembered by id, and a late ``deliver.speak`` for it is receipted
  without a sound. Offline, the grammar runs on the spoke: ``set_timer``
  arms a local timer in the mirror, ``cancel_timer`` and ``list_timers``
  answer from it in the hub's wording, ``reload`` restarts the room.
- *Say ids* (`spoke/client.py`, `hub/server.py`): every ``say`` carries
  one; the hub keeps the last 256 and queues each once, so the ledger's
  resend after a reconnect is acked but never doubled.
- *The away ladder* (`pipeline.py`): on the hub the owner's iMessage is
  the spoke's, so ``_run_proactive_message`` sends it while the spoke is
  seated and falls to Discord when it isn't.
- *The doctor* (`hub/doctor.py`, ``ciel hub --check``): the token, the
  bind address (a bind test — the tailnet address is not on the
  hostname's records), the brain's login by the CLI's own word, ``npx``
  for the connectors that need it, the state directory.

**Probes.** `probe_backfill.py`: the mirror's every rule and its
persistence, the say-id dedupe, the broadcast dedupe, the away ladder
both ways and with Discord down, the doctor's rows. `probe_spoke` grows
the offline grammar, the local ring, and the silent re-delivery.

## 2026-09-03 — the hub leaves the Mac (phase 4, in progress)

**Why.** The point of the whole move: the brain on a machine that never
sleeps. An Azure for Students VM (West US, arm64 — the only sizes the
subscription's policy and quota allowed; the SDK ships an arm64 Linux
wheel with the bundled CLI, so it works), reached over Tailscale.

**What.**

- `deploy/ciel-hub.service`: the hub under systemd — after the network
  and tailscaled, restarted in five seconds, an optional
  `~/.ciel/hub.env` for the brain's login on a box with no keychain
  (``hub.env`` joins ``FORBIDDEN_NAMES``). `deploy/ai.ciel.spoke.plist`:
  the room's half as a launch agent on the Mac, `ciel spoke` with
  `--no-sync` so a launch never drops the extras.
- The Origin gate trusts a configured name on any port: the public name
  arrives through a proxy on 443, not on the hub's port. `probe_wire`
  gains the case (70).
- Public Chart: a Cloudflare Tunnel (`ciel-hub`) from the VM to the
  hub's tailnet socket, a CNAME for the public name, and a Cloudflare
  Access application with a one-time-PIN policy for the owner's emails
  in front of it. The hub listens on the tailnet only; the tunnel and
  Access are the only way in from the internet, the hub token the gate
  behind them.

**State.** VM bootstrapped (uv, Node 22, Tailscale, cloudflared), the
repo and `~/.ciel` state copied with paths rewritten, the hub service
active and bound to the tailnet address, Discord signed in from the VM,
the wire driven from the Mac over the tailnet (direct, 7 ms). The
brain's login on the VM is the interactive `claude` `/login`, not
`setup-token` (which prints a token for an environment variable and
stores nothing) — the plan's assumption, corrected. Remaining: the
spoke pointed at the VM as the daily driver, the lid-closed test, the
kill-and-restart, and the move to Hetzner before the credit runs out.

## 2026-09-02 — the hub needs no Mac in it (phase 3)

**Why.** Phase 3 of the hub-and-spoke move: everything the hub still
reached into macOS for goes over the wire instead, so the hub can leave
this machine with nothing but an address changing. Still both on the
Mac, RPC forced on, which is how it gets proven before the move.

**What.**

- *Tool RPC* (`hub/server.py`, `hub/rpc.py`, `spoke/executor.py`):
  ``tool.request`` down, ``tool.result`` back, a cancel on the hub's
  deadline, every open call failed at once when the spoke leaves. The
  hub binds duck types instead of implementations — ``RemoteScreen``,
  ``RemoteMessagesClient``, ``RemoteLocator``, ``RemoteWorkWatcher``,
  ``RemoteCalendar``, ``RemoteMac`` — so ``bind_screen``,
  ``bind_client``, ``bind_locator``, ``bind_watcher`` just get
  different objects (``build_tool_server(config, remote)``). The
  spoke's executor runs the same modules the single process uses: the
  screen capture, the Messages client, the locator, the work watcher,
  the EventKit agenda; one task per call, failures as sentences.
- *The Mac tools* (`brain/tools/mac.py`): ``run_on_mac``,
  ``mac_read_file``, ``mac_write_file``, ``mac_list_dir`` — registered
  only on the hub. ``ShellGuard`` generalized to any tool that carries
  a command (``tool_name``, ``arg``, and a question that says where),
  so the Mac's shell sits behind the same three tiers and the same
  spoken yes; the workspace guard's path map gains the file tools; the
  recorder journals the Mac's shell and writes; the prompt gains a
  section naming the Mac as the default target. Defense in depth on
  the spoke: the classifier and the path check re-run from *its*
  config, the deny tier refused regardless, the confirm tier refused
  unless the call carries the hub's ``confirmed``.
- *Publishers* (`spoke/publisher.py`): ``PublishQueue`` is the queue
  the Mac's watchers push into — ``event.publish`` up, held in an
  outbox until ``event.ack``, resent after a reconnect, a local dedupe
  keeping rescans off the wire. The spoke runs ``CalendarWatcher``,
  ``LocationWatcher``, and ``WorkWatcher`` against it; the hub's
  ``_on_published_event`` pushes into the real ``EventQueue``, whose
  dedupe absorbs a resend. Portable watchers (the brief, Google's
  calendar, the ring, the sections) stay on the hub.
- *Presence* (`hub/rpc.py` ``PresenceView``, `spoke/publisher.py`
  ``Heartbeat``): the spoke sends the raw signals — locked, seconds
  since input, attended — every ``[spoke].heartbeat_s`` and at once on
  a change, with the roster of active watches for the agents chip; the
  hub folds them with its own conversation recency into the same
  ``PresenceState`` the policy always read. A heartbeat older than
  ``[hub].presence_stale_s`` is an empty room, whatever else is true.
- *The import audit*: the audio stack (sounddevice, webrtcvad, the
  speech models) and the Mac frameworks are imported where they are
  built — the local role's constructor, the methods only it runs, the
  broker's endpointer on first use — never at module level in anything
  the hub imports. `probe_hub_imports.py` refuses all of them by name
  in a child interpreter and constructs ``Pipeline(role="hub")``; the
  spoke is checked to fail the same test.
- *Config*: ``[hub].presence_stale_s``, ``[spoke].heartbeat_s``,
  ``[shell].command_timeout_s`` (the Mac-run command's own clock);
  ``event.ack``, ``presence.watches`` in the catalog.

**Probes.** `probe_tool_rpc.py`: every remote duck type through a real
executor over the real server, the tools bound to them end to end
(the screen with a fake capture, the watch tool arming on the Mac, the
four Mac tools), the quiet tier unconfirmed and the side-effect tier
confirmed, the deny tier refused on the Mac, a command past the Mac's
timeout killed, reads and writes inside the workspace and refused
outside it, no spoke, a spoke that never answers (deadline, cancel),
the spoke leaving mid-call, an unknown tool. `probe_presence.py`, 26:
the view as a table with the stale rule, the publish queue's outbox,
ack, resend and dedupe, the heartbeat on change, and the hub's side
through the real server. `probe_hub_imports.py`, 2. `probe_spoke`
grows the executor and outbox routing. Live: still both on this Mac —
the daily driver with RPC forced on, every Mac tool and a Vigil
speak/message/note each observed, is owed.

## 2026-09-02 — two processes: the room and the brain (phase 2)

**Why.** Phase 2 of the hub-and-spoke move, the riskiest cut: split
Ciel into the process that must be in the room and the process that
needn't be, both still on this Mac, so the second can leave later with
nothing but an address changing. `ciel local` is untouched and remains
the rollback.

**What.**

- *The pipeline in two roles* (`pipeline.py`): ``Pipeline(config,
  role="hub")`` builds no audio — no STT, TTS, wake, endpointer, or
  gate — and runs ``_run_hub`` instead of the frame loop: the same
  ladder (``pick_next``) on a tenth-of-a-second tick or the server's
  stir, the same enactments. The frame loop's dispatch blocks moved
  into ``_enact``, its idle reload-and-maintenance block into
  ``_idle_housekeeping``, shared byte-for-byte by both loops; the
  audio-owning turn became ``self._turn``. The voice lane on the hub is
  ``_WireSink``: sentences go down as ``turn.sentence`` frames and the
  sink awaits each ``turn.played`` receipt, so a stopped sentence
  abandons the turn (brain interrupted, confirm cancelled) exactly as
  the player's False does. Timers and Vigil nudges become
  ``deliver.speak`` with a receipt the budget hangs on; mute on the hub
  touches no sentinel and stops no player — it broadcasts, and the
  spoke's report comes back the other way.
- *The ladder's voice rank* (`schedule.py`): ``Source.VOICE`` —
  an utterance the spoke already endpointed and transcribed outranks
  every queue but timers, from any state but BUSY; never true in the
  single process.
- *The hub server* (`hub/server.py`): ``WebLink`` with the spoke's
  seat. One seat (a second spoke replaces the first); spoke says are
  voice turns, not chart turns; ``turn.played`` and ``deliver.result``
  resolve the sink's waits; ``confirm.answer`` goes to the broker;
  ``voice.state`` feeds the snapshot; a spoke that leaves fails every
  open wait and tells the pipeline, which cancels a pending question.
- *The spoke* (`spoke/frontend.py`, `spoke/client.py`): the frame
  loop's state machine with the thinking taken out — wake, gate, STT,
  the Cauchy hold, the dismissal-before-merge, barge-in, follow-up,
  the ack filler, the chime, the HUD, the mute sentinel — sending
  ``say`` and playing what comes back. ``confirm.request`` is spoken
  and, when it asks, listened for with the broker's own endpointer;
  the answer (or the window's timeout, as an empty one) goes back.
  The client speaks first, holds says until acked, resends after a
  reconnect, backs off. A hub that is down is said once, then a chime
  per swallowed utterance.
- *The broker's spoke origin* (`confirm.py`): the remote choreography
  with ``listen=`` on the lines that expect an answer, the spoken
  lane's ``you-confirm`` label (the answer was spoken here — presence
  evidence), an empty answer read as "no answer", and
  ``[hub].confirm_timeout_s`` as the hub-side deadline.
- *The catalog* (`wire.py`): ``turn.played``, ``voice.state``,
  ``confirm.cancel``, ``n`` on sentences, ``listen`` on requests; the
  replay ring now holds only the idempotent broadcasts (rows, state,
  muted, agents, timers.sync) — a replayed sentence would be spoken
  twice.
- *Config and entry*: ``[spoke]`` (``hub``, ``token``/``token_file``,
  ``client_id``, backoff); ``[hub].speak_timeout_s`` and
  ``confirm_timeout_s``; ``ciel hub`` / ``ciel spoke`` / ``ciel local``
  in ``__main__``, the role carried through a reload.

**Deferred, on purpose.** The spoke's local command fast path and
timer ringing (resilience, phase 5); the typed lane on the spoke's
terminal; presence, tool RPC, and the Mac watchers as publishers
(phase 3 — both processes are on the Mac, so the hub still reads them
directly).

**Probes.** `probe_hub_arbiter.py`, 44: the seat, the snapshot and the
voice rank, a full voice turn's frames and golden rows, barge-in through
an unfinished receipt and through ``turn.cancel``, the spoke leaving
mid-sentence and never receipting, confirm requests through the sink,
timers and nudges as deliveries with and without a spoke, mute both
ways. `probe_spoke.py`, 36: a turn end to end with the follow-up window
after, the gate, the hold, the dismissal, barge-in, the hub down (said
once, then a chime), a confirmation answered and one timed out, a
delivery rung and receipted, mute from both sides, the state reports,
the ack filler. `probe_confirm_wire.py`, 13: the spoke origin's labels,
``listen``, the empty answer, the hub deadline, cancel within a second,
a late answer ignored, a dead sink. `probe_ladder` 17, `probe_wire` 69,
`probe_turns` and `probe_vigil` adjusted for the new signatures; the
whole non-hardware suite green (963 checks). Live: the real hub process
(isolated state, brain and all) driven by a scripted spoke over a real
socket — "Four, sir." as sentence 1 between ``turn.begin`` and
``turn.end``, 2.6 s to first speech — which also caught the one bug the
probes had not: a ``continue`` in the hub loop that had become a
``return`` during the extraction, ending the loop after its first
enactment (``probe_hub_arbiter`` now drives the loop itself). Still
owed: the two-process daily driver with a real spoke, kill -9 each
side, sleep/wake.

## 2026-09-02 — the wire gets a catalog; the Chart reaches the tailnet (phase 1)

**Why.** Phase 1 of the hub-and-spoke move: grow the hub server in
place, one process still on the Mac, so the protocol every future
client speaks — the browser Chart today, the Mac spoke and a phone
later — is written down and enforced before anything is split. The
win that needs no VPS: the Chart from a phone over Tailscale, with a
door that asks for a token and a reconnect that doesn't blank the
screen.

**What.**

- *The catalog* (`wire.py`, codename Meridian): every frame type in
  each direction — the Chart's existing frames plus the ones the later
  phases need (`turn.*`, `confirm.request`/`answer`, `tool.request`/
  `result`/`cancel`, `event.publish`, `presence`, `deliver.*`,
  `timers.sync`) — with required and optional fields, a codec that
  refuses what it doesn't name (unknown types, missing fields, a
  bool where an int belongs) and passes unknown extra fields (a newer
  peer), versioned by `WIRE_VERSION` in both hellos.
- *The client speaks first* (`remote/web.py`): the hello now comes
  from the client — `role`, `token`, `client_id`, `caps`, and a resume
  claim — and the server answers with its own. `admit` is the whole
  auth policy as one pure function: a loopback peer is trusted by
  reach as before (its first frame need not even be a hello — an
  older page keeps working), every other peer must carry the hub
  token, compared constant-time, with an `error` frame naming the
  close code (4401, 4400, 4408) before the socket closes.
- *Seq and resume*: every broadcast frame carries a `seq` under a
  per-process `epoch`; a `ReplayRing` remembers the last
  `resume_frames` of them. A reconnecting client's claim, when the
  epoch matches and the ring still holds its seq, is answered with
  `resumed: true` and exactly the frames it missed — no history
  window, no blanked screen. Any other claim gets the fresh hello it
  always got. A confirm older than `resume_grace_s` is not replayed
  (the Discord catch-up rule: the broker has timed it out).
- *`[hub]` config*: `bind` (the tailnet address), `token` /
  `token_file` — minted owner-only at first start when the bind is
  not loopback, and named in `FORBIDDEN_NAMES` — `origins` for a
  MagicDNS name, `hello_timeout_s`, and the ring's size and grace.
  The Origin gate accepts loopback, the bind address, and the listed
  names — never the request's own Host header, which a DNS-rebinding
  page would satisfy.
- *The page* (`chart.html`): sends the hello with its remembered
  token and resume claim, tracks the epoch and last seq in
  sessionStorage beside the ack ledger, keeps its rows on a resumed
  hello, and on a 4401 close shows a token form instead of a retry
  loop. `wss://` when served over TLS, for `tailscale serve` later.

**Probes.** `probe_wire.py`, 69 checks: the codec round-trips every
catalog type and refuses each malformation; the ring's stamp and every
resume outcome; `admit` across every peer-and-frame combination; the
Origin gate off loopback; the welcome with planted queues; and a real
aiohttp socket on loopback — the 4401 refusal with its error frame,
4400 for a say before hello, 4408 after a silent hello timeout, a
resume that replays exactly the frames a dropped socket missed, an old
page's first say kept, a foreign Origin refused at the upgrade.
`probe_web.py` stays at 35 (one expectation grew a seq). Live smoke
from a phone over the tailnet is still owed — Tailscale is not yet
installed on this machine.

## 2026-09-02 — the section watcher keeps its own cookie warm

**Why.** The section watcher's one soft spot was its login: the site
authenticates through Canvas OAuth (CalNet + Duo), and a hand-pasted
cookie felt like a daily chore. Probing it changed the picture — the
session is a *sliding* two-hour window, so every poll refreshes it and a
running watcher never lapses. The only real death is an overnight sleep,
and that is the narrow thing worth automating.

**What.**

- *Cookie from a file, read live* (`config.py`, `sections.py`): the live
  cookie moves to `~/.ciel/sections-cookie` (the Oura-token pattern —
  config holds only a seed), and `current_cookie()` re-reads it on every
  scan, so another process's refresh is picked up on the next poll with
  no reload.
- *The refresh job* (`scripts/refresh_sections_cookie.py`): a
  self-contained Playwright script driving a dedicated Chrome profile
  that holds a bCourses session (seeded once, headed, by the user —
  CalNet and Duo stay in human hands; the script never sees a password).
  It makes one plain HTTP check first and only launches Chrome when the
  cookie is actually dead, then follows the OAuth redirects silently and
  writes the fresh cookie. Runs two ways: a launchd agent each morning
  and on wake (for when Ciel isn't up), and the watcher's own self-heal.
- *Self-heal* (`proactive/sections.py`): a scan that comes back signed
  out (`SectionsUnavailable.auth`) spawns `refresh_cmd` once per
  `refresh_cooldown_s`, distinct from a network failure, which leaves the
  cookie alone. A refresh that keeps failing (the remembered session
  lapsed) still surfaces the ordinary re-login note.

- *The email alarm* (`gmail.py`, `proactive/sections.py`): an opening
  can also email the user ``email_on_opening`` times, ``email_interval_s``
  apart, each with its own subject so they arrive as separate
  notifications. Sent by the watcher the moment the event is queued —
  outside the policy and quiet hours on purpose, since the user asked to
  be woken for this one thing. ``GmailSender`` borrows the ``[mcp.gmail]``
  connector's refresh token read-only (the calendar watcher's pattern);
  the recipient defaults to the signed-in account and is otherwise pinned
  in config, never chosen at send time; ``email_from`` sets a verified
  send-as alias as the From. And a second sender (`mail.py`,
  ``SmtpSender``): with ``smtp_token`` set the alarm goes out *as Ciel*
  — ``ciel@example.com`` through Cloudflare Email Service's SMTPS relay,
  free to the account's verified destinations — leaving the user's Gmail
  connector untouched. ``MailUnavailable`` is the failure shape both
  senders share.

- *Ciel's own address* (`[mail]`, `brain/tools/mail.py`): the relay
  became Ciel's, not just the watcher's. `send_as_ciel` sends from
  `[mail] address` behind the spoken-yes gate (a describer of its own:
  "Send an email from my own address to ..."), stays off the Witness
  observers, and the prompt gains a section on whose voice a message is
  in — Ciel's mail here, the user's through their own tool. The watcher's
  alarm borrows `[mail]`'s relay, address, and owner when it has no relay
  of its own, so one token serves both; its own `smtp_token` still wins.
  `copy_to` Bcc's every message to an inbox of the user's — their record
  of what Ciel sent — and the relay now reads `smtplib`'s partial-refusal
  return instead of ignoring it: a refused copy is a warning, a refused
  recipient is a failed send.

- *Self-knowledge* (`prompt.py`, `agent.py`): the watcher ran below the
  brain and the brain didn't know — asked "are you watching for a spot?",
  Ciel would honestly say no. `sections_watch_line` now tells the Watching
  section which sections are watched, how often, and every outlet that
  fires on an opening, and that enrolling stays the user's act.
  Keyed on the watch list, not `enabled`: on the hub the watcher runs
  on the spoke (enabled = false there) and the brain must still know.

**Probes.** `probe_sections.py` grows to 78 checks: cookie-file
precedence over the seed, the self-heal spawn and its cooldown,
`auto_refresh` off never spawning a browser, the sender's raw message and
default recipient and From, the alarm's count and distinct subjects, and its off
and unauthorized switches, the SMTP sender through a fake relay and the
watcher's choice among the three. `probe_mail.py` (12 checks: the tool
through a fake relay, the gate's question, the prompt's presence and absence,
and config arming). Playwright launch, the graceful not-signed-in
exit, the launchd load, and one live test email were verified by hand.

## 2026-08-31 — the section sniper (a Vigil watcher)

**Why.** CS 61B fills sections first-come-first-served on
sections.datastructur.es and a dropped spot is gone in minutes — a race a
poll wins and a person loses. Exactly the shape Vigil promised a new
watcher would be: one module and one line in the pipeline; the policy,
the away outlet, and the health streak were already waiting.

**What.**

- *The site as a client* (`sections.py`): one read-only call —
  `POST /api/refresh_state` with a browser cookie (the course's Canvas
  OAuth can't be automated; the session is borrowed, Discord-token
  style) — parsed tolerantly into `Section`s with spots, weekly slot,
  and the signed-in user's enrollments. A signed-out answer *raises*
  toward re-login rather than reading as "every section vanished". The
  API's `join_section` is deliberately not wrapped: Ciel says a spot
  opened; taking it stays a human act.
- *The watcher* (`proactive/sections.py`): a transition detector —
  full → open fires importance 3 (speak now, or text the away outlet),
  expiring in 30 minutes because a stale spot is pure disappointment;
  still-open stays quiet; a reopening fires again. Dedupe keys bucket
  by local hour, the deliberate trade that survives autoreloader
  restarts mid-opening and keeps a flapping roster from burning the
  day's three-text budget. Enrolled sections never fire. A dead cookie
  becomes a `WatcherHealth` note pointing at the fix.
- *Config* (`[sections]`): `url` (other courses run the same app),
  `cookie`, `watch` (section ids — the filter is the consent), and a
  30-second `poll_s`, the one watcher whose whole value is the race.

**Probes.** `probe_sections.py` (35 checks: payload shapes including
signed-out and broken rows, phrasing, every transition — first scans,
flaps, enrollment, the hour bucket — the watcher's lifecycle and failure
streak, config from TOML and environment; `--live` lists the site's
sections with ids and open spots, which is also where `watch` ids come
from).

## 2026-08-29 — the seams cut for the hub (phase 0)

**Why.** The hub-and-spoke plan is approved: the brain, memory, Vigil,
Discord, and the Chart move to a cloud hub; the Mac keeps the audio
pipeline and its Mac-only powers. Before any process splits, the
in-place refactors — the ones the exploration flagged as prerequisites
because lane identity and scheduling were smeared through the frame
loop — and each pays for itself locally even if the project stopped
here.

**What.**

- *Four lanes, one turn* (`turn.py`): a `TurnRequest` + lane registry
  (labels, console tags, system notes, held-note policy, confirm
  origins — today's strings byte-for-byte, because `user`/`user-web`
  rows are presence evidence and the Chart renders by them) and a
  `TurnSink` protocol for the delivery half. `Pipeline._run_turn` is
  the one copy of the skeleton the four handlers used to hand-wire;
  `_respond`/`_respond_stream` folded into the voice sink. In the
  endgame the hub drives this same method with a sink that writes wire
  frames.
- *The ladder is a pure function* (`schedule.py`): the frame loop's
  priority ladder — confirmation, timers, keyboard, page, phone,
  Vigil — extracted as `pick_next(Snapshot)`, no clock reads, no I/O;
  the loop builds a snapshot per frame and enacts the pick. The hub's
  mic-less arbiter runs the same ladder. `TimerService.any_due` is the
  arbiter's non-consuming look at the timer book. `State` moved to
  `schedule.py` (re-exported).
- *The lane surface is a Protocol* (`remote/lane.py`): the documented
  queue-and-send shape both links implement, now runtime-checkable and
  asserted by their probes — the third implementation is the hub link.
- *Wall-clock twins*: `TurnRequest.arrival_wall` and the confirm
  broker's `asked_at_wall` alongside the monotonic stamps nothing
  cross-host can compare. Groundwork only; nothing consumes them yet.

**Probes.** `probe_turns.py` (51 checks: golden row sequences per lane,
prompt composition, confirm origins, escalation shapes, barge-in
abandonment, local-command routing, failure shapes) and
`probe_ladder.py` (15 checks: every rank and guard of the table). All
existing probes green; the live instance hot-reloaded through the
refactor and came back up — brain connected, Chart serving, Discord
signed in.

## 2026-08-29 — the audit's flagged items, closed

**Why.** The documentation audit surfaced five findings too behavioral to
fix as prose. Four were worth closing (the fifth — the unused CoreLocation
dependency — is a design decision still owed).

**What.** *Undo can read its own snapshots*: the undo prompt sends the
model to read a snapshot in `~/.ciel/undo/snapshots`, which the workspace
guard denied under any narrow workspace. Kept the journal's
no-side-channel philosophy — undo stays ordinary tools — and gave the
guard one carve-out instead: reads (never writes) under the snapshots
directory, with a suffix check so a stray snapshot of a forbidden file
stays buried. *Confirmations are lane-matched*: `remote()` now records
which lane asked ("discord" or "web"), and a Discord text answers only a
Discord-origin question — a question shown on the loopback page was never
shown to the phone. The page still answers either origin, because every
question is mirrored to it as a ciel-confirm row. *Backfill drains*: the
reconnect sweep paged once (25 oldest) and silently lost the tail of a
deeper gap; it now pages until drained, with a flood cap. *Two logs tell
the truth*: the wake ready-log prints a custom model's phrase (the path
stem), not its path; the speaker gate says "centroid" when the centroid
row wins instead of naming a take that does not exist. Full scripted
suite passes: 666 checks across nine probes, plus a direct exercise of
the carve-out (read allowed, write denied, forbidden suffix denied,
other outside paths still denied).

## 2026-08-29 — the documentation audit: every claim checked against the code

**Why.** "Go through all the documentation and make sure it is all up to
date." Docs drift; this codebase documents itself unusually heavily, which
means unusually many claims that can silently stop being true.

**What.** Five parallel read-only audits swept every module docstring,
config field doc, and the README against the source, and everything found
was fixed. The README got the most: the intro and config example now name
mlx-whisper as the shipped STT default (faster-whisper is the CPU
fallback), the custom wake-word path is present tense (trained, probed,
ready-line aware) instead of future work, the codename table gained its
five missing rows (Singularities, Tower Clearance, Chart, Vigil, Witness)
and un-conflated Compact Support from Singularities, the status-pill table
gained the reasoning state, a full Vigil section now exists (watchers,
policy order, budgets, the hold brake), the probe list gained its five
missing scripts, enrollment documents all six modes and the real take
count, and the module table covers the tree as it is. Two dozen docstring
corrections landed across the packages — stale defaults (500 ms
endpointing, say-as-fallback), phantom references (`build_memory_tools`,
`probe_bargein.py`, the "plan"), wrong lists (Witness's deny list, Vigil's
event sources, the grantable catalog's reload promise), and the confirm
broker's Discord-only framing (the web lane shares it).

Where the doc was right and the code was wrong, the code moved instead:
the amara.org hallucination entry violated the list's own normalized-form
invariant and could never match; `origin_allowed` judged a portless
https Origin as port 80; the grants tool promised a reload that
`[dev] autoreload = false` would never deliver; the system prompt told the
model it cannot read outside the workspace even when `read_only_outside`
says it can (now threaded through); and the fast-path grammar's address
list lacked "jarvis" — the default wake phrase — so "hey jarvis, ten
minute timer" always paid for the brain. Two probe fixes: the Vigil
probe's minimal pipeline predated the web tap, and the Oura probe's
junk-days check hard-coded the wrong weekday, latent until the real date
moved past its fake one. Full scripted suite passes: 666 checks across
nine probes.

## 2026-08-29 — the voice speaks from the ceiling

**Why.** "Give my current piper voice the post processing that JARVIS
gets." The cinematic-assistant sound is mostly not a different voice but
a treatment: small-speaker low-end rolloff, a presence lift where
consonants live, and fast, quiet early reflections that place the voice
in the room's architecture instead of nowhere.

**What.** `tts/effect.py` — a `VoiceEffect` wrapper speaking the
`TextToSpeech` protocol on both sides, so neither the pipeline nor the
player learns it exists. One linear-phase FIR carries the whole tonal
tilt (~6 ms group delay, normalized to unity at 1 kHz); twelve sparse
early reflections at prime-ish spacings with alternating signs supply
the room (~-12 dB wet, ringing ~130 ms past each sentence into a
flushed tail); a tanh limiter with measured make-up gain keeps treated
loudness within 0.6 dB of the dry voice. Pure numpy on purpose — scipy
is only in the venv as a transitive — and stateful overlap-save per
chunk, so time-to-first-audio pays only the FIR delay. Wired as
`[tts] effect = "jarvis"` (default `"none"`, Literal-validated), applied
in `build_tts` and re-applied on the runtime `say` fallback: the
treatment belongs to the voice's character, not to whichever engine is
producing it. Verified by A/B synthesis: sub-100 Hz -8 dB, presence
+3.7 dB, peaks soft-limited under full scale, RMS matched.

## 2026-08-28 — the chart shows who is working

**Why.** "I want to be able to see all active agents on the UI." Ciel
does things between and during turns — a deep-thought pass someone is
sitting in silence for, watches armed on background work, timers
counting down — and the page showed none of it: a running watch was
invisible until it spoke, and the only trace of deep thought was one
event row scrolling away.

**What.** The active-agent roster. A new `agents` frame in the web
protocol (and a roster in the hello), carrying everything working on
the user's behalf right now: `deep` (the brain now stamps the
escalation in flight and clears it when the parent turn resumes —
`Brain.deep_thought_since`), `watch` (the work watcher's registrations,
labeled with their completion sentence), `timer`/`alarm`. Deliberately
*not* the Vigil watchers themselves — standing infrastructure that is
always on stops meaning anything in a roster; this lists work with an
end. The pipeline polls the roster once a second at the mute sentinel's
cadence (polling beats wiring change hooks through three sources) and
the link dedupes, so an unchanged second costs one list build. On the
page: a chip beside the state pill counts ("2 working", breathing while
a deep pass runs), and expands into a strip with per-kind icons, the
watched target, and live countdowns ("5m in · 24m left"). Unknown kinds
render as plain rows — the transcript-label rule, because new agent
sources will add new kinds. Verified scripted and live: `probe_web.py`
grew roster checks (broadcast, dedup, hello carriage) and a `--port`
flag plus a "deep" toggle in live mode, and the page was driven in a
real browser — chip, panel, countdown tick, deep engage/clear.

## 2026-08-26 — a page to talk through; a switch that keeps the room quiet

**Why.** "This GUI is meant for when I'm outside, in class, at the
library." Ciel had lanes for the keyboard and for Discord, but no way to
*see* the conversation, and no way to be in the room with it without it
making sound — a lecture hall tolerates neither a spoken reply nor a
false wake.

**What.** The web GUI (`remote/web.py`, codename **Chart** — a local
coordinate window onto the same manifold; a native app later is another
chart of the same atlas). A loopback aiohttp server, off by default
(`[web] enabled = true`, `uv sync --extra web`): the page shows every
lane's transcript rows live (a tap in `_record`), mirrors the HUD's
state pill (a `TeeIndicator` fans the announcements), takes typed turns
through the Discord lane's queue-and-send surface, and renders
confirm-tier questions as Yes/No buttons (`confirm.remote`, the
texted-question road). Web turns count as presence — reaching a
loopback port means being at the machine, the keyboard's trust — and
the one browser-shaped hole, cross-site WebSocket access, is closed by
an Origin check before a frame is read. The WebSocket protocol is
documented in the module as the contract a SwiftUI client builds
against later. And the mute switch: one gate at the top of
`Player.play()` silences everything (greeting, acks, timer rings,
sentences, apologies) with no call site knowing mute exists; the frame
loop stops watching for the wake word; Vigil "speak" decisions become
held notes; timers surface as text on the page. State lives in
`~/.ciel/mute` — the hold sentinel's trick — so the autoreloader's
re-execs can't un-mute a lecture, and `touch ~/.ciel/mute` works from a
hotkey with the page closed. Verified scripted and live:
`probe_web.py`, plus the page driven in a real browser (send, echo,
confirm banner, mute round-trip, reconnect with the draft preserved).

**Why.** "Put a watcher on my ring stats and my location." The readiness
watcher covered one number; a bad night is usually the *sleep* score, and
a still day is the activity one. And location had no source at all —
Ciel could not answer "where am I", let alone notice a move.

**What.** The Oura watcher now files at most two notes a day: one in the
morning when readiness or sleep (or both — "rough night by the ring")
sits at or under its threshold, with hours asleep from the session and
the weakest contributor named; one from `activity_check_after` when the
activity score is still low ("a walk would fix it", importance 1). Each
threshold is its own switch. And a location layer (`location.py`,
`[location]`): two sources, honestly labelled. The Wi-Fi network the Mac
is on (`system_profiler` — CoreWLAN and `ipconfig` redact the SSID now,
and needs no permission) locates the laptop; Find My's device cache
locates the phone, behind two gates the code names in its one-time
warning — Full Disk Access for the app running Ciel, and a macOS that
still writes the cache as JSON (14.4 and later don't). CoreLocation was
tried and is out: macOS never shows a Python process the permission
dialog (`kCLErrorDenied`, no prompt). `[location.places]` turns either
reading into a name; the `where_am_i` tool (Witness-listed, read-only)
speaks the place, the device it came from, and the reading's age; the
Vigil watcher notes moves between named places as importance-1 notes
after a silent startup baseline. Also repaired in passing: a bare
`uv sync` while adding the CoreLocation bindings dropped every optional
extra — the Piper voice, the voice gate, Discord — and Ciel fell back to
`say` mid-conversation; `uv sync --all-extras` put them back. Verified
scripted: `probe_location.py` and `probe_oura.py`.

## 2026-08-21 — the ring answers "how did I sleep"

**Why.** The most-asked morning question had no source: Ciel could read a
calendar and a message thread but not the one sensor worn all night. And
the ring's own verdict — a low readiness score — is exactly the kind of
thing worth one unprompted sentence, which is what Vigil exists for.

**What.** The Oura integration, end to end. `oura.py` is the client: Oura
Cloud API v2 over OAuth2, stdlib urllib on a worker thread, read-only by
construction (daily readiness, sleep, activity, and the sleep *sessions* —
the summaries carry scores, but the number people want is hours, and that
lives on the session). Oura stopped issuing personal access tokens in
December 2025, so the way in is an application of your own: `[oura]`
holds the client id (the secret by environment), `probe_oura.py
--authorize` runs the browser approval once — `extapi:daily` scope only,
PKCE, a localhost callback with a state check, the port bound only for
the duration — and writes `~/.ciel/oura.json`, a new `FORBIDDEN_NAMES`
entry written owner-only. The endpoints are the ones
`api.ouraring.com/.well-known/oauth-authorization-server` advertises
(`/v2/oauth/authorize`, `/v2/oauth/token`), not the legacy pair in the
older docs: an application from the new developer portal is registered
with this server only, and the legacy approval page mints a code the
legacy token endpoint then rejects as `invalid_client` — observed live
during setup, and the reason the module names its sources. Ciel owns that file: Oura's refresh tokens are
single-use, so every refresh (lazy, a day before the month-long access
token expires, serialized across the tool's and the watcher's threads) is
persisted atomically before anything else happens; a spent or revoked
refresh says how to repair it instead of failing forever. A legacy token
still works as a static bearer until Oura shuts it off; the OAuth file
wins. The `oura_summary` brain tool answers for a day or a range (up to
two weeks), labelled as a person would read it back ("Today: sleep score
79, 7 hours 12 minutes asleep, in bed 11:42 pm to 7:31 am…"), and is on
the Witness list so an unattended morning turn may check the ring before
it speaks. The Vigil watcher polls today's readiness every half hour and,
at or under `low_readiness`, queues one importance-2 nudge per day naming
the weakest contributor; a fine morning is silent. Armed is the one test
everywhere — enabled *and* holding a way in — so the registry, the prompt,
and the watcher can't disagree about whether the ring exists; enabled
without an authorization warns once and arms nothing. Verified scripted:
`probe_oura.py` (shaping, the tool through a fake client, the nudge, the
watcher's lifecycle and failure streak, the token store's refresh and
rotation, the live localhost callback, config).

## 2026-08-20 — the wall of "clear" dies in the filter

**Why.** Observed live: Whisper (large-v3-turbo) locked onto one token and
transcribed a spoken sentence as "I'm going to use the same way to make a
clear clear clear…" — three hundred clears. It sailed past both existing
decode-time mitigations (`condition_on_previous_text=False` and the
temperature fallback — the retry can loop too), then the Cauchy hold
faithfully held the wall as a "partial thought" and merged it into the
next utterance, doubling it. The model, to its credit, answered "That one
came through garbled, sir" — but a thousand tokens of noise per garbled
turn is a cost and a bug.

**What.** A deterministic backstop in the shared filter layer
(`stt/hallucinations.py`, both engines): transcripts that are *nothing
but* a short n-gram repeated are dropped as hallucinations, and
`collapse_repetition` salvages the head-and-wall case — real speech
survives untouched, two copies of the looping phrase remain as evidence,
the wall is gone before the hold, the merge, or the model ever see it.
The bar is six consecutive copies of an n-gram (n ≤ 4): "no no no" and
"very very very very" pass through untouched; no human utterance clears
six. Punctuation variants ("clear, clear. clear!") count as one run.
Verified scripted: `probe_stt.py`, 21 checks, including the observed
failure verbatim and a 2000-word wall collapsing in well under a second.


## 2026-08-20 — powers by text, mentions in servers, the token off-limits

**Why.** With the Discord lane live, two asks followed immediately: change
what Ciel may do from a phone ("enable your shell" from the couch across
town), and answer `@ciel` in a server channel. And the lane's own token
was sitting in model-readable config — a credential in a readable file is
a credential in every future context window.

**What.** Three pieces. *Grants* (`[grants]`, `brain/tools/grants.py`):
list/grant/revoke over a fixed catalog of capability switches. Enabling
passes the enforced confirmation gate ("Enable shell access — okay?" —
voiced at home, texted away) before one byte of config changes; disabling
runs instantly, because de-escalation with friction teaches people to
leave things on. Edits are surgical (comments survive, parse-verified,
rolled back on failure), journaled both directions but filing no read-back
event (the tool verifies its own edit), and trip the reload that makes
them real. The catalog is deny-by-construction: the Discord pinning, the
voice gate, tool tiers, and the workspace path are simply not in it.
*Mentions*: `@ciel` in a server the bot was invited to is a turn — owner
account only, reply to that channel, and the turn is told it's in public:
discretion note instead of the DM note, held Vigil notes never delivered
outside DMs. No privileged intent needed (mention content is exempt).
*Hardening*: the token moved to `~/.ciel/discord.token`, now a
FORBIDDEN_NAMES entry, so file tools and the shell gate both refuse it;
the DM last-seen mark persists (`discord.json`) so the catch-up sweep
spans restarts — texts sent during a grant's own reload come back out of
the gap, capped at ten minutes so stale instructions die unexecuted. Also
fixed in passing: both confirm-gate hook timeouts (120 s) sat *below* the
remote gate's worst case (two 120 s texted windows) — raised to 600 s, so
a slow answer can no longer let the SDK time the hook out mid-question.
Verified scripted: probe_grants (32 checks), probe_discord (37), and the
full suite still green (148 shellguard, 56 closure, 171 vigil).


## 2026-08-20 — the conversation follows you out the door

**Why.** Ciel only existed inside earshot: away from the machine there was
no way to ask it anything, and the away outlet (when armed at all) was
one-way. "Text Ciel when I'm not home" is the missing half of living with
an assistant.

**What.** The Discord lane, codename Parallel Transport (`remote/discord.py`,
`[discord]` in config, `uv sync --extra discord`). A DM from the pinned
`owner_id` becomes an ordinary turn — same brain, same session, same
transcript, held Vigil notes delivered — and the reply rides back as a
text, buffered into one message rather than streamed as notifications.
Identity is deterministic where the Barn Door is probabilistic: only the
pinned account's DMs are read, and everything else is dropped before it
reaches anything with tools. The Proof Obligation travels too: a
confirm-tier action mid-remote-turn texts its question over the same DM
and waits on notification time (~2 min) for a yes or no — and remote
answers are accepted *only* for remote-origin questions, because a yes to
a question voiced into a room you're not in is not an answer. Remote turns
are deliberately not presence: their transcript rows carry remote labels,
so Vigil keeps treating the room as empty and texts instead of speaking to
nobody. When Vigil wants to message and iMessage isn't configured, the
link is the fallback outlet (`discord.proactive` opts out). The lane
degrades like the indicator — missing dependency, bad token, dead network
are each one warning and a working voice assistant. Verified scripted
(`probe_discord.py`, 29 checks: the gate, the queue contract, every
walk-away path of the remote confirmation denying), and the existing
shellguard/closure/vigil probes still pass. The live half —
`probe_discord.py --live` against a real bot token — is the first thing
to run after the five-minute Discord setup in the README.


## 2026-08-19 — nudges read Google Calendar at the source

**Why.** This Mac syncs no calendars into Calendar.app, so the EventKit
watcher was watching an empty store — nudges would never have fired. The
real calendar lives in Google.

**What.** `[proactive] calendar_source = "google"` routes the calendar
watcher to Google's API directly, piggybacking the auth the [mcp.gcal]
connector already established: same OAuth client keys, same saved refresh
token, nothing new to authorize and no Calendars permission dialog. Access
tokens live in memory only — the connector's token file is never written,
because two writers of one token file is how refresh races start. Same
watcher contract as EventKit (nudges, agenda for the brief, kick on wake,
health streaks), stdlib urllib on worker threads, read-only endpoints
only. Verified live: token refresh, a real scan, and tomorrow's lectures
in the agenda. Adding a fourth watcher also tipped the deferred cleanup:
the poll heartbeat (tick, sleep-or-kick, clear-after-wait) now lives once
in watchers.poll_loop, used by all four.

## 2026-08-18 — the night review: ten findings, ten fixes

**Why.** A day that shipped three Vigil phases deserved an adversarial
pass before anyone slept on it. An eight-angle review of the day's diff
surfaced ten confirmed-or-plausible defects — several of them exactly the
class of bug an unattended layer must not have.

**What.** The worst: a user turn hastening the maintenance slot could ship
a *truncated* iMessage (interrupt ends the stream normally; nothing
checked); the brief consumed its overnight held notes before knowing
whether it would deliver, so a SKIP burned them; cancelling an unattended
turn (autoreload, shutdown) lost its event entirely; the WorkWatcher
mutated the queue from a worker thread, able to corrupt the whole state
file; and the new glob leniency in the shell gate let *targeted*
credential globs (`.ss?/id_*`) escape deny. All fixed — the unattended
choreography now lives once in `_compose_unattended` (timeout-interrupt
inside the suppression block, hastening detection, hold-on-cancel), the
queue moved to a peek/claim lifecycle with an in-flight dedupe horizon,
the brief peeks and consumes only on delivery, watcher threads only read,
and the glob downgrade applies to broad listing patterns alone, with the
reason spoken in the confirmation. Also: cold starts are no longer misread
as wakes from sleep, verify failures hold an honest fallback instead of an
internal directive, read-back checks cap their backlog at three, and the
older hardening reviews' four open issues are closed (resolution appended
to the report). Probes grew to 153 + 148 + 56 checks.

## 2026-08-18 — Vigil phase three: done means observed

**Why.** The wishlist's second structural item: Ciel reports success
because a tool returned without error, "which is a weaker claim than it
sounds." And nothing on the machine could say "that thing you started has
finished."

**What.** The verification loop closes. After any confirm-gated action a
present user approved, the recorder files a read-back check (the recorder
still only observes — the emitter can note that an action deserves
checking, never affect it). The check runs as a new kind of proactive turn:
a silent "note" — no audio, no budget, quiet-hours-exempt because it
disturbs nobody — where an unattended turn re-observes with read-only
tools and writes down what is actually true. The finding replaces the
instruction and rides a held note into the next conversation: "the message
to Sam did send", "the calendar event never appeared — it may need
redoing." SKIP means checked-and-nothing-worth-relaying; a failed check
degrades to holding the instruction so the next attended conversation can
verify with full tools.

And the WorkWatcher: `watch_for_completion` lets an attended turn register
a file that should appear or a process that should exit, with a deadline —
persisted like timers, polled every fifteen seconds, kicked on
wake-from-sleep. Completion files the labeled event through the normal
interruption policy; a missed deadline files its own honest event ("never
finished"). The tool is Witness-denied unattended — arming a watch is
scheduling future unprompted speech, which is the budget bypass the rule
exists to prevent — and withheld entirely when Vigil is off.

## 2026-08-18 — Vigil phase two: the away outlet and the morning brief

**Why.** Phase one could speak or hold; it had no way to reach you when
you're out, and no standing morning ritual. And Ciel now lives on a laptop,
which sleeps — every monotonic deadline in the process quietly stretches by
the length of each nap.

**What.** Three additions and a reconciliation. (1) The away outlet: an
urgent event (importance 3+) arriving while nobody is around is composed by
an unattended turn and *texted to you* — to the one handle pinned in
`[proactive] owner_handle`, through a pipeline-owned send the model cannot
reach (send_message stays Witness-denied inside the turn). Three switches
must agree (`messages.enabled`, `allow_send`, `owner_handle`), texting has
its own daily budget (3), and quiet hours hold texts exactly as they hold
speech — a 2am buzz violates sleep the same way a voice does. Send failure
holds the event for the next conversation rather than losing it. (2) The
morning brief: `[proactive] brief_time = "07:45"` arms a ScheduleWatcher
that fires one brief event per day (deduped by date, expired after four
hours — a brief is breakfast, not dinner); the brief turn receives today's
remaining agenda from EventKit plus everything held overnight, and consumes
those held notes as its material. (3) Wake-from-sleep catch-up: a
wall-clock jump between frames kicks every watcher awake (a nudge due at
lid-open lands *now*, not after the remainder of a paused poll interval),
routes the next timer poll through the grace-filtered missed sweep, and
clears reflect/rotate deadlines a long nap already mooted. Plus
`deploy/ai.ciel.plist`: Ciel as a KeepAlive LaunchAgent, the restart story
the mic-stall detector was designed around.

## 2026-08-18 — lessons taken from hermes-agent

**Why.** A deep read of NousResearch's hermes-agent (see
`reports/2026-08-18-hermes-agent-steals.md`) confirmed Vigil is ahead on
interruption policy — Hermes has none — but Hermes carries scar tissue
worth having: rules about what a self-improving agent must *not* save,
provenance on machine-written memory, and watchers that report their own
failure.

**What.** Six adoptions. Closure's prompt gains a negative-capture list
(never save "tool X is broken" — transient failures harden into refusals
cited long after the fix; save the workaround, never an unresolved failure
as a method). Memory gains kind `procedure` — facts say who the user is,
procedures say how a class of task is done for this user, updated in place
— with frustration named a first-class procedure signal. Every memory now
records write provenance (`context: conversation | proactive`), stamped
through the Witness mode's new labels, and recall renders the hedge for
facts no user ever heard ("noted unattended — unconfirmed"): the runtime
states provenance, the model doesn't narrate it. The recall tool states the
source-first rule: memory is never evidence about current external state —
check the live source before asserting absence; "I recall" is not "I
checked". Watchers get a failure-streak self-report (three failed scans →
one importance-1 event → a held note names the broken watcher, instead of a
log line nobody reads). And `touch ~/.ciel/hold` is the emergency brake:
proactive delivery pauses, events queue, nothing is dropped, until the file
is removed. Deferred steals (duplicate-marked redelivery, dead-target
registry, watcher notepads, hash-suppression tier, propose-never-auto-enable
suggestions, the hub wake contract) are assigned to their phases in the
report.

## 2026-08-18 — Vigil: the right to speak unprompted, earned

**Why.** Ciel existed only while being spoken to — her own capability
wishlist put it first: between turns there is no process of hers watching
anything, so a meeting can draw near, a job can finish, and nothing is said
unless the user happens to ask at the right moment. Timers proved the
mechanism (speak later, unprompted); what was missing was everything around
it — event sources, and the policy for when interrupting is justified,
which the wishlist correctly called the hard part.

**What.** A proactive layer, codename **Vigil** (`ciel.proactive`), off by
default behind `[proactive] enabled`. Watchers push events into one queue
persisted at `~/.ciel/proactive.json` — queue, held notes, dedupe record,
and the day's budget all survive the autoreloader's re-exec. The first
watcher reads the calendar through EventKit (new dep, one-time Calendars
permission) and nudges "your two o'clock is in ten minutes", expiring
unspoken at the meeting's start. A pure `InterruptionPolicy` — quiet hours
that may span midnight, an importance floor, presence, and a hard daily
budget of unprompted speech — picks each event's route: speak now, hold
for the start of the next conversation (delivered as a system note the
transcript never confuses with the user's words), or drop. Presence reads
the screen-lock state and keyboard recency through Quartz plus
conversation recency through the mic, selectable via `presence_mode`
because the deployment box is undecided; all of it is evaluated lazily,
never at frame rate — the voice hot path gained one boolean check.

The turn itself runs unattended under a new named rule, the **Witness
rule** (`brain/witness.py`): a turn with nobody listening may look and may
remember — read-only tools and Ciel's own memory/projects notebook — but
nothing outside the notebook changes; messages, timers, files, shell,
searches, and unlisted connector tools deny in a PreToolUse hook that
leads the guard chain. Reflection now runs under the same rule, so its
"no messages, no searches" is enforced rather than requested. Connector
entries gain an `unattended` list for reads a nudge may verify against
("check the meeting still exists before announcing it"). Routing is a
one-way valve: the policy picks the ceiling and the model may only decline
(SKIP) or de-escalate to a held note (HOLD) — event text is written by
other people, and words in a calendar title must never be able to argue
something off the machine. `scripts/probe_vigil.py` drives all of it —
queue persistence, the policy table, the midnight wrap, the Witness
matrix, the valve — on injected clocks and fakes, no audio required.

## 2026-08-18 — a fast driver and a slow specialist

**Why.** Opus drove every turn, including "set a timer" and "what's on my
calendar". That is the strongest model in the house spending its time on
work that cannot use it — paid for twice, once in time-to-first-speech
against a half-second target, and once in Max quota that then ran out
mid-afternoon. The escalation valve already existed for depth; the model
choice was still all-or-nothing.

**What.** The driver moves to Sonnet at `medium` effort (up from `low` —
low read as careless on the fiddlier turns, and medium still fits the
latency budget). Depth is not surrendered, it is relocated: a new
`[brain] deep_model` sets the model for the deep-thought agent, defaulting
to `"inherit"` and set to `"opus"` here, so the escalation valve now buys a
stronger model as well as more effort. Everything else about escalation is
unchanged — the same prompt rules, the same spoken "give me a moment", the
same per-question judgment — which is why this was one new setting rather
than a second escalation path. Fable left unwired pending a decision on
where it sits.

## 2026-08-18 — the sounds of listening and thinking

**Why.** Three silences were lying. After you spoke, nothing happened until
the brain's first sentence — one to several seconds that read as not having
been heard. When Ciel escalated to deep thought, the room went quiet for a
minute with no explanation unless the model happened to announce it. And
when she wrote "Hmm," the normalizer silently deleted it — the interjection
stripper assumed piper couldn't voice vowel-free words.

**What.** Three fixes. (1) A heard-ack: the moment a turn heads to the
brain, one of `[brain] ack_phrases` ("Hmm.", "Let me see.", ...) plays —
launched as a task overlapping the model's latency rather than adding to
it, with every later play awaiting it so voices never collide. Local
commands answer instantly and skip it. (2) Escalation is now verbalized
deterministically: the brain stream yields an `escalation` event when the
deep-thought agent spawns (detected on the wire, verified live), and if
nothing has been spoken yet that turn the pipeline says "Give me a moment.
I want to think this through properly." — skipped when the model already
announced it in its own words, so it is never said twice. (3) The
normalizer now *respells* the hum family to canonical forms instead of
stripping: measured on lessac-medium, piper renders "hmm" as a true 0.48s
hum (spelled-out letters measure 0.91s), so "Hm"/"Hmmmm"/"mhm" map onto
"hmm"/"mm-hmm"/"mmm" and are finally heard. "Shh"/"tsk"/"pfft" stay
stripped, still unverified as hums.

## 2026-08-18 — think quickly, escalate deliberately

**Why.** Effort sat at the model default (high) on the reasoning that
conversation barely pays for it under adaptive thinking. True, but it left
the depth decision entirely to the effort knob — one static level for both
"what's my name" and "which job offer leaves me ahead after ten years".
A voice assistant wants both: snappy by default, deliberate on demand.

**What.** `[brain] effort` now defaults to `low`, and a `deep-thought`
subagent — same model, `deep_effort` (default `high`), WebSearch/WebFetch —
is registered through the Agent SDK's per-agent effort support. The prompt's
new "Thinking harder" section tells Ciel to hand genuinely hard questions to
it and relay the conclusion, and to say "give me a moment" first, because a
deep pass measured at ~74 s on a real multi-step finance question and
silence that long reads as a hang. Escalation is her judgment call
per-question, not automatic: in testing she correctly answered a trick
arithmetic question directly at low effort without spawning, and spawned
when asked for real deliberation. Effort is fixed per SDK connection, which
is why escalation is a subagent rather than a mid-session switch.
`deep_effort = "xhigh"` buys more depth; `None` removes the valve.

## 2026-08-18 — local commands: the brain is not a regex

**Why.** "Ten minute timer" was paying a frontier model's latency and cost
to do what a regex does in microseconds — and mechanical requests are
exactly where seconds of thinking-pause feel most absurd.

**What.** A local command grammar (`commands.py`) runs on every transcript
before the brain sees it: plain duration timers ("set a timer for twenty
five minutes", "an hour and a half timer"), cancel ("stop the timer"),
status ("how much time is left"), and "reload" are handled entirely in the
pipeline — spoken reply included, model never invoked, session untouched.
The contract is conservative certainty: patterns fire only on whole
utterances that cannot mean anything else, with leading "hey ciel" (and
"seal", Whisper's favourite mishearing) stripped first; anything uncertain
falls through to the brain, so a miss costs the old latency, never a wrong
action. Labeled reminders and clock alarms stay brain-side on purpose —
"remind me when the rice is done" carries phrasing a model should own.
Spoken "reload" reuses the source-watcher's from-idle reload path. Local
timers still anchor "starting now" to the end of the reply, same as
brain-set ones. `[commands] enabled = false` turns the whole layer off.

Also local: the escape hatch. "Never mind", "forget it", "cancel", "stop",
"that's all" (and friends, with trailing thanks tolerated) end the exchange
instantly — any held partial thought is dropped, the follow-up window
closes, and Ciel says nothing back: the answer to "never mind" is the
absence of one, with the indicator going idle as the acknowledgement. A
dismissal is matched against the raw utterance *before* it merges into a
Cauchy-held thought ("set a timer for, um" ... "never mind" cancels the
held request rather than burying the dismissal inside it), and a dismissal
alone never arms reflection — an accidental wake ended with "never mind"
leaves nothing worth reflecting on. "Cancel the timer" still cancels the
timer; bare "cancel" dismisses.

## 2026-08-18 — timers, alarms, and the ability to speak later

**Why.** Every other capability answers inside a turn the user started. "Set
a ten minute timer" requires the opposite — speaking unprompted, minutes or
hours later — and Ciel had no machinery for it: the most-used feature of
every voice assistant had nowhere to live.

**What.** Three new tools (`set_timer`, `cancel_timer`, `list_timers`) drive
a `TimerService` (`timers.py`) that the frame loop polls: a due timer rings
— a two-note figure, deliberately distinct from the thinking chime — and
speaks its announcement the moment nothing else owns the audio (never over
Ciel's own turn, the user mid-utterance, or a Cauchy hold; at worst it waits
seconds for a turn to end). The label on a reminder *is* the announcement —
the model phrases "remind me when the rice is done" as the sentence "The
rice is done." at set time, so the fire path needs no brain call at all.
Timers persist to `~/.ciel/timers.json` and use wall-clock time, so
restarts, autoreloads, and a laptop asleep at 7 AM all keep their word;
timers that came due while Ciel was off are announced late-but-honestly
within a one-hour grace window and dropped past it. A duration's countdown
is anchored to the end of the turn's spoken reply, not to the tool call —
the call lands seconds before "ten minutes, starting now" reaches the
user's ears, and the count starts when "now" is heard, not when it was
decided. (Timers are born pending and the pipeline commits them as the
reply's audio ends; a crash in between falls back to the tool-call anchor.) This is the foundation
proactive announcements (calendar heads-up, message alerts) can build on.
Known v1 limit: an alarm rings once rather than nagging until acknowledged.

## 2026-08-18 — Ciel can look at the screen

**Why.** Natural speech points at the screen constantly — "put this event on
my calendar", "what does this error mean" — and those references cannot be
resolved from audio. Ciel's only move was to ask the user to read their own
screen aloud, which defeats the point of pointing.

**What.** New `look_at_screen` tool (`brain/tools/screen.py`) captures every
display via `screencapture`, downscales to 1568 px long edge with `sips`
(~200 KB JPEG per display), and returns the images straight into the model's
context — the Agent SDK's in-process MCP server passes image blocks through.
The prompt's new screen section sets the contract: resolve the reference from
the screen first, ask only if it isn't there, and never look unprompted.
Screen Recording permission is preflighted via Quartz because `screencapture`
without the grant exits zero with every window silently missing; a missing
grant comes back as words Ciel can relay (and triggers the system's own grant
dialog). `[screen] enabled = false` removes the tool entirely.

## 2026-08-18 — transcription moves to the GPU

**Why.** faster-whisper runs on CTranslate2, which has no Metal backend, so
every utterance was decoded on CPU cores that the rest of the pipeline also
wants. The STT protocol existed precisely so a GPU engine could slot in when
one arrived.

**What.** New `mlx-whisper` engine (`stt/local_mlx.py`) is the default; it
runs the same Whisper weights on the GPU via MLX. Measured on a 5.2 s
utterance with `small.en`: 0.24 s per decode against 1.39 s for
faster-whisper — 5.7x faster, identical transcript, and a shorter warm-up
(2.1 s vs 3.3 s). The hallucination filter moved to `stt/hallucinations.py`,
shared by both engines because the failure mode belongs to the weights, not
the runtime. Engine choice is now `build_stt` in `ciel.stt` — mirroring
`build_tts` — and falls back to faster-whisper if mlx-whisper is missing.
`[stt] mlx_model` selects the weights; `mlx-community/whisper-large-v3-turbo`
is the documented accuracy upgrade to try now that decode has ~20x-realtime
headroom.

## 2026-08-18 — thinking on the console, not through the speakers

**Why.** `speak_thinking` bundled two decisions into one switch: whether the
reasoning is audible and whether it is visible at all. Off meant the thinking
text wasn't even requested from the model — nothing on the console, nothing
in the transcript — and the only alternative was listening to every
deliberation read aloud.

**What.** New `[brain] show_thinking` (default on) streams reasoning to the
console and transcript independently of the voice. The local config now runs
show-only: reasoning is read, not heard, and the thinking chime is back on
with a new job — with silent deliberation, the tone is what tells the ears
"thinking over, answer next." The `reasoning` HUD state is reserved for
reasoning spoken aloud; show-only stays on `thinking`. "To first speech" in
the turn log now counts only sentences that actually reached the speakers.

## 2026-08-18 — split reconnect out of turn latency

**Why.** A "How are you?" at 09:17 took 80.9 seconds to first speech, after
the previous session had rotated the night before at 22:57. The existing turn
log reported one number — `turn: 80.90s to first speech` — which is
indistinguishable between a slow model and a cold client being rebuilt. The
timer in `_respond` starts *after* transcription, so wake and STT were already
ruled out; everything else was guesswork. Nothing is written to disk (logging
goes to stderr only), so there was no way to check after the fact either.

**Suspected cause, not yet confirmed.** Lazy reconnect. `Brain.ask()` calls
`_connect_locked()` when `self._client is None`, which builds a
`ClaudeSDKClient` and connects it — SDK subprocess plus every MCP server in
config (gmail and gcal both spawn via `npx`, the claude.ai connectors
handshake remotely). That whole startup is paid inside the first turn's
latency. A machine asleep overnight is exactly the case that leaves the client
dead and the next turn holding the bill.

**Changed.**

- `src/ciel/brain/agent.py`
  - `_connect_locked()` now times the connect, stores it on `_last_connect_s`,
    and logs `brain connected in %.2fs`.
  - New `_last_reconnect_s`, reset to 0.0 at the top of every `ask()` and set
    only when that turn actually rebuilt the client. Exposed as the
    `last_reconnect_s` property. Split the same way turn cost already is:
    most-recent-connect vs. what *this turn* paid.
  - Added `import time`.
- `src/ciel/pipeline.py`
  - The turn log line now reads
    `turn: %.2fs to first speech (%.2fs reconnect, %.2fs model), %.2fs total, $%.4f`.

**Note.** This is instrumentation, not a fix — the overnight wait still
happens, it just names itself now. If reconnect turns out to be the 78-ish
seconds, the real fix is reconnecting at idle (a heartbeat) rather than lazily
on the first thing the user says.
