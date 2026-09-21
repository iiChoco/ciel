# Ciel keeps the book open

2026-09-10. Implementation plan, revised the same day after four
reviews against the code (the revisions are marked *Revised* where
they change an earlier answer rather than add to it; the second
review's are marked *Revised again*, the third's *Third review*, the
fourth's *Fourth review*; the third's report with its reproductions is
`reports/2026-09-10-learning-plan-second-review.md` and the fourth's
note `reports/2026-09-10-learning-plan-ready-review.md`). The fourth
review called it ready to begin milestone 1. Nothing is built yet.

A persistent study workspace on Atlas. "Let's read Axler" resumes the
book at the bookmark; "let's work on Axler" resumes an unfinished
definition, proof, or problem. The first version covers local PDFs,
chapter-by-chapter LaTeX worksheets, spoken bookmarks, mathematical
review, individual practice, and mock midterms.

The subsystem has no codename here. The owner names things; this plan
calls it *the learning module* until one is chosen.

## Fit with Ciel

**A course is an Atlas project; the book and the study folder are its
resources.** [Atlas](../src/ciel/projects.py) already keeps the owner's
statements about where work lives: an id that survives a rename,
aliases, and resources with a role, a source, and a locator. A book is
a resource of role `book` (local, the PDF's path) with key
`book-<slug>`, its study folder a resource of role `folder` with key
`study-<slug>`, because the workbench's `roots_for` establishes a root
only from the roles `folder`, `root`, and `directory` and is not
changed. The project's aliases ("linear algebra", "110") resolve the
project through the same `resolve` every other project uses; a book's
own aliases ("Axler", "LADR") are in its `book` record and resolve the
book within the project. A project with several books and no book
named uses the one whose `book` resource is marked current, and asks
when none is. Nothing new is invented for identity, and the
workbench's rule — a document is readable when it is a bound resource
or lies under a folder bound to the same project — governs every read
of a worksheet with no change.

*Revised again.* The first revision named a `study` role, which would
have bound the folder without making it a root.

**The runtime's records live in a versioned `learning` namespace of the
task store.** Book identity, chapter items, bookmarks, attempt hashes,
assessments, exam state, and requests are machine-kept records,
owner-only, revisioned, and bounded, registered through
[`Namespace`](../src/ciel/tasks.py) with a validator and a migration.
The notebook keeps one line of prose per book ("reading Axler, at 3B")
so the owner can see it; the record is authoritative.

**Model work goes through the runner.** Extraction, worksheet
generation, review, question generation, and grading are steps of
tasks the [runner](../src/ciel/task_runner.py) gives one bounded turn
at a time, each model call an isolated
[extraction](../src/ciel/brain/extract.py) under the brain's lease.
Nothing in the conversational turn calls the model on a book. The
ladder runs a step only when the room is quiet and every human lane is
empty, and human input cancels an extraction in flight; the owner's
voice always wins.

**The Mac owns the PDF.** Search, page text, page labels, page images,
and worksheet publication are spoke operations beside `project.read`
and `project.open` in [the executor](../src/ciel/spoke/executor.py),
each re-checking the path against the Mac's own home, state directory,
and credential names. The hub receives what it asked for and never
stands in for the Mac: with the spoke away, a request that needs the
book waits and says so.

**The readers keep their purpose and grow two things.**
[`readers.py`](../src/ciel/readers.py) reports written coverage, never
correctness, and that does not change. Worksheets are generated in the
environments it already reads — `namedquestion` with the study item's
id as its argument, `framed` for the answer, `\answerbox` for an empty
one. Two additions make that enough.

An id is a lowercase kind, a hyphen, a label of letters, digits, and
dots, and then any number of further hyphenated segments of lowercase
letters and digits: `^[a-z]+-[A-Za-z0-9.]+(-[a-z0-9]+)*$`, which
admits `thm-3.21`, `cor-3.22`, `def-3B-span`, and `prob-1`. A
`namedquestion` whose argument matches becomes that item, its id the
argument instead of the running counter; a second item with the same
id in one reading is a gap, named, and the second is `unclear`. A
`namedquestion` with any other argument, and every `numedquestion`,
read exactly as they do today.

An item's answer is returned as exact text with its source: a tuple of
segments, each a file, a start and end character offset into that
file's original text, and the text between them, in document order,
so an answer that shares a line with its statement, or continues in an
included file, is isolated exactly. The line range stays for
navigation. The hash a caller keeps is of the isolated answer text
alone, so an edit to a statement leaves its answer's hash unchanged,
and a reordering of items changes nothing but their positions.

*Revised again.* The first revision claimed stable ids came free. They
do not: today the argument becomes title text and the id is the
counter, and the reading exposes an excerpt, not the span.

*Third review.* The grammar as first written rejected `def-3B-span`,
and a line range cannot isolate an answer that shares a line or spans
an include.

## The study workflow

- **Register a book once.** "Register Axler" takes a PDF path or
  searches the configured search roots on the Mac by filename and PDF
  metadata; matches are offered, never chosen, and the edition is
  confirmed in words before anything is bound. The tool binds the book
  and its study folder to the project, writes the `book` record (title,
  aliases, path and content hash, page count, whether the PDF carries
  printed page labels, the course or subject the owner named), and
  appends one log line to the notebook.
- **Resume explicitly.** "Section 3B", "page 84", "theorem 3.21" write
  the bookmark record at once, from the tool, through `write_records`
  — no task, no model. A printed page is stored as a printed page and
  mapped to a PDF index through the PDF's own labels, which PDFKit
  exposes as `PDFPage.label`; a PDF without labels refuses the mapping
  and asks for the PDF index or a section instead of guessing. An
  ambiguous location asks. Elapsed time and generated worksheets never
  move the bookmark; only the owner's words do.
- **Prepare one chapter at a time.** "Prepare chapter 3", or starting
  work on an unprepared chapter, queues a preparation. The chapter's
  pages are read from the Mac in windows, each window one extraction
  that returns the theorem statements — theorems, lemmas, propositions,
  corollaries — and explicitly introduced terms on those pages, with
  their numbering, hypotheses, notation, and the page they came from.
  A definition item also carries the book's own definition and a
  theorem item the book's proof sketch when one is short enough, as
  private reference text for review; neither is ever written to a
  sheet. The chapter's stated conventions ("F denotes R or C", "V is a
  vector space over F") are extracted from its opening pages into a
  `convention` record and handed to every review of that chapter.
  Items are records; the sheets are rendered from the records, not from
  a second model pass, so a sheet never drifts from what was extracted.
- **Write directly in LaTeX.** A theorem sheet has the statement and an
  empty proof area; a definition sheet has the term and an empty answer
  area. Ciel never fills them with the book's answers. An existing
  sheet is reopened, never regenerated over the owner's writing; a
  requested replacement is a new version (`chapter-03.v2.tex`).
- **Review on request.** "Check my definitions" or "check theorem
  3.21" queues a review. When the review step runs it reads the sheet
  through the workbench, follows its includes within the study folder,
  and takes a *snapshot*: the answer spans it is about to assess, kept
  in records, with the hash of each. The extraction sees the items,
  their private reference text, the chapter's conventions, the
  snapshot, and nothing else. Feedback names missing hypotheses,
  invalid steps, and gaps; it accepts equivalent definitions and
  alternative proofs. Each item is *assessed correct*, *needs
  revision*, or *uncertain*, and the feedback file says these are a
  model's assessment, not a verification, and names the hash it
  assessed. The version a review assesses is the one it read when it
  ran; an edit made while the room was busy is not assessed until the
  next review.
- **Protect the attempt.** Feedback identifies a gap without supplying
  the argument. A hint is given only on an explicit request, and
  recorded. A solution is given only when the owner asks for it
  outright, and recorded as *revealed*: a revealed item is no longer
  counted as assessed on the owner's own work, and a hint during an
  exam is recorded as assistance on the grade. Feedback lives in its
  own numbered file beside the sheet; the owner's file is never
  edited by Ciel.
- **Track distinct evidence.** The bookmark and, per item, one
  `progress` record holding the current answer-span hash, the current
  verdict with the hash it assessed, and a bounded history of earlier
  verdicts. The next read that finds a different hash reports the
  assessment as stale, keeps it in the history, and offers a fresh
  review.

*Revised again.* Attempts and assessments were one record each per
revision. They are one `progress` record per item with the history
inside it, because the store counts records, not bytes, and history is
what grows.

*Revised.* The first draft tracked staleness with new machinery. It
falls out of hashes: every read of a sheet — a review, "where did I
leave off", opening the chapter — compares the current hash of each
answer span to the hash the last assessment carried. The Mac's resource
watcher is not extended in this version; a sheet is read when the owner
asks about it, and that is current enough for studying. Binding every
sheet as a resource would also exhaust `[projects].max_resources` by the
third chapter.

### Layout

The class or subject is chosen when the book is registered; the folder
is created on the Mac under the study root when the first sheet is
published.

```text
~/Berkeley/Math/<class-or-subject>/Reading/<book-and-edition>/
    Definitions/chapter-03.tex
    Definitions/chapter-03.feedback-001.md
    Definitions/chapter-03.feedback-002.md
    Theorems/chapter-03.tex
    Theorems/chapter-03.feedback-001.md
    Problems/practice-001.tex
    Problems/practice-001.feedback-001.md
    Problems/midterm-001.tex
    Problems/midterm-001.submitted.tex
    Problems/midterm-001.feedback-001.md
```

Every file Ciel writes is new: feedback is numbered, the highest
number is the latest, and a submission is copied to its own file. No
file under the folder is ever replaced.

*Revised again.* Feedback was one file replaced under its hash. A
hash check before a replacement still races the editor, and numbered
files keep every assessment beside every edit for free.

## Practice and assessment

- **Practice.** One question by default, on the requested topic at the
  requested difficulty, from material up to the bookmark unless the
  owner names another range. The saved solution is reviewed on request
  like any other sheet.
- **Mock midterms.** Sixty minutes, one hundred points, five problems
  mixing definitions, examples or counterexamples, and proofs, unless
  the owner changes the duration or coverage. The sheet's header states
  the duration, points, and coverage.
- **Calibration.** A supplied syllabus, assignment set, or sample exam
  calibrates a set and is bound to the project like any other resource.
  Without one, the header says *book-based practice* and never claims
  to match an instructor.
- **A question is released only with a checked answer.** Generating a
  question is three extractions, one task per question: the first
  writes the question, a private solution, and a rubric; the second
  sees the question alone and solves it, stating any assumption it had
  to make; the third sees both solutions and says whether the
  assumptions are consistent, whether the two routes agree, and
  whether the rubric credits the second route. A disagreement, an
  added assumption, or an unresolved solution rejects the question,
  the task tries once more, and then reports the shortfall. A midterm
  is five such tasks and one that assembles the sheet.
- **Solutions and rubrics are never in reach, with two named
  exceptions.** They live only in the namespace records. They are
  never written to a sheet, never placed in the notebook's state
  (which rides in the system prompt), never returned as task evidence
  (which `inspect_task` renders), never in a notice. The grading task
  reads them after submission. The exceptions are the owner's own
  explicit request for a hint or for the solution. A hint is its own
  extraction: it sees the rubric, the solution, and the owner's
  attempt as private input and returns one nudge that names no step
  and no object of the argument; a raw rubric line is never returned,
  because a rubric line is a proof step. A solution is given only when
  asked outright, recorded as revealed, and never during an exam
  still open.
- **Grading follows submission, of a snapshot in records.** "Submit
  the midterm" reads the sheet and its includes through the workbench
  and, before it acknowledges anything, writes the complete submitted
  content — the sheet and every included file it followed, in parts
  under the record cap — as `submission` records that are never
  revised, together with the `exam` record's submitted-at and hash.
  A submission that would exceed `[learning].max_submission_chars` is
  refused in words and nothing is recorded. Only then is it
  acknowledged, and grading is queued: partial credit per rubric line,
  an explanation per problem, and the assistance recorded during the
  exam, into the feedback file. Grading reads the records, never the
  sheet or its includes again, so a later edit changes nothing. The
  copy at `midterm-001.submitted.tex` is published under the grant
  afterwards as a convenience for the owner's eyes; it is in an
  editable folder and proves nothing, and grading never reads it.

*Revised again.* A checker shown the solution reviews it; solving from
the question alone is what makes the check independent. And a hash
cannot be graded; the submitted content is kept.

*Third review.* The second revision kept the content only in the
published copy, so a submission without the grant kept a hash and
lost the work, and the copy itself was editable. The records are the
snapshot; the file is a courtesy.

## Records and bounds

One record per thing, because a record is capped at
`[tasks].max_record_chars` (16,000 characters) and a chapter's
statements verbatim do not fit one.

| Key | Payload |
|---|---|
| `book:<project>:<book>` | title, aliases, path, content hash, pages, labels present, class or subject, study folder |
| `bookmark:<project>:<book>` | the owner's words, the kind (section, printed page, PDF index, theorem), the resolved PDF index, when |
| `item:<project>:<book>:<chapter>:<id>` | id (`thm-3.21`, `def-3B-span`), kind, statement, hypotheses, notation, source page, notation confidence, the window it came from, private reference text |
| `convention:<project>:<book>:<chapter>` | the chapter's stated conventions and notation, for reviews |
| `chapter:<project>:<book>:<chapter>` | page range, windows read, items found, flagged pages, sheets published with their paths and hashes |
| `progress:<project>:<book>:<sheet>:<item>` | current answer-span hash and when read; current verdict with the hash it assessed and its feedback file; revealed or hinted; a bounded history of earlier verdicts |
| `snapshot:<project>:<book>:<sheet>:<run>:<part>` | the answer spans a review read, in parts under the record cap; deleted when the review's feedback is published |
| `submission:<project>:<book>:<set>:<part>` | the submitted sheet and every included file it followed, in parts under the record cap; written once, never revised, kept until the book is closed |
| `question:<project>:<book>:<set>:<n>` | the question, its private solution and rubric, the independent solution, the referee's verdict, whether released |
| `exam:<project>:<book>:<set>` | header, released at, hints given, submitted at with the snapshot's hash and part count, the courtesy copy's path once published, graded at |
| `request:<project>:<key>` | one queued preparation, review, or assessment, with its task id once derived |

Namespace version 1. The store holds at most
`[tasks].max_feature_records` records per owner (4,096). A chapter is
at most `[learning].max_items_per_chapter` items (200) and in practice
about forty, with a progress record each; a ten-chapter book is about
eight hundred records plus its snapshots and submissions, and four
such books fill the cap. Closing the Atlas project reclaims nothing.

**Closing a book is a destructive action and goes through the
broker.** `close_book` states what it will remove (the count of
records, that every file stays) and asks through the confirmation
broker; the yes is journaled through Inverse. It then cancels every
active task for the book through the store, so their fenced attempts
can write nothing late; deletes the book's `request` records with
their expected revisions, so a watch that already derived from one
fails its fence rather than reviving it; and only then deletes the
rest of the book's records, page by page. The book's target stays
listed in the worksheets grant until the grant is next approved,
deriving nothing because no `book` record answers for it; the README
says so, and revoking the grant is the owner's separate act.

**Records are loaded by key and by page, never as a namespace.** The
runner hands the synchronous `prepare` the namespace at `records()`'s
default limit of 256 in key order, which this namespace passes in its
first chapter, and a partial load in key order would hide every
`request` behind the `item`s. The learning adapter's `prepare`
therefore ignores that argument and answers *proceed*; every load
happens in the adapter's async `read` and `plan` steps through the
store it holds the way `ProjectAdapter` does through `bind_store`.
`records()` gains a `prefix` and an `after` parameter: a page is the
records in key order whose key has the prefix and sorts after the
cursor, at most `limit` of them, and a page shorter than the limit is
the end. An exact-key load past the limit is paged the same way.
Every scan in the module — a chapter's items, a book's cleanup, the
watch's discovery of requests — pages to the end, and
`probe_tasks.py` pins a prefix with more records than one page.

*Revised again.* The first revision sized storage and missed loading.

*Third review.* A prefix narrows a scan and still stops at the limit;
only a cursor reaches the end, and `prepare` cannot await a store.

## Model work

**A chapter is read in windows.** The extractor's payload is one
string bounded by `[tasks].extraction_max_chars` (32,000 characters),
one call takes at most ninety seconds of the model turn, and a task may
make `[tasks].max_model_calls` calls (sixteen) in its life. A chapter
of the first book is about fifty pages. So preparation is a sequence of
read steps, each one extraction over `[learning].pages_per_call` pages
(six, text only), and a task is created per section (3A, 3B, ...) when
a chapter's window count plus the generation and check calls would
exceed the allowance; the chapter's records are the union, and the
sheets are assembled from them.

**Windows overlap by one page, and the later window wins.** A
statement can begin on a window's last page and end on the next. Each
window after the first starts on the previous window's last page; an
item the model marks as *continuing past the window* is kept as
provisional, and the next window's extraction of the same item (the
same kind and number, or for a definition the same normalised term)
replaces it. Two complete extractions of one item keep the one with
the higher notation confidence, and a disagreement between them is
flagged on the chapter record.

**Images are the rule for notation, not the fallback for garbage.**
Extracted text of a mathematics book can read cleanly and still lose
a symbol: a subscript, a blackboard-bold F, a norm. Each window is
read from text first; every item whose statement contains
mathematical notation, or whose notation confidence is below
*confident*, or whose page shows replacement characters or an unusual
run of private-use code points, is then re-read from images of its
pages, `[learning].pages_per_image_call` at a time (three), and the
image reading's statement is the one kept. A page unreadable either
way is flagged. `[learning].images = "notation"` is the default;
`"always"` reads every window from images for a scanned book, and
`"never"` is for a probe. Nothing invents a statement for a page it
could not read, and a chapter with flagged pages says so on its
sheet's first line.

**The allowance is computed from the batches, not assumed.** A window
of `pages_per_call` pages is one text call plus, when any of its items
needs images, `ceil(pages_per_call / pages_per_image_call)` image
calls: with the defaults, one text call and two image calls, three per
window. The chapter's conventions are asked for in the first window's
text call, not in calls of their own. The tool that queues a
preparation counts windows from the page range with the overlap,
multiplies, adds a reserve of a quarter of the total for retries,
rounded up, and splits the chapter into tasks so that no task's count
exceeds `[tasks].max_model_calls`. The estimator sets the boundaries,
not the section headings: a task takes whole sections while they fit,
and a section too long for one task is split by page range. With the
defaults a task is four windows (twelve calls, three in reserve:
fifteen), some twenty-one pages with the overlap, and a fifty-page
chapter is ten windows in three tasks.

*Built.* The worked example was corrected on 2026-09-10 when milestone
2 landed: the conventions ride the first window's call, so four windows
fit at fifteen and five do not, and `probe_learning.py` pins the
arithmetic.

*Third review.* The earlier sentence assumed one image call per window.

*Fourth review.* Four windows came to eighteen calls, over the
allowance; three fit at fourteen, and the estimator, not the section,
decides where a task ends. (Superseded by the note above once the
conventions were folded into the first window.)

*Revised again.* The first revision used images only when text was
garbled, which would have passed a clean-looking statement with a
wrong symbol.

**Page images extend the extractor without changing its callers.**
`extract` gains an optional `images` argument: a tuple of (label, PNG
bytes), each at most `[learning].max_image_bytes` after rendering at
`[learning].render_dpi`, greyscale. With images the SDK query is sent
as a content-block message instead of a string; without them the call
is byte-for-byte what it is today, and the existing text-only callers
and their probe do not change. The runner's `StepContext.extract`
grows the same optional argument. An image call carries its own budget,
`[learning].image_call_budget_usd`, because vision tokens cost more
than the default ceiling allows.

**Results arrive as notices, and feedback is spoken when asked.** A
preparation, a review, or a graded exam completes with a notice
through the existing delivery path: "chapter 3 of Axler is prepared:
14 theorems, 9 definitions, two pages flagged". The notice never
carries the feedback itself. When the owner asks — "what did you
think of my proof of 3.21", "read me the feedback" — the feedback file
is read through the workbench and its verdicts and named gaps are
spoken on the private lane, the file remaining the record. The tool
that queued the work says "queued" and, if the room is busy, that it
runs when things are quiet. Only bookmarks, registration, and
submission answer at once.

*Revised again.* The first revision made feedback file-only, which
was not agreed. Spoken on request is the rule.

**Human input wins, and a long preparation survives it.** An extraction
cancelled by the owner's voice spends an attempt; a chapter read in
windows checkpoints after each window, so a cancelled window is the
only work redone, and a task with eight attempts and sixteen calls
finishes a section in an ordinary evening.

## Authority

*Revised.* The first draft said "confirmed, journaled writes" and left
open what confirms them. The runner dispatches a mutation only under a
standing grant at its activated revision or the owner's approval of
the exact payload digest. The LaTeX does not exist when the owner says
"prepare chapter 3", so per-payload approval would mean a held question
for every sheet. The learning module offers one standing grant instead,
the way the project adapter offers *Readings of bound documents*:

**Worksheets and feedback under the study folder.** Operations:
`learning.prepare` (new sheets), `learning.review` (a feedback file),
`learning.assess` (new practice and exam sheets, and their feedback).
Targets: one per registered book. Bindings: the study folder. Limits
from `[learning]`, capped by `[tasks]`. Approved once in Chart's Tasks
section, through the broker, with Proof Obligation's digest.

Under the grant the rules are the file's:

- **Every publication is create-only, atomically.** The spoke writes an
  owner-only temporary file in the destination's folder, fsyncs it,
  and then `link`s it to the destination: `link` fails with `EEXIST`
  when the destination exists and never replaces it, where `rename`
  would. The temporary file is unlinked afterwards, and a crash between
  the two leaves at most a stray temporary file, never a half-written
  destination. A destination that exists fails the precondition, and
  the task plans again under the next version or feedback number.
  Nothing under the grant can overwrite a file the owner wrote, and
  nothing is ever replaced: sheets, feedback, and submitted copies are
  all new files.
- Every write is **confined to the study folder** on both halves: the
  hub checks the path against the project's bindings, the spoke
  against its home, state directory, and the study root; a path
  escape is refused in words and journaled.
- The **journal** records each publication through Inverse like any
  mutation, and reconciliation asks the spoke whether the file exists
  with the planned hash.

*Revised again.* The first revision had `O_EXCL` on the temporary
file and `rename` to the destination, which is not create-only, and a
feedback file replaced under a hash, which races the editor.

Without an active grant, "prepare chapter 3" says that the worksheets
grant is not approved and does nothing; the tool never falls back to
`files.write` or to the brain's shell. Per-payload approval as a
held question is not built in this version.

**The owner's tools stay at once and quiet.** Registering, binding,
and bookmarking write records and notebook lines, not files, and need
no grant. Submitting writes the submission's records at once, whole,
and needs no grant for that; the courtesy copy and the grading's
feedback file wait for the grant, and without it the tool says the
submission is kept and grading waits for the grant. Reading a sheet
is a workbench read under the project's bindings, as
`project_progress` does today.

## The Mac's part

Four spoke operations, registered beside the project ones, each
checking its path with the same rules as `project.read` plus a
learning-specific suffix set (`.pdf` for the book, `.tex` and `.md`
for publication); `DOCUMENT_SUFFIXES` is untouched.

| Operation | Does |
|---|---|
| `learning.find_books` | Filename and PDF-metadata search under the configured search roots, at most `[learning].max_search_results`, each with title, author, page count, size, and path. No page text is searched. |
| `learning.pdf_info` | Page count, printed labels present, the outline (chapter and section titles with their first page) when the PDF has one, content hash. Bounded by `[learning].max_pdf_bytes`. |
| `learning.pdf_pages` | Text per page for a page range of at most `pages_per_call` pages through PDFKit's `PDFPage.string`, and, when asked, a greyscale PNG per page at `render_dpi` capped at `max_image_bytes`. Base64 on the wire, like `project.read`, under its 8 MiB cap. |
| `learning.publish` | One new file under the study root: owner-only temporary file, fsync, `link` to the destination, unlink the temporary; `EEXIST` is refused in words; returns the hash of what landed. |

PDFKit is already a dependency through `pyobjc-framework-Quartz`; no
package is added. Rendering runs in a thread; a PDF past the byte cap
is refused, not partially read.

Page text and page images leave the Mac for the hub and the model, as
project readings already do. The README's learning section says so
in one sentence.

## Configuration

An opt-in `[learning]` section, one dataclass in `config.py`, documented
in the README the same day:

```toml
[learning]
enabled = false
study_root = "~/Berkeley/Math"        # every publication lands under here
search_roots = ["~/Berkeley"]          # where "find the book" may look
max_search_results = 10
max_pdf_bytes = 100_000_000
pages_per_call = 6                     # text windows per extraction, overlapping by one page
pages_per_image_call = 3               # image re-reads of notation
images = "notation"                    # "notation" | "always" (scanned books) | "never" (probes)
render_dpi = 110
max_image_bytes = 400_000              # per page, after rendering
image_call_budget_usd = 0.75
max_pages_per_chapter = 80             # past this, prepare by section
max_items_per_chapter = 200
max_submission_chars = 400_000         # a submission past this is refused, not truncated
exam_minutes = 60
exam_points = 100
exam_problems = 5
publications_per_day = 40              # the grant's window; [tasks] caps it
grant_lifetime_s = 2592000             # thirty days; [tasks] caps it
```

Model choice and the task budgets are `[tasks]`'s. No file watching is
added.

## Milestones

Each milestone is buildable alone, has a gate in the probe, and has a
live acceptance before the next begins. The first makes "let's read
Axler" work with no model call at all.

1. **Register, bookmark, resume.** The namespace with `book` and
   `bookmark` records; the tools `open_study`, `register_book`,
   `set_bookmark`, `study_status`, and `close_book`; the spoke
   operations `learning.find_books` and `learning.pdf_info`; the
   `book` role and the `study-<slug>` folder binding; `records()` by
   prefix and cursor; the config section. Gate: two books on one project keep
   separate bookmarks and resolve by their own aliases, with the
   current one used and none asks; a printed page maps through labels
   and a label-less PDF asks; "theorem 3.21" is stored as the owner's
   words with its kind; an ambiguous title asks and the edition is
   confirmed before binding; a missing PDF and a disconnected spoke are
   said, not worked around; a bookmark survives a restart; closing a
   book asks through the broker, cancels its tasks, fences its
   requests, deletes its records across more than one page, and
   deletes no file; a prefix with more records than one page is read
   to the end; nothing leaves the private lane. Live: "let's read
   Axler" from voice and from a phone resumes at 3B.
   *Landed 2026-09-10* (`probe_learning.py`, 62 checks); the live
   acceptance is owed. One departure from the list above: the search
   is its own tool, `find_book`, rather than a mode of `register_book`,
   so that a tool that offers candidates never also binds.
2. **Prepare a chapter.** The reader's id-shaped `namedquestion` and
   answer spans; the extractor's images; `learning.pdf_pages` and
   `learning.publish`; the adapter with its grant, watch, and
   preparation task; `item`, `convention`, and `chapter` records; the
   sheet renderer in the reader's environments; the `prepare_chapter`
   tool. Gate: chapter boundaries from the outline and from the
   owner's page range; windows overlap by one page, a statement across
   the boundary is kept once and complete, and two readings of one
   item keep the more confident; windows checkpoint and a cancelled
   window is the only one redone; a chapter past the allowance splits
   by section; every statement keeps its hypotheses and numbering; an
   item with notation is re-read from images and the image reading is
   kept; an unreadable page is flagged, never invented; sheets contain
   no proofs, no definitions' answers, and no reference text; an
   existing sheet is reopened and a replacement is a new version; a
   publication to an existing path is refused and the next number
   used; a write outside the study root is refused on both halves; a
   duplicate request derives one task; without the grant the tool says
   so; a task's model-call count is computed from its windows and
   never exceeds the allowance; the reader reads a generated sheet
   back with the ids it was written with, isolates an answer that
   shares a line with its statement and one that continues in an
   include, leaves an answer's hash unchanged when only the statement
   changes, names a duplicate id as a gap, and every existing reader
   check still passes.
   Fixture sheets compile with `latexmk -pdf -no-shell-escape` and the
   layout is inspected. Live: chapter 3 prepared, sheets opened where
   the owner is.
   *Landed 2026-09-10.* Two departures from the list above: the
   chapter's conventions are asked for in the first window's call
   rather than in calls of their own, and a chapter cut into several
   tasks is read by one task per segment and published by one more,
   derived by the watch when every window is read, because a task's
   model-call allowance is fixed at its creation and a chapter's is
   not. The live acceptance is owed.
3. **Review.** `progress` and `snapshot` records; the `check_sheet`
   tool ("check my definitions", "check theorem 3.21"); the review
   task, its snapshot, and the numbered feedback file under
   `learning.review`; spoken feedback on request; hints and revealed
   solutions. Gate: an empty box is not reviewed and says so; the
   review assesses the snapshot it took and the feedback names that
   hash; a review sees the reference definition and the chapter's
   conventions; a hint is given only on request, recorded, and
   contains no rubric line, a solution only when asked outright and
   recorded as revealed;
   feedback never edits the sheet and each review is a new numbered
   file; an edit after review reads as stale with the history kept in
   the progress record; a review interrupted by voice is requeued and
   snapshots again when it runs; feedback spoken on request names
   verdicts and gaps, never the argument. Live: chapter 3 definitions
   checked, feedback read aloud and opened.
   *Landed 2026-09-10.* Departures: a review interrupted after its
   snapshot re-runs the assessment from that snapshot rather than
   snapshotting again, since the snapshot is the version the review
   was asked about; a theorem's reveal is where the book proves it,
   not a proof Ciel writes, because nothing in the conversational
   turn calls the model; and hints and feedback are files under the
   grant, spoken on request by `read_feedback`. The live acceptance
   is owed.
4. **Practice and midterms.** `question` and `exam` records; the
   `practice` and `mock_exam` tools; generation with the independent
   solve and the referee; the `submit_exam` tool with its `submission`
   records, its courtesy copy, and grading under `learning.assess`;
   calibration from a bound syllabus or sample. Gate: a question whose
   independent solve needed an added assumption is rejected; solutions
   and rubrics appear in no sheet, notebook state, evidence, or notice;
   a solution is not revealed while an exam is open; the header says
   book-based practice without a syllabus; a submission is whole in
   records before it is acknowledged, with its includes, and is
   refused past the cap; grading reads the records and a later edit to
   the sheet, to an include, or to the courtesy copy changes nothing;
   a submission without the grant is kept and grading waits; a grant
   that expires between submission and publication loses nothing; a
   restart between submission and grading grades the same snapshot; a
   hint during the exam is recorded on the grade; the default set is
   five problems, sixty minutes, one hundred points, and an explicit
   change is honoured. Live: one practice question, one mock midterm,
   graded.
   *Landed 2026-09-10.* Departures: a practice question is graded by
   the same submission path as a midterm rather than by the chapter
   review, since it has a private solution and rubric of its own; the
   independent check is three calls in one task per question, as the
   third review asked; and the courtesy copy is published by the
   grading task before it grades, so a submission without the grant is
   kept in records alone. `scripts/eval_learning.py` exists and has not
   been run; its cases await an independent check. The live acceptance
   is owed.

Modules: `learning.py` (records, sheets, the adapter, requests),
`brain/tools/learning.py` (the tools), the four spoke operations in
`spoke/executor.py`, the two reader additions in `readers.py`, the
images argument in `brain/extract.py` and `task_runner.py`, the paged
load in `tasks.py`, `LearningConfig` in `config.py`. No new dependency,
no new subsystem codename until the owner picks one, no watcher.

## Addendum, 2026-09-10: the learning steps run beside the conversation

*Asked for by the owner after the first live run; to be built by Codex.*

**Why.** The runner takes a step only when the ladder finds the room
idle and every human lane empty, and each step's model call holds the
conversation's lease. "Prepare chapter 1" therefore sat queued through
the whole first session: a study session is exactly when the room is
never quiet, and a window's call of a minute is either waited behind or
cancelled by the next message. The rule that a background call never
overlaps a turn was set for reflection and the inbox; for studying it is
the wrong rule, and this addendum revisits it for the learning module
alone. Record this reason in the changelog entry.

**What.** A second runner on the same store that takes only the
learning adapter's steps, with its own lease and its own extraction
client, ticked every second from the pipeline's loop instead of from
the ladder. In Ciel's terms that is the "agent" the owner asked for:
every isolated call is already a fresh SDK client with nothing attached;
this one simply does not share the conversation's lock.

1. `TaskStore.eligible()` gains an optional `operations` filter (a
   tuple of step operations; None means any), and `reconcilable()` the
   same, so two runners on one store never claim each other's tasks.
   Pin it in `probe_tasks.py`.
2. `TaskRunner` gains a `background=True` mode: it runs the same `step`,
   but `interrupt()` is a no-op, the pipeline never routes human input
   to it, and its `step` passes its adapters' operations as the filter.
   The foreground runner passes the complement (every operation it
   serves and none of the background's). Pin in `probe_task_runner.py`
   that a background runner's step is not cancelled by `interrupt()`
   and that each runner sees only its own tasks.
3. `[learning].background = true` (default true; documented in the
   README's block). When on, the pipeline builds the learning runner with
   `lease=` a private `asyncio.Lock` and `backend=` its own
   `AgentSdkExtractor`, and drives it: every loop tick, `refresh(now)`
   then `start_step(now)` when `ready`, regardless of the ladder and of
   the human lanes. When off, the adapter stays on the ladder's runner as
   today. The notifier, controller, and grant wiring are unchanged; the
   learning adapter is registered with exactly one runner.
4. Still one learning step at a time; at most one background call runs
   beside the conversation. Say in the README that spend can double while
   both are busy, and that a window's page images are rendered on the Mac
   during the session.
5. `probe_learning.py`: a chapter step runs while a human lane is
   pending and a conversational lease is held by someone else, and never
   touches that lease; the owner's interrupt leaves the background step
   running; with `background = false` the old rule holds.
6. Changelog entry in the project's voice with the reason above and the
   probe counts; README paragraph under *Prepare a chapter* saying the
   learning steps run beside the conversation; `probe_turns.py` if the
   pipeline's construction changed what it pins. Hub import check;
   `spoke ready` after the last source edit. Then the owner pushes.

**State at handoff.** Everything above this addendum is built and
uncommitted in the working tree (see `git status`); nothing is committed
or pushed by the assistant. The hub runs the code as of the config-loader
fix (2026-09-10 — "The switch that could not be turned on") and has
`[learning] enabled = true`; the Chart grant-form fix ("The form shows
the feature it says it shows") is in the tree and not yet pushed. On the
Mac the book is registered at `~/Berkeley/Math/110/Reading/LADR4e.pdf`
on the Math 110 project and chapter 1 is queued under the grant.

## Acceptance and delivery

**A learning probe**, `scripts/probe_learning.py`, with synthetic PDFs
built in a temporary directory (PDFKit can write them), a fake
extractor that answers from fixtures, a fake spoke, and a temporary
task store — never a real book, the study folder, or `~/.ciel`. Its
checks are the milestone gates above, in the project's words, plus:
edition ambiguity, printed-page mapping with and without labels, a
chapter boundary from the outline against one from a page range, a
statement across a window boundary, a complete hypothesis list, blank
definition areas, proof and reference-text exclusion, uncertain
extraction, independent books, empty versus attempted answers, stale
reviews after edits, the snapshot a review assessed, feedback
preservation across numbered files, exam submission and the submitted
copy, rubric separation, the withheld solution during the independent
solve, a hint free of rubric lines, duplicate-request recovery, path
escapes, a missing PDF, a disconnected spoke, an interrupted model
call, a destination that appears between plan and send, a namespace
past 256 records still loading its requests and one chapter with more
items than a page read to the end, a book closed while its
preparation runs, and a submission kept whole without the grant.

**Layer probes** for every touched layer: `probe_atlas.py` (the book
role and the folder binding), `probe_readers.py` (id-shaped
`namedquestion`, answer spans, and every existing check unchanged),
`probe_tool_rpc.py` (the four operations, their path checks, and
`link` refusing an existing destination), `probe_extraction.py` (the
images argument and the unchanged text path), `probe_task_runner.py`
and `probe_tasks.py` (the namespace, the grant, and the prefix load),
`probe_task_tools.py` and `probe_turns.py` (the tools), and
`probe_grants.py` (the Chart form). The hub import check after hub
changes; `spoke ready` in the spoke's log after the last source edit.

**LaTeX.** Fixture sheets are compiled in the probe's temporary
directory with shell escape disabled, and the rendered layout of one
theorem sheet and one definition sheet is inspected by eye before
milestone 2 is called done. Reading or grading an owner's file never
runs LaTeX.

**A model evaluation, run apart from the probes.** A fake extractor
proves the orchestration and nothing about mathematics. So
`scripts/eval_learning.py` runs the real review and generation prompts
against a small set of independently checked cases kept beside it: a
correct proof by a route the book does not take, a proof missing a
hypothesis, a definition equivalent to the book's in other words, a
definition that is wrong in one quantifier, a question with
inconsistent assumptions, a hint request whose answer must not name
the missing step, and a scanned page with a lost subscript. Each case
states, before it is run, the verdict expected and the spoiler that
would fail it: a phrase, an object, or a step that must not appear in
the feedback or the hint. The script fails on any case whose verdict
differs or whose spoiler appears, and prints the rest as agreement.
It costs money, so it is run by hand before milestones 3 and 4 are
called done, its result recorded in the changelog entry, and it is
never in the probe list. It does not read the owner's files.

*Revised again.* The first revision let mocks stand for the
mathematics.

*Third review.* An agreement report is not a pass condition; each case
now carries its expected verdict and its spoiler.

**Writing.** The README gains a learning section (workflow, layout,
what leaves the Mac, the grant, the notices), a `[learning]` block
under Configuration, and the probe in the list; each milestone's
changelog entry carries its probe counts.

**Not included.** No branch, commit, deployment, production
configuration change, or runtime-data inspection. The uncommitted
note-window work in the tree is preserved and untouched.

## Open with the owner

- The codename.
- The study root and search roots as written above, and whether
  `~/Downloads` is a search root.
- The first book and its edition, confirmed at registration, not here.
- The evaluation cases: who checks them independently before they
  become the yardstick.
