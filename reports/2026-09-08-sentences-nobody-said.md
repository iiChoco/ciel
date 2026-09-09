# Sentences nobody said — 2026-09-08

Ciel answered a run of turns in an afternoon when nobody had spoken. This
is what let each one through, what was measured, and what was changed.
Reproduction: `2026-09-08-sentences-nobody-said-repro.py` beside this file.

## Findings, by impact

### 1. Whisper never says "nothing", and cannot be asked how sure it is

`src/ciel/stt/local_mlx.py` and `local_whisper.py` hand every endpointed
utterance to the decoder and keep whatever comes back unless it is a stock
phrase, a prompt echo, or a repetition wall (`stt/hallucinations.py`).
Handed audio with no speech in it, large-v3-turbo returns a sentence every
time, and the sentences invented over a keyboard are not stock.

Reproduced with the script (synthetic audio, no microphone):

| audio                       | Whisper said                          | no_speech_prob | avg_logprob |
|-----------------------------|---------------------------------------|---------------:|------------:|
| pure silence, 1.5 s         | "Thank you."                          | 0.000 | -0.31 |
| room noise, 0.003 RMS       | "Thank you."                          | 0.000 | -0.32 |
| room noise, 0.02 RMS        | the initial prompt, verbatim          | 0.000 | -0.41 |
| keyboard-shaped clatter     | the initial prompt, verbatim          | 0.000 | -0.50 |
| same, without the prompt    | "We'll see you next time." / "So, let's see." | 0.000 | -0.85 / -1.27 |

The decoder's two confidence signals are unusable as a gate: `no_speech_prob`
is 0.000 on pure silence, and the log-probability of an invention (-0.3) sits
where real sentences sit. mlx-whisper's own `no_speech_threshold` (0.6) can
therefore never fire.

**Fix.** A speech gate in front of the transcriber, `src/ciel/stt/gate.py`:
Silero VAD (shipped inside openWakeWord, already a spoke dependency) scores
the utterance in 30 ms frames and the best frame must reach
`STTConfig.speech_threshold` (0.5). Calibration from the same script:

| audio                                   | Silero best frame | mean |
|-----------------------------------------|------------------:|-----:|
| silence                                 | 0.04 | 0.02 |
| noise 0.003 / 0.02 / 0.05 RMS           | 0.04 / 0.18 / 0.23 | ≤ 0.04 |
| keyboard clatter, 2.5 s / 4 s           | 0.06 / 0.08 | 0.02 |
| breath-shaped (low-passed) noise        | 0.09 | 0.05 |
| "Yes." at full / 0.1 / 0.03 gain        | 0.99 / 0.99 / 0.91 | ≥ 0.53 |
| "Hey." at full / 0.1 / 0.03 gain        | 0.82 / 0.65 / 0.26 | 0.42 / 0.34 / 0.12 |
| "Play some music." at three gains       | ≥ 0.98 | ≥ 0.64 |
| "Set a timer for ten minutes." at three | ≥ 0.97 | ≥ 0.76 |
| a 17-word question at three gains       | ≥ 0.91 | ≥ 0.54 |

The one speech case under 0.5 is "Hey." at 0.003 RMS — a single syllable at
the level of the room's own hiss, which Whisper transcribed but which is
below what the endpointer's floor gate admits in any real room. The peak is
used rather than the mean so a one-syllable answer survives; the widest
non-speech peak (0.23) and the narrowest audible speech peak (0.65) leave
the threshold a wide margin on either side.

The gate wraps the `SpeechToText` protocol (`build_stt` returns the engine
behind it; the runtime fallback in `_warm_up_stt` re-wraps faster-whisper),
so every transcription is gated: turns, held thoughts, and confirmation
answers in both the spoke and the single process. It fails open with a
warning when openWakeWord is missing.

### 2. The endpointer opens on keystrokes

`src/ciel/audio/vad.py` starts an utterance when webrtcvad calls a frame
speech and it clears the adaptive floor by 1.5×. A run of keys passes both
in a quiet room, and `min_utterance_ms` (250) is shorter than a burst of
typing. This is by design — webrtcvad is cheap and the floor gate exists for
fans, not keys — and is not changed; finding 1 is the layer that owns the
question "was that speech?".

### 3. The snap ear wakes on something no keystroke owns up to

During the afternoon the ear fired on impulses at the very bottom of its
peak range (`GestureConfig.min_peak`, 0.01 full scale — real snaps at the
desk on 2026-09-06 measured several times that), with tilt far above any
snap's, solitary (so `snap_quiet_ms` could not see them), and with the
keyboard veto silent (so they were not keys on this Mac). That is the
signature of a mouse or trackpad button: quiet, bright, one at a time.
The keyboard veto (`src/ciel/audio/keys.py`) asked macOS only about key
events; `CGEventSourceSecondsSinceLastEventType` answers for mouse buttons
just as readily and with the same privacy (a timestamp, never a position).

**Fix.** The veto now asks about key and button events and names which one
it found (`sounds like a click 12 ms ago`). Whether every false snap was a
click is not proven — no recording of them exists — so the ear's
thresholds were not retuned (see the memory note: tune by replay, not by
hand). With finding 1 fixed, a false wake nobody speaks after now costs one
log line.

## Not changed, and why

- `min_peak` and the other ear boundaries: no replayable recording of the
  false snaps; hand-set boundaries fitted to one afternoon were reverted once
  already on 2026-09-06.
- The stock-phrase list: adding the afternoon's inventions would catch those
  four sentences and none of the next ones.
- Barn Door (speaker verification) would also have stopped this and is
  enrolled on this Mac but switched off; it is a heavier gate with its own
  failure modes, and the speech question is the right one to ask first.
