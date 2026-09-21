# Ciel's nutrition design record

2026-09-13. Detailed decisions retained after the owner's five supplied
reviews. All four milestones are implemented in the checkout as of 2026-09-14,
opt-in by default. The owner authorized deployment and enablement on
2026-09-15; the designated hub now serves the private diary behind its existing
Access gate and required token. Start with the
[plan and milestone 1 contract](2026-09-13-nutrition-plan.md) and
[setup in the README](../README.md#keeping-a-food-log); this reference
preserves the product rules and later implementation detail.

## The product

A private page at `ciel.yunhan.me/nutrition`, designed for phones and
desktops, shares one food log with Ciel's voice and text tools. Reserve
`/health` for a possible broader overview. Reuse the Chart's Instrument
style and owner access. Scale integration is excluded.

Calories deliberately lean high when a meal is uncertain. Calories and
protein lead the display; carbohydrates and fat are recorded when known.
Targets and manual weight tracking are optional. No personal intake target
or expenditure value has been selected.

## Logging with little friction

**Describe a meal or photograph it.** Voice, text, meal photos, and readable
nutrition labels all produce foods and portions the owner can inspect.
Camera capture and choosing an image are both supported where the browser
allows them. Ask a focused clarification when it would materially change
the result; otherwise offer an explicitly approximate entry.

The page flow is `capture → editable estimate → Save → receipt`. Save is
the owner's confirmation of that exact draft revision: there is no second
"Log this?" prompt. A photo alone establishes neither consumption nor the
amount eaten. Dates and portions are editable before and after saving.

**Snap now, finish later.** Photo drafts persist across server restarts in
a visible inbox. Distinguish local, uploading, received, analyzing, ready,
and saved states. Drafts and failed uploads contribute nothing to intake.
A receipt names the saved record; a network failure must not look like
success. A full offline application is not promised for the first release.

**Exact repeats, then named defaults.** In milestone 1, "log the same breakfast
as yesterday" resolves one previous meal and copies its immutable foods,
portions, base values, and allowance into a new consumed entry. It saves with
a receipt and Undo, with no new estimate or broker question. Ambiguous meals
need clarification; a changed portion or fresh estimate needs review. The
copied allowance and policy snapshot stay intact unless the owner requests
recalculation. A repeat has a new identity; a network retry retains the old one.
A hypothetical mention never logs a meal: "maybe the same breakfast as yesterday"
does not authorize Save. Every saved repeat emits a visible receipt naming the
meal, diary date, calories, and Undo, plus a spoken receipt on the voice path.
The controller delivers committed values; it does not rely on the model to
decide whether to acknowledge a save.

Milestone 3 adds named defaults such as "my usual coffee", using that same
exact-value save path. A one-off correction can offer to update a default,
but does not silently do so.

**Recipes and meal prep.** Record ingredients and a batch yield once; log
"one of four portions", "half a serving", or an explicitly measured fraction.
Retain raw/cooked preparation and unit bases. Each batch and consumed meal
keep their nutrition snapshot, so changing the recipe affects future use.

**Leftovers, corrections, and undo.** "I left a third" or an after-meal photo
proposes a correction to the original meal. Mixed plates may need a
per-item correction. Edit foods, portions, dates, and sources on the page
or by voice. Concurrent edits check revisions. Undo is a validated inverse
operation, with receipts and history rather than a raw database restore.
An unambiguous "no, undo that" reverses the owner's latest ordinary nutrition
operation directly when its receipt belongs to the same live session. The
controller resolves that receipt, verifies that it remains the owner's latest
nutrition operation, checks current revisions, and refuses already-undone
operations or Undo-of-Undo. A retry returns the existing reversal receipt.
Emit a visible receipt and a spoken receipt on the voice path, naming what
was reversed. Older or ambiguous requests need an exact target and a broker
question; revision conflicts never silently select another target. Bulk
recalculation and its undo keep their strict scoped approval.

**Explain this estimate.** Selecting calories shows the food match, portion,
preparation assumptions, and the meal's extra calorie allowance. The owner
can change the assumption responsible for an allowance.

**Preview a meal.** "What would my totals look like with my usual dinner?"
shows projected totals separately. Planned meals and drafts do not count as
eaten. "I ate it" begins the save flow for the actual portion.

**Mark a day complete.** Put a one-tap Complete control beside the daily
total; accept "that's everything I ate" by voice. Show "Yesterday: unmarked"
when an elapsed diary day has not been completed, with a direct review/complete
action. This is an in-page status, not a scheduled reminder.

Completeness is an owner assertion independent of individual meal revisions.
A reviewed addition or correction to a completed day keeps it complete and
recalculates totals; logging a forgotten snack does not demand a second
completion step. The owner can explicitly mark a day incomplete. An empty
day requires an explicit statement of zero intake to count as completed;
absence of records is not that statement.

**Weekly review.** Show averages, complete-day count, recurring meals, and
the entries carrying the largest calorie allowances or unresolved nutrition.
Ciel answers questions from these aggregates and their source meals.
Unsolicited weekly messages and automatic target changes are not included.

## Dates that stay put

Add `NutritionConfig.diary_day_start_hour`; propose 04:00 for the private
trial, adjustable by the owner. A 01:00 meal then belongs to the previous
diary date. Determine this from local clock time and calendar date, including
daylight-saving boundaries, rather than subtracting a fixed elapsed duration.

Record capture context explicitly: browser timestamp, IANA timezone, and
whether the image came from the camera or library. The camera flow records
the page's clock when capture returns, as a suggested time for review, not
proof of when food was eaten. Canvas re-encoding can remove EXIF; do not infer
eating time from metadata, a filename, or upload arrival. For a library photo
used to log eating, require the owner to choose when it was eaten before Save;
a draft can wait without that answer. Importing a label alone sets no meal time.

At save, resolve the occurrence time and timezone from the owner's choice
or reviewed capture context, with the configured timezone as fallback.
Apply the local cutoff and store the diary date, timezone, offset, and cutoff.
Backdated meals can specify their original timezone. These values stay fixed
after travel or settings changes. Moving a meal is an explicit edit; ambiguous
or nonexistent local times need clarification. A target or expenditure value
applies to this diary interval, not the server's date.

## Nutrition and the calorie allowance

Resolve saved foods and recipes first, then an applicable label or supplied
values, then a bounded USDA FoodData Central lookup. Cache source records and
retain the exact snapshot used by a meal. A missing key or provider failure
still allows saved foods and explicit nutrition values.

The model identifies food, preparation, and portions. Validated code performs
serving conversions, multiplication, recipe fractions, and totals. Unknown
nutrients remain unknown; an aggregate discloses incomplete coverage. A model
estimate is labeled as such. A label or a source's text is data, not authority.

Start with **one base calorie value per item and one allowance for the whole
meal**. Each item carries uncertainty reasons such as guessed portion or
unknown oil. One deterministic function computes the allowance once from
the canonical items and their reasons; repeated analysis replaces it rather
than adding another allowance. A reason describes uncertainty not already
included in the base estimate. Then:

`logged calories = sum(item base calories) + meal calorie allowance`

Keep the reasons, resulting allowance, and policy version with the meal.
A calorie-only allowance never invents grams of protein, carbohydrate, or
fat. Label-derived values with a known consumed quantity start without an
allowance. Extra margins for such entries would be an explicit later choice.

Choose a small, configurable initial allowance policy before the trial,
inspect its effect on real meals during the trial, and record revisions.
Do not invent a medical target or claim the allowance measures the actual
error. Ranges and statistical confidence displays are deferred until there
is evidence they help. New estimates use the current policy; exact repeats
retain the source's saved allowance. Existing meals change only through an
explicitly reviewed correction or bulk recalculation.

Evaluate an optional local Foundation/SR Legacy subset after observing lookup
latency, cache misses, and available hub storage during the trial. The initial
slice uses cached records and bounded API calls; it does not download a full
dataset before the first lunch. Preserve dataset identity and attribution
if a local subset is later adopted.

## The dashboard

The page has a day selector, logged totals and optional targets, completion
status, meal history, quick camera/upload/text controls, a draft inbox,
planned meals, saved foods/recipes, a weekly review, and settings. A Chart
link opens Nutrition; Nutrition links back to conversation.

| Graph | Meaning |
| --- | --- |
| Calories over time | Logged calories against an optional target, with coverage and averages |
| Estimated daily deficit | Estimated total diary-day expenditure minus logged calories; positive is deficit, negative is surplus |
| Cumulative estimated deficit | Subtotal across eligible days, showing included-day count and gaps |
| Protein and macros | Known daily amounts, optional targets, coverage, and averages |
| Weight trend, optional | Manual weigh-ins and a smoothed observed trend |

Provide 7-day, 30-day, and custom ranges. Selecting a point opens its day or
meal evidence. Include text/table alternatives and keyboard access.

**A target is not expenditure.** Start with an owner-entered daily expenditure
estimate, effective from a recorded diary date. Without a configured value,
intake graphs work and deficit graphs explain what is missing. Ciel does not
invent a burn estimate. Later wearable data must align with the diary interval
and distinguish total from active energy without double-counting exercise.

**An unfinished day is not a deficit.** Historical deficit totals and averages
include completed days with usable calories and expenditure. Partial intake
remains visible as "logged so far". Missing days are gaps; cumulative subtotals
disclose coverage and do not imply continuity over unknown days. The current
unfinished day is not treated as though its eating is finished.

The deficit is always estimated. The separate allowance makes clear why a
higher logged intake produces a lower calculated deficit for the same burn.
Cumulative calories are not presented as measured body fat lost. Oura
comparisons and adaptive expenditure remain later work; inflated intake must
not be silently treated as unbiased input to an adaptive burn estimate.

### Milestone 4 implementation, 2026-09-14

`nutrition_dashboard.py` reads the graph and its seven-day review in one fenced
worker callback from stored meal values. Settings remain effective-dated. Each
nutrient average requires complete usable days with full coverage for that
nutrient; the graph still displays partial known amounts with coverage. A day
whose last meal was deleted remains complete but unusable until the owner
asserts zero intake or saves a meal. Future days never enter historical averages
or deficits. Cumulative points are null at gaps and resume a subtotal with
included-day counts; no energy-to-fat-loss conversion is provided.

Weekly evidence groups recurring names without claiming identical portions,
shows the largest allowances and missing nutrient values, and links to exact
meal IDs/revisions and diary dates. The weekly period always ends on the chosen
range's last date, including when the visible graph is shorter than seven days.
Graph/table alternatives and point keyboard activation use the same private
values as the SDK reader, with no external chart library or public data route.

Manual weights use the existing versioned `records`/history format, one reading
per explicit calendar date. Retain entered kg/lb values and normalized kg; no
schema-format change is required. Page Save/Remove authorizes its revision;
voice proposals ask the controller broker. Ordinary Undo, retry identity,
owner authority and history apply. A trailing observed mean appears only on
measurement dates with at least two readings inside the configured calendar
window, including eligible observations before the visible range. There is no
interpolation, invented weight, calorie-derived weight, or adaptive target.

Initial bounds are 366 graph days (hard maximum 366), 5000 source meals for the
graph and review (hard maximum 10000), ten examples per review list/group (hard
maximum 25), and a seven-day weight window (hard maximum 90). Each is a typed
nutrition setting checked by runtime and doctor. Excess meal reads refuse with
an instruction to shorten the range, without partial totals. List truncation
is explicit and does not affect aggregates. Readings and their Undo stay in
nutrition's approved Inverse source; no duplicate JSONL copy is introduced.

## Storage, media, and execution

**SQLite owns the diary.** Use `nutrition.sqlite3` under
`NutritionConfig.state_dir`, defaulting to `<brain state>/nutrition-state/`.
Follow [TaskStore](../src/ciel/tasks.py) and Python's standard-library SQLite:
one active runtime writer, bounded transactions off the event loop, schema
versions, revision checks, unique operation IDs, receipts, and history.
Use rollback-journal mode initially; protect all sidecar names defensively.
Derived totals are queries over stored snapshots, not model arithmetic.

Records cover meals/drafts, immutable item values, the meal allowance,
versioned defaults/recipes/batches, completion assertions, effective-dated
targets and expenditure, optional weigh-ins, media references, and receipts.

**Nutrition history is Inverse's nutrition journal.** A committed owner change
stores its validated before/after history, stable operation ID, and receipt
in the same SQLite transaction. Failure rolls them all back. Autosaves,
analysis imports, and intermediate edits remain draft revisions outside the
owner's action history. One reviewed Save produces one owner operation.

**Approved by the owner, 2026-09-13.** The owner instructed: "start
implementing with read time merge". Nutrition history is its durable Inverse
source, merged with the JSONL journal when actions are read. The shared
audit/undo interface satisfies the action-journal rule for nutrition; a
duplicate JSONL projection is unnecessary.

Under the owner's approval, nutrition's transactional history is a first-class source
for Inverse, mandatory whenever nutrition is enabled. A shared audit/undo
interface reads both durable sources. Copying nutrition events into JSONL
would add failure windows without improving the durable undo record.

Extend `recent_actions` with a bound private nutrition-history reader.
Read bounded recent rows from both sources, normalize their timestamps and
source-qualified IDs, and merge by time with a stable tie-breaker before
applying the requested count. Include both page and voice actions. Preserve
the existing journal's entries and snapshot retention unchanged. Bind and
offer the reader when either source is available; a configured source that
cannot be read is reported as unavailable or partial, never silently empty.

Each nutrition entry names its diary, affected records, operation ID, and
`nutrition_undo`, with no database or image snapshot path. The dedicated
undo controller reads that history and commits a revision-checked inverse.
Older meal-specific history remains inspectable beyond the recent list.

All nutrition tools stay out of `_gated_tools` and the generic JSONL
`ActionRecorder` watch list, including its `verify_for` set.
`ConfirmToolGuard` asks on every gated invocation; that would break exact
repeats and invite Vigil's read-back verification. The nutrition controller
checks private owner authority itself and calls the broker only for the
operations marked as needing a question in the authority table. Excluding
the generic gate does not remove the controller's commit-time checks.

Update `recent_actions` formatting and binding plus all three availability
and guidance touchpoints in milestone 1:

- [Tool registry](../src/ciel/brain/tools/__init__.py): stop removing the
  reader solely because `journal.enabled` is false. Offer it to private
  owner sessions when either history source is enabled, bind the applicable
  readers, and report configured-source failures explicitly.
- [Agent prompt wiring](../src/ciel/brain/agent.py): replace
  `undo=self._recorder is not None` with source-aware audit/undo capability.
  Nutrition history must enable its guidance even without a generic recorder.
- [Undo prompt](../src/ciel/brain/prompt.py): replace the file-only `UNDO`
  guidance with source-specific instructions. Nutrition events route to
  `nutrition_undo`; journal file snapshots keep their existing restoration
  guidance only when that capability exists.

Check journal-only, nutrition-only, both, and neither configurations, including
reader visibility and the rendered prompt. Do not copy nutrition events through
`ActionJournal.record`. The existing journal's UUID generation, retention,
and write path need no new idempotency mechanism.

**There is one authoritative diary.** The current split setup's hub is its
home. Milestone 1 records a diary ID and designated owner host and refuses
writes from the wrong host; switching process mode never initializes a second
diary silently. Migration tooling is deferred until requested and has no
first-release migration probe. A later migration must stop the old writer,
transfer a consistent SQLite backup, media, history, and receipts, verify
the result, then change the ownership binding. No automatic merge is planned.

**Confirmation belongs to the input route.** The owner's second review
refines the [note-save precedent](../src/ciel/notes.py):

| Operation | Authority |
| --- | --- |
| Page meal, default, or recipe Save | The explicit reviewed Save authorizes the exact revision; no broker prompt |
| Page delete or ordinary Undo | An explicit per-record control authorizes the named operation, with revision checks and a receipt; no extra prompt |
| Exact previous-meal repeat or saved-food voice/text shortcut | An explicit owner logging request saves the fixed saved values with receipt and Undo |
| Voice/text Undo of the owner's latest ordinary operation in the same session | Resolve the last receipt in the controller; reverse directly if current and not already undone or itself an Undo, with visible and voice-path spoken receipts |
| Voice/text-proposed estimate, edit, delete, default/recipe change, or older/ambiguous Undo | Resolve the exact target/proposal, then one broker question |
| Bulk historical recalculation or its undo from any route | Reviewed scope/digest and a scoped human broker answer; unavailable while confirmations are off |

Snapshots keep page recipe/default edits from rewriting consumed meals.
The controller binds UI actions to the live private session and revision;
a model argument saying "confirmed" supplies no authority. All paths,
including Undo, check authority through commit. Automatic draft work never
marks food eaten.

**Milestone 3 implementation decisions, 2026-09-14.** Saved foods/aliases,
recipe yields, immutable prepared batches, previews, persistent plans, and
historical allowance recalculation are implemented. Whole-yield fractions and
compatible mass/volume units use validated arithmetic under the saved policy.
A separate Save updates a default after a one-off correction. Plan consumption
and its meal share one grouped history record and atomic Undo; projections
read planned and consumed values together. Schema version 3 marks grouped
history, and opening photo storage cannot downgrade it.

Historical scope means the explicitly reviewed meal IDs/revisions within a
chosen diary-date range. Only allowances are recomputed under the current
policy: source lookup and recipe edits never silently rewrite eating history.
Defaults are 50 meals per review, eight live review slots, and a five-minute
review lifetime, shortened by the broker's configured answer deadline. The
library allows 1000 active food/recipe/batch records and 100 unconsumed plans
per day. These configurable bounds are implementation choices for the trial.
Both bulk apply and its Undo require the new private broker scope and a fresh
answer on the initiating page. Approval is checked through commit; all affected
meal revisions change together or the operation rolls back. Reviews/approvals
are ephemeral and cannot survive disconnect or process restart.

**Scoped answers are new broker work.** The existing grant-approval flow
provides addressed question delivery: `WebLink._run_task_request` sends to
one socket and `VoiceConfirmBroker.ask_through` asks through that callback.
It does not bind answers to that socket. Ordinary `say`/keyboard answers
can be accepted by timing, and `answer` permits missing IDs. Reuse only the
addressed sending mechanism; do not describe answer validation as existing.

Implement an opt-in strict question scope for nutrition bulk recalculation:
operation, confirmation ID, reviewed digest, initiating client/channel, and
expiry. Store it inside the broker and require it on every answer path.
Render the request on `/nutrition` itself. Identify its answers over private
frames and deny on disconnect, cancellation, or scope mismatch. Reject
ID-less answers, another tab's answer, and generic keyboard/chat Yes while
that strict question is pending; never replay approval after reconnect.

**Bulk recalculation requires confirmations to be on.** When
`confirm.ask_first = false`, refuse the operation with a clear explanation
before making changes. The strict broker mode must not call
`_answer_myself`; check the setting at entry and before commit, and discard
approval on restart. This applies to page, voice, and text requests. For an
unsupported voice source that cannot carry the strict question identity,
direct the owner to the page instead of accepting an unscoped answer.
Routine operations keep the existing confirmation setting's semantics.

The existing grant-approval answer-correlation gap is a separately scoped
follow-up. All its clients are already owner clients, which limits the risk,
but addressed question delivery is not answer ownership. Nutrition's new
opt-in scope does not claim to fix legacy grant approvals. Record that
follow-up here; do not silently broaden this implementation into that fix.

**Milestone 2 implementation decisions, 2026-09-14.** Photo capture, draft
review and persistence, bounded extraction, protected media, a reviewed
fraction eaten, and the shared learning/photo runner are implemented. The
initial defaults are 20 MB source files, 16 million pixels, a 1568-pixel crop
edge, and a verified 2 MB encoded image. Retention is 30 days within 250 MB;
200 unfinished drafts and 20 analysis requests per rolling 24 hours bound
the inbox and queue. Each job permits two durably charged model calls sharing
$0.25; a call gets at most 45 seconds and the smaller task deadline/budget.
These configurable starting values are implementation choices for the trial,
not measured photo quality. There is no new dependency. Both PNG and JPEG
cross the real socket and SDK boundaries in synthetic probes. The browser
flow is checked on phone/desktop sizes with synthetic images; live capture,
real extraction quality, and deployment policy are still untested.

**Photos have their own home.** Nutrition-page uploads go over the authenticated
socket directly into `nutrition-media/` inside the protected nutrition state
directory, using the existing upload validation/bounds as the construction
pattern. Do not stage them in the generally readable Chart uploads folder.

When the owner reuses an existing Chart attachment, import a bounded, verified
copy by admitted attachment ID. Do not move it: the Chart retains IDs and paths
that another turn may use. That already-shared original keeps its existing
Chart permissions and 14-day cleanup; the nutrition copy is independently
protected and retained. Do not claim that importing it erases the original.

Use stable media IDs, hashes, and atomic owner-only files. Receipt the draft
only after its media reference is durable; imports and uploads are idempotent.
Nutrition retention covers drafts, logged photos, and unreferenced imports.
Expiry leaves an image-unavailable state without deleting numeric records.
A crash between file creation and database commit leaves a recoverable import
or identifiable orphan, never a false receipt.

**Labels are cropped before shrinking.** Offer crop-to-label and a readable
preview before applying image compression. The current Chart has a 1568-pixel
edge and a 700,000-byte shrink trigger; the latter is not a guaranteed output
size cap. Keep explicit nutrition upload/extraction byte limits and verify
the output against them. Begin with cropping; raise a label-specific limit
only if evaluation justifies it. Unreadable text stays unresolved and can
be entered manually; do not guess digits to complete a label.

**One background runner serves the adapters.** Refactor the learning-specific
runner in `Pipeline` into one background runner built whenever the background
adapter list is nonempty and `[tasks].enabled` and `[tasks].runner` are true.
Put the learning adapter there when `learning.background` is enabled, and
the nutrition-photo adapter there when photo analysis is enabled. Use one
`TaskRunner(background=True)`, one `private_lease()`, and one isolated
`AgentSdkExtractor` for that list. Do not create a second nutrition runner.

Update construction, both hub/local loop ticks, ladder-operation exclusions,
execution availability, roster labels, and shutdown to use that shared runner.
Its existing one-step-at-a-time lifecycle serializes learning and nutrition
analysis; adapters must not launch detached extraction calls. Keep per-job
time/cost limits too: shared concurrency alone is not a spending limit.

Register the nutrition job namespace with `TaskController` before its store
opens, whenever nutrition and tasks are enabled, even if the runner is paused.
Bind nutrition job controls and summaries independently of learning. The
namespace contains jobs/proposals; `nutrition.sqlite3` remains the diary.

Photo analysis requires both task settings above. Without them, base logging
and saved drafts still work and analysis explains that it is unavailable.
Page and conversation submissions enqueue a job; a conversational tool never
calls `extract_json` recursively while holding the brain's lease.

A job names the draft revision and media hash. The adapter calls
`StepContext.extract` with byte, image, time, and cost limits and returns a
schema-validated proposal. The task store fences the result under its attempt;
the nutrition controller then imports it idempotently against the expected
draft revision. No cross-database transaction is assumed. Old, cancelled,
or superseded results cannot replace newer edits or log food.

The shared private lease allows extraction alongside speech and does not
automatically cancel on human input. Provide explicit job cancellation and
a queued state when learning has the current step. An unavailable runner leaves
the draft pending with an explanation, never a nested-model fallback.

The Chart uploads JPEG while the current extractor accepts and emits PNG
only. Extend the validated image path to admit bounded JPEG as well as PNG
and emit the matching MIME type, preserving existing PNG callers and probes.
Photo transport is not complete until this boundary is exercised end to end.

**Protect the whole state directory from the first slice.** Add
`nutrition-state`, `nutrition.sqlite3`, and its `-journal`, `-wal`, and
`-shm` sidecars to `FORBIDDEN_NAMES`. Protect configured state-directory
basenames through `forbidden_names(config)` too, so file tools and shell
guards cannot bypass the controller with `workspace = "~/"`. Require a
dedicated directory; do not allow an override to an ordinary shared folder.
The nutrition service and extractor read admitted media through bounded code,
not general model file access.

Define `NutritionConfig.fdc_api_key_file`, defaulting to
`~/.ciel/nutrition-api-key`, and protect both the default and configured
basename. The value never enters prompts, query logs, or README examples.

**Browser data travels only on the authenticated socket.** `GET /nutrition`
serves an empty application shell. Records, reads/writes, uploads, image bytes,
and broker requests use private, addressed `/ws` frames with bounded requests;
there is no media/data GET or static-directory mount. Browser images use
short-lived object URLs from received bytes. Sensitive replies do not enter
the shared replay ring; reconnects reauthenticate and re-fetch by request ID.

Require `[hub].require_token = true` whenever nutrition is enabled; fail
nutrition setup closed if it is absent. This also applies on loopback and
to existing clients sharing `/ws`, including the spoke; document token setup.
Explicit Origin checks and private owner admission remain required. No
forwarded header or claimed client role substitutes for the token.

In milestone 1, extend [the hub doctor](../src/ciel/hub/doctor.py), used by
`ciel hub --check`, with a nutrition readiness row. When enabled, report a
failing row and nonzero status if `require_token` is false or the required
token is unavailable, with the setting and client setup steps to remedy it.
Show token presence or a path, never its value. Correct the existing loopback
token row too: it must not say "no token needed" when the effective policy
requires one. Nutrition off is reported as off. Use the same readiness rule
for runtime refusal and diagnostics so they cannot disagree. This local
check does not assert that the deployed Access policy is correct.

Existing task wiring already forces tokens on some non-loopback binds, but
that is not a nutrition guarantee and current deployment settings have not
been inspected. Before an authorized deployment, verify the actual Access
policy keeps `/nutrition` and `/ws` under owner access and that the
`/interview` exception matches neither. This plan does not change that policy.
Nutrition remains excluded from public tools/projections and interview sessions.

No new dependency is selected. Final retention, capacity, and allowance
defaults are set before their milestone, not left implicit in implementation.

## Delivery reference

The [main plan](2026-09-13-nutrition-plan.md#the-later-milestones) owns the
milestone order. Exact repeats, the merged reader, controller confirmation
wiring, and doctor diagnostics belong to milestone 1. Photo transport and the
shared runner belong to milestone 2; named foods, recipes, previews, and
strict bulk scopes landed in milestone 3; graphs, weekly review and optional
manual weights landed in milestone 4 on 2026-09-14.

## Acceptance and remaining choices

Add `scripts/probe_nutrition.py` and extend existing probes for touched
layers. Use temporary state and fake providers/extractors. Pin:

- One reviewed page Save/Delete and milestone-1 exact previous-meal repeat,
  with receipt and Undo; a repeat copies the source's allowance rather than
  silently re-estimating it.
- Every committed repeat emits a visible receipt and invokes spoken receipt
  delivery on the voice path with committed meal/date/calorie values, even
  when model prose omits the acknowledgement. This verifies delivery, not
  the model's ability to distinguish hypothetical mentions reliably.
- Same-session latest-operation Undo bypasses the broker and emits its visible
  and voice-path spoken receipts. Older/ambiguous targets use the broker after
  resolution; cross-session, stale, already-undone, and Undo-of-Undo targets
  cannot use the shortcut. Retries return the existing reversal receipt.
- Through the SDK tool-dispatch path, exact repeats trigger neither
  `ConfirmToolGuard` nor Vigil verification; proposed estimates/edits/deletes
  reach the controller's broker call under the authority table.
- Atomic meal/history/receipt commits and stable operation IDs; bounded,
  deterministic merging of both action-history sources, partial-source
  reporting, zero nutrition copies through the generic recorder, unchanged
  JSONL retention, and undo through nutrition history rather than raw files.
- Journal-only, nutrition-only, both, and neither configurations expose the
  right reader and source-specific undo prompt. Nutrition-only has working
  history and `nutrition_undo` guidance without an `ActionRecorder`.
- In milestone 3, strict bulk scopes refuse cross-tab, keyboard, ID-less,
  stale, and replayed answers, and refuse the operation when confirmations
  are off; legacy grant behavior is outside that fix.
- Retries versus repeats, SQLite conflicts, stable recipe history,
  portions/leftovers/unknown nutrients, and allowance recomputation.
- Camera context versus library-time selection, cutoff, backdating,
  daylight-saving/travel, completion retained on edits, and graph coverage.
- Diary identity/wrong-host refusal, with no migration tooling required.
- Media import/upload/expiry/retry, label crop and byte bounds, PNG/JPEG,
  and whole-directory protection under a home-wide workspace.
- Nutrition analysis with learning disabled, both adapters sharing one
  background step, runner-disabled recovery, namespace registration while
  paused, cancellation, stale proposals, roster behavior, and shutdown.
- Tokenless loopback/proxy sockets refused; data/media GETs absent;
  authentication, Origin, private frames, and public/interview isolation.
- Doctor/readiness agreement for nutrition off/on, loopback/proxied binds,
  required/missing tokens, and diagnostic text without secret values.

Deployment validation on 2026-09-15 checked the live owner Access policy for
`/nutrition` and `/ws`, healthy tunnel routing, spoke/hub token equality, all
six hub doctor rows, private directory/database modes, live read-only diary
and photo-draft replies, and tokenless/unrelated-Origin rejection. The spoke
reconnected. These are rollout checks, not claims about the earlier planning
review or model judgment. The documented 04:00 cutoff and 15/10/10 percent
portion/oil/estimate policy are the initial enabled settings. No synthetic
records were added to the live diary; the owner's usability trial remains.

For each source milestone run the nutrition and affected config/doctor,
web/wire, turn authority/confirmation, action-history/undo, task-runner and
extraction, and any world-projection probes. Run the required hub import check when hub code
changes. Verify narrow/desktop layouts, keyboard focus, camera fallback,
graphs' text alternatives, error states, and voice-to-page consistency.
Report real photo evaluation separately from synthetic probes and confirm
the spoke reload after source edits when applicable.

Before the trial, choose the initial allowance mapping and diary cutoff.
Targets and a burn value can remain unset until the owner supplies them.
Before enabling photos, review the initial media retention and background
resource limits recorded above.
Manual weight tracking remains optional; a local food dataset is an option
to evaluate after the trial, not a dependency of logging the first meal.

Both implemented milestones are checked with synthetic probes and a
temporary browser fixture; counts live in the implementation changelog.
Live USDA, microphone/model intent, client credentials, and the Access
policy have not been evaluated. The private trial follows a separate
deployment request; the existing spoke's reload check is not deployment.

## References

- [Extension points](../README.md#extending-it),
  [hub/spoke](../README.md#two-processes-hub-and-spoke), and
  [Chart](../README.md#the-gui-chart).
- [Tool registry](../src/ciel/brain/tools/__init__.py),
  [task runner](../src/ciel/task_runner.py),
  [runner wiring](../src/ciel/pipeline.py),
  [extraction](../src/ciel/brain/extract.py),
  [uploads](../src/ciel/remote/web.py),
  [configuration](../src/ciel/config.py),
  [action-history reader](../src/ciel/brain/tools/actions.py),
  [tool guard](../src/ciel/brain/toolguard.py),
  [agent wiring](../src/ciel/brain/agent.py),
  [undo prompt](../src/ciel/brain/prompt.py),
  [file guards](../src/ciel/brain/permissions.py),
  [confirmation](../src/ciel/confirm.py), and
  [journal](../src/ciel/journal.py).
- [USDA API guide](https://fdc.nal.usda.gov/api-guide/) and
  [downloadable data](https://fdc.nal.usda.gov/download-datasets/).
- [Cronometer recipes](https://cronometer.com/blog/how-to-recipes/) and
  [MacroFactor on partial logging](https://help.macrofactorapp.com/en/articles/241-what-is-partial-logging).
