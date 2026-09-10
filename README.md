# Ciel

A local-first voice assistant. Speech in, speech out, running on your machine.

The source checkout lives at `~/Projects/ciel`; the Git remote is
`iiChoco/ciel`. Deployment and server configuration are maintained in the independent
`~/Projects/infrastructure` repository; see [deployment](deploy/README.md).
`scripts/push_hub.sh` deploys; `--preview` only prints the commands.
After relocating or rebuilding the Mac environment, use
`uv sync --locked --all-extras` to retain Piper and speaker verification.
Fresh environments also need wake-word support assets; see the infrastructure
README for the one-time provisioning command, including custom wake models.

Speech recognition and synthesis are local and free. Only the reasoning goes to
Claude, through your existing subscription — there is no API key.

```bash
uv run ciel
```

Say **"hey jarvis"**, then talk.

> Out of the box Ciel answers to "hey jarvis": openWakeWord ships a pretrained
> model for that phrase and none for "Ciel". Training your own "hey ciel"
> model is a supported, well-trodden path — free, offline, and it works — see
> [Personalizing the wake word](#personalizing-the-wake-word). The default
> *persona* is a butler named Jarvis too (`[brain] personality = "ciel"`
> switches her back).

## What it does

- **Hears you** — `openWakeWord` for the wake phrase (a finger snap or two
  claps can stand in for it — see [Snapping and clapping](#snapping-and-clapping)),
  WebRTC VAD for knowing when you've stopped talking, Silero VAD for whether
  anyone spoke at all before Whisper is asked (see
  [When the room hears sentences nobody said](#when-the-room-hears-sentences-nobody-said)),
  `mlx-whisper` (Metal GPU) for transcription with `faster-whisper` as the CPU
  fallback. All on-device.
- **Thinks** — Claude Opus 5 via the Claude Agent SDK, with web search — and a
  deep-thought escalation agent for the questions that deserve more than a
  conversational answer. Mechanical requests ("ten minute timer") never reach
  the model at all: a conservative local grammar (`commands.py`) handles them
  in milliseconds.
- **Answers out loud** — Piper, a local neural voice.
- **Remembers you** — durable memory that survives restarts, written unprompted.
- **Acts, behind gates** — optional file access confined to a workspace, shell
  commands behind a spoken three-tier gate, iMessage read/send, a look at your
  screen when you point at it — every side effect confirmed out loud and
  journaled so it can be undone.
- **Watches on its own** — Vigil, the proactive layer: calendar lead-ins, a
  morning brief, background completion watches, ring and location nudges —
  budgeted, quiet-hour-aware, and held for the next conversation rather than
  blurted.
- **Knows how you slept** — reads an Oura ring: "how did I sleep" gets hours
  and scores, and a rough morning or a still day can be raised before you ask.
- **Knows where you are** — the Mac's Wi-Fi, or the phone via Find My where
  macOS allows it, turned into a place name; moves between places are noted.
- **Answers texts from anywhere** — DM it on Discord when you're out: same
  brain, same conversation, same memory, and anything needing a yes is asked
  over the same channel.
- **Judges its sources** — web answers distinguish primary sources from
  aggregators, flag conflicts of interest, and state confidence rather than
  delivering everything in the same certain tone.
- **Shows what it's doing** — a floating pill in the corner of the screen,
  visible from any app, and a local web page (the Chart) that mirrors the whole
  conversation live, takes typed turns, and holds the mute switch for rooms
  that must stay quiet.
- **Knows what is true right now** — one table of the system's own readings
  (the time, whether you're around, where you are, what's armed, the rest of
  today's calendar, the ring) opens every turn with its ages, so "what time
  is it", "you're at home", and "the timer has four minutes left" need no
  tool, and "as of" is said when it should be.

## The protocols

The subsystems carry mathematical codenames. The identifiers in the code
stay literal (`WorkspaceGuard` is a `WorkspaceGuard`); these are the names
the docs — and you — get to use.

| Codename | System |
|---|---|
| **Proof Obligation** | The voice-confirmation gate (`confirm.py`, `brain/toolguard.py`) — nothing side-effectful proceeds without a spoken proof |
| **Trichotomy** | The shell's three tiers (`brain/shellguard.py`) — deny / quiet / confirm, every command in exactly one |
| **Compact Support** | The workspace guard — file access vanishes outside a bounded region |
| **Singularities** | `FORBIDDEN_NAMES` — the points inside the region where access is still undefined: credentials, shell startup files, agent state |
| **Tower Clearance** | The confirmation answer window — a yes to a live question inherits the turn's trust instead of re-fighting the speaker gate |
| **Inverse** | The action journal and snapshots (`journal.py`, `recorder.py`) — kept so operations can be run backwards |
| **Trace** | Conversation transcripts — the record of the path actually taken |
| **Barn Door** | Speaker verification — turns away the TV and the guests, and everyone knows a barn door only half-latches |
| **Characteristic** | The wake word — membership test for "being addressed" |
| **Cauchy** | The filler hold — a trailing "um…" means the sequence hasn't converged |
| **Discontinuity** | Barge-in — a jump that ends the current segment |
| **Analytic Continuation** | Autoreload — the process is replaced, the conversation extends through it |
| **Neighborhood** | The follow-up window — an open ball around the last turn, no wake word inside |
| **Isomorphism** | The typed lane — a line on stdin maps structure-preservingly onto a spoken turn: same brain, same session, same transcript, no audio either way |
| **Parallel Transport** | The Discord lane (`remote/discord.py`) — the same map carried along the path away from home: a DM from the pinned owner account is a turn, the reply rides back as a text, and confirmations travel the same road |
| **Chart** | The web GUI (`remote/web.py`) — a local coordinate window onto the same manifold: the conversation drawn live, typed turns in, the mute switch |
| **Vigil** | The proactive layer (`proactive/`) — watchers, one event queue, and the earned right to interrupt |
| **Witness** | The unattended-turn rule (`brain/witness.py`) — reflection and Vigil turns may observe and write Ciel's own notebook, never act outward |
| **Invariant** | Long-term memory — what survives every session transformation |
| **Closure** | End-of-conversation reflection — capturing the limit points before the session is discarded |
| **Atlas** | Projects — durable charts of ongoing work, with an index that says which chart to open |
| **Phase Space** | The world state (`world.py`) — one point that says where everything is right now: the time, presence, place, mute, timers, watches, the agenda, the ring; fed by every producer, opening every turn, mirrored to the Chart |

Closure and Atlas together are the continuity design (the "loving partner
protocol"): sessions are deliberately short-lived, and continuity comes from
what Ciel chooses to remember, not from an ever-growing context.

## Setup

```bash
uv sync --extra piper
uv run ciel
```

First run downloads a Whisper model (`mlx-community/whisper-small.en-mlx`), a
Piper voice (~60 MB), and the wake-word models (~5 MB); enabling `[voice]`
later adds a ~28 MB speaker model. After that it's offline except for Claude.

**Do not set `ANTHROPIC_API_KEY`.** The Agent SDK inherits Claude Code's
subscription login. Setting that variable silently overrides it and bills you
pay-as-you-go API usage instead. Ciel warns you at startup if it's set.

macOS will ask for microphone permission the first time.

## Usage

```bash
uv run ciel                  # wake word
uv run ciel --wake hotkey    # press Enter to talk — reliable in a noisy room
uv run ciel --wake always    # respond to any speech (quiet rooms only)
uv run ciel --new            # ignore the previous conversation, start fresh
uv run ciel --tts say        # macOS built-in voice instead of Piper
uv run ciel --model claude-sonnet-5   # override the brain model for this run
uv run ciel --voice Daniel   # a macOS voice name, for --tts say
uv run ciel -v               # debug logging
```

## Configuration

Everything lives in `~/.ciel/config.toml`, or as `CIEL_<SECTION>_<FIELD>`
environment variables for one-off runs. (Two carve-outs: `[mcp.<name>]`
connector tables are TOML-only, and `state_dir`/`log_level` sit at the top
level, outside any section.) The [Spotify connector](#spotify-from-whichever-device-is-playing)
uses its own `[spotify]` section, documented with the account setup below.
The `[tasks]` storage fields are documented under [Durable tasks](#durable-tasks-and-owner-controls).

```toml
[brain]
model = "claude-opus-5"      # "claude-sonnet-5" is ~3x cheaper and faster
personality = "jarvis"       # the butler; "ciel" is the reserve persona
effort = "low"               # reasoning effort for ordinary turns
deep_effort = "high"         # ...and for the deep-thought escalation agent
resume_window_minutes = 10.0  # silence beyond this rotates to a fresh session

[stt]
engine = "mlx-whisper"       # Metal GPU; "faster-whisper" is the CPU fallback
mlx_model = "mlx-community/whisper-small.en-mlx"
# model/compute_type/device apply to faster-whisper only
initial_prompt = "A spoken conversation with an assistant named Ciel."
speech_threshold = 0.5       # Silero must hear speech in one frame before Whisper is asked; 0 off

[tts]
engine = "native"            # Apple's Premium voices, streamed; "piper" and "say" below it
native_voice = "Jamie"       # a free download: Accessibility → Spoken Content → System voice → Manage Voices
rate = 190                   # words per minute, for native and say alike
piper_voice = "en_US-lessac-medium"
effect = "none"              # "jarvis" adds the installed-speaker treatment

[wake]
mode = "wakeword"
threshold = 0.5              # raise if the TV sets it off, lower if it ignores you
snap = true                  # a finger snap addresses Ciel too
double_clap = "play"         # two claps within 0.8 s: "wake" like the snap, or "play" music; the [gestures] table tunes the ear
double_clap_plays = "spotify:artist:0du5cEVh5yTK9QJze8zA0C"   # Spotify's "Copy Spotify URI"

[shortcuts]
enabled = false             # opt in; Ciel requests macOS Input Monitoring
talk = "ctrl+option+space"
stop = "ctrl+option+escape"
mute = "ctrl+option+m"

[notes]
enabled = true              # floating Mac scratchpad; uses the same Input Monitoring permission
shortcut = "cmd+backslash"   # empty disables the chord
# Both gestures are on so you can try them; the pair also types into the current app.
double_backslash = true
double_tap_ms = 350
dir = "~/.ciel/notes"        # private draft and confirmed recent notes on this Mac
max_chars = 16000
save_timeout_s = 10.0       # keep the draft and offer retry if the hub does not receipt it
history_limit = 100         # confirmed saves kept locally (0–1000)
undo_discard_s = 5.0        # Undo lives only in RAM (0–30 seconds); zero disables it
dictation_max_s = 60.0      # manual microphone capture, bounded to 1–300 seconds

[audio]
backend = "portaudio"       # "webrtc": AEC3 without ducking (macOS 14.2+); "apple": native voice processing
webrtc_capture_delay_ms = 40 # WebRTC only: 0–200, multiples of 10; reference timing allowance
apple_playback = "portaudio" # where Ciel's voice plays under "apple"; "engine" lisps, kept for comparison
apple_ducking = "min"       # "min", "mid", "max": other audio attenuation during voice activity
apple_agc = false           # Apple automatic microphone gain; echo cancellation stays on
silence_ms = 500             # how long a pause ends your turn (toward 700 if she interrupts)
barge_in = false             # see below
```

```bash
CIEL_WAKE_MODE=hotkey CIEL_BRAIN_MODEL=claude-sonnet-5 uv run ciel
```

## Keyboard shortcuts

The Mac can hear three keys even while another app has focus. Set
`[shortcuts] enabled = true` in `~/.ciel/config.toml` and restart Ciel. When
**Input Monitoring** is missing, Ciel makes the macOS permission request from
its own process. Allow the Python running Ciel in the system prompt or in
System Settings → Privacy & Security → Input Monitoring. A launchd spoke
uses its Python, not the terminal's permission. If macOS still withholds access
after the request, restart Ciel after granting it. An existing grant needs no
new prompt. With both voice shortcuts and notes disabled, no listener requests
permission; invalid active bindings also prevent it from starting.

The request runs on the keyboard listener's thread, leaving voice available
while macOS handles it. The listener reports `global shortcuts ready` when it
can open the tap, or a warning if permission remains unavailable. Shortcuts
for Talk, Stop, and Mute are off by default; [quick notes](#quick-notes) are on.

| Default | Action |
|---|---|
| Control–Option–Space | Interrupt the current response and open a listening window |
| Control–Option–Escape | Stop the response and close listening |
| Control–Option–M | Toggle mute; muting also interrupts the current response |

Talk respects mute: unmute first. It waits for an interrupted hub turn to
release the room before opening listening. These are press-once controls,
not push-to-talk; holding a key does not repeat. Stop denies any pending
confirmation and discards a held partial utterance. It cannot undo an action
that has already happened. The existing Barn Door gate still checks captured
speech. Both `ciel` and `ciel spoke` support the controls; the hub never watches
a keyboard. The stdin Enter fallback remains separate, and it exists only
where there is a keyboard: the typed lane attaches to a terminal, a pipe,
or a socket, and declines a service manager's `/dev/null` with one log
line before anything is attached.

Each binding accepts `ctrl`, `option`, `cmd`, and `shift`, joined with `+`,
followed by a letter, digit, `space`, `escape`, `return`, or `tab`. Aliases
`control`, `alt`, `command`, and `esc` work too. At least one of Control,
Option, or Command is required; all three bindings must be different.
Letter keys refer to physical US keyboard positions. Extra modifiers do not
match, while Caps Lock is ignored. The passive listener does not consume the
keys, so choose other bindings if a foreground app already uses them.
No characters or keyboard history are read or recorded: only matching action
names leave the native callback. `CIEL_SHORTCUTS_ENABLED=true` and the other
`CIEL_SHORTCUTS_*` variables use the same configuration path.

## Speak back (hearing the voice where you cannot talk)

Typed Chart turns are text: the reply lands on the page and the room
stays quiet. The **VOICE** chip on the Chart (or a typed "speak back on"
/ "voice off") flips that for the session: each reply sentence is also
spoken in the room — through the spoke on the hub, the player locally —
so the voice can be heard and judged from a lecture-hall seat. None of
the voice lane's theatre comes with it: no ack filler, no chime, no
follow-up window (the words were typed). Muted still wins, a Mac that is
not connected means text alone, and a sentence that will not play (a
barge-in, a lost device) makes the rest of that reply text only rather
than cutting it short. `[web] speak_back = true` starts a session with
it on. Not persisted: it is a session's choice.

## The voice (native, piper, say)

Three engines behind one protocol, best first, each falling to the next if
it will not warm up. `native` (`tts/native.py`) is Apple's own synthesizer
driven through a small Swift helper (`tts/native/CielVoice.swift`,
built once with the command-line tools' `swiftc`, cached by source hash
under `~/.ciel/bin`) so that its Premium voices — Jamie, Zoe, Ava, free
downloads about a gigabyte each — reach Ciel as a stream: PCM leaves the
helper as it is synthesized, first audio in ~30 ms once warm, a five-second
sentence rendered in ~120 ms. `piper` is the local neural voice that beat
`say` before the Premium voices were tried, and stays the hub's engine
(Linux). `say` is the compact-voice fallback that is always there.
`scripts/probe_native_voice.py --ab DIR` writes the same four sentences
through native and piper for listening, with the numbers.

## The status pill

A small always-on-top pill sits in the corner of the screen and tells you what
Ciel is doing without your having to find the terminal.

| State | Looks like |
|---|---|
| Idle | Dim grey, "Ciel" — running, waiting for the wake word |
| Listening | Green, pulsing — capturing your speech |
| Thinking | Amber, pulsing — transcribing, or Claude is working |
| Reasoning | Purple, pulsing — the deep pass, reasoning read aloud before the answer |
| Speaking | Blue, pulsing — talking |
| Error | Red — the turn failed |

It ignores mouse clicks, never takes focus, and follows you across Spaces. It
wears the Chart's state chip — the same cut-corner shape, the same six colours,
the same breathing dot and wide-tracked capitals — so the corner of the screen
and the browser tab read as one instrument. The dot, the border, and the tint
over the ground all carry the state colour: a dark chip with only a small dot
vanishes against a dark terminal, which is exactly where it sits. The chip is
sized to its label, so it grows and shrinks a little as the word changes.

```toml
[ui]
indicator = "hud"           # or "terminal" (for SSH), or "none"
position = "bottom-right"   # any corner
margin = 24
opacity = 0.92
scale = 1.0                 # make it bigger
hide_when_idle = false      # true = vanish entirely when idle
```

```bash
uv run ciel --indicator terminal   # console status line instead
uv run ciel --indicator none
```

It runs as a separate process, because AppKit permanently claims whichever
thread its run loop starts on and Ciel's asyncio loop wants the main thread.
The side benefit is fault isolation: if the pill dies, Ciel keeps talking.

## File access

Off by default. Turn it on and Ciel can read, write, and edit files — inside
one directory and nowhere else.

```bash
uv run ciel --files                        # ~/.ciel/workspace
uv run ciel --workspace ~/Documents/notes  # somewhere specific
```

```toml
[files]
enabled = true
workspace = "~/Documents/notes"
read_only_outside = false   # true = read anywhere, still write only in workspace
```

**Why it's confined rather than just switched on.** Ciel acts on transcribed
speech with no confirmation step, and it reads web pages into its context. That
combination — instructions that can be misheard, untrusted text in context, and
silent execution — makes unbounded file write a genuinely bad idea. So every
path the model supplies is resolved and checked against the workspace before
the tool runs. Resolution happens first, which is what defeats `..` traversal
and symlinks pointing out of the tree.

Credentials and shell startup files (`.ssh`, `.env`, `.zshrc`, …) are refused
even if they somehow appear inside the workspace.

**Bash is guarded separately** — see the shell section below. A shell walks
straight around path confinement — `sh -c 'cat > ~/.zshrc'` — so it never gets
a place on the file allowlist. With `[shell]` disabled it sits in
`disallowed_tools` explicitly rather than merely left out, so a stray config
edit can't quietly re-enable it.

> The check is a `PreToolUse` hook, not a `can_use_tool` callback. An entry in
> `allowed_tools` auto-approves its tool *before* that callback is consulted,
> so a guard wired there never runs for the tools it's meant to constrain. If
> you extend this, keep the enforcement in the hook.

### With `workspace = "~/"`

Setting the workspace to your home directory removes the *directory* boundary,
which makes the blocklist the whole of the defense rather than a backstop. It's
correspondingly thorough. Refused anywhere under home:

| Refused | Why |
|---|---|
| `.zshrc`, `.zshenv`, `.bash_profile`, … | A writable shell config is code execution on your next login |
| `.ssh`, `.aws`, `.gnupg`, `.env`, `.npmrc`, `.docker`, `.kube` | Credentials and keys |
| `sections-cookie` and the configured `[sections].cookie_file` name | The signup site's authenticated session |
| `.claude`, `.claude.json`, `.config` | Agent state and auth tokens — `.claude` also holds session transcripts |
| `~/Library` | Keychains, browser cookies and history, Messages, Mail |
| `LaunchAgents`, `LaunchDaemons` | Login persistence |

Everything else under home is fair game, including every repo you have checked
out — Ciel can edit her own source at `~/Projects/ciel`.

## Voice identity (Barn Door)

Off by default; needs a one-time enrollment. In enforcing mode, every utterance is
embedded by a local speaker model (CAM++ via sherpa-onnx, ~30 ms on CPU) and
compared against your enrolled profile *before* transcription — a stranger's
sentence never reaches Whisper, the brain, or a follow-up window.

```bash
uv sync --locked --all-extras
uv run --no-sync python scripts/enroll_voice.py          # record 10 short phrases, varied styles (Ciel stopped)
uv run --no-sync python scripts/enroll_voice.py --test   # score yourself live
uv run --no-sync python scripts/enroll_voice.py --add    # append takes to the existing profile
uv run --no-sync python scripts/enroll_voice.py --adopt  # review rejected clips, adopt the ones that are you
uv run --no-sync python scripts/enroll_voice.py --calibrate  # re-measure the threshold against impostor voices
uv run --no-sync python scripts/enroll_voice.py --prune  # inspect takes by name and drop bad ones
```

```toml
[voice]
enabled = true
diagnostic = false  # true measures decisions while letting every utterance through
# threshold: leave unset — enrollment *calibrates* one and stores it with
# the profile (impostor voices vs your takes); a number here overrides it.
```

The threshold is measured, not guessed: every profile change ends with a
calibration pass that scores a set of macOS `say` voices against your takes
using the gate's best-match similarity, compares that with your takes'
leave-one-out agreement, and places the bar between the two distributions.
Takes that disagree with the rest of the profile get flagged for pruning —
with best-match scoring, every take is another chance for a lucky impostor
hit. This sets the base threshold; short speech and a recent verified turn
can lower the actual bar. Synthetic calibration is not a measurement of
recognition accuracy through your microphone.

**Measure without being turned away.** On the machine doing the listening
(the Mac in a hub/spoke setup), set these values in `~/.ciel/config.toml`:

```toml
[voice]
enabled = true
diagnostic = true
diagnostic_file = "~/.ciel/voice/diagnostics.jsonl"
diagnostic_max_bytes = 1000000
```

Restart the listening process to load the config. For the launchd spoke:
`launchctl kickstart -k gui/$(id -u)/ai.ciel.spoke`. Keep the existing profile
and threshold; diagnostic mode does not enroll, adopt, or recalibrate.
Follow the numbers with `tail -F ~/.ciel/voice/diagnostics.jsonl`, or look for
`voice diagnostic:` in `~/.ciel/log/ciel-spoke.err.log`.

Every captured utterance that reaches Barn Door continues to transcription,
including ones it would reject. Wake detection, capture, transcription, and
the existing action permissions still apply. Only an embedding that would
pass advances the diagnostic grace clock; a bypassed rejection does not.
This measures the policy on the actual conversation, including follow-ups
that an enforcing gate might not have allowed to arise.

Each reading has a timestamp, `would_accept`, a reason, similarity, the base
and effective thresholds, both discounts, grace-window state, and the best
matching reference's number. Capture measurements include duration, RMS,
peak, clipped fraction (absolute samples at least 0.999), zero fraction, and
whether the samples are finite. Duration includes captured silence; these
numbers are not a speech detector or a confidence percentage. Reasons are
`matched`, `below_threshold`, `too_short`, or `short_grace`; `no_profile`,
`model_unavailable`, `encoder_error`, `invalid_audio`, and `invalid_embedding`
mean unavailable (`would_accept: null`). A missing model or failed reading
never becomes a claimed recognition. An unwritable diagnostic file is logged
and does not block the turn.

The JSONL file is owner-only and starts a new window before its byte limit
is exceeded (minimum 4,096 bytes). The process log also receives each record
under its ordinary retention. No audio, transcript, or embedding is included;
diagnostic mode skips the rejected-clip ring even if `keep_rejected` is set.
Other configured transcript/recording behavior is unchanged. For evaluation,
note the timestamp, who spoke, and the conditions while testing; these readings
cannot determine the correct speaker label by themselves. A wake or capture
failure produces no Barn Door row, so compare with the microphone and wake
logs when no row appears. Set `diagnostic = false` and restart to enforce
again, or `enabled = false` to turn speaker verification off.

The [speaker probe](scripts/probe_speaker.py) uses synthetic embeddings and
both real voice handlers to pin decision parity, grace, failures, and private
storage. It does not measure real-room accuracy.

Honesty section: an embedding is a filter, not authentication. It turns away
the TV, guests, and background chatter; it will not resist a recording of
your voice, and a heavy cold moves your own score (lower the threshold that
week). Short utterances are judged against a lenient bar rather than waved
through — the threshold tapers between ~2.5 s and 600 ms, since a short
embedding is a weaker measurement — and below ~350 ms, where nothing can be
measured, *context* decides: a blip passes inside the grace window of a
verified conversation and is ignored cold. Perfect separation on short words
is information-theoretically off the table; the layering (lenient judging,
context, and Tower Clearance for anything with consequences) is the design
answer to that. No profile enrolled? The gate fails open
and says so at startup, because an identity filter that silently bricks the
assistant is worse than the strangers it filters.

## Shell access

Off by default. Turn it on and Ciel can run shell commands — behind a voice
gate that the model cannot bypass.

```toml
[shell]
enabled = true
```

Every command is classified into one of three tiers:

- **Denied outright**, even if you say yes: privilege escalation (`sudo`,
  `launchctl`, `security`, `osascript`, …), anything touching credential or
  shell-startup paths, `curl | sh` shapes, and recursive-forced `rm` aimed at
  home or root. A "yes" to a misheard question must not be able to do these.
- **Quietly allowed**: a conservative read-only allowlist (`git status`,
  `git log`, `ls`, `pwd`, …) runs without asking. Configurable via
  `auto_allow`; asking aloud for every status check would train you to say
  yes reflexively. Arguments must also be known to be read-only: `git log`
  accepts `--oneline`, `--graph`, `--all`, `--decorate`, `--no-decorate`,
  `--no-patch`, `-N`, and `--max-count=N`. Other log forms, Git's
  diff/show/branch/blame commands, and `file` ask first; arguments to
  `date` or `hostname` ask too. Adding a prefix does not skip these checks.
- **Confirmed**: everything else. Ciel reads the command aloud — *"Run: touch
  notes dot txt — okay?"* — and waits for a spoken yes or no. Silence (about
  eight seconds), a "no", or two unclear answers all refuse the command.

The confirmation is enforced, not requested: it lives in a `PreToolUse` hook
that blocks the tool call until the pipeline has captured and transcribed your
answer, so neither a misheard instruction nor a prompt-injected web page can
run a side-effectful command without your voiced yes. Commands the classifier
can't see through — redirections, substitutions, backgrounding — always land
in the confirm tier. `scripts/probe_shellguard.py` drives the whole
choreography with fakes.

### Acting without asking

The question is a switch of its own, and it is off by turning `ask_first`
off:

```toml
[confirm]
ask_first = true             # false: every confirm-tier question is a yes, at once
```

With it off, Ciel answers its own questions. Every confirm-tier action —
a shell command, a connector write, a mail send, a standing grant's
activation — runs the moment it is proposed, and the transcript shows the
question and the answer it gave itself (`you (confirm, off): yes`), so a
scrollback still reads as what happened. The journal records each one as
it would have. What does not change: the deny tier is not a question and
still refuses, an unattended turn (reflection, Vigil) still cannot act
because nobody is there to answer, and the Discord lane's own gate still
decides who may speak. What you have given up is the one moment where a
misheard sentence, or a line planted in a web page or an email, meets
your own word before it runs; the journal is what is left of that.

It is meant to be flipped, not set: with granting on, *"act without
asking"* is a capability in the catalog below — granted through the
spoken gate once, revoked at once with *"ask before acting again"*, from
voice, Discord, or Chart. The words are a local command, not a request
the model interprets: *"act without asking"*, *"stop asking me"*, *"no
more confirmations"* on their own put the one question — *"Act without
asking from now on — okay?"* — over the lane they came in on, and *"ask
before acting again"*, *"ask first"*, *"confirmations on"* put it back
with no question. A sentence that says more ("act without asking on the
report") is not the switch and goes to the model. Editing the config does
the same by hand.

### macOS may block folders independently

`~/Desktop`, `~/Documents`, and `~/Downloads` are TCC-protected. Whether Ciel
can reach them depends on permissions granted to the *terminal app* you launch
her from, not on anything in this config. macOS prompts on first access; if it
was denied once, grant it again under System Settings → Privacy & Security →
Files and Folders.

This also makes verification confusing: a shell without Desktop permission
reports "Operation not permitted" for a file Ciel wrote perfectly well. Check
with `stat` rather than `ls`, or just ask Ciel to read it back.

## Texting Ciel from anywhere (Parallel Transport)

Off by default. Turn it on and you can DM Ciel on Discord from wherever you
are — the message becomes an ordinary turn in the same conversation, and the
reply comes back as a text instead of through the speakers.

```bash
uv sync --extra discord
```

```toml
[discord]
enabled = true
owner_id = 123456789012345678
# token: put it in ~/.ciel/discord.token (preferred — that filename is on
# the FORBIDDEN_NAMES blocklist, so the model can never read it), or as
# `token = "..."` here, or CIEL_DISCORD_TOKEN in the environment.
```

**One-time setup, about five minutes:**

1. [discord.com/developers/applications](https://discord.com/developers/applications)
   → *New Application* → **Bot** tab → *Reset Token*, save it to
   `~/.ciel/discord.token`. Untick *Public Bot* while you're there. No
   privileged intents needed — DMs carry their content without any, and
   guild messages that @mention the bot are exempt from the content
   restriction.
2. Discord only delivers DMs between accounts that share a server, so make a
   private one (just you) and invite the bot into it: **OAuth2 → URL
   Generator**, scope `bot`, zero permissions, open the generated URL.
3. Your own user id: Settings → Advanced → *Developer Mode* on, then
   right-click your name → *Copy User ID*. That's `owner_id`.

Then DM the bot. `uv run scripts/probe_discord.py --live` echoes your DMs
back without running the assistant, which is the fastest way to check the
plumbing.

**Identity is pinned, not inferred.** Only DMs from `owner_id` are read at
all — strangers, other bots, and anything said in a server channel are
dropped before the words reach anything that could act on them. The model
never chooses who may speak here or where replies go; both are config. The
flip side is honest too: this gate is exactly as strong as your Discord
account. Anyone holding your Discord session, or the bot token, is you as
far as this lane is concerned — treat the token like a password.

**Confirmations follow you out the door.** A confirm-tier action mid-text
— a shell command, a connector send — texts you its question over the same
DM and waits about two minutes for a yes or no, instead of voicing it into
an empty room. Silence, a "no", or two unclear answers refuse the action,
exactly as they do out loud.

**@ciel works in servers too.** In any channel of a server the bot has
been invited to, `@ciel <question>` is a turn — same conversation, reply
posted to that channel. Still only your pinned account: anyone else's
mentions are dropped at the same gate as their DMs, and nobody else's
channel chatter is read at all. The turn knows it's in public — it's told
to keep private context out of channel replies and to offer DMs when a
real answer would need it, and held Vigil notes are only ever delivered
into your DMs. `mentions = false` restricts the lane to DMs. One honest
limit: mentions missed while Ciel is down are not backfilled — missed DMs
are (within ten minutes), because the DM history has one place to look.

**When Vigil is on**, urgent watched things may reach you over this link
too: iMessage keeps priority when fully configured, and the Discord DM is
the fallback outlet (`discord.proactive = false` keeps the lane strictly
two-way). Held notes ride into your first text from away the same way they
ride into a spoken conversation.

**The honest limits.** Ciel lives on this machine: a closed lid means no
answers until it wakes. Short network naps are survived (the gateway
replays what was missed), and DMs sent while Ciel was disconnected or
restarting are picked up on reconnect — the last message seen is persisted,
so the sweep spans restarts — but only up to ten minutes back: a question
from ten minutes ago is still being waited on; an instruction from six
hours ago should be asked again, not executed stale. Replies compose under
the speech rules, so they read like Ciel talking — short, plain, no
markdown — which happens to be exactly how texts should read.

## The GUI (Chart)

Off by default. Turn it on and Ciel serves a small chat page on loopback —
a live window onto the whole conversation, and a place to type when the
room must stay quiet: a lecture, a library, a call. Every lane's turns
appear as they happen (spoken ones included — you see what Ciel heard),
the status pill mirrors the HUD and, while listening, says what opened the
window — `listening · spoken`, `listening · snap`, `listening · clap twice`,
and bare `listening` for a follow-up window nothing opened — confirm-tier
questions become Yes/No buttons, and the mute switch lives in the header. Next to the status
pill, an agents chip counts everything working on your behalf right now
— a deep-thought pass mid-flight, background watches, running timers —
and expands into a list with live countdowns; it disappears when
nothing is running. Under the header, a strip of readings mirrors the
world table (Phase Space, below): whether the Mac holds the seat and
whether you're at it, the place, the timers and watches, what's next on
the calendar, the ring — each chip with its age, dimmed once its reading
has gone stale.

```bash
uv sync --extra web
```

```toml
[web]
enabled = true
# port = 8765        # the page lives at http://127.0.0.1:8765
# max_upload_bytes = 8388608   # the largest file the page may send with a message
# max_files_per_turn = 8       # files one message may carry
# upload_keep_days = 14.0      # the Chart's own uploads older than this are pruned; 0 keeps all
# max_inline_chars = 16000     # a text file this small is quoted into the prompt as data
# image_prompt_chars = 900000  # the base64 budget for images shown to the model in one turn
```

**Mute** is the reason this exists. While muted, Ciel holds its tongue
*and* its name: nothing leaves the speakers (greeting, timer rings, and
replies included) and the wake word is not watched for — a false wake in
a lecture hall costs exactly the attention mute was bought to avoid. The
typed and web lanes keep working, and Vigil nudges that would have been
spoken become held notes that ride into your next turn. Timers that come
due while muted appear as text on the page instead of ringing. The state
survives restarts (the autoreloader re-execs constantly) as
`~/.ciel/mute` — a sentinel like Vigil's `hold`, so `touch ~/.ciel/mute`
from a hotkey or another shell flips it without the page open, and it is
announced on the page, in the terminal, and in the transcript either way.

**Trust model.** The server binds `127.0.0.1` only: reaching the port
means being at the machine, the same trust the keyboard gets — so web
turns count as presence, unlike Discord ones. The one browser-shaped hole
(any web page may try `ws://127.0.0.1`) is closed by an Origin check:
pages from other origins are refused before a frame is read. Think hard
before widening `host`; there is no account id here to gate on.

**Files with a message.** The FILE button, a paste, or a drop puts files
beside the composer as chips; SEND uploads them ahead of the words, each as
its own `file.put` frame answered by id, and the say names the ids it
carries. The page scales an image down to 1568 pixels on its long edge as
a JPEG before it leaves, so a screenshot arrives well under the model's
line budget; other files arrive as they are, up to `max_upload_bytes`. The
server keeps nothing it was not told: the name is reduced to a safe
basename, the type is what the first bytes say it is (a claimed image that
does not begin like one is an octet stream), and the file is written
owner-only under `[files].workspace/uploads`, where the brain's own file
tools can open it. The model is told each file's name, type, size, and
path with a note that their contents are data, never instructions; a text
file under `max_inline_chars` is quoted there, an image within
`image_prompt_chars` of base64 is shown to it, and anything past either
bound is named for it to read. The transcript row names what was attached
and never its contents, and a public Discord turn never carries a file.
The folder does not grow forever: at startup and after each new file, the
Chart's own files older than `upload_keep_days` are removed — only files
in its `<id>-<name>` shape, so anything the owner put in the workspace by
hand is never touched; 0 keeps everything.
The hello says whether the server takes files and how large; an older
server hides the button.

**A native app later** is already provided for: the page speaks a small
JSON protocol over one WebSocket, documented at the top of
`src/ciel/remote/web.py` — a SwiftUI client connects to the same `/ws`
and the server never knows the difference. The frame catalog itself —
every type in each direction, the codec, and the resume rules — lives
in `src/ciel/wire.py`.

**From the phone, over Tailscale.** The socket can listen on the
machine's tailnet address instead of loopback, so a phone on the same
Tailscale network opens the Chart from anywhere — and nowhere else, since
the address is not routable from the internet:

```toml
[hub]
bind = "100.101.102.103"   # this machine's Tailscale address: `tailscale ip -4`
# origins = ["ciel-hub.tail1234.ts.net"]   # only if you open the page by MagicDNS name
```

Reach is no longer identity off loopback, so every non-loopback client
must present a token. Ciel mints one at first start into
`~/.ciel/hub.token` (owner-only, and on the model's forbidden list by
name) and says so in the log; open `http://100.101.102.103:8765` on the
phone, the page asks for the token once, paste it, done — it is
remembered per browser. Loopback keeps working exactly as before, no
token asked. A dropped connection (a Wi-Fi handoff, a phone that
slept) picks up where it left off: the page tells the server the last
frame it saw, and the server replays only what was missed, so the
screen never blanks and re-fills. `scripts/probe_wire.py` drives the
codec, the ring, the door, and a real socket on loopback.

## Two processes: hub and spoke

Optional. Everything above runs as one process (`ciel`, or `ciel local`),
and that stays the way to run Ciel on one Mac. The split exists for the
day the brain moves to a machine that never sleeps: `ciel hub` is the
brain, memory, Vigil, the Discord lane, the Chart, and timers'
bookkeeping — everything that is judgment or a text lane — served over
the same socket the Chart uses; `ciel spoke` is the microphone and the
speakers — the wake word, the endpointer, the speaker gate, STT, TTS,
barge-in, the follow-up window, the HUD, the mute sentinel — as one more
client of that socket. What the spoke hears goes up as text; what it
says comes down as text, one sentence at a time, each one receipted so
the hub's generation stays paced to real speech and a barge-in aborts it
exactly as before.

```bash
ciel hub      # in one terminal (or a launchd agent)
ciel spoke    # in another
```

```toml
[spoke]
hub = "ws://127.0.0.1:8765/ws"   # the hub's socket; a tailnet address once it moves
```

Both on this machine, nothing else changes: the same config file, the
same `~/.ciel`, the same lanes. A confirm-tier question in a spoken turn
is spoken by the spoke and answered in the room; timers and Vigil nudges
ring through the spoke at its idle; the mute switch flows both ways
(the spoke owns the sentinel, the hub owns the Chart's switch, and each
tells the other). A spoke that loses the hub says so once and chimes
after; a hub that loses its spoke abandons the sentence it was speaking
and denies any question it was asking, the barge-in rule. Not yet on the
spoke, on purpose: the command grammar's fast path and local timer
ringing — both come back in the resilience phase, for a hub that might
be unreachable.

`scripts/probe_hub_arbiter.py`, `scripts/probe_spoke.py`, and
`scripts/probe_confirm_wire.py` drive each half with fakes and a fake
other half.

**A reload has a deadline.** Both processes watch their own source
(Analytic Continuation, `reload.py`) and re-exec on an edit — from idle
only, so an edit mid-conversation waits for it to end, announced with
"Reloading." in the room. A room that never returns to idle would hold
the old code forever, and on 2026-09-09 one did until a hand restarted
it. So the watcher keeps a clock from the change it saw: past
`reload_grace_s` it tells the app, which leaves anyway — cancelling its
own run, which closes the audio and the link the way any shutdown does —
and the re-exec happens. The log says `reload forced from state BUSY`.
`scripts/probe_reload.py` drives the watcher and its deadline.

```toml
[dev]
autoreload = true           # watch src/ciel and ~/.ciel/reload; re-exec on a change
reload_grace_s = 120.0      # how long the room may stay busy before the reload is forced; 0 never
```

**The hub needs no Mac in it.** Everything Mac-bound reaches the machine
over the same socket. The brain's tools for the screen, Messages, the
location, and background watches bind the wire on the hub and run on
the spoke; the calendar store (EventKit), the locator, and the work
watcher run *on the spoke* and publish their events up, acked one by
one and resent after a reconnect; presence is a heartbeat — the raw
lock-and-idle signals plus the roster of active watches — and a
heartbeat that goes stale reads as an empty room, so Vigil never speaks
to a spoke that stopped listening. Four tools exist only on the hub:
`run_on_mac`, `mac_read_file`, `mac_write_file`, and `mac_list_dir` —
the user's shell and files from a brain elsewhere, with the Mac named
as the default target of "run this" and "open that" in the prompt. Both
sides guard them: the hub's shell gate reads a side-effectful command
aloud for a spoken yes and the workspace guard checks every path, and
the spoke re-runs the classifier and the path check from its own config
before touching anything — the deny tier is refused whatever the hub
says, the confirm tier refused unless the hub says it asked. The
ordinary Bash and file tools on the hub act on the hub's own workspace.
`scripts/probe_hub_imports.py` proves the point by refusing every Mac
and audio module and constructing the hub anyway; `probe_tool_rpc.py`
and `probe_presence.py` drive the calls and the publishers.

A canceled Mac shell command stops its whole process group and waits for the
shell to exit; the RPC deadline, the command's own deadline, and shutdown do
the same. Canceling the hub's wait also sends cancellation to the spoke.

With the journal enabled, a Mac overwrite first sends the previous contents
to the hub's journal as a complete, owner-only snapshot. `recent_actions`
names that hub file: ordinary `Read` can read it, then `mac_write_file` can
restore the Mac file through its usual guards. The smaller of the two
`[journal].max_snapshot_kb` settings bounds the copy. A missing, oversized,
or unreachable original leaves an explicit note, so Ciel can say when undo
has no saved contents. The snapshot path itself is read-only outside the
workspace, and an undo gets its own journal entry and snapshot.

**When the hub is away.** The alarm clock never depends on the server:
the hub broadcasts its timer set, the spoke keeps a mirror
(`~/.ciel/spoke-timers.json`) and rings a timer itself when the hub
can't — the link down at the due moment, or the hub silent about it
past a short grace — and a timer rung here is receipted silently if the
hub later delivers it, so nothing rings twice. With the hub down the
command grammar runs on the spoke: "ten minute timer" arms a local
timer, "cancel the timer" and "what timers are running" answer from the
mirror, "reload" restarts the room. Every spoken turn carries an id, so
a resend after a reconnect is never queued twice. On the hub, an away
text goes through the Mac's iMessage while the spoke is seated and
falls to Discord when it isn't. `ciel hub --check` is the doctor: the
token, the bind address, the brain's login, the connectors' runtime,
the state directory — one line each. `scripts/probe_backfill.py` drives
all of it.

**The Chart from anywhere.** With the hub on a server, the page can
also sit behind a public name through a Cloudflare Tunnel: `cloudflared`
on the server carries the hostname to the hub's tailnet socket, so the
hub still listens nowhere public, and a Cloudflare Access application
in front of the name asks for your email before a byte reaches the
page; the hub's own token gates the socket behind that. List the public
name in `[hub].origins` — a configured name is trusted on any port,
since a proxied name arrives on the proxy's port, not the hub's — and
the page speaks `wss://` on its own when served over TLS.

## The interview room (Adjoint)

Off by default. A second surface on the hub's web page, at `/interview`,
for people who are not the owner: a voice-driven mock interviewer that
invents a company from what a candidate asks for, or runs a consulting
case, or poses a coding problem beside an editor — then records the whole
thing and writes a debrief. It shares the process with Ciel and nothing
else: its own accounts, its own brains with no tools and no memory, its
own socket, its own files.

```toml
[interview]
enabled = true                  # the hub serves /interview
dir = "~/.ciel/interview"       # accounts, sessions, added cases
model = "claude-opus-5"
effort = "medium"               # the interviewer's turns; debrief_effort = "high"
max_budget_usd = 3.0            # per session, enforced by the SDK
daily_sessions_per_user = 4
max_concurrent = 3              # one model subprocess per live interview
silence_ms = 2000               # how long a candidate may pause before the answer is taken
extend_ms = 3000                # extra wait when the transcript trails off
piper_voice = "en_US-lessac-medium"
```

Accounts are the owner's to make. `ciel interview add-user alice` prints
a generated password once; the admin panel on the page does the same, and
can reset, disable, or delete. There is no signup. A session belongs to
the account that made it, not to the username: deleting an account ends
its live interviews, closes their sockets, and moves its directory to
`users/.retired/<username>.<id>` so a name given to someone else starts
empty. A password change or a disable closes every socket open under the
old sign-in at once. The accounts can also
be the door's — yunhan.me's shared login (`yunhan.me/door`), the same
module and the same cookie scheme, so one sign-in covers the room and
every other surface on the domain:

```toml
[interview]
accounts_dir = "~/.door"       # the door's accounts.json and secret, instead of the room's own
cookie_name = "yh_session"     # the door's cookie, set on the parent domain
cookie_domain = ".yunhan.me"
cookie_path = "/"
``` Public reach is the
Chart's Cloudflare Tunnel plus a second Access application on
`ciel.yunhan.me/interview` with a Bypass policy, so friends reach the
room's own login while the Chart itself stays behind the owner's PIN.

Three modes. **Company**: the candidate says what they want ("PM role at
a mid-size fintech, behavioural plus product sense"), Ciel invents the
company, the role, and the interviewer, and shows a brief with likely
questions before the interview begins. **Case**: a consulting case from
the library — three ship with the code, grounded in public business
history with the client renamed; `ciel interview seed-cases -n 5` asks
the model for more, and `~/.ciel/interview/cases/*.json` is where they
land — or a freshly generated one; exhibits appear on the page when the
interviewer shares them, and the debrief compares the recommendation with
what really happened. **Technical**: a coding problem beside an editor
(Python, C, C++, Java, Rust, JavaScript, TypeScript, Go); the interviewer
reads the code and asks about it. Nothing is executed.

The candidate speaks through the browser's speech recogniser (Chrome,
Edge, Safari; elsewhere a text box); the interviewer speaks through piper
on the hub, or the browser's own voice when piper is not installed. The
room waits two seconds of silence before taking an answer — four times
Ciel's own window — and longer when the words trail off. When it gets
that wrong and the candidate goes on while the interviewer is already
answering, the interviewer stops, says "Sorry, go on", and hears the
whole answer. Every session is recorded (microphone and interviewer
mixed) and replayable from the page with a click-to-seek transcript.

`ciel interview serve --dev` runs the room alone on loopback with a
scripted interviewer and a `dev`/`dev` account, for working on the page;
`scripts/probe_interview.py` covers each layer.

## Watching things (Vigil)

Off by default. Everything else Ciel says was asked for; Vigil is the
machinery for the one exception — events the world produces on its own,
flowing through a single queue, judged by a deterministic policy, and
delivered by an unattended turn that the Witness rule keeps read-only
(observe and take notes, never act outward).

```toml
[proactive]
enabled = true
brief_time = "08:30"         # arms the morning brief; empty keeps it off
quiet_hours_start = "22:00"  # nothing crosses a quiet window — not even a text
quiet_hours_end = "08:00"
```

**The watchers.** The calendar (EventKit or Google Calendar via
`calendar_source`, ~10 minutes of lead time), the morning brief (today's
agenda plus anything held overnight), the work watcher (everything
registered with `watch_for_completion` — a file appearing, a process
ending — polled every 15 s), the ring, and location moves. Each produces
plain events; none decides anything.

**The policy** is a fixed order, and the order is the point: an expired
event drops; a read-back verification becomes a silent note; quiet hours
hold everything (a 2am buzz violates them exactly as speech does); then
the importance floor, presence, and two daily budgets — spoken nudges
(`max_spoken_per_day`, 6) and texts (`max_messaged_per_day`, 3). When
you're away, an important event may be texted — iMessage when configured,
the Discord DM as fallback. Everything that clears no bar is *held* and
rides into the start of your next conversation, aging out after 18 hours.

**The brake.** `touch ~/.ciel/hold` silences the whole layer — nudges
become held notes — until the file is removed; the mute switch does the
same for anything that would have been spoken. `scripts/probe_vigil.py`
drives every branch of "when may Ciel speak unprompted" with fakes.

## What is true right now (Phase Space)

On by default. Everything above produces readings — the presence probe,
the locator, the ring, the calendar, the timers, the mute switch — and
before this table each was read by exactly one consumer, and the brain
by none of them: it learned the world by calling a tool, or from a
lane's fixed note, and never knew the time of day. `world.py` is the one
table those readings are written into, each with who reported it, when,
and how long it stays trustworthy.

**What opens a turn.** Every user turn (and every unattended Vigil turn)
opens with one parenthesized block the runtime writes — the clock; the
Mac's seat, on the hub; whether you're around; the place; the mute switch
and the hold, only when set; the armed timers and watches; the rest of
today's calendar; the ring's numbers — each reading with its age in the
runtime's words. "As of" and "last known" are computed from timestamps,
never narrated by the model, which is what lets Ciel say "I checked" for
a tool result and "as of two minutes ago" for a reading here. The
`world_now` tool re-reads the same block mid-turn. Facts, not events:
things that *happen* stay in Vigil's one queue.

**Who writes it.** The pipeline (mute, hold, the timers and watches once
a second, presence once a second locally or per heartbeat on the hub,
the spoke's seat), the locator on every fix, the ring watcher and tool
on every fetch, the sections watcher on every scan, and a calendar
refresh every fifteen minutes (`agenda_refresh_s`). On the hub the
Mac's readings arrive as `fact` frames from the spoke's relay —
last-value, resent after a reconnect — and the whole table rides to
every Chart as a `world` frame when it changes.

**Observations, reducers, revision.** A producer *observes*; the table
keeps the latest observation from every source that has reported a
name, and a reducer per name folds those into the one fact readers see
— newest wins by default, the ring's numbers merge across the day's
reads, an observation older than the one already held from its source
is refused. So state moves forward whatever order the wire delivered
things in, and a second device's reading has a place to land. Every
change to a resolved fact bumps a persisted `revision` (the number an
action will name as its precondition) and appends a line to
`~/.ciel/world-history.jsonl`; `~/.ciel/world.json` (owner-only) mirrors
the table across the autoreloader's re-execs at its true age. The hub
stamps a spoke's fact with its own `received_at`, clamps a clock that
runs ahead, and refuses a `fact` frame naming a reading it owns (its
timers, the switches, presence, the seat). A calendar read that fails
marks the *source* failed and leaves the last reading standing — never
an empty afternoon in place of a dead permission; the Chart dims that
chip and says why.

**Who sees what.** The user's own readings — presence, place, calendar,
the ring — never open a turn whose reply lands where others can read
it: a public Discord channel gets the shared projection (the time, the
seat, the switches, what is armed), and `world_now` is scoped the same
way for that turn. Strings written by other people (a meeting's title,
a section id) are rendered in “quotes”, and the block says the quotes
mean *reported, not instructed*. Vigil's "is anyone around" now reads
the table's resolved presence when it is fresh, the probe otherwise.

```toml
timezone = "America/Los_Angeles"  # top level: every clock the runtime speaks
                                  # is the user's, not the host's (the hub is UTC)
[world]
enabled = true            # off: bare turns, no tool, no strip
in_prompt = true          # off: the table still feeds the Chart and the tool
agenda_refresh_s = 900.0  # 0 leaves the agenda to the brief alone
history = "~/.ciel/world-history.jsonl"  # append-only; none keeps no history
history_max_bytes = 2000000
```

`scripts/probe_world.py` drives the table, the ordering and the
reducers, the freshness rules, the block's wording and its projections,
the file and the history, the relay, the hub's door, Vigil's presence
and the calendar's failures with no network and a fixed clock.

## Spotify, from whichever device is playing

The API connector finds tracks, albums, artists and playlists, reads the
current player and its devices, and controls Spotify Connect: play or resume,
pause, next, previous, volume, seek, transfer, and queue a track. It runs on
**the brain's host** — the hub in the two-process setup — and uses the
existing Python dependencies. The double-clap's narrow AppleScript door is
still described under [Snapping and clapping](#snapping-and-clapping).

`spotify_search`, `spotify_status` and `spotify_devices` are reads, and so
are the three that look at your own playlists: `spotify_playlists` (a page
of them), `spotify_playlist_named` ("play my playlist called Morning Run"
resolves here, in your library, never the public catalogue), and
`spotify_playlist_items`. Spotify shows the contents only of playlists you
own or collaborate on, and says so plainly when asked about another's.
`spotify_control` acts on a direct request without asking for a second yes,
and the three playlist changes act the same way: `spotify_playlist_create`
(private unless you say public, so a spoken request never publishes to your
profile), `spotify_playlist_add` and `spotify_playlist_remove` (one to a
hundred track or episode URIs at a time, on the renamed `/items` endpoints).
Each change returns Spotify's snapshot id, the version of the playlist it
made, which is what the journal keeps.
Inverse still records each control and each playlist change and schedules
a read-back; without the action journal none of the four is offered. Set `confirm_controls = true`
under `[spotify]` to opt into Proof Obligation — then a confirmer is required.
Account tools refuse public-channel turns. An unattended verification can
read playback and devices, but cannot search or change playback. Names from
Spotify arrive as quoted data. An accepted control is only an accepted
request; read status to see what happened, especially after a timeout.

**Connect the account once:**

1. Create an app in the [Spotify developer dashboard](https://developer.spotify.com/dashboard).
   Register `http://127.0.0.1:8888/callback` as its redirect URI and select
   the Web API. Copy its **Client ID**; this connector needs no client
   secret. Add the listening account to the app's authorized users if
   needed. Spotify's [development-mode requirements](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
   require the app owner to have Premium and limit new development apps to
   five authorized users. Playback control also requires Premium and a
   running Spotify player.
2. On the brain's host, put this section in `~/.ciel/config.toml`:

   ```toml
   [spotify]
   enabled = false                 # true after browser approval
   confirm_controls = false        # a direct playback request is enough
   client_id = "YOUR_CLIENT_ID"     # public identifier, not a secret
   token_file = "~/.ciel/spotify.json"
   redirect_port = 8888
   timeout_s = 15.0
   authorize_timeout_s = 300.0
   ```

3. From the Ciel checkout on that host, run
   `uv run --no-sync python -m ciel.spotify authorize`. Approve in your
   browser. The callback uses PKCE and checks its state; tokens and their
   refresh lock stay owner-only. Default and configured token filenames
   are forbidden to both file and shell tools. Only playback-read and
   playback-modify scopes are requested; there is no library or playlist
   editing permission.
4. Set `enabled = true`, restart the brain so it offers the tools, and open
   Spotify on a device. `uv run --no-sync python -m ciel.spotify status`
   is a live, read-only check. Then ask Ciel to find a track or say what is
   playing. A 404 usually means the player needs opening; a 403 calls for
   checking Premium, the app's users, and its grants. Rate limits hold
   subsequent requests until Spotify's retry interval has passed.

For the Azure hub, keep the login on the hub: open
`ssh -L 8888:127.0.0.1:8888 ciel@172.184.253.239` from the Mac, then run
`cd ~/ciel` and `uv run --no-sync python -m ciel.spotify authorize --no-browser`
in that SSH session. Open the printed approval link in the Mac's browser;
the callback crosses the tunnel and the tokens stay on the hub. Keep the
session open until authorization completes. If port 8888 is occupied,
change the config, the registered redirect, and both tunnel ports together.

Search uses the current development API's maximum of ten results. It does
not read other people's playlist items or use the removed recommendations
and audio-feature endpoints. The connector streams no audio itself; Spotify
Connect controls the Spotify application. The [Spotify probe](scripts/probe_spotify.py)
checks the client, browser callback and Ciel gates using only fixture state.

## The ring (Oura)

Off by default. With a ring, "how did I sleep", "what's my readiness", "how
active was I yesterday", and "how has my sleep been this week" are answered
from the source — hours asleep, bed and wake times, the scores, and the
contributor dragging readiness down, when one does.

Oura stopped issuing personal access tokens in December 2025 (and will shut
the issued ones off), so the way in is an OAuth application of your own.
**One-time setup, about five minutes:**

1. [developer.ouraring.com/applications](https://developer.ouraring.com/applications)
   → *New Application*. Any name; the website, privacy-policy, and
   terms fields just need URLs that resolve (a GitHub profile and a gist
   will do for a single-user app); set the redirect URI to exactly
   `http://localhost:8791/callback` (or another port, matched in config).
   Copy the client id and secret. No review needed — an unreviewed
   application may connect ten accounts, and this needs one.
2. Put the credentials where the authorize step can see them:

   ```toml
   [oura]
   enabled = true
   client_id = "..."
   low_readiness = 60   # 0 keeps the tool and drops the nudge
   # client_secret: pass it as CIEL_OURA_CLIENT_SECRET to the authorize
   # step (preferred — after that it lives in ~/.ciel/oura.json, owner-only
   # and on the FORBIDDEN_NAMES blocklist, so the model can never read it),
   # or as `client_secret = "..."` here.
   ```

3. Approve once:

   ```bash
   CIEL_OURA_CLIENT_SECRET=... uv run scripts/probe_oura.py --authorize
   ```

   It opens Oura's approval page asking for the `extapi:daily` scope only
   (with PKCE), catches the redirect on localhost, and writes
   `~/.ciel/oura.json`. From then on
   Ciel keeps the tokens fresh itself — access tokens last about a month
   and Oura's refresh tokens are single-use, so the file is rewritten on
   every refresh. If it ever stops working (a revoked application, a
   refresh that failed mid-write), the tool says so and the same command
   repairs it. `uv run scripts/probe_oura.py --live` reads today from the
   ring without running the assistant.

A personal access token you already hold still works: `token = "..."` under
`[oura]`. The OAuth file wins whenever it exists.

**Read-only by construction.** The client only ever GETs the daily
summaries and sleep sessions; nothing Ciel does can write to the ring or
the account. That is also why the tool is on the Witness list: an
unattended morning turn may check the ring before it says anything about
the night.

**When Vigil is on**, the ring's verdicts become at most two notes a day.
In the morning, a readiness score at or under `low_readiness` or a sleep
score at or under `low_sleep` queues one nudge — "rough night by the ring:
sleep score 52, 5 hours 40 minutes asleep; readiness 55" with the weakest
contributor named — which the policy voices or holds like any other event.
From `activity_check_after` (18:00), an activity score still at or under
`low_activity` files a note for the next conversation ("a still day — a
walk would fix it"). A fine day produces nothing. Scores appear once the
ring syncs through the phone; the watcher rescans every half hour and on
wake from sleep. Any threshold set to 0 switches that check off.

## Ciel's own email address

Off by default. With `[mail]` set, Ciel has an address of its own —
`ciel@example.com`, say — and one tool, `send_as_ciel`, that sends from it.
The Gmail connector sends *as you*; this sends *as Ciel*, and the prompt
teaches the difference: a note you asked Ciel to send you, a message on
its own behalf, anything a reader should see as coming from an assistant
goes here; mail in your name and voice goes through your own tool. The
send sits behind the same spoken confirmation as every other one ("Send
an email from my own address to ..., subject ... — okay?") and is absent
from the Witness observers, so an unattended turn can never use it.

Sending runs through Cloudflare Email Service's SMTPS relay, which needs
the domain on Cloudflare DNS and onboarded for Email Sending (it adds the
SPF/DKIM/DMARC records itself) and an API token with the *Email Sending:
Edit* permission. Relaying to the account's verified destination
addresses — yours — is free on every plan; mailing anyone else needs the
Workers Paid plan. Inbound mail to the address is Email Routing's job:
forward it to your inbox, with a filter on `to:` / `from:` the address to
give it a label of its own.

```toml
[mail]
enabled = true
address = "ciel@example.com"
owner = "you@gmail.com"     # where "email me" goes; a verified destination
copy_to = "you@work.edu"    # optional: a silent Bcc of everything Ciel sends
token = "..."               # or CIEL_MAIL_TOKEN in the environment
```

`copy_to` gives you a record of Ciel's outgoing mail in an inbox of your
choosing: the relay Bcc's it on every message, tool and alarm alike, and
the recipient never sees it. A refused copy is logged, not fatal; a
refused recipient is a failed send.

The section watcher's alarm borrows this relay and address when it has
none of its own, so one token serves both.

## Events from email

The [email-to-calendar feature plan](design/2026-09-08-email-calendar-plan.md)
is the first application of
[independent action](design/2026-09-08-independent-action-plan.md): the inbox
read as a source of dated commitments, previewed first and, later, added to a
calendar under a scope the owner approves in Chart. Its first milestone, the
preview, landed on 2026-09-09 in `email_calendar.py`, and the calendar writer,
the inbox watch, and the standing grant followed the same day: one previewed
candidate can be added under the owner's approval of that exact event, and,
under a grant approved in Chart, confirmed commitments are added as they
arrive, with no question asked, and what is on the edge is asked at the
start of the next conversation.

**A preview is an ordinary finite task, and it writes nothing.** With
`[email_calendar].enabled = true` beside a running task runner, a private
owner turn can ask, through `preview_inbox`, for the inbox since a date. The
task lists a bounded window of mail through the Gmail connector's login on
the execution host (a login on the Mac authorizes nothing on the hub),
records each message once, and takes them one at a time: bulk mail, marked by
a list-unsubscribe header or a bulk precedence, is a promotion without a
model call; anything else goes to the isolated extraction call with a fixed
prompt that says the message is untrusted data, and spends one of the task's
own model calls. The result arrives as a notice, and `inspect_task` or Chart
shows the roster in the messages' own words, quoted.

**The model is held to the message.** Every excerpt it cites must be in the
message word for word; every time must parse as a wall time in the zone the
message names, or the owner's `timezone`, or it stays unresolved; a wall time
daylight-saving time skipped or repeated, an end before its start, and a
missing end are unresolved and named. Then policy, never confidence, decides:
a confirmed commitment with nothing unresolved is *ready*; an invitation, an
unresolved field, a citation that is not in the text, or a change to an
event is *review*; a promotion is *ignored*. Two events in one message are
two candidates. The sender is a criterion only when `allowed_senders` names
some: then a confirmed commitment from an address not on it is *review* too.
Either way a sender certifies nothing: a display name, a matching address,
or a header the message carries about itself is not proof it is genuine,
and the ready reason says so.

**An event is added once, under a name only Ciel would choose.** With a
`destination_calendar` configured, `add_event_from_mail` takes a candidate
key from a preview and makes a second finite task: `calendar.check` looks
for the event already there, first under the id Ciel would give it and then
across the destination and `check_calendars` by start time and title; an
independent match is *present* and the task completes without sending, a
foreign event under Ciel's id is a recorded *conflict* that waits and
overwrites nothing, an event Ciel added and the owner then deleted is
*suppressed* and never recreated. Otherwise `calendar.create` plans one
insertion, the owner is asked to approve that exact payload once, and it is
sent once under an id derived from the mailbox, the message, and the
calendar, with Ciel's ownership in the event's private properties and no
mail text anywhere on it. `calendar.verify` reads it back and completes the
task only when it is there. A lost answer is reconciled by the id, applied
or not, never resent blind; an id that exists but holds another event is an
unknown the owner is asked about; an event the owner edited afterwards is
theirs, placed and noted as edited, never overwritten.

**A page is queued before the cursor moves.** A watch, the task behind a
standing mandate, reads the mailbox's history from the anchor it took at its
start and never before it, one page a step: the messages a page names are
recorded as queued and the page token saved in the same write, and the
history id advances only with the last page, so a crash replays a page and
never skips one. Then the queue is extracted and the watch goes round again
after `poll_s`. When Gmail has forgotten back to the cursor, the watch lists
the window since its anchor once, bounded by `max_messages_per_poll`, takes
what it did not have, and anchors again, counting the resync on its record.
**The candidates are in Chart.** Under **Tasks**, below the standing
grants, *Events from email* lists every dated candidate as a row of durable
record: its state in a word — *found*, *needs clarification*, *ready*,
*adding*, *added*, *already present*, *conflict*, *change proposed*,
*dismissed* — the event's title and time, the sender and the subject
quoted, the decision's reason, what is unsettled, and where it stands on
the calendar. Each row offers only the controls the feature named for it:
**Add to calendar** makes the same finite task the voice tool would, which
asks for the exact event's approval before anything is sent; **Dismiss** is
the tombstone; **Approve change** on an open proposal is `approve_proposal`.
Every string is shown as text, never as markup. The rows come from a
`listing` the feature binds on the controller, which puts them in the list
view under the setup's title; a listing that fails is left out and the rest
of the view stands.
`dismiss_candidate` is the owner's no to a candidate: a tombstone on its
record that outlives replay, so the same message never puts it forward or
adds it again, and a second preview of the window reads it no more.

**Automatic means the same, without the question.** With a destination
calendar configured, the feature offers a grant setup to Chart's *Standing
grants* form: the operations it would be granted, the calendar and the
mailbox as targets, the senders it acts on — the list, or any sender — with
the caution that a message reading as genuine is not proof it is, and the day's
`max_creates_per_day` and the grant's `grant_lifetime_s` as its limits, each
capped by `[tasks]`. The owner's yes activates the grant and its mandate, and
the feature's first move under it is the watch, as the turn that approved.
Each ready candidate the watch extracts is proposed as a derived add task;
the store admits it only inside the grant, once per message, within the
allowances, and the runner dispatches it under the grant's authority with no
question, sending exactly what a per-action add would. Pausing the mandate
pauses the watch, resuming it resumes the watch, and revoking the grant ends
it.

**On the edge, Ciel asks next time you talk.** A candidate the watch finds
that is a real commitment with a gap — an invitation, an end time the email
never gave, a zone it never named — is not added and not left to be found in
the roster: its record carries a question, and the task notifier hands it to
Vigil as news (importance one), so it is never spoken into an empty room and
opens the next conversation instead, in the message's own words, quoted:
*"An email from sam@friends.test reads as 'Dinner with Sam' on 2026-09-20 at
19:00 — unsettled: end. Does it go on the calendar?"* Your yes is
`add_event_from_mail`, which can now take the `end` or `timezone` you just
gave — those two fields and no other; the date and the start are the
message's or nothing — and writes them on the candidate's record, at the
revision it read, with the add task. Your no is `dismiss_candidate`. Either
answer given before the question was put retires it. At most
`[tasks].max_held_questions` wait as held notes at once; the rest stay owed
in the store and are asked after those, oldest first. A preview you asked
for in person asks nothing: its roster is the answer.

**A change is a proposal until the owner says so.** A later message from
the sender of an event Ciel added, about that event by title, saying it moved
or is cancelled, never touches the calendar by itself: the watch or the
preview records an inert proposal on the event record, naming the update or
the removal it would take and the message it came from, and the create's
receipt stays as it was. A create-only grant covers neither, so nothing is
derived; a newer message about the same event supersedes the open proposal,
which is kept under its source's key. `approve_proposal` takes the exact open
proposal, at the revision `inspect_task` shows, and makes one task whose
scope is that operation alone, marking the proposal approved in the same
transaction, so a stale or repeated approval makes no task. The change is
planned at the event's current version and sent with it, and carries only
the fields the proposal set: an edit of yours landing between the plan and
the send is a failed precondition, unsent and planned again, and your edits
to other fields stand. A moved commitment with no original on record is
review, never a fresh add. The removal reads back as gone; the change reads
back as proposed.

**What runs out is said.** A window larger than the task's model calls
leaves the rest recorded as unread with the reason; a runtime with no
extraction backend records the same; a mailbox that is not connected is a
resource wait that names the fix. A second preview of the same window
records no message twice. Everything the feature keeps lives in its own
namespace in the task store, owner-only, revisioned, and bounded, and a
public lane can neither ask for a preview nor read one.

```toml
[email_calendar]
enabled = false              # register the inbox adapter with the task runner
mailbox = ""                 # the identity expected; empty accepts the connector's account
timezone = ""                # the owner's IANA zone for messages that name none; empty keeps them unresolved
allowed_senders = []         # empty: any sender's confirmed commitment is ready; listed: only these are
max_messages_per_preview = 25
max_body_chars = 32000       # of one message's text, to the extraction call
max_extractions_per_day = 100
destination_calendar = ""    # the Google calendar id events are added to; empty means preview only
check_calendars = []         # calendars also searched for an event already present
poll_s = 300.0               # between a watch's looks at the inbox's history
max_messages_per_poll = 25   # messages one page of history may queue
max_creates_per_day = 10     # what a standing grant asks for; [tasks].max_grant_per_window caps it
grant_lifetime_s = 2592000.0 # thirty days; [tasks].max_grant_lifetime_s caps it
```

The reader borrows `[sections].gmail_oauth_keys` and `gmail_token_file`,
read-only, exactly as the section alarm's sender does; the writer borrows
`[proactive].google_oauth_keys` and `google_token_file` the same way. Both
must be authorized on the execution host. The plan's five milestones are
built against fakes; `probe_email_calendar.py --live` is the separate
acceptance against the real accounts on this host: it reads the mailbox's
anchor and window read-only, reporting counts and nothing of the mail, and
on the destination calendar inserts one synthetic event under its own id,
reads it back, changes and removes it at its version, proves a stale
version is refused, and reads it back as gone; with no destination set it
lists your calendars by id instead. It sends no mail and touches no other
event. The watch under a grant against real mail has not been run. Nothing is enabled by
these words: automatic additions need the runner, the feature, a destination
calendar, both logins on the execution host, and a grant the owner approved
in Chart.

## A spot in a section (the signup site)

Off by default, and only useful with Vigil on. Berkeley courses fill
sections first-come-first-served on the *sections* app
(sections.datastructur.es for CS 61B), and a dropped spot is gone in
minutes. Watched, an opening becomes an importance-3 event the moment a
scan sees it — spoken if you're around, texted through the away outlet if
you're not — phrased whole: "a spot just opened in CS 61B lab section 31 —
Tuesdays 10:00 AM to 12:00 PM at Soda 275 with Erin."

The site shows sections only to a browser signed in through the course's
Canvas OAuth, which can't be automated, so setup borrows your browser's
session. **Two minutes:**

1. Sign in at the site, open DevTools → Network, click any `/api/` request,
   and copy the whole `Cookie` request header.
2. Point Ciel at it and pick the sections you want:

   ```toml
   [sections]
   enabled = true
   cookie = "session=..."   # or CIEL_SECTIONS_COOKIE in the environment
   watch = ["12", "31"]     # section ids, from the listing below
   ```

   `uv run scripts/probe_sections.py --live` lists every section with its
   id, weekly slot, and open spots — the ids are what `watch` wants — and
   marks the watched and enrolled ones.

The scan is a transition detector: full → open fires, still-open stays
quiet, a refill and reopening fires again (dedupe is bucketed by the hour,
so a flapping roster can't burn the day's texting budget). A section the
site says you're already enrolled in is old news and never fires. Read-only
by construction: the client can see `join_section` in the site's API and
deliberately doesn't call it — Ciel tells you a spot opened; taking it
stays a human act. Polls every `poll_s` (30 s) and on wake from sleep,
since a spot that opened mid-nap is exactly the race this watcher exists to
win.

### The email alarm

A spoken nudge is one sentence and a text is one buzz; a spot that is
gone in minutes may deserve more. `email_on_opening = 5` makes the watcher
itself email you when a watched section opens — five distinct messages
(each with its own subject, so Gmail doesn't fold them into one thread
with one notification), `email_interval_s` apart. It runs alongside the
queued event, deliberately outside the interruption policy and its quiet
hours: the policy judges whether a moment has earned an interruption, and
this setting is your standing answer that for this one thing it always
has. Mail goes through the `[mcp.gmail]` connector's login (its refresh
token is borrowed read-only, like the calendar watcher borrows the
calendar's — `gmail.py`), to that same account unless `email_to` pins
another, and as that account unless `email_from` names a verified "send
mail as" alias. Or, with `smtp_token` set, the alarm goes out *as Ciel*
through an SMTP relay instead (`mail.py`) — Cloudflare Email Service's
`smtp.mx.cloudflare.net:465`, username `api_token`, an API token with the
Email Sending permission as the password, `email_from` on the onboarded
domain (`ciel@example.com`) and `email_to` a verified destination, which
Cloudflare relays free on every plan. No usable sender: a warning at
startup, and openings fall back to the ordinary nudge.

### Keeping the cookie alive

The site's session is a sliding two-hour window: every request pushes its
expiry forward, so while Ciel is polling, the cookie *never* lapses — the
watcher keeps itself signed in for free. It only dies after two hours with
no request, which in practice means an overnight sleep. Reviving it means
the Canvas OAuth login again, and that needs a live bCourses session, so it
can't be done from a bare script (CalNet and Duo would block it, and Ciel
will not store your password or answer your Duo prompt).

The way around that is a dedicated browser profile you sign into once, which
then holds the bCourses session (Duo's "remember this device" keeps it
alive for days). After that, `scripts/refresh_sections_cookie.py` follows
the OAuth round-trip silently in a headless Chrome and writes the fresh
cookie to `~/.ciel/sections-cookie`, where the watcher reads it on the next
poll. The file is owner-only and its name is on both the file and shell
credential blocklists. A different `[sections].cookie_file` reserves that
filename too; moving the cookie does not grant the brain access to it.

**One-time setup:**

1. Make a small venv with Playwright (kept out of Ciel's own dependencies):

   ```bash
   uv venv ~/.ciel/sections-refresh-venv
   VIRTUAL_ENV=~/.ciel/sections-refresh-venv uv pip install playwright
   ```

2. Sign in once, in a visible browser, and complete CalNet + Duo yourself
   (tick "remember this device", and approve the app if Canvas asks):

   ```bash
   ~/.ciel/sections-refresh-venv/bin/python \
       scripts/refresh_sections_cookie.py --login
   ```

3. From then on it's automatic, two ways over. A launchd agent
   (`~/Library/LaunchAgents/es.datastructur.sections-refresh.plist`) runs
   the script each morning and on wake — for when the cookie died overnight
   and Ciel isn't up yet. And with `auto_refresh = true` in `[sections]`,
   the watcher itself runs it the moment a scan comes back signed-out — for
   when Ciel *is* up but the Mac was off past the two-hour window. Both are
   cheap: the script makes one plain HTTP check first and only spins up
   Chrome when the cookie is genuinely dead.

When the remembered bCourses session eventually lapses (a week or two), a
silent refresh fails, the watcher's failure streak files a note, and one
more `--login` re-arms it. Uses Google Chrome via Playwright's
`channel="chrome"`, so nothing but the `playwright` package is downloaded.

## Where you are (location)

Off by default. On, "where am I" — and anything that depends on place —
is answered from what this machine can actually see, turned into a name
you chose:

```toml
[location]
enabled = true
# findmy_device = "iPhone"   # the phone's position, where macOS allows it (see below)

[location.places]
home = "attinternet"                  # a Wi-Fi network name…
office = ["OfficeNet", "OfficeNet-5G"]  # …or several
cabin = [44.0, -121.0]                # …or coordinates (with findmy_device)
```

`uv run scripts/probe_location.py --live` reads the sources once and tells
you the network name to put under `[location.places]`.

**Two sources, honestly labelled.** The Wi-Fi network the Mac is on needs
no permission and locates the laptop — which is you whenever the laptop is
with you. Find My's cache locates the *phone*, and is used when two gates
allow: the app running Ciel (your terminal) must have Full Disk Access
(System Settings → Privacy & Security), and macOS must still write the
cache as JSON — 14.4 and later don't, in which case the watcher says so
once in the log and carries on with the network. CoreLocation is not an
option at all: macOS never shows a Python process the permission dialog.
The answer always names which device it came from and how old the reading
is. Read-only throughout, and the tool is on the Witness list.

**When Vigil is on**, moves between named places become importance-1
notes — "you arrived at office around 9:12" — mentioned at the start of
the next conversation, never announced. The first reading after startup is
a silent baseline; hopping between two unnamed networks is not a move.


## Granting powers by text

Off by default. With `[grants] enabled = true`, Ciel can change its own
capability switches — but only when you ask, and never quietly:

- *"Enable your shell access"* (spoken or texted) → Ciel asks **"Enable
  shell access — okay?"** through the enforced gate — voiced at home,
  texted over the Discord lane — and only a yes edits the config. The
  change lands surgically in `config.toml` (your comments survive, the
  result is parse-verified, a bad write rolls back), gets journaled like
  every confirmed action, and takes effect after the reload it triggers.
- *"Kill your shell"* → immediate, no question. De-escalation never has
  friction; it's the one-way valve's philosophy applied to permissions.
- The catalog in `brain/tools/grants.py` is the boundary: files, shell,
  screen, iMessage read/send, Vigil, barge-in, the morning brief, Discord
  away texts, and acting without asking (`act_without_asking`, the one
  switch whose grant turns a value *off*; the model is told that *"act
  without asking"* on its own is this grant, not a manner for a task it
  has not been given). What's *not* in it is the point — the pinned Discord
  account and token, the voice gate, connector tool tiers, and the
  workspace path cannot be reached by any phrasing, from anywhere.
- Unattended turns (reflection, Vigil) are denied both tools outright by
  the Witness rule, and the model is instructed to grant only on your
  explicit request — never because something it read suggested it. The
  instruction is manners; the gate is the enforcement.

## Memory

Ciel saves things it learns, without being asked, as one Markdown file per fact
in `~/.ciel/memory/`. Open the directory to see exactly what it believes about
you; delete a file to make it forget.

Three mechanisms, deliberately separate:

- **Conversation continuity** — a thread survives 10 minutes of silence
  (resume across restarts included); past that the session is rotated away
  and the next conversation starts on a clean, fast context. This is what
  `--new` forces early.
- **Durable knowledge (Invariant)** — outlives any session. About a minute
  after each conversation ends, Ciel runs one silent reflection turn
  (Closure — you'll see `[reflecting]` in the console) and commits anything
  durable to these files while the session still remembers it.
- **Projects (Atlas)** — durable working state for anything spanning
  conversations, one file per project in `~/.ciel/projects/`. Say "let's get
  back to the wake word project" and Ciel opens the file rather than trusting
  its recollection; it updates the state and logs milestones as work moves.

Only the one-line summaries go into the prompt each turn. Full contents load on
demand, which is what keeps memory affordable as it grows.

**A project is bound to the work it is about.** Since 2026-09-09 a project
also carries, as lines in its own frontmatter, the owner's statements about
where its work lives: an `id` minted the first time the file is written or
bound and kept across a rename; `aliases`, the other names the owner uses
("analysis homework", "h104"), which `open_project` honours before any
guess; and `resource.<key>` lines, each a role (solution, handout, folder,
draft, dataset), a source (`local` or `url`), whether it is the current one
of its role, an optional opener, and the path or URL. "Pull up my analysis
homework" resolves the phrase to the project by exact name, then exact
alias, then a unique partial match; several matches are a question, never
a guess. `bind_resource`, `select_resource`, `unbind_resource`, and
`rename_project` write these only on the owner's own words — a place Ciel
found in a listing is something to ask about, not to bind — and only the
private brain has them. A file from before this date loads exactly as it
did, its prose untouched, until it is bound; a rename keeps the old name
as an alias. `scripts/probe_atlas.py` drives all of it.

**Pull it up, and where did I leave off.** `open_document` opens a bound
resource where the owner is — the current one of the role asked for, in
the app its binding names or the Mac's default, a URL in the browser —
and `project_progress` reads the bound document now and answers from it:
which questions or sections have an answer written, are in progress, or
are not started, each with its line and the words it rests on. The
readers (`readers.py`) are deterministic passes, never a compile or a
macro: LaTeX by the template's own environments — `numedquestion`,
`alphaparts`, `framed`, both spellings, comments dropped, verbatim
opaque, the unused `\answerbox` read as not started, a box that only
repeats the statement read as not started, a TODO as in progress — and
Markdown by headings. Counts describe what is written, never correctness
or effort, and a roster read from the working file alone says its
completeness is unknown. Includes are followed only within the folders
bound to the project — the owner naming the folder is what makes it
readable, not the brain's workspace guard — up to `max_includes` and
`include_depth`, each file within `max_document_bytes`; a reference that
escapes or fails is a gap named in the reading. The hands are the
*workbench* (`project_work.py`): on the hub, the spoke's two new
operations, `project.read` and `project.open`, which re-check the path
against the Mac's own home, state directory, credential names, and
document suffixes whatever the hub said; in the single process, the
filesystem and `open`. A hub without its spoke says the Mac is not
reachable rather than reading the server. `scripts/probe_readers.py`
drives the readers, and the documents ride in `probe_atlas.py` and
`probe_tool_rpc.py`.

**Readings are kept under a grant, and only then.** Opening or remembering
a project never starts watching it. Under **Tasks → Standing grants**, the
feature *Readings of bound documents* offers one target per project with a
readable local document; the owner narrows to the projects to watch, and
the yes starts a watch task under the mandate. The Mac then watches those
projects' bound documents — a stat every `watch_poll_s`, a hash only when
the stat moved, one event when a change has held still for two looks, so
a burst of saves is one event and an unchanged save is none — and reports
each settled change up the wire by path and content hash, nothing else,
mirrored in `~/.ciel/spoke-resources.json` so a restart catches up on what
moved while it was down. On the hub the change is a `change` record in the
`atlas` namespace; every `observe_poll_s` the watch derives one bounded
reading task per hash no reading has, and that task reads the document
through the workbench and keeps the reading — the summary, every followed
file's hash, the roster within `max_items` — only if the change it was
derived for is still the newest on record. A save during a reading makes
that reading superseded, and the newer hash gets its own; a file gone
derives nothing and the last reading stands at its age. The Mac is told
about each include a reading followed, and told the whole set again every
time it seats. A resource change is never news: it goes to the adapter,
not to Vigil, and nothing is spoken. `open_project` then shows the last
readings with their age; `project_progress` still reads afresh. Pausing
the mandate pauses the watch, revoking the grant ends it and the Mac
watches nothing. In the single process the same watcher runs beside the
work watcher, on this machine. `scripts/probe_project_watch.py` drives the
watcher and the adapter; the wire and the routing ride in
`probe_tool_rpc.py` and `probe_turns.py`.

```toml
[projects]
enabled = true
dir = "~/.ciel/projects"    # one Markdown file per project
max_index_entries = 30      # projects named in every system prompt; close the rest
max_state_chars = 4000      # an opened project's state, whole; more is refused, not truncated
log_tail = 15               # log lines returned on open; the file keeps them all
max_resources = 24          # places one project may be bound to
max_document_bytes = 2000000 # the largest bound document a reading takes
max_includes = 20           # includes one reading follows, within the project's folders
include_depth = 3           # how deep an include of an include is followed
watch_poll_s = 15.0         # on the Mac, between looks at the watched documents
observe_poll_s = 60.0       # on the brain's host, between the watch task's looks at settled changes
max_reads_per_day = 200     # what the readings grant asks for; [tasks] caps it
reading_lifetime_s = 2592000.0 # thirty days; [tasks] caps it
```

### Quick notes

An idea does not have to become a conversation to be remembered. On the Mac,
**Command–backslash** or **two backslashes within 350 ms** opens a floating
Instrument window with the cursor ready. Both gestures are enabled under
`[notes]`, independently of the voice shortcuts; they use the same macOS
Input Monitoring permission described [above](#keyboard-shortcuts). This is a
passive listener: the backslash pair also reaches the app you were typing in.
Set `double_backslash = false` to keep only the command chord, or set
`shortcut = ""` to keep only the pair. Bindings name physical ANSI positions.

The window follows the Quick Note Prototype supplied on 2026-09-09: a
560-point cut-corner bar with a gold dot and an inline save hint. Native colours
use the prototype's sRGB space, preserving its dark ground and accents. The editor
grows with the thought up to 180 points, then scrolls. Hover reveals the
character count, **Discard**, and **Save**; Tab reveals the same controls
and focuses Save, with a cyan focus border. A narrow window puts the count
on its own row. Drag the bar's background to move it; reopening keeps that
position for the life of the window process.

**Command–A** selects the whole note; **Command–X/C/V** cut, copy, and paste
through the native text editor. These shortcuts act while the editor has
focus and is editable, including after a failed save restores the draft.

**Enter** adds a line. **Shift–Enter** or Save puts the note into Invariant as
a reference, preserving its wording and paragraphs without a model call.
While saving, the bar folds to a single line with a breathing gold dot and a
progress label. A real receipt shows “Saved to memory.” in green and fades
with a six-point downward drift over 0.2 seconds before returning focus.
Reduce Motion keeps the fade stationary; reopening cancels it. A failure restores the
editor, turns the dot and border red, and keeps the error and **Retry** visible
even without hover. **Escape** or **Discard** removes the unsaved draft and
closes the window; reopening starts blank. Only Shift–Enter or Save submits
to memory, and closing after submission does not undo that save.
A quiet **Undo** chip stays for five seconds after discarding an unsaved
draft. Its copy exists only in RAM: the disk draft is already gone. Undo
restores the editor without saving to memory; expiry, a process restart, or
starting a new draft forgets it.
Ask Ciel later, for example, “Find my note about the moon garden”;
notes use the ordinary memory index and `recall` keyword search.

While open, the draft lives in `notes.dir/draft.json`, owner-only, for recovery
after an unexpected restart. Explicit dismissal deletes that backup.
In split mode the hub writes `memory.dir/note-<id>.md`; local mode
writes the same format locally. The note and its human-readable index are
owner-only, and note text is quoted as data when it enters the brain's prompt.
Nothing is broadcast to another Chart or entered as a conversation. Two notes
with the same opening remain distinct. Retrying after a lost receipt uses the
same id and cannot create another copy of that note.

Hover or use Tab to reach **Recent**, **+ Context**, and **Dictate**:

- **Recent** opens a searchable, selectable history of confirmed saves on
  this Mac. Back or Escape returns to the current draft unchanged. The latest
  `history_limit` entries live owner-only in `notes.dir/history.json`, so
  browsing still works offline. This history begins with saves made after
  the feature was installed; older notes remain available through Ciel's
  memory search. Retried receipts do not add duplicate entries.
- **+ Context** attaches the app you came from and a page title and URL for
  supported browsers (Safari, Chrome, Edge, Brave, Chromium, and Arc).
  Reading happens only on that click. The attachment appears as ordinary
  text before Save, with **Remove Context** to take it back out. Browser
  Automation permission may be requested by macOS; if a page cannot be read,
  the app name is attached and the window explains the limitation.
- **Dictate** borrows Ciel's existing microphone while Ciel is idle and
  unmuted. Wait for **Stop Dictation**, speak, then click it to transcribe
  with the running speech engine. Capture stops automatically after
  `dictation_max_s`. Words enter the draft at the cursor, ahead of attached
  context, and still need Shift–Enter or Save. These frames do not enter a
  voice conversation. Discard cancels capture or pending transcription;
  late results cannot recreate a closed note. Becoming busy or muted cancels
  dictation, preserving typed text. This feature holds audio in memory and
  never writes it to a file.

An offline brain, a disabled memory store, a full disk, or a hub without note
support leaves the draft and an error beside the editor. Keep it open and press
Shift–Enter to retry after the problem is resolved; drafts are not submitted
automatically. Both halves need this version for split-mode saving. `[notes]
enabled = false` disables capture; `[memory] enabled = false` on the brain
also prevents saving. `max_chars` bounds the note in the editor and writer,
and `save_timeout_s` bounds the wait for a hub receipt. The spoke keeps its
local draft until that receipt or explicit dismissal, so a reload or failed save cannot silently
throw an idea away.

## Independent action (planned)

The [independent-action plan](design/2026-09-08-independent-action-plan.md)
describes Ciel carrying authorized responsibility between conversations:
remember the outcome, take bounded steps, wait, recover after interruption,
verify the result, and report privately. It builds on the durable task store
and owner controls below. The shared foundation owns grants through Proof
Obligation, scheduling, Inverse correlation, recovery, and reliable receipts;
each feature supplies its own sources, actions, and evidence. Events from email
are the first feature. The foundation's acceptance checks use a synthetic
adapter so another feature can reuse it without depending on an inbox.

Implementation builds on the task controls that landed as commit 45e4fd7.
Its first milestone, the runner, the ladder's task step, namespaced feature
records, and the isolated extraction call, landed on 2026-09-08 and is
described under durable tasks below. The second milestone's records, two
kinds of origin, grant drafts, standing grants, mandates, and derived tasks,
landed on 2026-09-09, and the Chart grant form with the broker's approval,
dispatch with reconciliation, and delivery with receipts
followed the same day, and the third milestone, dispatch intent, guarded
mutation dispatch, and reconciliation, landed on 2026-09-09 as well; nothing
derives work under a grant until an adapter offers one, and no adapter can
write yet. The revised plan specifies derived-task origins, a private Chart grant form,
versioned feature records, a grant-less preview task, one task per operation
on a persistent event record with inert proposals for changes the grant does
not cover, and an isolated extraction call sharing the ordinary model-turn
lease. The [review response](reports/2026-09-08-independent-action-review-response.md)
records the decisions and their limits.

## Durable tasks and owner controls

A task is the runtime's record of an explicit owner mandate: outcome, scope,
selected checks, next step, evidence, and the reason it is waiting. Atlas remains
the project notebook. With `[tasks].enabled = true`, Ciel can save one PR-check
watch per owner request, list and inspect saved tasks, and pause, resume, answer,
or cancel them. For example: “Save a watch for the build and unit-test checks on
repo-owner/repo PR 12.” The repository, PR, and exact check names are required;
missing targets or a request for several watches need clarification first.

**Saved is not started.** Creation commits directly into a resource wait, and
with `[tasks].runner = false` (the default) nothing ever moves a task: no
observation, no credential setup, no automatic notification, no external
action. Resume and accepted owner answers also remain waiting while execution
is unavailable. Ciel says it saved the request; it does not claim to be
watching. The hub owns one asynchronous store in split mode, standalone owns
its local store, and the spoke owns neither. Failure to open storage disables
task controls while ordinary conversation continues.

**A task gets another turn.** With `runner = true`, the bounded runner in
`task_runner.py` takes the bottom rung of the ladder: from an idle room, after
every human lane and after Vigil, it claims the oldest queued task whose time
has come and gives it exactly one step. An adapter serves a set of operations;
its `prepare` is pure and may say wait, its `read` observes the target and
returns evidence and what should happen next: completion when the evidence
matches the criteria, a checkpoint with a delay, an external or resource wait,
or a question for the owner. A mutation step is dispatched only through an
adapter that declares how it plans, sends, and reconciles one; a mutation
whose adapter cannot, or an operation no adapter serves, waits visibly. No
adapter ships yet: the probes supply synthetic ones, and the first real ones
belong to the inbox feature.

**Nothing is sent that was not first written down.** For a mutation the
runner claims the attempt, has the adapter plan the payload against what it
reads of the target now, digests the payload and those preconditions,
writes the intent to the action journal, and asks the store for authority:
a derived task's grant, at the revision its mandate was activated with,
active and unexpired and covering the operation and the target; a
proposal's task's approval; or, for any other owner task, an approval the
owner gave to this exact payload. Nothing else is authority, and the check
is made at the send, every time. The store commits the intent, the
operation and exact target, both digests, the authority, the journal
reference, and a deadline in the same write that marks the attempt
dispatched. Then the adapter sends, once, under `mutation_timeout_s`, and
the effect is verified by the read the plan named before anything
completes. A target that moved between the plan and the send, or a passed
`dispatch_deadline_s`, is an unsent attempt: the intent closes as not
applied and the mutation is planned again. A runtime with no journal sends
nothing. A human task with no standing authority asks the owner, in the
task's own question, to approve this action; approve dispatches it once,
cancel ends it, and a changed payload asks again.

**An outcome nobody knows is reconciled, never resent.** A timeout, an
exception, the owner's voice, or a process death after the send leaves the
attempt uncertain and the task waiting for reconciliation, ahead of any
new step. The runner asks the adapter what happened, read-only: applied
resolves the intent, records the evidence, returns the retry allowance the
uncertainty had charged, and queues the verifying read; not applied
reopens the mutation for a fresh plan under fresh authority; a read that
cannot tell is counted on the intent, and after `max_reconcile_reads` the
owner is asked in two exact words, whose answer resolves it the same way.
A grant revoked after the send still lets the effect be reconciled and
refuses the next send. A task the owner cancelled while uncertain has the
effect recovery finds recorded on it and stays cancelled. An edit the
owner made to the target after the effect is read, not overwritten. With
a runner present, a resumed or answered task is queued rather than parked.

One exception keeps a task from starving: one that has waited longer than
`task_aging_s` moves ahead of a *nonurgent* Vigil nudge. An urgent one, the
kind the policy would message the owner about, stays ahead, as does every
human lane. Human input wins outright: if a step is holding the model turn
when the owner speaks, the pipeline interrupts it, the attempt is spent, and
the task is requeued with `retry_backoff_s`. A plain read holds nothing anyone
is waiting for and is left to finish. Every write the runner makes names the
attempt it holds; the store refuses one for an attempt that is no longer
current, so a step that outlives the owner's cancellation or a restart writes
nothing. Giving up is recorded, never retried blindly: a timeout, an adapter's
exception, or an outcome the store refuses abandons the attempt through the
store, which requeues a read with backoff, holds a sent mutation for
reconciliation, or fails the task where the owner can see why.

**A feature's records have a namespace, not a column.** An adapter registers a
namespace with a version, a payload validator, and a migration; the store
keeps that namespace's records owner-only, revisioned, bounded by
`max_feature_records`, and committed in the same transaction as the checkpoint,
wait, or completion they belong to. The store never reads inside a payload. A
namespace it does not recognise, or one newer than its registration, is
preserved untouched and reported unsupported: the tasks that need it wait,
everything else runs. A store from schema version two or three is lifted to
four at open with every task in place; a newer store is still refused.

**A mandate can stand.** A standing grant is the owner's approval of one exact
scope, digest and all: a draft in `grant_drafts` is what the owner looks at,
its revision and digest move together when it is edited, and it is never
executable. Activation takes an attended private owner turn, the exact draft
revision and digest the broker's yes was bound to, a registered adapter
namespace, and limits within the configured caps, and commits the grant and
the mandate under it in one transaction. The mandate is the responsibility
that derives finite tasks from that adapter's events until the owner pauses
or revokes it, its grant expires, or its allowances run out; it is never run
itself, and completing a child completes nothing above it. Revoking a grant
ends its mandates at once, and an expired grant is marked so on the record
the moment it is asked to derive. Config caps what a grant may hold and can
never mint one.

**The form is in Chart; the yes is the broker's.** Under **Tasks**, a
*Standing grants* section lists mandates with their grants, open drafts, and
a form for every feature that offers one. An adapter offers a `GrantSetup`:
its title, outcome, execution host, the operations and targets it can be
granted with their labels, the accounts it would act as, and its limits. The
owner narrows the operations and targets; everything else is shown, not
chosen, and the saved draft carries the setup's host, account, outcome, and
limits — lowered to the `[tasks]` caps where a setup asks for more, the
smaller number governing — so the draft holds nothing the owner did not see
and the question names what will actually be allowed. Approve sends the
revision and digest the page rendered. The controller refuses before any
question when they are not the current draft, then asks through the
pipeline's broker on that one private session: the question arrives as the
ordinary confirm prompt on the socket that pressed Approve and on no other,
and is answered the way every web question is. The broker takes the question
only when it is idle and no turn has a channel installed, for exactly the
ask's duration; a no, a timeout, a reload, or a draft edited while the
question was open leaves the draft, never a grant. Activation rechecks the
draft and the caps in its own transaction and journals the approval
reference. Voice can say where the form is and can pause, resume, or revoke
what was approved through `pause_mandate`, `resume_mandate`, and
`revoke_grant`; it never fills the form or answers for the page. No feature
offers a grant yet; the probes supply a synthetic one.

**A result is owed until the owner has seen it.** Completion, failure, and
the question a task is waiting on write their notice in the same
transaction as the state, so a crash before anyone is told loses nothing;
the store keeps the notice owed until the owner looks at the task on a
private lane, which is the receipt, and records every delivery attempt
beside it with the lane that carried it. The notifier hands each owed
notice to Vigil once per attempt as an ordinary event, in one spoken line
with only identifiers in its payload; Vigil's presence, quiet hours, and
budgets decide when and where it is said, and a spoken nudge, a text, or a
held note read into the next conversation is recorded as sent. A notice
that was sent is not offered again; one Vigil holds is not a failure, it is
still owed and rides the next conversation. A delivery that failed outright
is offered again after `notice_retry_s`. A feature's questions ride the
same notifier by a convention of the record: a feature record whose payload
carries a `question` and no `asked_at` is owed, `owed_questions` lists them
oldest key first, and the notifier hands each to Vigil as news of source
`question` at importance one — held for the next conversation, never
spoken into a room — marking the record asked as Vigil takes it, with at
most `max_held_questions` waiting at once. The notice switch is its own
control, persisted in the store and reachable from Chart and by voice as
`mute_task_notices`; muted means nothing is offered, every task keeps
running, and the view says so without any task reading as paused. A public
lane can neither look nor switch. Lanes without idempotent delivery may
repeat a notice after a send whose receipt was lost; that limit stands.

**Derived work inherits, it never invents.** A task's origin now says which
kind it is. A `HumanOrigin` is a live private owner turn, as before, and is
still the only kind `create` accepts. A `DerivedOrigin` names its mandate,
grant, adapter, event, and source revision, and nothing that claims attendance
or a human lane. The runtime-only `derive_task` admits a child only while its
mandate is active under an active, unexpired grant at the revision it was
activated with, only inside the grant's operations and targets, and only
within the parent's lifetime and window allowances, counted from the persisted
window start so a restart refills nothing. The same event at the same source
revision returns the child it already made and spends nothing. A newer source
revision revises an unfinished child in place while it has no dispatch intent
and its scope stands, voids its open question, and keeps the older revision
as a replay alias; a child whose mutation was sent is reconciled, never
rewritten; a finished child is never reopened, so the revision derives a new
one. A change the grant does not cover is the adapter's inert proposal in its
own records, and only the owner's approval of that exact proposal, carried as
an approval reference on an ordinary create with the proposal's expected
record revision, becomes a task. The controller derives only through a
`Namespace` it registered, journals the derivation, and offers mandate pause,
resume, and revoke and grant revoke through the same owner admission as every
other control; the owner view lists mandates and grants beside tasks.

**One model call has one bounded context.** When a read needs a model to say
what it saw, `brain/extract.py` runs a client of its own: a fresh SDK session
with a fixed system prompt, no tools, no MCP servers, none of the owner's
settings, an empty private working directory, and a JSON schema for its
answer, checked again in runtime code before anyone acts on a field. It sees
the bounded payload and the context the adapter declares, never the world's
chips, the notebook, or the conversation. It holds the Brain's turn lease, so
it never overlaps a conversational turn, and it spends one of the task's
`max_model_calls` before it is made. There is no fallback: a failed extraction
is an abandoned attempt, never a prompt to the conversational client.

The implementation follows the [stage-two plan](design/2026-09-07-task-controls-plan.md)
and its [review](reports/2026-09-07-task-controls-plan-review.md). Task attendance
means a live private owner turn on any lane, not physical room presence. An
owner Discord DM qualifies; public channels, reflection, and Vigil do not.
Diagnostic speaker bypass and an enabled voice gate with no profile confer no
task authority. Admitted local input, the spoke, Chart, and the configured
Discord owner map to one stable `[tasks].owner`; do not change that principal to
switch identities in an existing store.

The SDK gives in-process tools arguments alone. The Brain installs immutable
owner context only after draining all owed results, and revokes it at turn end
or interruption. A turn starting with drain debt has no task authority and
reports controls unavailable. A worker checks the captured authority through
commit; cancellation cannot turn a queued stale callback into a new owner's
operation. Private sessions stay warm. Public Discord audiences use a separate
client with public web tools, no private MCP tools or prompt context, and no
persisted resume; changing public channels starts a fresh public history.

Chart's **Tasks** section uses private, addressed requests and the same controller
as the tools. It shows waits, questions, history, and evidence, including the
last observed head and age, and each feature's own rows (`listing` on
`bind_feature`) with the controls the feature named. Controls submit the rendered revision; a conflict
refreshes the view. Spoken controls read the current revision in their store
transaction. Question answers must match the specific waiting question and an
offered choice. An ambiguous answer leaves it waiting; cancellation makes old
answers stale. No answer can widen scope or refill allowances.

The server advertises task support in its hello without task data; an older hub
hides the section. Reconnect fetches a fresh snapshot, and unavailable, offline,
pending, and error states never claim success early. Task frames never enter the
shared replay ring. Eligibility is admission on the Chart path, not a claimed
role: loopback uses the existing reach/Origin policy; a remotely bound task hub
requires its token even for loopback peers. Spoke-seat task frames are refused;
the interview room has no task route.

Chart mints each message's ID and keeps it in the resend ledger; ack `seq` is
not identity across tabs. The spoke keeps `say_id`, Discord keeps its message
IDs, and local input gets a fresh ID. Ordered ingress IDs survive batching and
reopen. A retry resolves its saved task or a conflict, never a second mandate.
A mixed batch containing consumed and new IDs conflicts as a whole; repeat the
new part alone. The model cannot split one batch into several tasks.

Explicit owner controls need no second confirmation. The shared controller
journals each applied control, including Chart controls, if journaling is on.
The journal is best-effort; task history is durable. Future external mutations
still require the confirmation broker and action journal before dispatch.

The store uses Python's built-in SQLite in a dedicated private directory, with
one worker thread and a process ownership lock. Task state, attempt state,
history, evidence, and notification intent commit together. It uses a DELETE
rollback journal with EXTRA synchronization; database and journal files stay
owner-only. A corrupt, foreign, incomplete, or newer schema is refused without
resetting it. A rolled-back executable cannot silently downgrade the store.
An existing directory must be dedicated to tasks and have mode `0700`; the
store refuses a shared directory rather than changing its permissions.

```toml
[tasks]
enabled = false             # private owner controls; no execution
directory = "~/.ciel/tasks"  # contains tasks.sqlite3 and owner.lock
owner = "local-owner"       # stable principal for this single-owner runtime
max_pending_controls = 32   # concurrent Chart task requests
max_active = 32
max_attempts = 8
max_polls = 288
busy_timeout_s = 5.0
evidence_max_age_s = 300.0
max_record_chars = 16000
runner = false              # give eligible tasks a step at the bottom of the ladder
task_aging_s = 300.0        # after this long waiting, a task passes a nonurgent nudge
step_timeout_s = 60.0       # the longest one adapter read may take
retry_backoff_s = 30.0      # how long an abandoned attempt waits before retrying
max_model_calls = 16        # isolated extraction calls per task, captured at creation
max_feature_records = 4096  # records one adapter namespace may hold per owner
max_grant_children = 256    # finite tasks one standing mandate may derive over its life
max_grant_per_window = 32   # derivations per window, counted from the persisted window start
max_grant_lifetime_s = 2592000.0  # the longest a grant may run from approval to expiry
dispatch_deadline_s = 60.0  # a committed intent unsent past this is abandoned, not sent late
mutation_timeout_s = 60.0   # the longest one send may take before its outcome is unknown
max_reconcile_reads = 3     # reads that cannot tell before the owner is asked
notice_retry_s = 900.0      # after a delivery that reached no lane, offer the notice again
max_held_questions = 3      # feature questions (a candidate on the edge) waiting as held notes at once
extraction_model = ""       # empty: the brain's model
extraction_timeout_s = 90.0
extraction_max_chars = 32000
extraction_max_budget_usd = 0.25
```

`runner` starts execution of read steps only; it is separate from `enabled`
so a store can be inspected and steered without anything moving. The runner
lives where the Brain lives: the hub in split mode, the one process otherwise.
`task_aging_s` is the only way a task passes Vigil, and never an urgent nudge.
`max_model_calls` is captured per task like the other allowances; a spent call
is never refunded, an interrupted one included. `extraction_model` empty means
`[brain].model`; the extraction budget and timeout bound one call, and the
turn lease bounds concurrency to one. The three grant caps bound what any
grant may hold: a grant asks for its own numbers at approval, the smaller
governs, and raising a cap later never widens a grant already approved.

These fields also accept `CIEL_TASKS_*` environment overrides. `max_active`
bounds non-terminal records and the bounded recent task view.
`max_pending_controls` bounds concurrent Chart requests; overflow returns busy.
`owner` is the stable ingress-to-store mapping, never a model argument. `max_attempts` counts attempts that end without a
clean checkpoint or completion: interruptions, failures during an attempt, and
uncertain actions. Successful polling does not spend or refill it. `max_polls`
bounds all claimed execution rounds, including retries and mutations; the
default allows 288 rounds (24 hours at five-minute intervals). A read followed
by a durable wait also counts as a clean checkpoint. Both allowances and the
evidence-age limit are captured at creation; defaults and restarts cannot refill
them. Attempt history retains every round and records clean checkpoints.
A rejected claim reports an exhausted allowance; a later runner must persist
its wait/failure policy explicitly. The size limit bounds each serialized
request, next step, observation batch, and private view. Enabling controls does
not start scheduling; `runner` does.

This is schema version seven. Version-two through -six stores are lifted at
open with every task in place, an open version-four draft discarded rather
than guessed at; version-one stores and newer stores are refused without
migration or reset. Choose a fresh dedicated directory for these
controls; keep an existing store intact. The database, its full rollback-journal
name `tasks.sqlite3-journal`, and `owner.lock` are forbidden to model file and
shell tools, even in a broad workspace.

SQLite waits up to `busy_timeout_s` for a lock on the worker thread. If that
wait expires, `TaskBusy` refuses the operation after a certain rollback; the
same store remains usable when the reader or writer leaves. A failed rollback,
corruption, or other storage failure disables the handle until reopening.

The internal async `TaskStore` API can create/deduplicate an owner request,
inspect/list records, claim an attempt, record dispatch intent and observations,
checkpoint a scoped next step, retarget an observed head, wait, pause, cancel,
fail, or resume. It also stores structured questions/answers, ingress retry
associations, and reserved attempt-to-tool bindings. Bindings have no runtime
writer until a runner exists. Owner identity, origin privacy/attendance, and a
step's read/mutation class come from trusted runtime/adapter context. A stored
scope or dispatch marker grants no tool permission and bypasses no broker.

Every change checks its task revision within the transaction. Old callbacks cannot dispatch
or complete a replaced attempt. Reopening recovers prepared attempts and
interrupted reads to the queue; a possibly dispatched mutation becomes
`waiting/reconciliation`. Pause/resume cannot erase that uncertainty, and
cancellation records it rather than pretending to undo the action. Reconciliation
adapters are not implemented yet: uncertain actions remain blocked.

Only `complete` can enter `done`: all criteria must have matching observations
for the current attempt, with the expected target revision and acceptable age.
The store supports exact string-value criteria; it does not independently read
external systems. The later verifier must supply authentic observations and
recheck changing targets. Empty, stale, unknown, or mismatched evidence cannot
complete a task. Completion and its durable notification intent are atomic;
reading a notice has no delivery or execution side effect.

When a trusted read observes a changed head, `retarget` takes the owner, expected
task revision, target, and new target revision. It requires fresh evidence of
that head from the current read attempt and moves all criteria for that target
together, recording old and new revisions in history. Expected values and scope
stay fixed, and the original request remains available for deduplication. Old
callbacks are fenced by the new task revision; evidence for the old head still
cannot complete any retargeted criterion. The adapter must checkpoint the next
read's arguments if they name a head explicitly.

Cancelling an async caller does not roll back a transaction already running on
the worker. Reread the record instead of assuming failure. Request identity
makes creation retries idempotent, revisions reject stale transition retries,
and shutdown drains submitted work before releasing ownership. The task probe
uses real process kills and a synthetic external effect to exercise both sides
of dispatch and commit, entirely in temporary directories.

## Echo cancellation and barge-in

For echo cancellation that leaves music volume alone, the Mac can use the
**WebRTC backend**. Apple's voice-processing unit lowers other audio when it
hears speech, even between Ciel turns. The WebRTC route instead captures a
nonmuting copy of the default output and removes that echo from the microphone.
Ciel's voice still plays through the ordinary PortAudio speaker. No Apple
voice-processing unit opens, and no speaker gain or system volume is changed.

```toml
[audio]
backend = "webrtc"
webrtc_capture_delay_ms = 40
```

This route requires macOS 14.2 or later, the Xcode command-line tools, and the
pinned `pywebrtc-audio` dependency in the spoke group. Run
`uv sync --locked --all-extras` after updating the checkout. Grant **Microphone**
and **System Audio Recording** permission if macOS asks. For the launchd
spoke, its responsible `Ciel.app` launcher must also declare
`NSAudioCaptureUsageDescription`. Update the sibling infrastructure checkout's
launcher with `python3 scripts/render_launchagents.py` from that checkout and
restart the spoke; its `services/launcher/Info.plist` owns this declaration.
Allow Ciel in System Settings → Privacy & Security → Screen & System Audio
Recording. Permission granted to a terminal or Codex does not grant the
launchd app access. The signed helper is
built atomically at `~/.ciel/bin/cielcapture` with the stable identity
`ai.ciel.capture`; a failed rebuild preserves its last working binary.

A zero-filled PortAudio stream keeps the output device clock running before
the first greeting and while muted; it adds no audible signal. The private
Core Audio aggregate puts the raw mic and stereo reference on one clock, enables tap drift compensation, and resamples the three channels
together. WebRTC processes exact 10 ms blocks at 16 kHz; the rest of Ciel still
receives the same 30 ms mono microphone frames. `webrtc_capture_delay_ms` holds
capture by 40 ms by default so device buffering does not put the echo ahead of
its reference. It accepts multiples of 10 from 0 to 200 and adds that amount
to input latency. Neither queue drains nor Stop resets the adaptive filter.
Mute retains protected capture, but that capture no longer ducks playback.
Automatic gain control and separate noise suppression remain off in this route;
AEC3 includes a microphone high-pass filter and residual echo suppression.

Only system-default devices are supported: remove explicit `input_device` and
`output_device` settings. The output must expose a stereo float stream, and
the paired capture rate must be between 16 and 96 kHz. A route change, stopped
reference, or malformed stream ends protected capture visibly; it never falls
back to raw automatic listening. Launchd restarts the spoke for those failures.
A timing gap, full capture ring, or busy callback lock instead drops the affected
mic and reference together. At the next gap boundary, the resampler and echo
processor reset and queued mic audio is cleared inside the same spoke process.
Echo cancellation remains enabled while the filter learns again; the gap can
interrupt a word, and echo rejection may briefly weaken during readaptation. The reference includes Ciel and other apps on that output,
but not an independently playing TV or network speaker. Reference audio stays
in bounded memory and is never saved or passed to transcription.

On 2026-09-08, the initial controlled Mac-speaker comparison measured a +0.21 dB
playback amplitude change with the tap and 18.5 dB raw-to-clean echo reduction.
It also passed a cold start before any audible playback. After gap recovery
was corrected, an expanded test passed with +0.38 dB playback change and
16.9 dB echo reduction, including recovery from a deliberately paused helper.
An earlier recovery run missed the volume tolerance (−2.88 dB); the report
retains both results. These measurements describe those runs, not every room
or voice.
Synthetic checks preserve voiced speech above the echo and quiet speech without
playback; a quiet voice under loud media can still be attenuated by AEC3. Check
wake recognition, Barn Door, snaps/claps, and simultaneous speech in the room
before enabling barge-in. Barge-in remains off by default. The repository-wide
backend default stays `portaudio`; `webrtc` is an explicit Mac selection.

`uv run --no-sync python scripts/probe_webrtc_audio.py` checks actual AEC3,
framing, native pairing/resampling, builds, paired gap recovery, failure handling, and cleanup without
opening a device. The explicit live comparison is
`uv run --no-sync python reports/2026-09-08-nonducking-audio-live.py --live`;
it briefly stops/restores the installed spoke, plays a quiet signal, pauses the
capture helper to test recovery without a restart, and keeps
measurements in memory. The investigation is
`reports/2026-09-08-audio-ducking.md`.

The Apple backend is **experimental**. A room trial reported lisp-like speech,
and a controlled listen on 2026-09-07 found where it lives: one Piper sentence
played three ways through the MacBook speakers, and only the play scheduled
inside Apple's engine lisped. The engine reported 48 kHz on input, output, and
mixer, and a 300 ms Python stall no longer cuts a word, so neither rate nor
starvation explains it. Apple's voice-processing far end treats what the engine
plays as a phone call. So the canceller hears, and the old speaker speaks: with
`backend = "apple"`, capture comes from Apple's engine and Ciel's voice plays
through the PortAudio speaker on the same default output. The echo reference is
taken at the device rather than at the engine, and in that listen the split
route left no more of Ciel's voice in the processed capture than the engine
route did. Gesture detection and acoustic rejection in a full turn still need a
room check; the experiment script is
`reports/2026-09-07-split-pair-experiment.py`.

Set the following in the **Mac's** `~/.ciel/config.toml`, then restart Ciel.
The hub does not open an audio device.

```toml
[audio]
backend = "apple"
apple_playback = "portaudio"
apple_ducking = "min"
apple_agc = false
```

`apple_playback = "engine"` schedules Ciel's voice inside the engine instead,
with audible receipts and underrun accounting as described below. It is kept
so the two routes can be compared in a room; it is the route that lisped.

The helper requires macOS 14 or later and the Xcode command-line tools
(`xcode-select --install`). It compiles into `~/.ciel/bin/cielaudio`, with no
new Python dependency. Source, embedded metadata, architecture, and compiler
identity determine whether it rebuilds. A lock serializes builds; atomic
replacement preserves a running helper and a failed build preserves the last
working binary. Successful builds remove only the former `cielaudio-<hash>`
files. The path and code-signing identifier `ai.ciel.audio` stay fixed.

The signature is local and ad hoc, not a developer certificate: macOS still
controls permission attribution and may ask again after a code change. Ciel
does not reset permissions or remove other apps' privacy entries. Grant its
microphone request if macOS asks; a denied request reports the Microphone page
in System Settings. `~/.ciel/bin/cielaudio --permission-status` checks the
calling context's authorization without requesting access or opening a mic.
A missing `webrtcvad-wheels` installation reports the spoke dependency and
`uv sync --locked --all-extras` repair command. This first version uses
**system default input and output devices**: remove explicit `input_device` and
`output_device` values and choose devices in macOS. An unsupported configuration,
failed helper, or changed audio route ends the audio session visibly; it never
silently switches to raw capture. Under the installed launchd service, a route
change exits the spoke and `KeepAlive` starts it again after the service's retry
delay, including the normal startup greeting. A foreground process must be
restarted manually. Recovery is a new session, not seamless device switching.

The processed input reaches wake detection, Barn Door, endpointing,
confirmations, and transcription as the existing 16 kHz mono frames. Capture
overflow or lock contention ends the session visibly. Echo-cancelled digital
silence is normal; the missing-frame watchdog still catches a stopped capture
stream. On the default route the speaker is the same PortAudio player the raw
backend uses, so Stop, mute, and a lost output device behave as they always
have, and a speaker failure never closes the microphone.

On the engine route, Ciel's voice, wake acknowledgements, chimes, and alarms go
through the same engine as the microphone. Playback remains active until Apple
reports the last buffer played, and Stop discards scheduled sound. Up to twenty
50 ms buffers keep a short Python stall from cutting a word; playback starts
with the first buffer and does not wait for the window to fill. The speaker's
hardware rate is selected independently of the microphone, and the startup log
reports the negotiated input, output, and mixer rates. Synthesis errors stop
that utterance without closing a healthy microphone.

A refill after the last audible receipt inside an unfinished utterance logs
`Apple playback underrun`, a count, and a lower bound on the empty-queue time.
Normal sentence endings and Stop do not count. These diagnostics distinguish
observed queue starvation from a possible timbre change; they do not promise to
detect every render gap, since device latency and native callback delays can
hide part of it. A logged gap does not disable echo cancellation.

`apple_ducking` chooses Apple's attenuation of other apps during voice activity:
`min`, `mid`, or `max`. Advanced ducking relaxes it between speech; `min` does
not mean zero attenuation. The microphone is open between turns, so nearby
speech can duck other apps even when nobody addresses Ciel. The supported Mac
API has no ducking-off level; turning advanced ducking off would apply constant
ducking instead. Minimum advanced ducking is the least intrusive supported
choice here. `apple_agc` controls automatic microphone gain and
is off by default to avoid adding gain changes to the existing energy gate.
Echo cancellation and noise suppression remain enabled. Apple-mode capture
may change snaps, claps, and speaker-verification scores, so check those in the
room before relying on them. A separate TV or independently playing Spotify
Connect speaker has no audio reference in this engine.

**Barge-in stays off by default.** Set `[audio] barge_in = true` after checking
that Ciel hears you over playback without interrupting itself. Both Apple routes
add speech detection to the sustained ambient-relative loudness check. Know its
limit: on the MacBook tested, the detector called Apple's processed silence
speech in every frame, since the canceller's comfort noise sits well above the
raw microphone's floor, so the loudness check is what actually gates there. With
`backend = "portaudio"`, the original raw microphone and device selection remain
available; there is no echo cancellation, and barge-in is most useful with
headphones. The default backend remains `portaudio` until the room is checked.

`uv run --no-sync python scripts/probe_apple_audio.py` checks the native build,
resampling, framing, the default split pair and the engine route, playback
receipts, interruption, synthesis-error isolation, reported device rates,
capture contention, underrun accounting, compiler-aware builds, and cache
cleanup without opening a microphone. Add `--live` for a separate device smoke
check: it captures processed frames and plays a short quiet tone through each
route, without saving audio.
That checks the device path, not echo reduction, Spotify/browser rejection,
speaker recognition, or simultaneous user speech; those need a room trial.

## Extending it

**A new capability Ciel can invoke** — add a module under
`src/ciel/brain/tools/` with `@tool`-decorated functions and append them to
`TOOLS` in that package's `__init__.py`. Nothing else changes; they're exposed
through an in-process MCP server and auto-allowed.

**An external service** (Google Calendar, Gmail, Slack) — an existing MCP server
is a connection entry in config. The first-party Oura and Spotify clients
live beside the other services and are exposed through Ciel's in-process
tools; their authorization and narrower permission boundaries live here.

**A different speech engine** — implement the `SpeechToText` or `TextToSpeech`
protocol and add it to the factory that names the engines (`build_stt` in
`stt/__init__.py`, `build_tts` in `pipeline.py`) — from there it's a config
value. The `faster-whisper` → `mlx-whisper` swap already happened exactly this
way, and Piper → anything else wouldn't be a rewrite either.

## Personalizing the wake word

To make it answer to "Ciel", train a custom openWakeWord model — free and
offline; their notebook generates synthetic clips with Piper and outputs
`.onnx`. Qualify the candidate with `scripts/probe_wake_model.py <model.onnx>`
(it streams synthesized speech through the real detector and reports trigger
rates for the phrase, near-misses, and negatives), then point `wake.model` at
the file — the ready line takes the phrase from the path's stem.

Train **"hey ciel"**, not bare "Ciel". One-syllable wake words have much higher
false-trigger rates; the two-syllable prefix gives the detector enough to work
with. You'd still address it as Ciel.

## When the room seems deaf

The spoke's log names the microphone it opened (`microphone open: MacBook
Pro Microphone (default)`), because the default shuffles when a phone or a
headset appears and the wrong one is the first thing to rule out. If frames
keep arriving but every sample is exactly zero for five seconds, the log says
so once — a real room always carries hiss — and names the two causes: an
input device with nothing behind it, or a process macOS has not been allowed
the microphone (System Settings › Privacy & Security › Microphone). A stall,
where frames stop arriving altogether, is reported separately and ends the
capture so the process can be restarted.

## When the room hears sentences nobody said

Whisper answers every question with a sentence. Handed a quiet room or a run
of keystrokes after a false wake, it does not say "nothing"; it says "Thank
you." in the voice it uses for real speech, and the sentence is answered as
a turn — and the follow-up window that opens after the answer catches the
next run of keys, so one false wake becomes a conversation with the
keyboard. Its own confidence cannot be asked: with large-v3-turbo the
no-speech probability it reports is 0.000 on pure silence. So before
Whisper is asked at all, Silero VAD — the speech model openWakeWord already
carries — scores the utterance frame by frame, and its most confident 30 ms
frame must reach `speech_threshold` (0.5). One frame is enough, so "Yes."
survives. Measured 2026-09-08 on synthesized speech and synthetic rooms:
silence, room noise, keyboard clatter, and breath never reached 0.23, while
speech reached 0.65 and up at every level down to a whisper. A gated
utterance leaves one line in the spoke's log — `no speech in 1.40s of audio
(speech peaked at 0.08) — not transcribed` — and nothing else happens: no
turn, no follow-up window. The gate wraps the transcriber rather than the
turn, so a confirmation answer is gated the same way: a keyboard cannot say
yes. `speech_threshold = 0` switches it off; without openWakeWord it fails
open and says so at startup. `stt/gate.py`, beside the stock-phrase filter
in `stt/hallucinations.py`.

The false wakes are the other half, and the snap ear is the usual source:
an impulse a fiftieth as loud as a snap at the desk, bright as a mouse
button, and solitary, which no model was trained to name and no keystroke
had owned up to. The keyboard's own word now covers the mouse (below), and
the gate above means a false wake that nobody speaks after costs a log
line and nothing more.

## Snapping and clapping

A finger snap, or two claps, can address Ciel the way the phrase does: the
same acknowledgement, the same listening window, the same log line. Switch
either on under `[wake]` (`snap = true`, `double_clap = "wake"`); the ear sits
*beside* the wake word, never instead of it, and is not built in `always`
mode where there is nothing to wake.

Two claps can also do something rather than wake: `double_clap = "play"`
starts `double_clap_plays` in Spotify — an artist, album, playlist, or track,
as Spotify's "Copy Spotify URI" gives it — the way a switch on the wall
starts music. Nothing is asked first: the pair is itself a deliberate
gesture, mute gates it, and a wrong song costs one tap. The door
(`music.py`) is deliberately narrow — the shell guard still denies
`osascript` to the brain — and only a string of the URI's exact shape is ever
handed to Spotify, as an argument rather than as script. What played is
logged.

With the [Spotify connector](#spotify-from-whichever-device-is-playing)
switched on and authorized **on this Mac** (the spoke's host: `[spotify]
enabled = true`, its `client_id`, and `uv run --no-sync python -m
ciel.spotify authorize` run here), two claps go through the Web API first
and play on whichever device is active — the phone in the kitchen included
— and when nothing is active the API is aimed at this Mac by its Connect
device id, which starts the desktop app's player without touching its
window. If the app is not running it is launched by bundle identifier and
the front is handed straight back to whatever you were looking at — Spotify
ignores a hidden launch, so a flash is the best macOS allows — and an app
already running is never activated. The AppleScript surface answers only
when the API has no login yet. The log says which door opened.

The ear (`audio/gestures.py`, part of Characteristic) needs no model. Every
impulse that clears an onset gate — a twenty-decibel step inside two
milliseconds, well above the room's floor — is measured four ways: its peak,
the width of its body above half that peak, its *tilt* (energy above 2 kHz
against the band below), and how far it has fallen ten milliseconds on. At
the pipeline's 16 kHz a snap and a clap are both one to three milliseconds
wide, so tilt is what tells them apart (a snap is bright, a clap dark), and
loudness is what tells a clap from a keystroke. A single clap never wakes:
on a laptop microphone it is the same sound as a knuckle on the desk, which
is why the gesture is a pair. Reach is the same room and line of sight —
snaps to about five metres in a quiet room, claps further — and it shrinks
with music on or the lid closed.

Every boundary is a field in `[gestures]`, read off one MacBook's lid
microphone on 2026-09-06. Rooms differ, so before trusting it, listen with
the tester, which prints the four numbers and a verdict for every impulse
and names the cue a miss failed on:

```bash
uv run scripts/listen_gestures.py                       # live, through Ciel's own microphone path
uv run scripts/listen_gestures.py --csv /tmp/hands.csv  # ...and log every impulse (owner-only)
uv run scripts/listen_gestures.py --wav /tmp/hands.wav  # replay a 16 kHz mono recording instead
uv run scripts/listen_gestures.py --snap-min-tilt 2.0   # try a boundary before writing it to config
```

Snap ten times, clap ten times, then type and knock on the desk, and move
each boundary to sit midway between the clusters. A boundary that separates
one room's snaps from its claps is not a promise about another room.

**The keyboard's own word.** A mechanical key is a snap by shape and by
loudness, and a lone key in a quiet second does not sound like typing to
any model; a mouse button is quieter, brighter, always solitary, and sounds
like nothing any model was trained to name. But the sound of a key is made
by a key, and macOS reports to any process in the session how long ago a
key went down, up, or changed modifier state, and how long ago a mouse
button did — timestamps only,
never which key or where, and no permission dialog. A snap or a clap inside
`keyboard_veto_ms` (300) of either is rejected as that keystroke or that
click, and the log says which: `sounds like a keystroke 41 ms ago`, `sounds
like a click 12 ms ago`. The keyboard is asked before the model, since its
answer is certain and costs nothing. A snap made while typing is lost, which
every gate below was already going to cost. Off the Mac the veto is inert; 0
switches it off. Shift, Command, Option, Control, and other modifier/status
keys report a separate flag-change event; those presses and releases are
included. Volume and brightness keys are not guaranteed to report these
event types. `audio/keys.py`.

**A snap is solitary.** A keystroke arrives in a run and a snap does not,
so a would-be snap that follows any other gated impulse inside
`snap_quiet_ms` (400) is rejected as typing cadence, whatever its shape.
Claps are not held to it. The cost is a snap made mid-typing, which the
next paragraph was already going to take for a keystroke.

**A second opinion that only says no.** The sounds that fool the rules are
not other hand sounds but the room's ordinary ones: a keystroke has a snap's
shape at a snap's loudness, a plosive has a clap's. Set `veto_model =
"yamnet"` in `[gestures]` and the ear fetches Google's AudioSet classifier
(YAMNet, 16 MB, Apache 2.0, checked against a pinned hash) into
`~/.ciel/models` on first start, then asks it, about each snap or clap the
rules accept, whether the second of audio around it was really *typing*,
*computer keyboard*, *speech*, *conversation*, or *music*. Above
`veto_threshold` the gesture is dropped, and the log says what was heard
instead: `ear: rejected (…) — sounds like typing 0.82`. The model is not the
judge, on purpose: on 2026-09-06 it missed half of a set of real snaps and
called a loud clap a snap, but it named typing and speech every time. One
cost to know: a snap made *while* typing is taken for a keystroke — pause
your hands. `audio/audioset.py` says the rest.

The spoke can narrate the same thing itself: with `log_candidates = true` in
`[gestures]`, every impulse the ear gates becomes a line in the spoke's log —
`ear: rejected (peak 0.03, width 2.1 ms, tilt 0.40, fall 21 dB) — snap: tilt;
clap: peak` — beside the `wake: snap` and `gesture: double clap` lines it
acts on. Candidates that reach the keyboard veto also show the elapsed key
and click times and the veto window, including when the window has expired.
No key identities are read. Follow it live with `tail -F ~/.ciel/log/ciel-spoke.err.log`; switch
it off once the boundaries are set, since typing produces a few lines a minute.

## Development probes

Each isolates one layer, so when something misbehaves you can tell which half to
blame:

```bash
uv run scripts/probe_input.py         # the microphone's silence watch, no mic
uv run --no-sync python scripts/probe_webrtc_audio.py # nonducking AEC3: pairing, speech retention, gap recovery, failures; no mic
uv run --no-sync python scripts/probe_apple_audio.py # Apple audio: native build, framing, audible receipts, failures; no mic
uv run --no-sync python scripts/probe_apple_audio.py --live # processed mic + short quiet tone; no recording
uv run scripts/probe_audio.py vad     # endpointing, synthetic speech, no mic
uv run scripts/probe_audio.py hold    # the Cauchy mid-thought judgement
uv run scripts/probe_audio.py mic     # live capture -> /tmp/ciel_capture.wav
uv run scripts/probe_voice.py speak   # TTS + playback only
uv run scripts/probe_voice.py barge   # interrupt path
uv run scripts/probe_voice.py echo    # mic -> STT -> TTS, no model in the loop
uv run --no-sync python scripts/probe_tasks.py       # records, owner controls, questions, retries, evidence, crash recovery, feature records, the v2→v3 lift
uv run --no-sync python scripts/probe_task_runner.py # a synthetic adapter: one step, restart, fencing, giving up, human input wins, unsupported records
uv run --no-sync python scripts/probe_extraction.py  # the isolated call: bounds, lease, timeout, cancellation, schema check, the client with nothing attached
uv run --no-sync python scripts/probe_task_authority.py # drafts, grants, mandates, derived work, the form's draft and the broker's yes, the v3 and v4 lifts
uv run --no-sync python scripts/probe_task_dispatch.py  # a mutation sent once: intent, authority, reconciliation, the owner's word, process kills; a fake remote
uv run --no-sync python scripts/probe_task_notices.py # what is owed, the notifier and Vigil, receipts, the notice switch, the v6→v7 lift
uv run --no-sync python scripts/probe_email_calendar.py # the inbox as data: normalization, the model held to the message, a preview through the runner, the owner's door
uv run --no-sync python scripts/probe_email_calendar.py --live  # the real accounts on this host: read-only mail, one synthetic calendar event it removes
uv run --no-sync python scripts/probe_task_tools.py  # real SDK dispatcher, turn authority, cancellation, drain debt
uv run --no-sync python scripts/probe_task_wire.py   # two private Chart sockets, controls, conflicts, reconnect, a draft and its private approval
uv run --no-sync python scripts/probe_task_wire.py --live  # synthetic task states in Chart; temporary storage
uv run --no-sync python scripts/probe_turns.py       # lane contract, trusted ingress, public/private clients
uv run --no-sync python scripts/probe_hub_imports.py # Linux imports and temporary task-store lifecycle
uv run --no-sync python scripts/probe_shellguard.py  # confirmation and mutating command options
uv run --no-sync python scripts/probe_reload.py      # the source watcher and its deadline
uv run --no-sync python scripts/probe_shortcuts.py   # global Mac controls, note chord and backslash pair, lifecycle, interruption, mute
uv run --no-sync python scripts/probe_notes.py       # private drafts, receipts, recent history, context, bounded dictation; temporary state only
uv run --no-sync python scripts/probe_note_window.py # native bar: save, discard, Undo, Recent, Context, Dictate, focus and narrow layouts
uv run --no-sync python scripts/probe_speaker.py     # Barn Door: diagnostic policy, private readings, both voice paths
uv run --no-sync python scripts/probe_files.py       # file/search boundaries and session credentials
uv run --no-sync python scripts/probe_tool_rpc.py    # Mac snapshots, undo, and process cancellation
uv run scripts/probe_closure.py       # Closure + Atlas: rotation, turn lock, atomic writes
uv run scripts/probe_atlas.py         # Atlas bindings: ids, aliases, rename, resources, resolution, documents
uv run scripts/probe_readers.py       # the readers: LaTeX by the template's environments, Markdown by headings
uv run scripts/probe_project_watch.py # readings kept under a grant: the Mac's resource watcher and the project adapter
uv run scripts/probe_vigil.py         # Vigil: queue, policy, presence, the Witness guard
uv run scripts/probe_discord.py       # the Discord lane's scripted checks
uv run scripts/probe_discord.py --live  # connect for real and echo your DMs
uv run scripts/probe_discord.py --send hi  # send one DM and exit
uv run scripts/probe_web.py           # the GUI lane: queue, origin gate, mute relay, roster, files with a message
uv run scripts/probe_web.py --live    # serve the real page and echo, no mic or model
uv run scripts/probe_grants.py        # capability granting: catalog + surgery
uv run scripts/probe_stt.py           # the speech gate; transcript filters: hallucinations, loops
uv run --no-sync python scripts/probe_spotify.py  # Spotify: PKCE, refresh, API shapes, gates
uv run scripts/probe_oura.py          # the ring: summaries, the nudge, tokens
uv run scripts/probe_oura.py --authorize  # connect the ring (one browser approval)
uv run scripts/probe_oura.py --live   # read today from the ring for real
uv run scripts/probe_location.py      # places, both sources, move notes
uv run scripts/probe_location.py --live  # read where this Mac is right now
uv run scripts/probe_world.py         # the world table: facts, freshness, the block, the relay
uv run scripts/probe_wake_model.py ~/.ciel/models/hey_ciel.onnx  # qualify a custom wake model
uv run scripts/probe_gestures.py      # the gesture ear: snaps, pairs, and its seat beside the wake word
uv run scripts/listen_gestures.py     # hear snaps and claps for real, with the numbers behind each verdict
```

`enroll_voice.py` (Barn Door's enrollment and tuning) is documented in the
voice-identity section above.

## Cost

Roughly **$0.011 per conversational turn** on Opus 5 in steady state, dominated
by a ~20 k-token cached prompt prefix that's re-read every turn. The first turn
of a session costs more (~$0.043) because it writes that cache; a gap longer
than five minutes pays it again.

Web-search turns are much more expensive — around **$0.21** — because search
results land in the context.

Switching `brain.model` to `claude-sonnet-5` cuts this substantially at some
cost in research judgment. Reasoning effort is a second dial: ordinary turns
run at `effort = "low"` and only the deep-thought agent pays for
`deep_effort = "high"`, which is what keeps the per-turn figure where it is.

> A monthly Agent SDK credit (Pro $20 / Max 5x $100 / Max 20x $200) has been
> announced but isn't live on all accounts yet. Until it is, this usage draws on
> your plan's ordinary limits — the same pool as Claude Code.

## How it fits together

```
mic ──▶ wake word ──▶ VAD capture ──▶ whisper ──▶ Claude ──▶ piper ──▶ speaker
                                                     │
                                          memory + web search
```

One loop reads the microphone and routes each frame by state: to the wake
detector when idle, the endpointer while you're talking, the barge-in check
while Ciel is. Replies stream back sentence by sentence, so Ciel starts speaking
as soon as the first complete thought exists rather than after the whole answer.

| Module | Role |
|---|---|
| `pipeline.py` | The loop and the state machine |
| `config.py` | Every swappable choice, in one place |
| `shortcuts.py` | Global Mac Talk, Stop, Mute, and quick-note controls, with a passive keyboard listener |
| `notes.py` | Private scratchpad, note identity, memory writer, hub receipts, and native window bridge |
| `commands.py` | The no-brain fast path — mechanical requests matched locally |
| `confirm.py` | Proof Obligation — the spoken/texted yes-or-no broker |
| `audio/` | Paired PortAudio, Apple voice processing, or nonducking WebRTC capture, endpointing, playback, wake (the phrase and the gesture ear), speaker identity |
| `stt/`, `tts/` | Engine protocols and implementations (plus the voice effect) |
| `brain/` | Claude client, system prompt, sessions, guards, tools |
| `memory/` | The durable file-backed store (Invariant) |
| `proactive/` | Vigil — watchers, the event queue, the interruption policy |
| `remote/` | The lanes away from the mic — Discord DMs (`discord.py`) and the loopback GUI (`web.py`) |
| `messages/` | iMessage — reading the database, sending through Messages |
| `journal.py` | Inverse — the action journal and snapshots, so actions can be undone |
| `timers.py` | Timers and alarms, ringing or held while muted |
| `music.py` | Spotify on the Mac, through one narrow AppleScript door |
| `spotify.py` | Spotify Web API — browser login, search and Connect playback from the brain's host |
| `projects.py` | Atlas — durable working state per project, bound to where the work lives |
| `readers.py` | What a document says about where the work stands, read as data: LaTeX, Markdown |
| `project_work.py` | The workbench: a bound document read within the project's folders and opened where the owner is; the project adapter keeping readings under a grant |
| `proactive/resources.py` | The resource watcher: a bound document changed on the Mac, reported by path and hash |
| `email_calendar.py` | Events from email: the inbox as a source, a message as bounded data, candidates held to the message, and the preview adapter |
| `tasks.py` | Private task records, atomic owner controls, questions, evidence, recovery, eligibility, abandonment, namespaced feature records, grant drafts, standing grants, mandates, derived work, dispatch intents, and approvals; the store dispatches nothing |
| `task_context.py` | Turn authority captured by in-process tools and fenced through commit |
| `task_controls.py` | Shared private owner controller, offline PR-watch validation, namespace registration, the grant form's drafts and approval, mandate and grant controls, the runtime-only derive path, and control journaling |
| `task_runner.py` | The bounded runner: one step for the oldest eligible task, the adapter contracts for reads and for mutations, guarded dispatch, reconciliation, abandonment, and the human-input interrupt, and the notifier that hands owed notices to Vigil and records what came back |
| `brain/extract.py` | One isolated model call per extraction: a client with nothing attached, the turn lease, and the schema checked twice |
| `transcript.py` | Trace — the record of the path actually taken |
| `reload.py` | Analytic Continuation — watch the source, re-exec, resume |
| `oura.py` | The Oura client — sleep, readiness, activity; read-only |
| `location.py` | Where the user is — Find My's cache, the Mac's Wi-Fi, named places |
| `world.py` | Phase Space — the one table of what is true right now, with ages; opens every turn |
| `ui/` | The status pill and quick-note window (`hud.py` and `notes.py` run as child processes), plus the indicator tee |
