# An event in the inbox finds its place

Implementation plan — 2026-09-08. Proposed, not implemented.

Revised against the supplied critique; see the
[review response](../reports/2026-09-08-independent-action-review-response.md).

## The result

An email confirms an appointment for 2026-09-15, 14:00–15:00 America/Los_Angeles.
Ciel identifies the commitment, checks the chosen calendars, adds one private
event to the owner's chosen destination, reads it back, and reports:
“Added your appointment for September 15 at 2 p.m.” The private detail view
links the source email and the calendar entry. Reprocessing the email or
restarting the hub produces no second appointment.

This is the first feature of [Ciel acting independently](2026-09-08-independent-action-plan.md).
That foundation owns mandates, grants, task scheduling, recovery, verification,
and reporting. This plan owns email interpretation, calendar policy, provider
adapters, and the owner's inbox experience. It replaces PR monitoring as the
first application because the owner wants email commitments handled.

The two plans have separate acceptance gates. Offline extraction fixtures
need nothing from the foundation; live read-only preview needs its first
milestone (the runner and the isolated extraction call) and no grant;
automatic calendar writes require all four foundation milestones. Do not build a second scheduler, grant system,
or task store inside this feature.

The request authorizes this plan. It does not activate inbox access, grant
standing calendar authority, authorize a dependency, or deploy code.

## Scope and defaults to settle during setup

Assume Gmail and Google Calendar for the first implementation, because the
repository already integrates both. The actual connected accounts and granted
scopes have not been inspected. Confirm the mailbox identity, destination
calendar ID, calendars to check for duplicates/conflicts, and timezone at setup.
If the owner's calendar is elsewhere, revise the adapter choice before building
the writer; do not silently substitute a Google calendar.

Use one mailbox and one owner-controlled, non-shared destination calendar.
Suggest a dedicated calendar that the owner can display beside their primary
calendar; choosing the primary calendar is also supported after its sharing
settings are checked. Calendar creation is a separate explicit action.
An event's private flag alone is not a promise that calendar administrators
cannot see it.

Start with mail arriving after activation, excluding spam, trash, drafts, and
sent-only messages. Include eligible inbound mail archived by filters, not just
unread mail. Backfill requires an explicit date range and starts in preview.
A new or changed mailbox scope creates a new checkpoint, not a silent historical
import.

Automatic additions cover confirmed appointments, reservations, and bookings
with explicit dates, start/end times, and timezone evidence. Tickets qualify only
when they express a dated commitment, not an offer to buy. Start with bounded
plain-text/HTML message bodies. Structured calendar attachments, recurrence,
multi-leg trips, PDFs, images, and remote booking-page extraction are later
extensions; recognize their presence and request review instead of guessing.

| Email or calendar situation | First-version behavior |
|---|---|
| Confirmed commitment with complete details from an approved sender, without warning signals | Add under the active standing grant and its disclosed source-policy limits |
| Unapproved sender, suspected phishing, or negative/ambiguous authentication hints | Review; never auto-add just because the text says “confirmed” |
| Invitation that still needs an RSVP or says “let's meet” | Ask whether the owner intends to attend |
| Promotion, newsletter, suggested event, or conditional offer | Ignore; retain a short decision reason |
| Missing end time, ambiguous timezone/date, or conflicting details | Ask one concrete question; save the wait |
| Matching event already exists | Link it and report already present; do not insert or RSVP |
| A plausible but uncertain duplicate exists | Ask; a similar title is insufficient proof |
| A different event overlaps | Ask whether to add; never move the existing event |
| Reschedule or cancellation | Show the matched original and proposed change; ask first |
| Owner edits or deletes an imported entry | Preserve that decision; do not silently restore it |
| Source is unavailable or access is revoked | Pause with a visible reason; do not claim the inbox is clear |

Explicit all-day commitments can follow once date-only and exclusive end-date
handling are pinned by probes. They are not a fallback for a missing time.
Relative dates are anchored to the source message's date and timezone, never
the processing date; inconsistent or unreliable anchors require clarification.
Use IANA zones and reject nonexistent/ambiguous daylight-saving wall times
unless an explicit offset resolves them. Travel events may have different
departure and arrival zones; leave them for the later travel slice.

### Setup happens in Chart, on the execution host

Use the foundation's private GrantDraft form. It shows the execution host,
connected mailbox identity, destination calendar name and stable ID, readable
duplicate/conflict calendars, IANA timezone, allowed sender addresses/categories,
start date, expiry, polling/write limits, and a complete create-only scope.
Pick accounts/calendars from adapter reads, not model-invented identifiers.
Save preview choices without granting writes; after review the broker asks once
for the exact displayed scope. Changed fields invalidate that approval.
Preview itself is the foundation's grant-less finite task: a private owner
turn asks for it, it reads and extracts under its own allowances, and it
writes nothing to any calendar.

In split mode, Gmail and Calendar must each be authorized for the hub service
account on the hub. This is an activation precondition, not an assertion that
they are authorized now. Existing Path.home() defaults resolve under the
process user's home; a connector login on the Mac does not authorize the hub.
Configure explicit connector credential paths on that host and verify identities
and required scopes there. The adapter borrows refresh tokens read-only;
only the connector's authorization flow writes them. Do not copy Mac tokens,
send them over task frames, or fall back to spoke-side execution.

Standalone uses credentials authorized for its own service user. Missing Gmail
authorization leaves only synthetic/offline preview available and shows
“Connect Gmail on the execution host.” If Gmail works but Calendar does not,
mail extraction preview can proceed but duplicate checks are marked unavailable
and automatic additions remain disabled. Reauthorization/account changes
invalidate readiness until the same pinned identities and scope are verified.

## Existing integrations and ownership

| Feature integration | Reuse and remaining work |
|---|---|
| [gmail.py](../src/ciel/gmail.py) | Borrow connector-token/HTTP conventions; add a separate inbox reader while preserving sending behavior. |
| [proactive/gcal.py](../src/ciel/proactive/gcal.py) | Reuse calendar reads and agenda refresh; keep this watcher read-only. |
| [config.py](../src/ciel/config.py) | Add feature choices in a documented dataclass; credential presence does not prove scope. |
| [task_controls.py](../src/ciel/task_controls.py) | Register email-specific mandate validation through the foundation's adapter contract. |
| [remote/chart.html](../src/ciel/remote/chart.html) | Add candidate previews and calendar/source links to the foundation's private owner view. |

The [foundation plan](2026-09-08-independent-action-plan.md) owns the shared
task store, runner, grant and broker changes, journal correlation, and durable
notification delivery. This feature supplies candidate metadata, completion
criteria, provider operations, and reconciliation evidence to those contracts.

The checkout contains other uncommitted work. One writer owns this checkout;
re-read the files and reconcile changes before implementation. Audio and
deployment configuration are outside this feature.

## The inbox mandate

Apply the foundation's broker-owned standing permission to one concrete scope:
source mailbox and approved message window, destination calendar, eligible
categories, exact sender allowlist, timezone rules, start date, expiry, daily create limit, and the
create-only operation. The authenticated owner approves this scope at setup.
Feature configuration cannot grant permission or widen an existing grant.

The adapter derives one bounded task per operation on an event from that
mandate; the event itself is a persistent feature record, not a task.
It supplies the source/candidate revision, exact calendar payload, digest,
and preconditions for foundation authorization. Email content cannot approve
an action. If an event needs a decision, save a question through the shared
owner controller; a stale response must not revive a cancelled candidate.

Updates, deletions, undo, and responses to invitations require fresh concrete
approval in v1. Undo names the exact event and its current version.
No attendees, RSVP changes, outgoing emails, or conference creation are part
of the automatic payload. Do not rely on notification flags alone to prevent
messages to other people. Keep extraction without mutation tools.

The foundation supplies dispatch fencing and reconciliation after cancellation:
pausing the inbox prevents new writes but cannot recall an API request already
sent. The inbox view must show any effect that later reconciliation discovers.

## Implementation shape

Use small protocol-backed adapters following neighboring code. Suggested new
modules are `src/ciel/email_calendar.py` for candidate records and policy
and `src/ciel/gcal_adapter.py` for the guarded Calendar adapter. The runner
belongs to the foundation. Extend Gmail with a separate
reader capability; keep sending behavior intact. Final file ownership should be
settled before edits. No new dependency is planned: use existing HTTP patterns,
stdlib email parsing and zoneinfo, and the existing brain integration.

### 1. Observe mail without losing a page

Verify mailbox identity and required scopes through the adapter during explicit
setup; credentials stay in their existing owner-only locations and are never
written back by the watcher. Pin calendar identity and access separately.
Missing/revoked access becomes a resource wait, with redacted errors.

Persist Gmail history checkpoints and message IDs. On each poll, page through
changes, durably enqueue eligible message IDs, then advance the checkpoint.
A crash may replay a page; it must not skip one. Capture an initial history
anchor and reconcile arrivals during bootstrap. Fetch bounded message bodies
only when needed. Label/read-status changes must not rerun a completed import.

Expired history requires a bounded resynchronization of the approved date
window, with deduplication and a visible backlog. Do not advance beyond work
that has not been durably queued, or describe an incomplete scan as caught up.
This follows Google's [Gmail synchronization contract](https://developers.google.com/workspace/gmail/api/guides/sync),
including the expired-history 404 case.

Use bounded requests, backoff for rate limits/transient failures, a durable
next-poll time, and per-round message limits. A poll checkpoint consumes no
model turn. Large backlogs stay visible and cannot monopolize conversation.

### Source policy for the first version

V1 does not claim independent sender authentication. Use an owner-approved
allowlist of exact sender addresses, empty by default, as a scope filter.
Display names, matching From addresses, and a passing header are not proof
that the appointment is genuine. The setup review explicitly says that an
inbox email can be forged or misleading and an automatic addition may be wrong;
the grant approves a bounded create-only policy with a receipt and guarded undo.

Mail outside that list, obvious phishing/contradictions, or negative/ambiguous
authentication hints requires review. Never treat the top Authentication-Results
header as an authoritative grant or implement an unverified header-chain trust
algorithm. Neither an allowlist nor a model classifier reliably detects phishing.
Thus a convincing forged confirmation inside the chosen scope remains a stated
v1 risk, even when all extraction checks pass. If the owner does not accept
that policy, use preview and per-event approval; do not activate automatic mode.

An empty list makes automatic mode add nothing, so the owner builds it from
what preview shows. Enrolling a sender from a candidate's review view widens
the grant: it opens the GrantDraft with the address filled in and needs the
same fresh approval as any other scope change. A one-click "always allow"
that skips the draft is not offered.

This narrows the previous vague “provider-trusted evidence” requirement rather
than claiming it is implemented. Stronger provider/provenance checks are a
later reviewed capability. Google's [authentication guidance](https://support.google.com/mail/answer/180707)
also distinguishes authentication from a guarantee that a message is safe.

### 2. Extract a candidate, not an instruction

Normalize bounded MIME text, strip active HTML, and preserve source references.
Treat subjects, sender names, bodies, and quoted threads as untrusted data.
Do not open links, load images, execute attachments, or treat instructions
inside mail as commands. Sender display names and self-asserted authentication
headers are not proof of origin. Apply the explicit v1 source policy above;
a sender match filters scope but does not certify authenticity.

Use the foundation's new `brain/extract.py:extract_json` one-shot path,
patterned on `interview/brain.py:AgentSdkBackend.ask_json`. Never call the
normal Brain response path or share an interview session. Each extraction is
a scheduled model step under the shared turn lease; interrupted and failed
calls consume attempt budget. If the isolated backend is unavailable, retain
a resource wait instead of falling back to a prompt carrying world or memory.

That isolated call produces a typed candidate:
message/thread IDs, source digest, category, commitment status, title,
start/end/zone, location, relevant source excerpts, and unresolved fields.
It has no private notebook or unrelated conversation history. Minimize mail
content sent to the configured reasoning service; explain this at setup.
“Local-first” does not mean this extraction model runs locally.

Validate the schema and cross-check required fields against cited source text.
A model confidence score cannot authorize a write. Plain text/HTML permits
model extraction; deterministic policy and source evidence decide eligibility.
Quoted older appointments cannot override a newer cancellation. Multiple
events get distinct candidates or a review wait, never one merged date.

### 3. Register durable inbox work

Register an email_calendar namespace, schema version 1, with the foundation's
FeatureRecord facility. The adapter validates and migrates its payloads:
mailbox cursors, candidate/source metadata, source-to-event mappings, decisions,
and deletion/dismissal tombstones. These are namespaced payload fields; no
Gmail-specific column or migration logic belongs in the generic task schema.

A message page commits its record write-set and derived tasks before its cursor
advances, using the foundation's bounded atomic API. Namespace registration
and migration are application code; the model cannot select versions or supply
SQL. Unknown feature versions disable inbox work without deleting its records.

The standing inbox mandate owns finite operation tasks. One event record can
own several over its life: the create that put it on the calendar, later an
update for a reschedule, or a deletion for a cancellation. Each is keyed by
the event's identity, the source revision that asked for it, and the
operation. A completed create keeps its receipt. A reschedule or cancellation
arriving after it needs an update or a deletion, which the create-only grant
does not cover, so the adapter records the foundation's inert proposal on the
event record. The owner sees the matched original and the proposed change;
approving it creates one bounded task. A reschedule that arrives while the
create is still in flight waits for that create to reconcile, then becomes a
proposal against the event as it actually exists. Completed mappings and owner dismissals outlive notification
history so older mail cannot resurrect an event. Prune bounded excerpts after resolution, keep minimal dedupe/tombstone
records while their source window can be replayed, and require preview if a
requested backfill predates retained mappings.

Register the feature's bounded read, extraction, create, and reconciliation
steps with the shared runner. Supply typed completion criteria for the current
source revision and calendar payload. Use shared persisted questions, resource
waits, and parent/child allowances. The feature does not create another brain
loop, scheduler, grant store, or notification outbox.

### 4. Reconcile before writing again

Use a stable candidate identity scoped to mailbox, source identity, destination,
and event instance. Thread ID alone is not an event identity. Across different
emails, use structured event/booking identity when available; otherwise compare
specific dates, participants/context, and location and ask on uncertainty.
Search all owner-selected duplicate calendars, including pending invitations
and events automatically added by Google. Do not claim reliable duplicate
checking if a required calendar is unreadable.

Before dispatch, persist a Google-compatible deterministic event ID and payload
digest. Create with that same ID on every retry and attach minimal Ciel
ownership/correlation metadata. Google supports caller-supplied event IDs;
private extended properties support mapping an entry back to its source.
See [event insertion](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert)
and [extended properties](https://developers.google.com/workspace/calendar/api/guides/extended-properties).
Keep sensitive source text out of those properties.

If insertion times out, recovery reads that ID before attempting another write.
An active conflict is success only if ownership and fields match.
If the bound ID is status=cancelled, persist a local deletion tombstone and
resolve the candidate as suppressed/deleted: do not retry insertion, invent
another ID, or claim it is an added event. Use the saved dispatch/mapping
identity because deleted resources may expose no ownership fields.
An unrelated cancelled ID with no local binding remains an identity conflict.

Do not claim to know who deleted or moved the event unless there is evidence.
Google says deleted events may retain only an ID and eventually disappear;
see [cancelled event semantics](https://developers.google.com/workspace/calendar/api/v3/reference/events).
Keep the local tombstone through the replay window. A later 404/410 after a
known deletion remains suppressed. A missing previously verified entry is not
permission to recreate it. Missing/unreadable state without prior deletion
evidence is a bounded reconciliation wait, never proof of non-dispatch.
An explicit restore requires a newly reviewed action against current state.
Client-chosen IDs reduce duplicate risk; they are not an exactly-once promise.

Read back calendar ID, event ID, title, times/zones, location, privacy, and
ownership metadata. Store evidence with time and remote version. Only then mark
the candidate added and atomically save its notification intent.
For approved updates/deletions, compare the remote version using conditional
requests; an owner edit must cause a conflict instead of an overwrite.
Google documents this in [resource version checks](https://developers.google.com/workspace/calendar/api/guides/version-resources).
A newer source revision invalidates an older pending approval before dispatch.

Before each insertion also search again for an independently created match.
Another client can still race between search and insert: detect the resulting
duplicate during reconciliation and request resolution; do not silently delete
either event.

### 5. Show the result and let the owner steer

Chart shows found, needs clarification, ready, adding, added, already present,
paused, failed, and awaiting reconciliation as views of durable state.
Each candidate has its source, proposed event, decision reason, destination,
and available controls. Escape mail content and links; never expose private
records through public Discord, shared replay, or an interview room.

A private notification follows verified creation: date/time/zone, calendar
link, and source link. Reuse Vigil's quiet hours and delivery policy, but keep
durable undelivered intent until acknowledged or explicitly dismissed.
Persist delivery attempts; channels with no idempotent send may duplicate after
a crash. Do not promise exactly-once notification delivery. Private Chart
remains the authoritative receipt when a transport is unavailable.

Snooze/dismiss a candidate, pause the import, and revoke its grant through the
same owner controller on supported private lanes. Notification mute does not
revoke write permission; stopping calendar work must be clearly labeled.
Use Instrument tokens and check narrow layout, keyboard focus, offline/pending
states, escaped email titles, and action conflicts.

## Configuration proposal

Add an `EmailCalendarConfig` dataclass in config.py and document it when
implemented. Proposed fields/defaults (none exist merely because of this plan):

| Choice | Proposed default |
|---|---|
| enabled / mode | false / preview |
| mailbox identity / destination calendar ID | unset; required at setup |
| duplicate/conflict calendar IDs | explicit owner selection |
| mailbox scope | approved inbound scope; spam/trash excluded |
| allowed_senders | empty exact-address list; automatic mode requires owner enrollment |
| connector paths | none added: the reader borrows `SectionsConfig.gmail_oauth_keys` / `gmail_token_file` (the section alarm owns them today; a shared Google-credential dataclass is a separate small change that keeps the alarm working) and the writer borrows `ProactiveConfig.google_oauth_keys` / `google_token_file`, set explicitly on the execution host |
| timezone | explicit IANA zone; no server-local fallback |
| poll_s / max_messages_per_poll | 300 / 25 |
| max_body_chars / max_extractions_per_day | 32000 / 100 |
| max_creates_per_day / grant lifetime | 10 / 30 days |
| backfill | none |
| supported categories | confirmed appointment, reservation, dated booking |
| notification lane | existing private owner delivery policy |

These are proposed limits, adjustable during setup. Validate them centrally.
The effective write limit is the stricter of configuration and the persisted
grant; increasing a config value cannot increase a previously approved grant.
Credentials follow existing connector-path conventions. Keep all new private
state names in FORBIDDEN_NAMES and write owner-only files.
No missing-duration default in v1.

## Feature milestones and foundation dependencies

| Feature milestone | Foundation dependency | Acceptance gate |
|---|---|---|
| 1. Preview the inbox | Offline fixtures: none. Live preview: foundation 1 (runner, isolated extraction) as a grant-less finite task; synthetic preview needs no accounts | Show a confirmed event accurately, distinguish promotions and invitations, and surface missing account access without writes. |
| 2. Remember inbox decisions | Foundation 1–2: runner, mandates, controls | Resume pagination and a saved question across restart; preserve dismissals; reject stale answers and duplicate candidates. |
| 3. Add one approved event | Foundation 1–4 complete | With per-action approval in a test calendar, exercise guarded creation, read-back, reconciliation, and the private receipt. No blind retry after remote success. |
| 4. Activate automatic additions | Foundation 1–4 complete plus explicit inbox grant | An eligible confirmation produces one verified event during the observed run; replay/restart adds none; ambiguity, existing invitations, and inaccessible calendars follow feature policy. |
| 5. Handle changes with the owner | Same foundation; operation-scoped child tasks from foundation 2; approved update/delete adapter support | Present a matched reschedule/cancellation as a proposal on the existing event record, create one task only from the owner's approval of that exact proposal, leave the original create's receipt untouched, and preserve newer source revisions and owner edits/deletions. |

Runtime implementation builds on the task-controls commit 45e4fd7. Offline
fixtures and document work can proceed at any time. Preview does not claim
live account readiness from the mere presence of paths.

Foundation milestones are defined in
[the independent-action plan](2026-09-08-independent-action-plan.md).
They are not reimplemented as inbox stages. Use a synthetic adapter to prove
shared mechanisms and this feature to prove actual email/calendar behavior.

Each implementation change gets its own README paragraph, changelog entry,
and relevant probes. Broader categories or automatic updates need a later
feature scope and separately approved grant. No deployment or account
activation is implied by completing code.

## Verification

Create `scripts/probe_email_calendar.py` and `scripts/probe_email_calendar_wire.py`
with synthetic messages, a fake clock, fake Gmail/Calendar providers, and private
temporary state. Use the repository's check-sentence style and list invariants
in their docstrings. Never load real mail, tokens, or ~/.ciel in a probe.

Pin: pagination/replay and expired checkpoints; archived versus unread mail;
promotion/invitation/confirmation distinction; hostile embedded instructions;
missing times, relative dates, DST, and source contradictions; multiple
candidates; account/calendar boundaries; existing Google-added entries;
pause/revoke/expiry/budget exhaustion; stale approvals; repeated messages;
process death around dispatch/commit/read-back/delivery; remote owner edits;
schema preservation; journal failure; and public-lane refusal. Add fixtures
for spoofed headers/approved From addresses (no authenticity claim), unapproved
senders, missing hub authorization, Chart draft revisions, namespaced cursor
transactions, cancelled resources containing only id/status, and 404 after a
retained tombstone. No duplicate insertion is allowed in those deletion cases.

Run the foundation's regression suite when this feature changes shared layers;
its plan owns those checks. Also run `probe_world.py` when calendar refresh
changes. Record measured before/after counts; do not invent target totals.
Use `uv run --no-sync python scripts/probe_email_calendar.py` and
`uv run --no-sync python scripts/probe_email_calendar_wire.py` once created.
Hub changes also require
`uv run --no-sync python -c "import ciel.hub.server"`.

After source edits, follow the repository's spoke-reload readiness check.
A separately authorized live test uses synthetic mail and a chosen test
calendar; verify creation, replay, read-back, notification, and approved cleanup.
No test sends invitations to other people.

This planning change requires only local-link/command verification and
`git diff --check`. It changes no runtime behavior and needs no service restart.
