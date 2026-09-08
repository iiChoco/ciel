# How close Ciel is to the proposed architecture

Assessment date: 2026-09-06 (America/Los_Angeles). Scope: the current checkout,
including existing uncommitted changes. No application source or production
state was changed. This is a capability assessment, not a complete bug review
or a measurement of the deployed configuration.

The shared observation infrastructure is substantially implemented. The broader
persistent executive remains partial: Ciel can retain context, monitor a few
conditions, and check selected actions, but it does not yet own a general
multi-step outcome across restarts. Percentages would imply a precision that
neither the original proposal nor these checks support.

## Gaps, ordered by impact on the proposal

1. **Persistent responsibility has no general execution engine.**
   `src/ciel/projects.py:67` stores a project's name, status, free-text state,
   log, and timestamps; `src/ciel/projects.py:44` permits active, paused, done.
   `src/ciel/proactive/work.py:40` stores file/process watches, and its
   completion check at line 177 tests file existence or process disappearance.
   Inspection reproduced a durable notebook plus condition monitoring, with
   no structured step dependency, retry, acceptance criterion, or resume-next-step
   scheduler in these paths. A stopped process alone does not establish a
   successful outcome. Proposed next milestone: one durable task with objective,
   next step, blocker, pending action, and an observable completion condition;
   prove resumption after restart without repeating an already completed action.

2. **The world has a shared table, but limited coverage of meaningful activity.**
   `src/ciel/world.py:86` onward defines presence, place, switches, timers,
   watches, agenda, Oura, sections, and spoke connection. `Fact` at line 148
   records source, observation/receipt time, and lifetime. `World` at line 262
   resolves observations and persists facts. `src/ciel/pipeline.py:3185` feeds
   and broadcasts the table, and line 2184 injects it into turns. The world
   probe passed 123 checks covering freshness, ordering, projections,
   persistence, relay boundaries, and integration. There is no corresponding
   structured activity/project/task/commitment relationship model in this
   vocabulary, nor an explicit belief confidence field. Proposed extension:
   connect task state and a small set of useful, attributable activity readings;
   distinguish direct evidence from inferred activity. Preserve existing
   freshness and privacy rules rather than replace the table.

3. **Vigil routes events; it does not yet coordinate progress toward objectives.**
   `src/ciel/proactive/policy.py:77` routes an event according to expiry,
   verification, quiet hours, importance, presence, and budgets. Its decision
   is speak/message/note/hold/drop, not a task-state transition.
   `src/ciel/brain/witness.py:1` explains the deliberate unattended boundary:
   observe and write the notebook, with outward actions restricted.
   The Vigil probe passed 173 checks. Proposed extension: relate events to
   explicit delegated tasks and wake their next eligible step. Keep initiative
   and authorization distinct; any unattended action scope needs an explicit
   design, not removal of Witness's existing guard.

4. **Verification is useful but does not establish a durable task outcome.**
   `src/ciel/brain/recorder.py:127` records tool results and schedules selected
   read-back checks. `src/ciel/pipeline.py:2916` queues those checks only when
   the proactive queue exists, caps pending checks at three, and gives them
   a thirty-minute expiry. Line 2961 turns findings into held prose notes.
   `src/ciel/pipeline.py:1067` creates that queue when proactive mode is enabled.
   Inspection therefore shows best-effort read-back, not guaranteed verification
   of every action. World revisions exist (`src/ciel/world.py:434`) but the
   inspected Python consumers expose them to the UI rather than enforce action
   preconditions. Proposed extension: link action IDs, observations, and explicit
   success/failure/uncertain outcomes to a task; recover ambiguous actions before
   retrying. The probe passes do not prove this unimplemented lifecycle.

5. **Perception is on demand, and conversation still has an acoustic gap.**
   `src/ciel/brain/tools/screen.py:1` explicitly specifies on-demand capture;
   line 126 exposes the tool. There is no maintained active-app/project/file
   reading in the current world vocabulary. `src/ciel/stt/base.py:18` accepts
   captured PCM and returns a transcript, rather than a partial transcript
   stream. `src/ciel/brain/agent.py:603` supports interruption, and the pipeline
   streams sentence delivery; `README.md:1214` documents barge-in as disabled
   by default and unreliable on laptop speakers without acoustic echo
   cancellation. The turns probe passed 71 fixture checks; this does not measure
   live latency or acoustic performance. Proposed sequence: bounded app/window
   context with clear capture controls, then live voice measurements before
   choosing streaming STT or echo work.

6. **Memory supports continuity but not the full proposed retrieval model.**
   `src/ciel/memory/store.py:40` includes preference and procedure kinds;
   `Memory` at line 65 includes conversation/proactive provenance.
   `search` at line 336 ranks keyword overlap, with a documented semantic-query
   limitation. Reflection and Atlas already preserve knowledge and working
   state; the Closure probe passed 56 checks. The inspected store does not
   implement importance/confidence/frequency-based retention or a relationship
   graph. Proposed work: evaluate actual recall and correction scenarios before
   adding a retrieval engine or changing retention.

## Verification and limits

Run `python3 reports/2026-09-06-world-readiness-repro.py` from the checkout.
It runs existing probes through `uv run --no-sync`, with temporary home/state
and a guard against accessing the actual runtime directory:

- `probe_world.py`: 123 passed.
- `probe_vigil.py`: 173 passed.
- `probe_closure.py`: 56 passed.
- `probe_turns.py`: 71 passed.

Total: 423 checks. No new regression checks were necessary for this assessment.
No live microphone, screen, model, connector, production configuration, hub,
or service log was tested. No deploy or commit was performed. Existing work
was preserved. The report and companion runner are local investigation material.

The next complete scenario should be small: delegate one outcome, persist its
state, restart, resume, verify the result, and report completion or the exact
decision needed. That connects the substantial foundations already here.
