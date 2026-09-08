# 2026-09-07 — The playback gap is measured; the lisp is not yet isolated

The user reported lisp-like speech with Apple's backend both before and after
the TTS effect was disabled. The requested outcome is echo cancellation with
normal speech. The runtime remains on Apple audio with the effect off. No voice
model, account, transcript, or microphone recording is copied into this report.

## 1. Python stalls can cut the rendered voice

`src/ciel/audio/apple.py:49` and `:397` formerly allowed three 50 ms buffers.
After the oldest audible receipt, only about 100 ms remained scheduled. A
controlled 300 ms Python stall produced an internal silent gap of 202.83 ms
in the initial mixer-tap experiment. Larger buffers with twenty outstanding
preserved the entire three-second signal. The saved reproduction
beside this report repeated the comparison after the fixes:

| Window | Input / output / mixer | Rendered tone span | Internal silence |
|---|---|---|---|
| Former three buffers | 48000 / 48000 / 48000 Hz | 3.215 seconds | 213.5 ms |
| Current twenty buffers | 48000 / 48000 / 48000 Hz | 3.000 seconds | none over 1 ms |

Reproduce with `uv run --no-sync python reports/2026-09-07-apple-playback-repro.py --live`.
The script builds a temporary instrumented helper, plays generated tones, saves
only that helper's mixer output temporarily, and discards all microphone
frames. It does not read runtime state. The deliberately blocking file tap is
investigation instrumentation, not part of the production helper. The test
requires real Mac devices and does not quantify acoustic echo rejection.

Fix: twenty 50 ms buffers, at most one second, with the matching native bound.
The first buffer plays immediately; there is no one-second startup delay.
Stop still discards the native queue and the final receipt still means played.
Python must service a stop request, so a blocked Python loop can delay that
request; increasing the queued duration does not remove that existing limit.

This reproduces a cause of chopped consonants. It does not establish that the
user's perceptual complaint was caused by a stall. No production event-loop
stall was measured, and a steady alteration of every sibilant remains open.

## 2. A low microphone rate was not present on this Mac

Before changing the rate selection, the native graph was measured directly:
processed input mono 48000 Hz, output hardware stereo 48000 Hz, output client
mono 48000 Hz, mixer mono 48000 Hz. The new helper reports the same three rates
in its ready frame (`src/ciel/audio/native/CielAudio.swift:239`), and Python
validates and logs them (`src/ciel/audio/apple.py:132`).

A pre-output multitone experiment at Piper's 22050 Hz source rate measured
500, 2000, 4000, 6000, and 8000 Hz within roughly 0.01 dB of the source, and
10000 Hz down 6.87 dB. Both small and large buffers produced the same result;
without an injected stall, neither had internal gaps. This checks the mixer
signal only, not the final speaker signal or perceived speech quality.

Consequently, the review's hypothetical 16/24 kHz microphone bottleneck is not
a diagnosis for this machine. Also, 24 kHz output has a 12 kHz Nyquist limit,
which exceeds the source's 11.025 kHz limit; that rate alone would not discard
frequencies present in 22.05 kHz Piper audio.

The old rate coupling was nevertheless unnecessary. Playback now queries the
default speaker's nominal rate through Core Audio (`CielAudio.swift:150,207`),
independently of the microphone. Simply reading outputNode.outputFormat before
connecting the graph failed live with a zero rate. The direct hardware query
passed the real startup check. The measured rates after startup remain 48 kHz.

## 3. Two reproduced reliability defects are separate from the lisp

The findings in `reports/2026-09-07-apple-audio-review.md` were valid:

- The broad RuntimeError/OSError/TimeoutError catch could mistake a failed
  synthesizer for a dead audio device. A device-specific exception now bounds
  transport and receipt failures (`apple.py:52,360,413`). All three source
  exception types propagate unchanged; playback stops, capture stays alive,
  and another utterance can play.
- Capture lock contention dropped a frame silently. The tap now signals a
  separate semaphore and wakes the worker, which fails visibly before emitting
  more capture (`CielAudio.swift:62,83,98`). No unsynchronized shared error flag
  or blocking wait was added to the tap. An offline compiled contention case
  proves the worker reports the failure.

## What remains unknown

Apple voice processing enables both I/O nodes, although echo removal targets
incoming microphone audio. See Apple's
[AVAudioEngine explanation](https://developer.apple.com/videos/play/wwdc2019/510/).
Ciel's implementation also changes its playback route. Enabling echo
cancellation is not proof that the route preserves perceived TTS timbre.

An exploratory acoustic multitone comparison, with samples held only in
memory, showed different microphone-measured levels between the routes. An
isolated run briefly stopped the spoke and restored it in a finally block.
These measurements do not distinguish changed microphone gain/processing,
ducking, speaker output gain, and output treatment, and are not used to claim
that Apple's far-end processing caused the lisp. The raw-output experiment did
not qualify system-audio echo rejection, either.

There has been no matched fixed-sentence listening judgment, real-room music
rejection test, snap qualification, or double-talk test. If the user still hears
steadily softened sibilants after this correction, the next controlled test is
identical speech PCM through standard and Apple playback, with an explicit
listening judgment and level matching. Do not equate the gap fix with a proven
speech-quality fix or silently disable echo cancellation.

## Verification

- `probe_apple_audio.py`: 49 → 61 checks, including synthesis error isolation,
  bounded deeper playback, reported-rate validation, native contention failure.
- `probe_apple_audio.py --live`: 7 checks passed with processed capture and
  played-back receipt.
- The live reproduction above passes both its former-window gap assertion and
  current-window continuity assertion.
- `probe_spoke.py`: 56 checks; `probe_turns.py`: 78 checks.
- `git diff --check` passes. After the final restart, the log confirms Apple
  audio ready, 48000 Hz input/output/mixer, and spoke ready; launchd reports
  running. The saved config still selects Apple with the TTS effect off.
- No dependency, branch, commit, push, or hub deployment.
