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
  WebRTC VAD for knowing when you've stopped talking, `mlx-whisper` (Metal GPU)
  for transcription with `faster-whisper` as the CPU fallback. All on-device.
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

[audio]
silence_ms = 500             # how long a pause ends your turn (toward 700 if she interrupts)
barge_in = false             # see below
```

```bash
CIEL_WAKE_MODE=hotkey CIEL_BRAIN_MODEL=claude-sonnet-5 uv run ciel
```

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

Off by default; needs a one-time enrollment. With it on, every utterance is
embedded by a local speaker model (CAM++ via sherpa-onnx, ~30 ms on CPU) and
compared against your enrolled profile *before* transcription — a stranger's
sentence never reaches Whisper, the brain, or a follow-up window.

```bash
uv sync --extra voice
uv run python scripts/enroll_voice.py          # record 10 short phrases, varied styles (Ciel stopped)
uv run python scripts/enroll_voice.py --test   # score yourself live
uv run python scripts/enroll_voice.py --add    # append takes to the existing profile
uv run python scripts/enroll_voice.py --adopt  # review rejected clips, adopt the ones that are you
uv run python scripts/enroll_voice.py --calibrate  # re-measure the threshold against impostor voices
uv run python scripts/enroll_voice.py --prune  # inspect takes by name and drop bad ones
```

```toml
[voice]
enabled = true
# threshold: leave unset — enrollment *calibrates* one and stores it with
# the profile (impostor voices vs your takes); a number here overrides it.
```

The threshold is measured, not guessed: every profile change ends with a
calibration pass that scores a set of macOS `say` voices against your takes
exactly the way the gate scores, compares that with your takes'
leave-one-out agreement, and places the bar between the two distributions.
Takes that disagree with the rest of the profile get flagged for pruning —
with best-match scoring, every take is another chance for a lucky impostor
hit.

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

`spotify_search`, `spotify_status` and `spotify_devices` are reads.
`spotify_control` acts on a direct request without asking for a second yes.
Inverse still records each control and schedules a read-back; without the
action journal the control is not offered. Set `confirm_controls = true`
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
  away texts. What's *not* in it is the point — the pinned Discord
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

## Barge-in (off by default)

Talking over Ciel to interrupt it is implemented but **disabled**, because on
laptop speakers it doesn't work reliably. There's no acoustic echo cancellation,
so the microphone hears Ciel's own voice. Measured on this hardware: a silent
room sits around 0.085 RMS and Ciel speaking reaches 0.18 — not enough
separation to reliably tell "the user is interrupting" from "Ciel is talking".

**On headphones there's no echo path and it works properly.** Turn it on:

```toml
[audio]
barge_in = true
```

The detector adapts to your room's noise floor rather than using a fixed
threshold, so it survives both a silent room and a noisy one.

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
any model. But the sound of a key is made by a key, and macOS reports to
any process in the session how long ago a key went down or up — timestamps
only, never which key, and no permission dialog. A snap or a clap inside
`keyboard_veto_ms` (300) of a key event is rejected as that keystroke, and
the log says so: `sounds like a keystroke 41 ms ago`. The keyboard is asked
before the model, since its answer is certain and costs nothing. A snap made
while typing is lost, which every gate below was already going to cost. Off
the Mac the veto is inert; 0 switches it off. `audio/keys.py`.

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
acts on. Follow it live with `tail -F ~/.ciel/log/ciel-spoke.err.log`; switch
it off once the boundaries are set, since typing produces a few lines a minute.

## Development probes

Each isolates one layer, so when something misbehaves you can tell which half to
blame:

```bash
uv run scripts/probe_input.py         # the microphone's silence watch, no mic
uv run scripts/probe_audio.py vad     # endpointing, synthetic speech, no mic
uv run scripts/probe_audio.py hold    # the Cauchy mid-thought judgement
uv run scripts/probe_audio.py mic     # live capture -> /tmp/ciel_capture.wav
uv run scripts/probe_voice.py speak   # TTS + playback only
uv run scripts/probe_voice.py barge   # interrupt path
uv run scripts/probe_voice.py echo    # mic -> STT -> TTS, no model in the loop
uv run --no-sync python scripts/probe_shellguard.py  # confirmation and mutating command options
uv run --no-sync python scripts/probe_files.py       # file/search boundaries and session credentials
uv run --no-sync python scripts/probe_tool_rpc.py    # Mac snapshots, undo, and process cancellation
uv run scripts/probe_closure.py       # Closure + Atlas: rotation, turn lock, atomic writes
uv run scripts/probe_vigil.py         # Vigil: queue, policy, presence, the Witness guard
uv run scripts/probe_discord.py       # the Discord lane's scripted checks
uv run scripts/probe_discord.py --live  # connect for real and echo your DMs
uv run scripts/probe_discord.py --send hi  # send one DM and exit
uv run scripts/probe_web.py           # the GUI lane: queue, origin gate, mute relay, roster
uv run scripts/probe_web.py --live    # serve the real page and echo, no mic or model
uv run scripts/probe_grants.py        # capability granting: catalog + surgery
uv run scripts/probe_stt.py           # transcript filters: hallucinations, loops
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
| `commands.py` | The no-brain fast path — mechanical requests matched locally |
| `confirm.py` | Proof Obligation — the spoken/texted yes-or-no broker |
| `audio/` | Capture, endpointing, playback, wake (the phrase and the gesture ear), speaker identity |
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
| `projects.py` | Atlas — durable working state per project |
| `transcript.py` | Trace — the record of the path actually taken |
| `reload.py` | Analytic Continuation — watch the source, re-exec, resume |
| `oura.py` | The Oura client — sleep, readiness, activity; read-only |
| `location.py` | Where the user is — Find My's cache, the Mac's Wi-Fi, named places |
| `world.py` | Phase Space — the one table of what is true right now, with ages; opens every turn |
| `ui/` | The status pill (`hud.py` is its own process) and the indicator tee that feeds every view |
