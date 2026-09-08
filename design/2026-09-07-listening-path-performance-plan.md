# The listening path costs less at rest

Investigation and plan — 2026-09-07. Nothing here is implemented; this
document records what the code and the running Mac say about three
suspected costs of always-on listening, and how each would be fixed and
measured. Two of the three suspicions survive contact with the code; the
third cannot be done as asked.

## What the Mac measures right now

Sampled with `top -l` at 2 s intervals over 30–60 s while the spoke sat in
`WAITING`, on 2026-09-07 between 22:40 and 23:10 PDT. Resident memory is
`ps -o rss`. The Energy tab in Activity Monitor is a window I cannot drive;
its "Energy Impact" column is the user's reading to take beside these.

| Process | CPU at rest | RSS | Note |
|---|---|---|---|
| `coreaudiod` | 13.2–16.4 % | 25–41 MB | steady across every sample |
| `WindowServer` | 7.3 % … 34.6 % | ~1.2 GB | swings with whatever else is on screen; 44.9–48.5 % while a chip breathes |
| spoke (`python -m ciel spoke`) | 2.1–3.3 % | 88–163 MB | includes wake, VAD, gesture ear, silero VAD |
| HUD (`python -m ciel.ui.hud`) | 0.0 % | 29–30 MB | idle, `hide_when_idle = true` |

Two cautions about the numbers. WindowServer's rest figure moved between
7 % and 35 % across four control samples taken minutes apart with no change
to Ciel, so anything under about 15 points is noise unless the screen is
otherwise quiet; the desktop app streaming this very session is one of the
things moving it. And `top`'s MEM column reports 2.7 GB for the spoke where
`ps` reports 88–163 MB; the plan uses RSS from `ps`.

Reproduction scripts from this investigation sit in the session scratchpad
(`hud_cost.py`, `hud_cost2.py`, `mic_block_cost.py`); the implementation
should carry a cleaned-up sampler into `reports/` beside the write-up.

## 1. Capture wakes CoreAudio 33 times a second — confirmed, fixable

**What the code does.** [`input.py:116-124`](../src/ciel/audio/input.py)
opens the PortAudio stream with `blocksize=FRAME_SAMPLES` (480 samples,
30 ms), on purpose: the comment says the callback then hands over exactly
one webrtcvad-sized frame. The cost is one CoreAudio IO cycle plus one
PortAudio callback every 30 ms. Every consumer downstream is written in
30 ms frames and must stay that way: the endpointer rejects any other size
([`vad.py:94-97`](../src/ciel/audio/vad.py)), the gesture ear's ring
buffers are sized in `FRAME_BYTES` multiples, `SilenceWatch` counts frames,
and the wake word's cooldown and near-miss windows are literally `33`
frames ([`wake.py:189,203`](../src/ciel/audio/wake.py)).

**What the measurement says.** Opening a *second* 16 kHz stream beside the
live one added nothing measurable at any block size (480: 16.4 %, 1920:
14.0 %, 3840: 15.4 %, against controls of 15.4 % and 16.0 %), and each
delivered exactly the expected callback rate (33.3 / 8.3 / 4.2 per second).
That is the expected shape if `coreaudiod` runs the device at the smallest
buffer any client asks for: while the spoke's 30 ms stream is open, a
larger client changes nothing. So the only honest before/after is with the
spoke's own stream at the new size, which the autoreloader gives us for
free: edit, wait for `spoke ready`, sample.

**The change.**

- A field `capture_block_ms: int = 120` on `AudioConfig` in
  [`config.py`](../src/ciel/config.py), with the docstring explaining the
  trade: fewer CoreAudio wakeups against up to `block − 30` ms added before
  a wake word is scored. Must be a positive multiple of `FRAME_MS`;
  validated where the other audio fields are.
- `MicStream.__aenter__` opens the stream with `blocksize = capture_block_ms
  × 16`. `_on_audio` slices `bytes(indata)` into `FRAME_BYTES` pieces and
  appends each to the deque, so the queue, its `maxlen`, the drop counter,
  `drain()`, `frames()`, and every consumer keep their 30 ms unit. A
  remainder shorter than a frame (PortAudio only produces one at teardown)
  is carried, not dropped. The slicing is a small pure function so the
  probe can pin it without a device.
- The "microphone open" log line names the block in ms beside the frame
  size, since that line is where the next person will look.
- The Apple backend is untouched: [`apple.py:282-285`](../src/ciel/audio/apple.py)
  already re-frames the helper's PCM into whole `FRAME_BYTES` frames before
  calling `_on_audio`, and a whole frame slices to itself.

**Latency.** openWakeWord scores in 80 ms (1280-sample) chunks and returns
the previous score for anything shorter
(`openwakeword/model.py:287-303`), so a 30 ms feed already only decides
every 80 ms. A 120 ms block adds at most 90 ms before the score that fires
the wake; the acknowledgement phrase covers that. 240 ms adds up to 210 ms
and is the point where "Uh huh?" may start to feel late. Plan: implement
with the default at 120, measure both 120 and 240, keep the larger only if
the acknowledgement still feels immediate by ear over ten wakes.

**Probes.** `probe_input.py` gains checks for the slicer: a block of four
frames yields four frames in order, a block plus a partial carries the
remainder into the next block, an exact frame yields itself, and a block
that is not a multiple still loses nothing. `probe_audio.py mic` prints the
observed callback rate so the live check has a number. Target
`probe_input.py 9 → 13`.

**Measure.** Sixty seconds of `coreaudiod` and spoke CPU and RSS before,
at 120, and at 240, screen quiet; plus the Energy tab readings.

**README.** The `[audio]` block in Configuration gains the field with its
one-line trade; "How it fits together" needs no change.

## 2. The hidden pulse — hypothesis not supported, a cheaper fix still applies

**What the code does.** Idle has no breath:
[`hud.py:110`](../src/ciel/ui/hud.py) defines the idle `Look` with
`breath_s = 0.0`, and `applyState_` removes the breath animation
unconditionally ([`hud.py:328`](../src/ciel/ui/hud.py)) before deciding
the window's alpha. With `hide_when_idle` the idle window sits at alpha 0,
ordered in, with no animation attached. The HUD process itself was at
0.0 % CPU in every sample, but Core Animation runs in the render server, so
that alone proves nothing about WindowServer.

**What the measurement says.** A throwaway HUD (`hide_when_idle = true`,
top-left corner, driven over stdin exactly as the spoke drives the real
one) was sampled through idle → listening → idle → quit, twice. An idle
hidden chip cost nothing detectable (11.1 % vs 7.3 % control in run one;
19.4 % vs 29.6 % in run two, i.e. inside the noise). A *breathing* chip
took WindowServer to 44.9–48.5 % both times. Returning to idle brought it
down again in run two (26.4 %); in run one it did not (44.4 %, and 43.6 %
after the process had quit), which points at something else on screen
rather than the departed chip. The user's 39 % reading therefore most
likely came from a moment when the chip was breathing, or from another
window entirely; the hidden idle chip is not animating.

**What is still worth doing.**

- *Order the window out when idle.* An alpha-0 window remains in the
  compositor's window list, across all Spaces, at level 25. With
  `hide_when_idle`, call `orderOut_` on idle and `orderFrontRegardless` on
  any other state instead of setting alpha 0. Strictly cheaper, and it is
  what "hide" should have meant.
- *Make the breath cheaper.* The dot layer carries a soft shadow (the glow)
  and the breath animates `transform.scale`; a scaled layer with a live
  shadow re-renders that blur every frame at the display's refresh rate,
  which is where a 15–25 point WindowServer cost during a turn comes
  from. Setting `shouldRasterize` with `rasterizationScale` matched to the
  screen on the dot lets the render server cache the glow and only
  composite it. If that alone does not close the gap, animate the glow's
  opacity rather than its scale. Either way the Chart's `br` keyframes
  are the reference and the chip must still read as the same instrument;
  the visual checks (both corners, scale 1 and 1.5, light and dark
  desktops, each state) are named in the changelog per the Instrument
  rule.

**Probes.** The HUD has no probe today. Add `scripts/probe_hud.py`
importing the module on macOS and pinning the pure decision: for each state
and each `hide_when_idle`, whether the window is ordered in and whether a
breath runs (idle never breathes; hidden idle is ordered out; a breathing
state is ordered in and rasterized). `probe_spoke.py`'s indicator checks
stay as they are.

**Measure.** The decisive A/B for the live chip: kill the HUD's pid (the
spoke logs "hud pipe closed; disabling indicator" and keeps running; the
chip returns on the next reload) and sample WindowServer for sixty seconds
with the screen quiet, then again with the chip back. Then during a spoken
turn, before and after the rasterize change, since that is where the cost
actually lives.

**README.** "The status pill" already says `hide_when_idle` vanishes the
chip; add a sentence that hidden means removed from the screen, not
transparent.

## 3. tflite — cannot be done as asked; needs a decision

**What the code says.** `tflite_runtime` is not installed and cannot be:
PyPI's `tflite-runtime` 2.12–2.14 ship no macOS wheels for any Python, and
openWakeWord itself only depends on it on Linux
(`Requires-Dist: tflite-runtime ; platform_system == "Linux"`). On this Mac,
passing `inference_framework="tflite"` reaches `openwakeword/model.py:122`,
logs a warning, and silently falls back to onnx for a pretrained name (or
raises for a custom path). The docstring on `OpenWakeWord` in
[`wake.py`](../src/ciel/audio/wake.py) already records this conclusion, and
[`pyproject.toml:97`](../pyproject.toml) records it for the resolver.

The thread argument does not hold either: the wake model's onnx session is
already `inter_op = intra_op = 1` (`model.py:150-151`), and the melspectrogram
and embedding sessions run with `ncpu = 1` (`utils.py:80-81`). The tflite
path sets `num_threads=1` on the same three interpreters: the same
constraint, not a tighter one.

**Expected size of the prize.** The whole spoke process, wake included, is
2.1–3.3 % CPU at rest. Item 3's ceiling is a fraction of that.

**Options.**

1. *Drop it, and note why in the changelog for item 1* — recommended.
2. *Add a dependency.* Google's successor package `ai-edge-litert` 2.2.0
   does ship `cp312 macosx_12_0_arm64` wheels. Using it means a new
   dependency in `pyproject.toml` plus a shim that presents
   `ai_edge_litert.interpreter` under the `tflite_runtime.interpreter` name
   openWakeWord imports. That is a new dependency and a hack, and the
   project's rule is no new dependency without asking. If chosen, the
   benchmark is spoke CPU and RSS over sixty seconds at rest and across ten
   wakes, onnx versus litert, with wake-latency and false-accept unchanged
   per `probe_wake_model.py`.

If per-frame model cost ever matters, the knob that exists is
`wake.vad_threshold`: above 0 it runs silero VAD as a third onnx session on
every 80 ms chunk. Out of scope here; named so the next person knows.

## Order of work

1. Baseline: sixty seconds of all four processes with the screen quiet,
   plus the Energy tab, written to `reports/2026-09-07-listening-path-*.md`
   with the sampler beside it.
2. Item 1 at 120, measure; at 240, measure; choose. Changelog entry, README,
   `probe_input.py`.
3. Item 2: order-out first (small, certain), A/B; then the rasterized glow,
   with the visual checks named. Changelog entry, README, `probe_hud.py`.
4. Item 3: the user's decision between options 1 and 2 before anything is
   touched.

Each item is its own commit and changelog entry, in the project's voice.

## A note on the checkout

While this investigation ran, the spoke's out log recorded reloads from
edits to `src/ciel/audio/apple.py` and `src/ciel/config.py`;
`apple.py`, `device.py`, and `audio/native/CielAudio.swift` were not in
this session's opening `git status`, and `input.py` was modified at 23:08
(a `_warn_on_digital_silence` opt-out so the Apple subclass can skip the
silence watch — nothing near the block size). Another writer is building
the Apple voice-processing backend in the audio layer right now, and
`AppleMic` subclasses `MicStream`. Item 1 touches `input.py`'s
`__aenter__` and `_on_audio`; agree file ownership before any edit lands,
per "one writer per checkout", and re-read `input.py` first.
