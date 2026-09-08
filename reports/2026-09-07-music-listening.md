# 2026-09-07 — Music can inherit the conversation

The reported loop has a reproducible path through the current state machine.
The acoustic trigger remains a hypothesis: this investigation did not read
runtime state, recordings, or logs, and did not exercise Spotify or a microphone.
Existing uncommitted implementation work was left intact.

## Findings, ordered by impact

1. **Every spoken reply can open another capture window.**
   `src/ciel/spoke/frontend.py:556` drains queued microphone frames, then opens
   follow-up listening whenever `_spoke` is true and `followup_ms` is positive.
   `src/ciel/pipeline.py:1406` makes the same decision for the local loop.
   `src/ciel/config.py:101` defaults that window to five seconds. The reproduction
   executes the current spoke method and demonstrates three consecutive replies
   reopening capture. If audible music is admitted as speech and produces a
   spoken answer, this permits the reported cycle to repeat. Draining queued
   frames cannot remove music that continues playing afterward.
   Proposed fix: after a playback-start request succeeds, close this voice turn's
   follow-up window explicitly. Keep ordinary conversational follow-ups intact.
   `src/ciel/pipeline.py:640` sends only turn identity and completion status;
   carry a turn-scoped follow-up decision to the spoke and apply the same policy
   locally. Derive it from the actual tool outcome, not the assistant's wording.
   Preserve failure explanations and confirmation windows. Reset it for each
   new turn and reject stale turn signals.

2. **The deadline does not close a capture that already contains speech.**
   `src/ciel/spoke/frontend.py:1073` and `src/ciel/pipeline.py:3512` deliberately
   ignore the follow-up deadline while the endpointer is speaking. The
   reproduction confirms both methods do so after the deadline. This protects
   long human sentences, but also retains music classified as speech.
   `src/ciel/audio/vad.py:125` ends capture at the length cap; the default at
   `src/ciel/config.py:118` is 30 seconds. That cap submits an utterance rather
   than breaking a conversation cycle. Proposed fix: prevent automatic capture
   after playback starts; simply shortening the window or the utterance cap
   does not reliably solve the cycle.

3. **Always-awake mode bypasses a return to idle.**
   `src/ciel/audio/wake.py:103` implements an unconditional wake. Therefore
   disabling follow-ups alone cannot solve this mode. The reproduction confirms
   `followup_ms = 0` returns the spoke to waiting; source inspection shows that
   always-awake mode immediately wakes again. Proposed fix: require explicit
   activation during playback, including an override for always-awake mode.
   Wake-word mode retains hands-free access but can still falsely wake on music;
   the Talk shortcut or hotkey is the stricter option if that occurs.

## Proposed behavior

A successful play/resume closes follow-up capture after the acknowledgement.
Ciel remains available through deliberate activation. For broader protection,
track playback state so later voice commands made while music plays do not
reopen automatic follow-ups either. Account for playback on another Connect
device, pause, failed or uncertain controls, and playback initiated outside Ciel.
Any future automatic pause or volume change must retain the project's existing
action authorization and journal boundaries.

An immediate configuration workaround is `[audio] followup_ms = 0`, with
`[wake] mode = "wakeword"` or `"hotkey"`; it removes follow-ups for all
conversations. Apply it to the Mac spoke's configuration and restart to load it.
No configuration was changed during this investigation. The documented Stop
shortcut is Control–Option–Escape if global shortcuts are enabled; mute is
available in the Chart.

## Verification

`uv run --no-sync python reports/2026-09-07-music-listening-repro.py`:
all eight checks passed. The script extracts and executes the current relevant
methods with synthetic state, without importing the runtime or loading models.
It verifies control flow, not acoustic recognition or a full end-to-end loop.
`git diff --check` passed. No production files, dependencies, services, or
configuration changed; no commit or deployment was made.
