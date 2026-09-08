# 2026-09-07 — Ciel needs to hear the person through the speakers

The scope has expanded from the music follow-up loop to echoes in general.
Assume Mac speaker playback first until the user identifies a different route.
The machine reports macOS 15.6 and has an Apple SDK available. No microphone,
recordings, runtime configuration, or production services were accessed.

## Findings, ordered by impact

1. **Speaker output reaches every detector without echo cancellation.**
   `src/ciel/audio/input.py:119` opens a raw PortAudio input stream. It has no
   playback reference or echo processor. `src/ciel/audio/output.py:100` opens
   output separately. The source therefore supports queue management and
   loudness filtering, but not cancellation of a known speaker signal.
   Proposed fix: provide echo-processed capture at the audio boundary, before
   wake-word detection, utterance endpointing, speaker verification, and STT.
   Barge-in must consume that same processed stream. Requalify gesture detection
   because speech-oriented processing may suppress snaps and claps.

2. **Loudness cannot establish who is talking.**
   `src/ciel/spoke/frontend.py:1013` detects barge-in using sustained RMS above
   the ambient threshold. It does not compare incoming sound to rendered audio.
   It can therefore stop Ciel on its own loud output. The existing disabled
   default is consistent with the README's measured limitation.
   Proposed fix: after echo processing, require evidence of near-end speech
   before interruption. Validate simultaneous user speech and playback; a filter
   that merely silences the microphone is not an acceptable success.

3. **Discarding a queue does not remove continuing playback or room decay.**
   `src/ciel/audio/input.py:220` clears buffered frames. It does not suppress
   future echo. `src/ciel/audio/output.py:209` clears its playing flag after
   writes finish, without an explicit confirmation that the final sample has
   reached the speaker. Residual output timing is a risk inferred from source,
   not a measured fault here. The existing music reproduction next to this
   report, `2026-09-07-music-listening-repro.py`, demonstrates the repeated
   follow-up state transitions and the speech-in-progress timeout exception.
   Proposed fix: maintain processing across playback and its tail, and keep a
   fallback that closes automatic follow-ups when echo protection is unavailable.

## Recommended first implementation candidate

Evaluate a small native Mac audio backend using AVAudioEngine voice processing,
with input and Ciel playback owned by the same engine. Preserve the Python
consumer contract of 16 kHz mono PCM in 30 ms frames by converting and repacking
processed input outside the realtime callback. Realtime handoff must remain
bounded and nonblocking. This is a candidate, not a measured solution yet.

Apple describes voice processing as removing device playback from capture and
requires both I/O nodes in that mode. It runs against a real audio device, not
manual rendering, and must be configured while the engine is stopped:
[Apple, What's New in AVAudioEngine](https://developer.apple.com/videos/play/wwdc2019/510/).

Apple also supplies echo cancellation, noise suppression, and configurable
attenuation of other audio through its native voice processing. The attenuation
behavior matters for an assistant whose input stays open while the user listens
to music. Test it explicitly; do not silently make Spotify permanently quieter:
[Apple, What's new in voice processing](https://developer.apple.com/videos/play/wwdc2023/10235/).

The first acoustic experiment must test independent Spotify/browser playback,
not only audio routed through Ciel's engine. Do not assume every output route
has the same cancellation coverage. If native processing cannot meet that
requirement, the alternative is system-output capture plus a WebRTC echo
canceller, with measured timing between render and capture. This adds complexity
and potentially a dependency requiring user approval under AGENTS.md.

Core Audio taps can capture output from processes; Apple's sample describes
system-audio recording permission and the required usage-description key:
[Apple, Capturing system audio with Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).
WebRTC's audio processor consumes render audio as an echo reference:
[WebRTC audio processing interface](https://webrtc.googlesource.com/src/+/7f95732fe2a05a39da7533db0145614a17042aa8/webrtc/modules/audio_processing/include/audio_processing.h).

A separately playing TV or Spotify Connect speaker provides no reference through
Mac system-output capture. Treat that as background speech: deliberate
activation and the enrolled Barn Door gate can reduce accidental admission, but
neither guarantees rejection of all recordings. Do not promise universal echo
cancellation without access to the source audio.

## Acceptance before changing the live default

- Ciel's replies, acknowledgements, chimes, and alarms do not trigger wake,
  transcription, or self-interruption, including their endings.
- Spotify vocals and browser dialogue playing on the Mac do not initiate turns.
- The user can wake Ciel and interrupt it while that same audio plays; measure
  recognition and interruption delay as well as echo reduction.
- Short and quiet user speech remains usable, including Barn Door verification.
- Snaps and claps are rechecked; do not preserve their behavior by silently
  routing speaker-contaminated audio into an unsafe fallback.
- Device changes, headphones, sleep/wake, and backend failure recover cleanly.
  Show when echo protection is unavailable and require deliberate activation
  rather than silently enabling unprotected automatic listening.
- Probes cover framing, cancellation, process failure, and turn state; a separate
  real-device comparison establishes acoustic performance. Any captured audio
  must be explicitly opted into and handled as private temporary material.

## Verification and limits

Read the current input, output, and spoke code and checked Apple's documentation
and WebRTC's reference-stream contract. The earlier eight-check reproduction
covers the follow-up mechanism only. No acoustic benchmark or native prototype
was run, no dependency was installed, and no production file was changed.
`git diff --check` passed. This report supersedes a music-specific fix as the
main direction; closing follow-ups remains a fallback, not echo cancellation.


## 2026-09-07 — Speech quality did not pass the room trial

After the Apple implementation was enabled, the user reported that Ciel had a
lisp. This is a reported playback regression, not a reproduced acoustic diagnosis.
The previous backend was restored and the spoke restarted successfully. No
synthesis code was changed by the Apple implementation or by this rollback.

`src/ciel/audio/native/CielAudio.swift:150` enables voice processing for both
I/O nodes. The new playback route also schedules short buffers and resamples
through the native mixer. Those are several changed variables; source inspection
alone cannot establish which changed sibilants. Do not attribute the symptom
specifically to Apple's filters without a comparison.

`scripts/probe_apple_audio.py:271` checks one 440 Hz tone and a completion receipt.
Its six live checks prove device transport, not speech fidelity, consonant
preservation, or continuity during streamed synthesis. The 49 offline checks
likewise do not establish those acoustic properties. They were insufficient
justification for enabling this backend for normal use.

The next reproduction must render a fixed sentence once, then compare that
identical PCM through the old and new outputs, including sibilants and the
normal speaker effect. Repeat with streamed chunk timing to separate synthesis,
resampling, processing, and scheduling. Leave the running assistant on the
previous backend during that experiment. This comparison has not been run;
no microphone recordings or runtime configuration contents are included here.

The README and change entry now identify the Apple backend as experimental.
Rollback readiness and `git diff --check` were verified; audible recovery still
needs the user's observation.
