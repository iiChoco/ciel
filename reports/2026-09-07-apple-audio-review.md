# 2026-09-07 — Review of the Apple voice-processing backend

Scope: `src/ciel/audio/apple.py`, `src/ciel/audio/device.py`,
`src/ciel/audio/native/CielAudio.swift`, the `input.py` and `config.py`
changes that carry it, its wiring in `pipeline.py` and `spoke/frontend.py`,
`scripts/probe_apple_audio.py`, and the changelog and README paragraphs.
Read only; the reproduction script beside this report,
`2026-09-07-apple-audio-review-repro.py`, builds its fixtures in temporary
storage and opens no microphone, device, or file under `~/.ciel`.

The design is the right shape: one helper owns both directions, playback is
confirmed by audible receipts, every failure closes capture rather than falling
back to raw audio, and the probe pins the protocol from both sides through real
pipes. The findings below are about what happens around that core. The first
two matter before the backend is tried in the room again; the third is the
leading, unmeasured explanation for the reported lisp.

## Findings, ordered by impact

### 1. A synthesis error tears down the microphone

`src/ciel/audio/apple.py:381` catches `(OSError, RuntimeError,
asyncio.TimeoutError)` around the whole play loop and hands the message to
`AppleSession._failed`, which closes capture and kills the helper. The chunk
source is inside that loop. Piper re-raises any exception from its worker
(`src/ciel/tts/piper.py:137`), onnxruntime's failures arrive as
`RuntimeError`, and the native voice helper raises `RuntimeError` on exit
(`src/ciel/tts/native.py:209`). Any of those now reads as "the device died":
`play()` returns `False` with `device_lost` set, the turn is abandoned with
the wrong cause, the mic loop ends, and the spoke exits for launchd to restart.
The PortAudio `Player` lets the same exception propagate and keeps its device.

Reproduced: repro step 1 plays a generator that raises `RuntimeError` after one
chunk; the microphone is closed and the session error is the TTS message.

Fix: only failures of the session itself should reach `_failed`. Pull the
chunk outside the device-failure handler (or catch around `_next` separately
and re-raise after `session.stop()`), so a TTS exception behaves as it does in
the base class. Add a probe check: "a synthesizer's failure does not close the
microphone".

### 2. Playback runs only 100 ms ahead of the speaker

`src/ciel/audio/apple.py:344` submits 50 ms chunks (`CHUNK_MS` from
`output.py`) and waits for the oldest receipt once three are outstanding.
A `dataPlayedBack` receipt means that chunk has already left the hardware, so
when it lands only two chunks, 100 ms, remain scheduled. The next chunk needs
Python to wake, pull the TTS generator, write and drain the pipe, and the
helper's main queue to schedule it. The frame loop runs wake detection, VAD,
and the gesture ear every 30 ms on the same event loop, and a whisper or
speaker-embedding call can hold the loop's thread pool. A stall past the budget
is silence from `AVAudioPlayerNode`, which splits words at whatever instant the
stall hits, and consonants are what a short gap removes. This is consistent
with the "lisp" report but has not been measured.

The three-deep window buys nothing here that it buys PortAudio: a stop drops
every scheduled buffer inside the engine (`CielAudio.swift:247`), so stop
latency does not grow with the window. Repro step 3 states the arithmetic.

Fix: deepen the window to several hundred milliseconds (eight chunks is
400 ms and still bounded), raise the helper's `pending.count < 4` guard in the
same change, and have the helper report scheduling underruns, for example by
counting `dataPlayedBack` receipts that arrive with nothing left scheduled, so
the room trial can tell a gap from a filter.

### 3. The output route is resampled to the microphone's rate

`src/ciel/audio/native/CielAudio.swift:181` connects the mixer to the output
node at `inputFormat`, whose rate is whatever the voice-processing input node
reports. Ciel's voice enters at 22.05 kHz and carries content to 11 kHz; if
the voice-processing client rate is below 44.1 kHz, every sibilant is
low-passed before it reaches the hardware. The comment says both client sides
must agree, but the live-probe fix was about channel count, not rate; the
mixer resamples between differing rates, and the output side should follow the
output node's own hardware rate. Voice processing also applies its own
processing to the far-end path on macOS, which is the second variable the
echo-handling report names. Neither has been measured; this review cannot open
the device.

Fix: connect the mixer to the output at
`engine.outputNode.outputFormat(forBus: 0).sampleRate` with one channel, put
both rates into the ready frame so `apple.py` logs them, and then run the
fixed-sentence comparison the echo report describes: the same PCM through
PortAudio, through the helper with voice processing off, and with it on.

### 4. The capture ring drops a buffer silently under lock contention

`src/ciel/audio/native/CielAudio.swift:77`: `guard lock.try() else
{ return }`. If the render tap fires while the worker is inside `pop`, the
10 ms buffer is discarded and neither `overflow` nor any counter records it,
contradicting the comment above the class. The copy under the lock is a few
kilobytes, so the collision is rare, but a rare dropped 10 ms is exactly the
kind of gap the wake model and endpointing see as a glitch, and it would be
invisible in any log.

Reproduced: repro step 2 compiles the ring class alone, holds the lock, pushes
one buffer, and shows `count=0 overflow=false`.

Fix: count the dropped buffers under a separate atomic or in the tap-side
branch and have the worker either fail or report after the first, matching the
overflow path.

### 5. Smaller points

- `ensure_built` (`apple.py:56`) hashes the source, the plist, and the
  machine, not the compiler, so a toolchain update never rebuilds. Old binaries
  accumulate in `~/.ciel/bin`; two are already there. Consider hashing
  `swiftc --version` and removing stale `cielaudio-*` files after a successful
  build.
- Each rebuilt helper is a new ad-hoc-signed binary at a new path. If macOS
  attributes the microphone request to the helper rather than to the
  responsible launcher, every source edit re-prompts and leaves a stale row in
  System Settings. Worth one look at the Microphone list after the next
  rebuild.
- Ducking is engaged for as long as the microphone is open, which is always.
  With advanced ducking on, other apps dip whenever anyone speaks near the
  Mac, not only during a turn. The README says `min` is not zero; the room
  trial should listen for this with Spotify playing and nobody addressing Ciel.
- A route change ends the helper, the mic loop, and the process; launchd's
  `KeepAlive` brings the spoke back in about fifteen seconds with a fresh
  greeting. The README says "restart Ciel"; it could say this happens on its
  own, and the greeting on every headphone plug is worth knowing about.
- `ApplePlayer.__init__` imports `webrtcvad` at construction, so building the
  pair on a machine without the extra fails before any device is touched.
  That is fine, but the error is an `ImportError` rather than the backend's
  own message.

## What was verified

- `uv run --no-sync python scripts/probe_apple_audio.py`: all 49 checks pass.
- `uv run --no-sync python reports/2026-09-07-apple-audio-review-repro.py`:
  findings 1 and 4 reproduced; finding 2 is arithmetic.
- `git diff --check` is clean.
- Not run: `--live`, any device, the fixed-sentence comparison. Findings 2
  and 3 are causes consistent with the reported symptom, not diagnoses.

## 2026-09-07 — After the four fixes, the lisp remains

The fixes landed: the playback window is twenty chunks (one second), the
output side takes its rate from the default speaker, the helper reports the
negotiated rates in its ready frame, and the ring counts contention. The spoke
log then showed the input, output, and mixer all at 48 kHz, and the room still
heard a lisp. That rules out findings 2 and 3 as the cause, and findings 1 and
4 never touched playback.

What is left is the route itself. With voice processing enabled, everything
the engine plays passes through Apple's voice-processing I/O unit as far-end
audio, and that unit treats the far end as a call: mono, voice band, its own
equalisation. Nothing in `CielAudio.swift` chooses that; enabling voice
processing on the input node enables it on the output node too, and the code
requires both. Piper at 22.05 kHz carries sibilants to 11 kHz, which is the
part a call-grade far end discards.

The experiment that separates the cause from the remedy: keep the helper for
capture only and play Ciel's voice through the PortAudio `Player` on the same
default output. The helper's engine still needs its output node running for
voice processing, playing nothing. The echo reference on this Mac is taken at
the aggregate device (the live probe found its reference channels there), so
playback from another process should still be cancelled; that is also the
independent-playback check the echo report asked for. If Ciel sounds right and
its own voice no longer wakes it, the pair becomes Apple microphone plus
PortAudio speaker, and `device.py`'s one-owner rule has to be relaxed on
purpose. If Ciel sounds right but hears itself, the far end must stay inside
the engine and the voice band is the price of cancellation.

## 2026-09-07 — The room confirms the far end

`2026-09-07-split-pair-experiment.py` played one Piper sentence three ways
through the MacBook speakers: PortAudio alone, PortAudio while the helper
captured with an idle output node, and inside the engine. Only the engine
play lisped. The engine reported 48 kHz on input, output, and mixer, so the
rate is not involved: the lisp is Apple's voice-processing far end, and
playing Ciel's voice outside the engine removes it.

Whether the split pair still cancels is not yet settled. Processed capture
rose about 9 dB during the PortAudio play and did not rise at all during the
engine play, so playback from another process is cancelled less completely.
The speech fractions in that run cannot be read: the engine play showed 80%
"speech" at a level that never left the quiet floor, which is the detector
reacting to comfort noise. The script now prints the quiet-window speech
fraction beside it and a scale-free coherence between the capture and the
played sentence; a synthetic check scores a buried echo at 0.24 and no echo at
0.02. The next run reads that column for B.

## 2026-09-08 — The split pair is live and the lisp is gone

`audio.apple_playback` landed with `portaudio` as its default: Apple's engine
captures, the old PortAudio speaker speaks on the same default output. The
spoke reloaded onto it and the user judged the lisp gone by ear. Still owed in
the room: whether Ciel wakes on her own voice or opens follow-up windows on her
own answers through the split pair, snaps and claps on processed capture, and
Barn Door scores. Nothing is committed.
