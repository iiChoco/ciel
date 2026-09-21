# Ciel keeps the food log

2026-09-13. Revised after the owner's five supplied reviews and approval
to implement with a read-time merge. All four milestones are implemented in
the checkout as of 2026-09-14, opt-in by default. The hub rollout completed
on 2026-09-15 with the existing owner Access gate and required hub token. The
[README](../README.md#keeping-a-food-log) describes setup and current behavior.
The [design record](2026-09-13-nutrition-design.md) retains later work.

A private page at `ciel.yunhan.me/nutrition` shares one diary with Ciel's
voice and text tools. Reuse the Chart's Instrument style; reserve `/health`
for a possible broader overview. Calories lean high through a visible,
deterministic allowance. Calories and protein lead; unknown nutrients stay
unknown. Targets and manual weight tracking are optional.

## Milestone 1 contract

Deliver a useful private food log: a phone/desktop page, voice/text entry,
exact repeats, editable meal history, totals, completion, receipts, and Undo.
Use one SQLite diary on its designated host, source snapshots, validated
arithmetic, fixed diary dates, and protected private access.

**Keep these invariants.** Each reviewed change atomically commits its data,
before/after history, stable operation ID, and receipt. Retry returns the
same operation; a requested repeat creates a new meal. Repeats copy the saved
foods, portions, base values, allowance, and policy version unchanged.
Ambiguity needs clarification; a portion change or fresh estimate needs review.
A hypothetical mention never logs a meal: "maybe the same breakfast as yesterday"
is not a logging request. Every saved repeat emits a visible receipt and, on
the voice path, a spoken receipt naming the meal, diary date, calories, and Undo.
The controller delivers this from committed values, independently of model prose.

`logged calories = sum(item base calories) + one meal calorie allowance`

Compute the allowance once from explicit uncertainty reasons. Known label
values and consumed quantities start without it. Preserve source values;
a calorie allowance does not invent macros. Resolve saved snapshots and
supplied values before a bounded, cached USDA lookup; missing credentials
or provider failure still permits explicit values. No new dependency is
selected.

Store occurrence time, timezone, offset, cutoff, and diary date at Save;
travel or settings changes do not move history. Completion is an owner
assertion retained after reviewed corrections. An empty completed day needs
an explicit zero-intake assertion. Proposed cutoff: 04:00, chosen with the
initial allowance policy before the trial.

### Authority

| Milestone 1 operation | Owner authority |
| --- | --- |
| Page meal Save/edit | Explicit reviewed Save authorizes that revision; no extra broker question |
| Page per-record Delete or Undo | Explicit control authorizes the named operation, with revision checks and a receipt |
| Exact previous-meal repeat by voice/text | Explicit logging request saves the fixed snapshot, with receipt and Undo; no broker question |
| Voice/text Undo of the owner's latest ordinary operation in the same session | An unambiguous "undo that" reverses the operation directly, with a visible receipt and spoken receipt on the voice path; no broker question |
| Voice/text-proposed estimate, edit, delete, or older/ambiguous Undo | Resolve the exact proposal, then the controller calls the broker |
| Day completion/incompletion | Explicit page control or owner statement; zero intake must be explicit for an empty day |

The controller binds actions to the live private owner session/turn and checks
authority and revisions through commit. A model argument saying "confirmed"
is not authority. Routine broker calls retain the existing confirmation
setting's semantics. For direct Undo, the controller resolves the last receipt
from the same live session and verifies that it still names the owner's latest
nutrition operation, is not itself an Undo, and has not already been undone.
Revision conflicts refuse the reversal; they never select a different target.
Retries return the existing reversal receipt. Older or ambiguous requests
need an exact target before the broker question. Bulk recalculation and its
undo are later scoped operations, outside this shortcut and milestone 1.

### History and tool wiring

**Approved by the owner, 2026-09-13.** The owner instructed: "start
implementing with read time merge". Nutrition history is its durable Inverse
source, merged with the JSONL journal when actions are read. The shared
audit/undo interface satisfies the action-journal rule for nutrition; a
duplicate JSONL projection is unnecessary.

All nutrition tools stay out of `_gated_tools` and the `ActionRecorder`
watch list, including `verify_for`. The nutrition controller calls the
broker itself only for the authority-table operations needing a question.
This keeps exact repeats free of the generic confirmation and Vigil read-back.

Merge bounded journal and nutrition-history reads by time, with stable
source-qualified IDs and tie-breaking. Report unavailable configured sources
as partial/unavailable. Nutrition events route to `nutrition_undo`; no raw
database restore or copy through `ActionJournal.record` is involved.

Update `recent_actions` formatting/binding in the
[action reader](../src/ciel/brain/tools/actions.py), and name all three
required visibility/guidance changes:

- [Tool registry](../src/ciel/brain/tools/__init__.py): retain the private
  reader when either source is enabled, including nutrition with journal off.
- [Agent](../src/ciel/brain/agent.py): derive audit/undo guidance from the
  available source capabilities, replacing `undo=self._recorder is not None`.
- [Prompt](../src/ciel/brain/prompt.py): make `UNDO` source-specific, with
  nutrition undo and applicable file-snapshot instructions.

The existing JSONL journal and retention remain unchanged. Nutrition history
is mandatory when nutrition is enabled, independent of `journal.enabled`.

### Private access and doctor rule

Protect the dedicated state directory, database/sidecars, and configured
API-key basename through `forbidden_names(config)`; write owner-only files.
The designated-host binding refuses wrong-host writes. Do not silently create
another diary when the process mode changes.

`GET /nutrition` serves an empty shell. Private addressed `/ws` frames carry
data; sensitive replies stay out of shared broadcasts and the replay ring.
Require owner admission, Origin checks, and `hub.require_token = true`,
including on loopback and for all existing clients sharing that socket.
Nutrition is unavailable to public tools and interview sessions.

Use one readiness rule for runtime refusal and
[the doctor](../src/ciel/hub/doctor.py). `ciel hub --check` must report a
failing nutrition row and nonzero status when enabled without the required
policy or token. Explain how to fix client/token setup without printing the
token. Correct the loopback bind/token rows to reflect the effective policy;
"no token needed" is false when tokens are required. Nutrition off reports off.

### Milestone 1 acceptance

Add `scripts/probe_nutrition.py` and extend the existing probes for each
touched layer, using temporary state and fake providers. Pin:

- Reviewed Save/Delete/Undo and exact repeats produce receipts; retries
  deduplicate, repeats remain distinct, and concurrent stale edits fail.
- Every committed repeat delivers its visible receipt and, on the voice path,
  invokes spoken receipt delivery with committed values even if model prose
  omits an acknowledgement. Probes pin delivery, not model intent judgement.
- Same-session latest-operation Undo commits directly with its receipt and
  voice delivery; older/ambiguous targets use the broker after resolution.
  Cross-session, stale, already-undone, and Undo-of-Undo targets cannot take
  the shortcut; a retry returns its prior result without reversing twice.
- Through SDK tool dispatch, repeats invoke neither the generic confirmation
  guard nor Vigil verification. Proposed changes reach the controller's
  broker; model claims of approval cannot bypass authority.
- Data/history/receipt commit atomically; merged reads are bounded and stable,
  partial failures are explicit, and nutrition undo checks current revisions.
- Journal-only, nutrition-only, both, and neither configurations expose the
  correct reader and rendered undo guidance, with no duplicate JSONL writes.
- Allowance recomputation, conversions, unknown nutrients, cutoff/DST,
  backdating/travel, and completed-day corrections preserve their invariants.
- Whole-directory/key protection, owner-only files, wrong-host refusal,
  tokenless loopback/proxy rejection, Origin checks, private replies, absent
  data GET routes, and public/interview isolation hold.
- Runtime and doctor agree on nutrition off/on, required/missing tokens,
  loopback/proxied binds, exit status, and diagnostics without secret values.

Relevant existing probe areas are config, turns/confirmation, files/shell
guards, web/wire, doctor (currently in `probe_backfill.py`), and any changed
projection layer. Run `uv run --no-sync python -c "import ciel.hub.server"`
when hub code changes. Check phone/desktop layouts, keyboard focus, errors,
and voice-to-page consistency. Report probe counts and applicable spoke
reload verification with the implementation.

After implementation and an explicit deployment request, validate the live
Access policy and all client tokens before a private trial of about a week.
Measure first-time logging and exact repeats separately: time, unnecessary
questions, corrections, completion friction, and allowance choices. Record
which repeats would benefit from names or recipes.

## The later milestones

All eight accepted additions remain in scope; the trial is the first slice.

| Milestone | Deliverable |
| --- | --- |
| 2 — Photos and quick capture (implemented 2026-09-14) | Camera/upload, persistent photo drafts, capture timezone and library eating-time choice, protected media, cropped readable labels, PNG/JPEG, estimate explanations, and leftover corrections |
| 3 — Familiar meals and planning (implemented 2026-09-14) | Named food defaults, recipes/batches/fractions, hypothetical meal previews, and explicitly reviewed bulk recalculation |
| 4 — The dashboard (implemented 2026-09-14) | Calories versus target, estimated daily and cumulative deficit, protein/macros, optional manual weight trend, drill-down, 7/30/custom ranges, and weekly review |

One shared background runner now serves learning and nutrition photos.
Milestone 2 keeps drafts out of intake, fences analysis by draft revision and
media hash, and atomically saves the reviewed meal with its draft state.
The [photo probe](../scripts/probe_nutrition_photos.py), private socket/SDK
probe, shared-runner probes, and synthetic phone/desktop checks cover this
slice. Real camera/model quality and production Access remain trial checks.
Initial media, request, and model limits are recorded in the
[README configuration table](../README.md#keeping-a-food-log).
Milestone 3 now supplies named foods and aliases, recipe yields, immutable
prepared batches, read-only hypothetical previews, and persistent plans outside
intake. Logging a plan and consuming its planned state share one transaction
and one grouped inverse. A one-off correction never silently updates a default.

Strictly scoped bulk approval is new broker work implemented in milestone 3:
the answer names the question, reviewed digest, operation, and server-bound
initiating page session. Historical recalculation changes allowances under the
current policy, preserving source values, portions, dates, and completion.
Both it and its Undo refuse while confirmations are off, require a new private
page question, and commit all affected revisions together or none. Reviews
expire and are forgotten on disconnect/restart. Initial limits are 50 meals,
eight pending reviews, and five minutes; the broker's answer timeout can be
shorter. Saved-food/batch capacity is 1000, with 100 unconsumed plans per day.
The [library probe](../scripts/probe_nutrition_library.py) and
[planning socket probe](../scripts/probe_nutrition_planning_wire.py) pin this
contract alongside SDK, broker, configuration, and browser checks. The existing grant-approval answer
gap remains a separate follow-up. The [design record](2026-09-13-nutrition-design.md)
holds these later wiring details and their acceptance cases.

Milestone 4 now provides the range dashboard, graphs with keyboard/table
alternatives and day/meal drill-down, a trailing seven-day review, and optional
manual weigh-ins. Page weight Save/Remove uses an explicit reviewed revision;
voice proposals use the controller broker, and both keep ordinary receipts and
Undo in nutrition history. One observation per calendar date retains its kg/lb
entry; the graph uses kg and a seven-calendar-day mean of at least two observed
readings. Missing weigh-ins are not interpolated. Ranges default to seven days
and are bounded at 366; reads refuse above 5000 meals rather than truncate totals.
Review lists show up to ten examples and disclose the full count. These bounds
and the weight window are configurable in the README. The
[dashboard probe](../scripts/probe_nutrition_dashboard.py) and SDK/socket/browser
checks cover the slice. Implementation is complete; the owner authorized
"enable and push" on 2026-09-15. The hub now owns the enabled diary. Rollout
checks verified private reads, token/Origin rejection, owner-only files, all
six doctor rows, and the spoke connection. The private usability trial remains
unperformed.

Historical deficit statistics use completed days with usable intake and
owner-supplied expenditure. Gaps remain visible; a target is not expenditure.
The deliberate allowance stays visible, and cumulative deficit is never
presented as measured fat loss. Planned meals and drafts do not count as eaten.

Choose allowance policy and diary cutoff before the trial; media retention,
capacity, and background budgets before photos. Targets and expenditure may
stay unset. Scale integration, reminders, Oura comparisons, adaptive
expenditure, migration tooling, and the broader health overview are outside
this release.

Milestone 1 validation uses temporary diaries, a fake USDA transport, real
SDK dispatch, and authenticated loopback sockets. Browser checks cover phone
and desktop layouts, keyboard focus, Save, Undo, settings, completion, and
an invalid edit that keeps its form. The implementation changelog records
probe counts. On 2026-09-15 the rollout inspected only relevant configuration
and access settings, compared the spoke/hub token without exposing it, enabled
the documented cutoff/allowance defaults, and deployed an explicit nutrition
file list. An unrelated gesture edit was preserved on the server. Source and
private config rollback copies remain owner-only on the hub. No test meals,
weights, or photos were added to the live diary; the private trial remains to
be conducted by the owner.
