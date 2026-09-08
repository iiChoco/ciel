# 2026-09-07 — The Apple audio review, resolved item by item

The requested code fixes are in the checkout. The spoke was restarted and is
running with Apple echo cancellation, minimum ducking, and no TTS effect.
The original review is `reports/2026-09-07-apple-audio-review.md`; the initial
fixes and measurements are in `reports/2026-09-07-apple-playback-investigation.md`.
No claim is made that transport checks establish perceived speech fidelity.

## Playback starvation is now observable

`src/ciel/audio/native/CielAudio.swift:171` tracks an utterance separately from
its pending buffers. The protocol has explicit begin/end commands. A refill
after the last audible receipt while that utterance remains open reports a
count and elapsed empty-queue time. Ending a sentence, stopping it, or beginning
another does not manufacture an underrun. `src/ciel/audio/apple.py:287` validates
and logs the report without ending protected capture.

The live reproduction at `reports/2026-09-07-apple-playback-repro.py` now checks
both the rendered waveform and native telemetry. A 300 ms Python stall with
the former three-buffer window produced 213.5 ms of internal silence and one
underrun report (182.1 ms measured after the audible receipt). The current
twenty-buffer window produced no internal silence and no underrun report.
The log duration is a lower bound, not an exact render-gap meter. Native
callback delays or output latency can conceal some starvation; no claim is
made that every possible underrun is detected.

## The four main fixes remain in place

- A synthesis RuntimeError, OSError, or TimeoutError propagates to the caller,
  stops the utterance, and leaves healthy capture open. A subsequent utterance
  can play. Transport and receipt failures still end the protected session.
- Both sides enforce the twenty-buffer playback bound; Stop discards it and
  completion waits for the last audible receipt.
- `CielAudio.swift:151` reads the default output device's nominal rate through
  Core Audio. Output no longer inherits the microphone rate. The startup log
  reports input, output, and mixer rates independently. All measured 48000 Hz.
- Capture lock contention uses a semaphore to wake the worker with a visible
  failure; it no longer silently discards a microphone buffer.

The Apple probe reruns regression coverage for all four.

## Rebuilds have a stable location and compiler identity

`src/ciel/audio/apple.py:62` fingerprints the source, embedded plist,
architecture, resolved compiler path, and compiler version. It publishes an
owner-only `cielaudio` executable and private manifest under a build lock.
Atomic replacement leaves a running executable's inode untouched. Failed
compilation preserves the prior executable and manifest and removes temporary
outputs. A cache hit verifies the recorded executable hash.

The code-signing identifier is explicitly `ai.ciel.audio`. Cleanup at
`apple.py:128` only removes regular, non-symlink files matching the former
`cielaudio-<16 hex digits>` convention. Other binaries, directories, and
symlinks remain untouched. Runtime inspection after migration found no old
hash-named helper binaries.

Probes demonstrate a compiler-version change triggering exactly one rebuild
at the same path, reuse afterward, stable signing identifier, narrow cleanup,
and preservation after a failed compilation.

## Permission attribution is explicit, not bypassed

`CielAudio.swift:320` adds `--permission-status`, which reads authorization
without prompting or opening a microphone. The new executable reported
`authorized` in the calling context, and the launchd spoke subsequently
opened Apple audio successfully. The stable path and signing identifier remove
avoidable identity churn.

A local ad-hoc signature is still tied to a particular program version;
macOS may ask again after a rebuild depending on the responsible launcher.
Guaranteeing approval across code changes would require a suitable persistent
signing identity. No certificate, Keychain item, custom trust requirement, or
privacy-database change was introduced. See Apple's
[code-signing requirements explanation](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements/).

The requested visual inspection of the System Settings microphone list could
not be completed: the computer-use tool's process failed on both attempts.
The authorization check and successful production startup were verified; the
absence of historical duplicate UI entries was not verified.

## Ducking and route changes are documented accurately

The supported Mac voice-processing API exposes default/minimum/medium/maximum
attenuation, not an off level. The old disable-ducking property in the installed
SDK is deprecated and explicitly unavailable on macOS. Minimum advanced ducking
remains selected, retaining echo cancellation. Because the microphone stays
open, room speech may attenuate other apps outside Ciel turns. Disabling
advanced ducking would make attenuation constant, not remove it. See Apple's
[ducking levels and speech activity behavior](https://developer.apple.com/documentation/avfaudio/avaudiovoiceprocessingotheraudioduckingconfiguration/level).

The README now states that a route change terminates this audio session;
launchd KeepAlive restarts the spoke after its retry delay, with the normal
startup greeting. A foreground invocation needs a manual restart. This is
session recovery, not seamless device switching. No physical route change or
Spotify/nearby-speech listening test was performed in this pass.

## Missing VAD has a useful failure message

`src/ciel/audio/apple.py:391` converts the missing-import case into an Apple
backend error naming `webrtcvad-wheels`, the spoke dependency group, and the
repository's `uv sync --locked --all-extras` restoration command. No dependency
was added or installed. An isolated missing-module probe verifies the message.

## Identical speech went through the requested three paths

`reports/2026-09-07-apple-speech-comparison.py --live` generated one sentence
using macOS say at 22050 Hz, attenuated it once, and used exactly those PCM
bytes for PortAudio, a temporary Apple helper with voice processing off, and
the production Apple helper with voice processing on. The temporary disabled
helper truthfully reported its disabled state to a separate diagnostic client;
production has no raw-capture fallback. All three completed, both Apple paths
reported 48000 Hz for input/output/mixer, and neither reported starvation.

The spoke was briefly stopped to isolate the routes and restored in a finally
block. No microphone audio, runtime data, or model was copied or recorded.
This was a system-voice fixture, not the user's Piper voice. Output loudness
was not physically matched across devices. The user's listening judgment of
the lisp, real-room echo rejection, snaps, and double-talk remain unverified.

## Checks and running state

- `probe_apple_audio.py`: 61 → 73 checks passed.
- `probe_apple_audio.py --live`: 8 checks passed.
- The live stall reproduction verified waveform continuity and telemetry.
- The three-path speech comparison completed successfully.
- `git diff --check` passed.
- Final startup: spoke ready, Apple audio ready, input/output/mixer 48000 Hz;
  launchd reports running. Saved config remains Apple backend and TTS effect off.
- No dependency, branch, commit, push, or hub deployment.
