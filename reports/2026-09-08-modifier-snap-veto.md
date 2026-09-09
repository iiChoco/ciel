# Keyboard clicks heard as snaps — 2026-09-08

## Finding: modifier-only events were omitted

`src/ciel/audio/keys.py:47` defined its keyboard clock with only
`kCGEventKeyDown` and `kCGEventKeyUp`. A recent modifier press could therefore
leave the queried ordinary-key age outside the veto window, letting a
snap-shaped keyboard sound reach the wake boundary. The corrected event list
also includes `kCGEventFlagsChanged`.

Apple documents that modifier presses and releases produce this separate event
in [the Quartz event-counter documentation](https://developer.apple.com/documentation/coregraphics/cgeventsource/counterforeventtype(_:eventtype:)).
The same documentation says some volume, brightness, and eject keys do not
produce ordinary key-down/up events; this patch does not promise to cover
all media keys.

**Reproduction.** Run:

```sh
uv run --no-sync python reports/2026-09-08-modifier-snap-veto-repro.py
```

The fixture supplies only fake Quartz ages and synthetic audio. With a recent
modifier event and old ordinary-key events, the former query allows a synthetic
snap. The corrected query vetoes it. Once the 300 ms window expires, the same
sound is eligible again. No microphone, model, network, or runtime state is read.

## Diagnosis boundaries

The live keyboard timing query was checked under the existing Ciel launcher.
It returned changing ordinary-key ages even though Input Monitoring was denied.
That denial affects the shortcut event tap; it does not explain this timestamp
veto failure. No permission or launcher identity was changed in this correction.

A live typing-then-snap comparison was requested, and the owner reported it
finished. Gesture recognition alone cannot tell which acoustic impulse the
owner intended. Whether typing itself caused a wake during that trial still
requires the owner's answer. The modifier omission is independently reproduced;
it is not proof that every reported false wake came from a modifier.

The existing candidate-logging switch now exposes event ages and the veto
window at the decision point, so an expired veto can be distinguished from a
missing event. `src/ciel/audio/wake.py:395` passes that switch to the keyboard veto.
It does not log key identity, typed text, or raw audio. No runtime log or config
contents were copied into this report.

## Verification

`probe_gestures.py` passed 146 checks (138 → 146), including modifier press and
release, gesture/action rejection, expiry, ordinary/modifier recency, and silent
versus enabled diagnostics. The separate reproduction has three checks. This
change does not adjust acoustic thresholds or the 300 ms window, disable real
snaps, or alter WebRTC echo cancellation. No commit, push, or hub deployment.

The final reload reached both `spoke ready` and `WebRTC audio ready`; protected
capture is active. The standalone reproduction passed all 3 checks and
`git diff --check` passed. A permission dialog was neither requested nor
bypassed for this correction. Actual modifier-key acoustic testing and a
label for the typing portion of the owner's comparison remain unconfirmed.
