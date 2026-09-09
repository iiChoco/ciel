# 2026-09-08 — The split pair still turns the room down

The split pair is a strong explanation for the reported intermittent quiet
MacBook audio. Source inspection establishes the mechanism, and a read-only
process check found the Apple helper running with minimum ducking and AGC off.
No listening comparison or measurement of the reported dips was performed, so
this does not establish that every dip has this cause.

## Findings, ordered by impact

### 1. Apple voice processing remains active between turns and ducks other audio

**Where.** `src/ciel/audio/native/CielAudio.swift:213` enables voice processing;
`:226` unconditionally enables advanced ducking. `src/ciel/audio/device.py:30`
creates the same Apple session for the split route, while
`src/ciel/spoke/frontend.py:267` keeps the microphone open around the entire
frame loop. `src/ciel/config.py:57` defaults the ducking level to minimum.

**Evidence.** The split moves playback to PortAudio, but it does not remove the
Apple input/output voice-processing unit. Apple describes advanced ducking as
attenuation that increases when speech is detected and relaxes between speech.
It acts before Ciel's wake detector or transcription gate decides whether the
sound is addressed to Ciel. Nearby conversation can therefore lower music or
browser audio even when Ciel never wakes. The implementation contains no system
volume-slider write in the audio or spoke paths; this mechanism attenuates the
mix rather than implementing a volume-key change.

Apple also counts an app's streams rendered outside voice processing as other
audio. Consequently Ciel's own PortAudio voice is eligible for attenuation on
the split route. This is an inference from the documented routing semantics;
its audible size and timing were not measured here.

**Proposed fix.** For immediate relief, explicitly select
`[audio] backend = "portaudio"` and restart the spoke. That removes this Apple
voice-processing path but also removes its echo cancellation. Changing only
`apple_playback` cannot remove the ducking. A lasting solution that preserves
continuous echo cancellation needs an echo-processing path with an explicit
render reference and no system ducking; it requires its own design and room
validation. Opening Apple only during active turns is another tradeoff, but
loses protected idle wake detection and introduces device-transition latency.

### 2. Ciel's mute and Stop do not release the source of attenuation

**Where.** `src/ciel/spoke/frontend.py:973` changes Ciel's mute state and stops
its player; `src/ciel/audio/apple.py:396` gives the split player no ownership of
the helper. Only microphone close (`src/ciel/audio/apple.py:382`) closes the
session, whose cleanup is at `src/ciel/audio/apple.py:322`.

**Reproduced.** The companion script runs the real pair factory and spoke mute
method with a temporary fake helper. Capture begins before any playback, and
Stop, mute, and speaker close leave the helper alive. Capture still delivers
frames while muted. Microphone close reaps it. This verifies lifecycle behavior,
not actual attenuation by the fake helper.

**Proposed fix.** If mute is meant to return the Mac's audio to normal, have it
release the voice-processing session and reopen it on unmute, with lifecycle
and real-device checks. Setting Apple's microphone-mute property alone should
not be assumed to disable ducking without a device test. This would address
muted operation, not the idle unmuted case above.

## What the supported API permits

Minimum means minimum attenuation, not an off switch. Apple's documented
levels are default, minimum, medium, and maximum. Disabling advanced ducking
selects fixed attenuation rather than disabling ducking. The installed SDK's
old `kAUVoiceIOProperty_DuckNonVoiceAudio` switch is deprecated and explicitly
unavailable on macOS. Using an undocumented enum value or that obsolete switch
would not be a supported fix.

Sources checked on 2026-09-08:

- [Apple: What's new in voice processing, Other audio ducking](https://developer.apple.com/videos/play/wwdc2023/10235/)
- [Apple: Supported ducking levels](https://developer.apple.com/documentation/avfaudio/avaudiovoiceprocessingotheraudioduckingconfiguration/level)
- Installed SDK headers `AVFAudio.framework/Versions/A/Headers/AVAudioIONode.h`
  and `AudioToolbox.framework/Versions/A/Headers/AudioUnitProperties.h` under
  `/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/System/Library/Frameworks/`.

The README already describes the idle ducking limitation at `README.md:1606`.
The split playback improvement did not remove it.

## Verification

- `uv run --no-sync python scripts/probe_apple_audio.py`: all 81 checks passed,
  including native compilation, temporary helper lifecycle, and split routing.
- `uv run --no-sync python reports/2026-09-08-audio-ducking-repro.py`: all 7
  checks passed. All fixtures and mute state live in a temporary directory.
- `git diff --check` and whitespace checks on the two new report files passed.
- No microphone opened, audio played or recorded, runtime files read, settings
  changed, production source edited, service restarted, dependency added, or
  commit/deploy performed. Existing uncommitted work was preserved.

To establish audible causality, compare the same media passage with Ciel's
Apple session running and with the spoke explicitly using the raw PortAudio
backend, at unchanged system and media volume. Include quiet and nearby speech
in each run. Ciel mute is not a valid off condition, as finding 2 demonstrates.


## Keeping echo cancellation without ducking

Follow-up investigated on 2026-09-08. The requirement is continuous echo
cancellation while preserving normal Mac playback volume. The supported Apple
voice-processing controls inspected do not supply an independent ducking-off
setting. Fixed minimum ducking would remove the speech-dependent variation but
still attenuate playback, so it does not satisfy this requirement.

**Proposed implementation.** Add an opt-in audio backend through the existing
`build_audio` factory. Keep the ordinary PortAudio speaker. Capture raw mic
samples and a private, nonmuting Core Audio process tap covering the audio
rendered on the selected output, including Ciel's Python process and browser or
music processes. Send the tapped reference and raw mic to WebRTC Audio
Processing with its echo canceller enabled. Return only processed microphone
frames to the existing wake, Barn Door, endpointing, and transcription consumers.
Never feed the processed reference back to the speaker; the tap observes it.

The native helper should own the tap and microphone timing together. Use
hardware timestamps, bounded queues, resampling, and drift handling to keep
render and capture aligned. Process at 48 kHz in 10 ms blocks and convert the
cleaned mic back to the existing 16 kHz mono, 30 ms contract. Disable automatic
gain changes initially. A lost reference, overflow, route change, or denied
permission must report that protected capture is unavailable, rather than
silently passing raw audio into automatic listening. This is a proposed design;
a build and acoustic trial must determine achievable quality and latency.

**New dependency.** WebRTC Audio Processing (native, including its echo
canceller); choose and pin a maintained build compatible with this Mac before
integration. The installed `webrtcvad-wheels` is a voice detector and does not
supply this processor. `AGENTS.md:111` explicitly says "No new dependency
without asking." No dependency was downloaded or installed during this review.
This is the decision requiring approval before implementation can proceed.

**Platform permission.** This Mac reports macOS 15.6; Apple's process-tap sample
requires 14.2 or newer. Add `NSAudioCaptureUsageDescription` to the helper's
metadata. macOS asks for system-audio recording permission when the tap starts.
The intended backend keeps the reference in bounded memory; it does not save
or transmit that reference. An independently playing TV or network speaker has
no reference in this tap and remains outside the cancellation claim.

**Acceptance.** Before switching the live backend, compile and run synthetic
checks using known render, echo, and independent near-end signals; cover delay,
drift, double talk, clipping, lost reference, route changes, and cleanup. Then
measure a controlled Mac-speaker trial for music/browser dialogue, Ciel's
replies, simultaneous user speech, and quiet user speech. Establish that
playback stays unchanged during nearby speech while echo reduction remains
useful. Recheck wake recognition, Barn Door, gestures, and interruption latency.
A low residual level alone is insufficient: suppressing the user's voice must
fail the trial. The final implementation needs the affected probes, README,
configuration documentation, and changelog; no production implementation was
made in this follow-up.

References checked:

- [Apple's process-tap sample and permission requirements](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)
- [WebRTC's render-reference, capture, frame-size, and delay contract](https://webrtc.googlesource.com/src/+/refs/heads/main/api/audio/audio_processing.h)

Verification for the follow-up was source/API review and `git diff --check`.
The prior 81 + 7 checks establish the existing backend's behavior, not the
proposed replacement's echo quality. No live audio was opened or changed.


## 2026-09-08 — The replacement is implemented; service permission is pending

The user approved the new dependency and implementation. `audio.backend =
"webrtc"` now selects `src/ciel/audio/webrtc.py:159` through the existing
factory. `pywebrtc-audio==0.2.0` is pinned in the Mac spoke group and lockfile;
`uv sync --locked --all-extras` preserved the other installed extras.

`src/ciel/audio/native/CielCapture.swift:161` creates a private nonmuting tap
of the default stereo output. The aggregate at `:175` synchronizes it with the
raw mic and compensates tap drift. The three channels are resampled together
to 16 kHz; `src/ciel/audio/webrtc.py:112` feeds exact 10 ms blocks into AEC3,
retaining stereo reference channels, then returns the usual mono 30 ms frames.
Processing at 16 kHz, rather than the preliminary proposal's 48 kHz, matches
Ciel's microphone contract and passed the paired-resampling and acoustic tests.

**The idle output needed its own clock.** The first live comparison opened a
PortAudio stream before opening the tap, which hid a cold-start failure. The
expanded comparison opens protected capture before any audible output and
reproduced the stall. Including a silent output only in the native aggregate
did not resolve it. An ordinary, zero-filled PortAudio stream did;
`src/ciel/audio/webrtc.py:189` now owns that stream through microphone close.
Its samples are zero, its gain is untouched, and no voice-processing unit
opens. The final live script pins cold start as well as amplitude and echo.

**The responsible app needs permission too.** The installed launchd launcher
initially lacked `NSAudioCaptureUsageDescription`. The sibling infrastructure
repository's `services/launcher/Info.plist` now includes it, its regression
checks verify it, and the installed Ciel.app was rebuilt and signed. macOS's
permission log then explicitly reported a new microphone prompt for the rebuilt
responsible app. The running service initially did not reach `WebRTC audio ready`. Do not confuse the earlier `spoke ready` warm-up message with an
opened microphone: that message precedes audio startup. Settings were opened for the owner to allow Ciel audio access.
A subsequent check confirmed `WebRTC audio ready`: permissions are now working. No permission records were reset or edited.

**Measurements.** The final `2026-09-08-nonducking-audio-live.py --live` run
passed all 4 checks: cold start before playback, +0.21 dB speaker amplitude
change with the backend, 18.5 dB raw-to-clean echo reduction, and clean capture
shutdown. An earlier run measured −0.13 dB and 21.8 dB. These are room/signal
measurements, not promises for arbitrary playback or accessories. Audio was
held only in memory and discarded. The service was restored after each test;
restored means registered/running, not that its new permission was granted.

**Speech boundary.** The 40-check offline probe measured 23.2 dB removal of a
synthetic delayed stereo echo and 0.92 correlation for voiced near-end speech
above that echo, compared against the same microphone high-pass path. Quiet
speech without playback passed separately. A voice below loud broadband echo
was attenuated in investigation, so whispered double talk is not qualified.
Actual owner speech, Barn Door, snaps/claps, and other routes still need a room
check. Barge-in was not enabled.

**Final verification.** `probe_webrtc_audio.py` passed 40 checks,
`probe_apple_audio.py` 81, `probe_input.py` 9, and `probe_hub_imports.py` 6.
The infrastructure `python3 -m unittest discover -s tests -v` suite passed 9
tests, and both launcher plists passed `plutil -lint`. Both repositories passed
`git diff --check`. No branch, commit, push, or hub deployment was performed.
Existing unrelated uncommitted work was preserved. The initial activation
was permission-blocked; the subsequent restart investigation below supersedes
that status.


## Restart regression after permission was granted

**Impact.** The nonducking helper reached audio-ready, then exited on capture
clock gaps or ring contention. Launchd's KeepAlive restarted the entire spoke.
The short initial live comparison had missed a sustained-use failure. The
permission failure was no longer the cause; source autoreload was not causing
these repeated exits.

**Reproduction and fix.** `src/ciel/audio/native/CielCapture.swift:73` previously
signalled fatal failure on a busy callback lock or full ring, and its writer
terminated on every sample-time discontinuity. `scripts/probe_webrtc_audio.py`
now invokes native contention and overflow cases and injects a discontinuity
into the real Python subprocess protocol. The native ring drops mic and stereo
reference together and copies an occupied slot outside the callback lock.
`src/ciel/audio/native/CielCapture.swift:121` detects the next timing gap;
the writer resets the resampler and sends a recovery boundary.
`src/ciel/audio/webrtc.py:241` creates fresh AEC3 state and clears queued audio,
without closing the microphone or restarting the spoke. The protocol fixture
verifies output is identical to a fresh protected stream: delayed mic, partial
frames, and queued pre-gap samples cannot cross the boundary. A malformed or
missing reference still fails visibly. No raw capture fallback was added.

**Limits.** Recovery can interrupt a word. Echo cancellation remains enabled,
but its filter must readapt after a gap; this is not a promise of unchanged echo
rejection during recovery. The live script now deliberately pauses the helper
for 300 ms and verifies that the same helper resumes protected frames. Audio
measurements remain in memory and are discarded.

**Verification.** The corrected offline probe passes 47 checks (40 → 47);
Apple audio remains 81 → 81 and input remains 9 → 9. The first repeated live
comparison passed cold start, same-helper gap recovery, resumed protected frames,
and clean shutdown; echo reduction was 25.9 dB. Its speaker amplitude comparison
was −2.88 dB, outside the ±1.5 dB tolerance, so the overall live run failed.
This result is retained rather than counted as a passing volume check.

A single repeat passed all 6 live checks: +0.38 dB playback amplitude change,
16.9 dB echo reduction, cold start, same-helper recovery, resumed protected
frames, and clean shutdown. The variation between the two acoustic runs means
the earlier out-of-tolerance comparison is not explained conclusively; it must
not be silently replaced by the passing run. Neither test retained recordings.

**Installed service.** After restoring launchd, the spoke reached both
`spoke ready` and `WebRTC audio ready`. A subsequent 205-second observation
kept the same launcher and run count, with no new audio startups or capture
errors. This establishes short sustained uptime after the correction, not
an overnight stability claim. `git diff --check` passed. No commit, push,
hub deployment, launcher rebuild, or permission reset was performed during
this recovery correction.
