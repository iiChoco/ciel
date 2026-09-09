# Atlas stays connected to the work

2026-09-09. Implementation plan; no new behavior is active.

Ciel should know where an ongoing piece of work lives, which sources belong
to it, what changed, and what the evidence says about its current state.
“Pull it up,” “where did I leave off?”, and “get the latest one” should work
across conversations without the owner repeating the same paths and context.

This plan extends Atlas with reusable project awareness. Homework is the
first application: its source, LaTeX reader, retrieval behavior, and acceptance
checks are included alongside the shared architecture and implementation
sequence.

## What the capability knows

For a named project, Ciel remembers five things:

- **Meaning:** the owner's names for it, its purpose, and which part is current.
- **Places:** its local files, remote sources, and the relationships between
  them, such as working draft, reference, published version, or deliverable.
- **Evidence:** what it last read, the exact revision, and when it checked.
- **Expectations:** what the owner is trying to finish, and any known criteria.
- **Responsibility:** whether to check on demand or keep watching, and which
  retrievals or actions the owner has authorized.

These arrive through ordinary conversation and bounded discovery of the
named files and sources. Known mappings persist. Ambiguous matches need a
clarification; inferred mappings remain distinguishable from owner statements.
The user does not fill out a generic workflow form or maintain a manifest.

Examples illustrate the same behavior:

| Request | Context | Evidence of state |
|---|---|---|
| “Pull up my analysis homework” | Course folder, assignment source, active set | Questions and subparts with written answers |
| “Where did I leave off on the proposal?” | Draft, brief, references | Sections drafted, missing requirements, unresolved comments |
| “Get the latest data for this analysis” | Notebook, named dataset source, local versions | Source revision, local copy, which revision the analysis used |

These examples share identity, sources, revisions, observation, retrieval,
and a private answer. Their completion criteria differ. A generic engine
must not equate more words, a file save, or a newer download with completion.

## Fit with Ciel

[Atlas](../src/ciel/projects.py) remains the durable project notebook.
Extend its connection to the real work rather than introduce a competing
project list. Preserve its existing prose and dated log. Its current
name-based identity needs a backward-compatible stable binding ID for
observations; a project rename must not orphan its tracked files.

The [independent-action foundation](2026-09-08-independent-action-plan.md)
owns mandates, task lifecycle, authorization, scheduling, recovery, and
receipts. This feature supplies sources, resource selection, readers, and
claims about project state. A project can exist without a standing task;
opening or remembering one never implicitly starts monitoring it. The
existing `open_project` tool reads the notebook; document opening needs an
explicit action rather than silently changing that tool's meaning.

The [task runner](../src/ciel/task_runner.py) and
[isolated extractor](../src/ciel/brain/extract.py) already support bounded
read steps. Standing grants, derived-task admission, and authorized mutation
dispatch are still foundation work. On-demand previews can be built first.
Continuous observation must use the foundation's admitted derived reads;
automatic retrieval must wait for its authorized action path.

[The existing work watcher](../src/ciel/proactive/work.py) watches for file
appearance or process exit. Preserve those one-shot semantics. Add reusable
resource-change observation, not one permanent watcher implementation per
kind of project. Vigil owns notification policy, not project analysis or a
second task queue.

The Mac owns local sources, snapshots, downloads to local disk, and editor
opening. The hub owns records, extraction, and conversation. Use the existing
local/spoke binding pattern; server filesystem tools must never stand in for
an unavailable Mac. Both halves validate paths and action authority.

## Small shared model

Keep structured observations in a versioned project namespace in the task
store, linked to Atlas. This is machine-owned state alongside the notebook;
background extraction must not overwrite the owner's narrative.

| Record | Shared contents |
|---|---|
| Project binding | Stable Atlas identity, aliases, scope, selected resources, owner preferences, observation state |
| Resource | Stable identity, source, locator, role, related resources, preferred opener |
| Observation | Resource revision/hash, snapshot time, received time, freshness, coverage and failure state |
| Assessment | Claims about state, supporting revision/locations, uncertainty, reader/schema version |
| Expectation | Owner-supplied or sourced requirements, scope, provenance and optional domain structure |
| Declaration | Owner corrections or assertions, time, and what they supersede |

A source location is not proof of provenance or a permission grant. A fact
learned from a document cannot widen scope. Store the distinction between
observations, interpretations, and owner declarations. Unknown requirements
permit “here is what changed,” but not an invented completion percentage.
Domain-specific details belong in validated reader output: problem numbers
for LaTeX, sections for a proposal, dataset versions for an analysis. The
shared task schema gets no course, assignment, or question columns.

Existing Atlas entries continue to load without bindings. Bind lazily when
resources are connected; migration must preserve prose, status, and history.
Unsupported record/reader versions leave observations intact and mark refresh
unavailable rather than reinterpret them silently.

## One reusable loop

1. **Resolve.** Match the request to a project and resource role. Prefer an
   explicit selection, then a saved current selection, then source evidence.
   “Latest” is source-specific, not a global filename/mtime rule. Ambiguity
   produces a useful question and persists the answer.
2. **Observe.** Read bounded, stable revisions of the selected sources.
   A watcher detects changes and proposes eligible work; it cannot authorize
   itself, call the model directly, or perform an external action.
3. **Interpret.** A reader extracts structure and evidence from the snapshot.
   Use deterministic parsing where useful and isolated model extraction for
   semantic interpretation. Validate returned claims against supplied evidence.
4. **Remember.** Commit only if the project binding, mandate, and source
   revision are still current. Preserve the previous reading at its true age
   on failure; show newer work as pending or unavailable.
5. **Respond or act.** Answer from the reading, open the requested resource,
   or retrieve a missing/new version under the existing action contract.
   Ordinary background refreshes stay quiet.

Readers need only bounded snapshots, declared context, and an evidence schema.
Sources provide identity, revision discovery, and content access. Action
adapters provide concrete acquisition/open operations and reconciliation.
Register a few explicit implementations using the existing protocol/factory
style. Do not build a workflow language, universal ontology, or dynamically
loaded plugin framework to support the first examples.

## Observation mechanics

Watch only registered sources within the mandate. Local filesystem observation
runs off the audio loop, debounces saves, and hashes stable contents. Ignore
format-specific build noise through reader rules. Identical content causes no
new model call. Dependency changes count: a LaTeX include or a linked input
can alter meaning while the main file stays unchanged.

Handle atomic replacement, rename, deletion, sleep, and restart. Source
adapters state which identities survive moves; do not assume arbitrary
cross-volume renames are provable. Rescan at startup and reconnect, preserve
pending receipts, deduplicate replay, and coalesce revisions. A new save
invalidates an older in-flight result. Missing or unreadable sources mean
unknown, not erased work or finished work.

Each assessment records the complete revision set it depended on. Refreshing
one dependency invalidates dependent claims, not unrelated project records.
Bound counts, bytes, dependency depth, execution time, and model budget.
Parsing treats documents as data and executes no embedded code or macros.
Unsupported formats can still be located and opened; reading progress must
report unsupported coverage instead of pretending to understand them.

Model work uses the shared lease, yields to human input, and is schema-checked
in isolation without tools, credentials, or the owner's full notebook. Old
line numbers remain attached to old revisions. Pausing observation, changing
scope, or revoking a mandate fences late results and queued work.

## Retrieval and authority

“Get the missing/latest resource” resolves against its registered source and
role. Local presence and source freshness are separate facts. A working draft
is not replaced by a downloaded published copy merely because the names match.
Track remote identity, source revision, local content hash, and lineage.

Bound time, bytes, redirects, and destinations. Validate response status and
content type; login HTML is not a PDF or dataset. Use authenticated adapters
where needed and report expired access. Download through an owner-only staged
file and install atomically without overwriting existing work. Keep revised
source copies distinct and reconcile after uncertain completion before retrying.

Proof Obligation owns per-action approval or scoped standing grants, and
Inverse records the result. A grant can cover acquisition from selected
sources into selected folders without repeating the question every time.
Reader output cannot mint or widen it. Recheck grant and target on dispatch,
including on the spoke. Background acquisition, if expressly covered, need
not open windows; opening follows an attended request. Submitting, sending,
or publishing remains a separate action requiring its own authority.

All resources, prompt projections, notebook additions, assessments, tools,
and notices remain in the private owner lane. Use a compact private project
index and load detailed evidence on demand. No full-home crawl is needed.

## First application: homework

Use the shared bindings, observation, evidence, retrieval, and task lifecycle
for coursework. The following details belong to the first reader and source;
there is no separate coursework runtime.

### Real layout and remaining setup

`~/Berkeley/Math` contains `H104`, `110`, and `191`, with files such as
`hw01/hw01.tex` and a sibling PDF. H104 is the candidate for “analysis,”
pending owner confirmation and identification of the current term. The owner
saves LaTeX. The assignment source and editor preference are still unknown.

Register the course as an Atlas project, its familiar names as aliases, the
folder as a local source, and the actual assignment page/service as a remote
source. Resource roles distinguish the assignment handout, working solution,
and rendered solution PDF. A saved current selection identifies the homework;
a folder name or newest modification time does not establish current work.

### Expected behavior

- “Pull up my analysis homework” opens the selected working `.tex` document
  on the Mac. If it does not exist, open the handout. An explicit request for
  the PDF selects that role. Ambiguous current assignments ask once.
- If the handout has not been downloaded, retrieve the identified assignment
  from the registered source, install it without overwriting local work, verify
  it, and open it. Revised handouts remain distinct. A download does not create
  or edit a solution document. Use the shared authorization and journal path.
- After a saved revision settles, refresh progress silently. “Where did I
  leave off?” names the latest reading's age and unfinished questions/subparts,
  with evidence. A new edit during analysis makes the old result obsolete.
- Owner statements such as “this is finished,” “I submitted it,” “use this
  folder,” and “stop tracking analysis” persist through the shared controls.
  Finished, submitted, and mathematically correct remain distinct claims.

### LaTeX reader

The observed source uses `numedquestion`, `alphaparts`, and `framed`, including
`\begin {framed}` as well as `\begin{framed}`. Statements and answers share
one document. Support the existing template without requiring edits.

Read LaTeX as data, without compiling or executing macros. Handle whitespace,
nested environments, comments, escaped percent signs, verbatim content,
template definitions, and placeholders. Follow literal `\input` and
`\include` within permitted roots under the shared dependency limits; missing,
cyclic, escaping, or dynamic references leave explicit coverage gaps. Included
file saves must refresh dependent assessments. Ignore build artifacts such as
`.aux`, `.log`, `.synctex.gz`, and generated PDFs as save triggers.

Build a problem/subpart roster from the assignment or working source, retaining
whether it is complete. Questions absent from a partially copied assignment
must not disappear from the denominator. A handout in an unsupported format
leaves roster completeness unknown until an appropriate bounded reader exists.

Match problem statements and answer spans, and feed bounded source plus the
roster to the shared isolated extraction path. Domain claims are `not started`,
`in progress`, `answer written`, or `unclear`, with revision-bound locations.
A populated answer may cover only some subparts. TODOs or explicit uncertainty
support “in progress”; time without editing does not establish being stuck.

Written work is not verified mathematics. Keep owner declarations and any
later explicit correctness review separate. Avoid percentages for incomplete
rosters. Counts describe written coverage, not hours of effort or correctness.
Failures retain the prior assessment at its true age with a pending/error state.

### Source adapter

Implement the actual course page or authenticated service the owner uses.
The exact URL and any account setup are unresolved. Do not infer the source
from an unrelated public course or collect browser cookies. The source adapter
supplies assignment identity, revision, attachment, and any evidenced due date;
the shared acquisition path handles downloads, limits, grants, and recovery.

This source adds no new scheduler or course fields to shared task records.
Until foundation authorization is available, local lookup and on-demand
progress previews can proceed; automatic fetching must not claim to be active.

## Implementation sequence

1. **Connect Atlas to named resources.** Add stable bindings and role/source
   records, backward-compatible loading, private lookup, and conversational
   registration/correction. Extend existing project tools where their semantics
   fit. Gate: aliases survive restart/rename, ambiguous matches ask, existing
   notebook prose stays intact, and public requests cannot disclose records.
2. **Resolve, read, and open on demand.** Implement a local source and explicit
   Mac document opening, plus the shared snapshot/assessment contract. Use
   LaTeX as the first real reader and a small synthetic Markdown project as
   the portability test. Gate: both share the same lifecycle and tools;
   unsupported structure produces honest gaps rather than invented progress.
3. **Keep observations current.** Add persistent resource-change observation,
   acknowledged transfer, and bounded derived reads under the foundation.
   Gate: burst saves coalesce, dependency edits refresh, stale results cannot
   commit, restart catches up, human input wins, and ordinary saves stay quiet.
4. **Acquire missing or newer resources.** Add the owner's actual assignment
   source first, behind the shared acquisition interface and foundation grants,
   dispatch, and reconciliation. Gate: missing homework downloads and opens
   once, and a fake second source uses the same machinery without course logic
   in the runtime. Login errors, revisions, revocation, and interrupted writes
   preserve work and surface actionable state.
5. **Finish the homework experience.** Verify the real course mapping, source,
   editor, and save-to-assessment loop. The application is complete only when
   “pull up my analysis homework,” missing-download fallback, and later progress
   questions work together. Reusability does not defer those original needs.

Use existing project/config/tool modules and add a small observation service
and readers/source modules as responsibilities demand. Do not introduce
`CourseworkConfig` or a homework scheduler. Shared enablement, bounds,
debounce, and budgets belong in documented config dataclasses; project-specific
paths, selections, and expectations belong in records. Reuse dependencies;
ask before adding one. No new subsystem codename.

## Acceptance and delivery

Add a general project-observation probe, with a synthetic Markdown project
and synthetic LaTeX homework using identical resolve/observe/interpret/store
operations. Changing reader or source must not require changes to scheduler,
grants, recovery, private projection, or the shared record schema. The second
example proves this boundary; it does not require a new live integration.

Pin stable identity, aliases, migration, ambiguous/current selection, source
roles, dependency revisions, evidence bounds, incomplete coverage, unsupported
formats, rapid and unchanged saves, stale results, rename/deletion, restart,
reconnect, pause/revocation, private-lane enforcement, corrections, model errors,
download validation, non-overwrite, and uncertain-action reconciliation.
Use fake sources, extractors, openers, wire peers, and temporary state; never
use real homework or `~/.ciel` as fixtures.

### Homework acceptance

Use synthetic LaTeX shaped like the owner's environments, never real homework
or runtime state as fixtures. Add reader-specific checks beside the shared
project-observation probe: whitespace variants, nested subparts, commented
answers, placeholders, partial rosters, includes and dependency changes,
statements mistaken for answers, and evidence location validation.

Drive the full fake flow: register analysis, select homework, open the local
solution, find a missing handout remotely, download/open under authorization,
save an answer, refresh silently, and ask about progress. Test expired login,
HTML disguised as a PDF, changed handouts, interrupted downloads, owner edits,
revocation, offline Mac, and restart. A synthetic Markdown project must use
the same shared flow with different reader semantics.

Before live acceptance, confirm H104/current term, obtain the exact source
and any required login, and select an editor or use the Mac's default `.tex`
handler. Verify real opening, downloading, and reading after saves separately
from fake probes.

### Layer checks and delivery

Run the probes for each touched layer: tasks, runner, extraction, file guards,
project tools/turns, tool RPC, grants, and relevant wire/Vigil probes. Run the
hub import check for hub changes. Each implementation change includes its
README/configuration paragraph and changelog with actual probe counts. Verify
`spoke ready` after source edits that reload the spoke. Real login, downloads,
and editor behavior need separate live acceptance; fakes do not prove them.
Commit, deploy, or introduce dependencies only when asked or approved.
