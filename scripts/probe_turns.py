"""Probe the unified turn skeleton — all four lanes through _run_turn.

    uv run scripts/probe_turns.py

The lane registry's labels, notes, and delivery rules are load-bearing:
``user``/``you-confirm``/``user-web`` rows are Vigil's presence evidence,
``user-remote`` is deliberately evidence of the opposite, the Chart
renders rows by these exact strings, and the system notes are the
model's only way to know where the user is. This probe drives every lane
through the one shared skeleton with fakes and pins that contract —
golden row sequences, public client/history isolation, admitted task context, prompt composition, reply routing, confirm-context
origins, the conversation flags, and the failure shapes. A regression
here is a lane quietly changing meaning, which is exactly what the
hub split must never do.
"""
from __future__ import annotations


import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import BrainConfig, Config
from ciel.pipeline import Pipeline, _DiscordSink, _TextSink, _VoiceSink
from ciel.remote.discord import RemoteUnavailable
from ciel.turn import (
    _REMOTE_NOTE,
    _REMOTE_PUBLIC_NOTE,
    _WEB_MUTED_NOTE,
    _WEB_NOTE,
    TurnRequest,
    lane_spec,
)

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeBrain:
    """Yields a scripted stream of (kind, sentence); records the prompt."""

    def __init__(self, script, error=None):
        self.script = script
        self.error = error
        self.prompts: list[str] = []
        self.interrupts = 0
        self.last_turn_cost_usd = 0.0
        self.last_reconnect_s = 0.0

    async def ask(self, text, **context):
        self.context = context
        self.prompts.append(text)
        for item in self.script:
            yield item
        if self.error is not None:
            raise self.error

    async def interrupt(self):
        self.interrupts += 1


class FakeConfirm:
    """Records remote-context entries; the broker surface _run_turn uses."""

    def __init__(self):
        self.remotes: list[str] = []
        self.cancels: list[str] = []

    def remote(self, send, *, origin):
        self.remotes.append(origin)
        import contextlib

        return contextlib.nullcontext()

    def cancel(self, reason=""):
        self.cancels.append(reason)


class FakeIndicator:
    def __init__(self):
        self.states: list[str] = []

    def set_state(self, state):
        self.states.append(state)


class FakeWebLink:
    """The record tap's row recorder — the golden transcript."""

    def __init__(self):
        self.rows: list[tuple[str, str]] = []
        self.speak_back: list[bool] = []
        self.sent: list[str] = []

    def note_row(self, speaker, text):
        self.rows.append((speaker, text))

    def note_speak_back(self, on):
        self.speak_back.append(on)

    async def send(self, text):
        self.sent.append(text)


class FakeTTS:
    sample_rate = 16000

    def stream(self, text):
        async def chunks():
            yield text.encode()

        return chunks()


class FakePlayer:
    """Plays by collecting; a scripted budget of completions."""

    def __init__(self, complete_after=None):
        self.played: list[str] = []
        self.complete_after = complete_after  # None: always complete
        self.device_lost = False

    async def play(self, stream):
        text = b""
        async for chunk in stream:
            text += chunk
        self.played.append(text.decode())
        if self.complete_after is not None and len(self.played) > self.complete_after:
            return False
        return True


class FakeRemoteLink:
    def __init__(self, fail_send=False):
        self.sent: list[tuple[str, object]] = []
        self.fail_send = fail_send

    async def send(self, text, channel=None):
        if self.fail_send:
            raise RemoteUnavailable("gateway down")
        self.sent.append((text, channel))

    def typing(self, channel=None):
        import contextlib

        return contextlib.nullcontext()


class FakeEvents:
    """Held notes: one note, consumed on take."""

    def __init__(self, notes=()):
        self._notes = list(notes)
        self.takes = 0

    def take_held(self, now):
        self.takes += 1
        notes, self._notes = self._notes, []
        return notes


class FakeNote:
    def __init__(self, summary):
        self.summary = summary


class FakeTimers:
    def __init__(self):
        self.commits = 0
        self.armed: list[float] = []

    def commit_pending(self):
        self.commits += 1

    def set_relative(self, seconds, label=""):
        self.armed.append(seconds)


class FakeChannel:
    """A Discord guild channel — makes a turn public."""

    def __init__(self, name="general"):
        self.name = name
        self.guild = object()


def make_pipeline(script, *, error=None, events=None, muted=False,
                  remote=None, ack=False):
    cfg = replace(
        Config(),
        brain=replace(
            BrainConfig(),
            ack_phrases=("Hmm.",) if ack else (),
            ack_delay_s=0.01 if ack else 0.6,
            speak_thinking=False,
            thinking_chime=False,
        ),
    )
    p = Pipeline.__new__(Pipeline)
    p._config = cfg
    p._role = "local"
    p._brain = FakeBrain(script, error)
    p._confirm = FakeConfirm()
    p._indicator = FakeIndicator()
    p._web_link = FakeWebLink()
    p._transcript = None
    p._presence = None
    p._events = events
    p._timers = FakeTimers()
    p._tts = FakeTTS()
    p._remote_link = remote
    p._muted = muted
    p._spoke = False
    p._conversed = False
    p._brain_conversed = False
    p._interrupted = False
    p._pending_text = None
    p._continue_listening = False
    p._reload_pending = False
    return p


STREAM = [("thinking", "Consider."), ("reply", "First."), ("reply", "Second.")]


def rows(p):
    return p._web_link.rows


# ── the golden row contract ──────────────────────────────────────────────────


async def probe_labels_and_rows() -> None:
    print("labels and the golden row sequence")

    p = make_pipeline(STREAM)
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    golden = [
        ("user", "hi"),
        ("ciel-thinking", "Consider."),
        ("ciel", "First."),
        ("ciel", "Second."),
    ]
    check("typed: label user, golden row order", rows(p) == golden)
    check("typed: conversation flags set", p._conversed and p._brain_conversed)
    check("typed: timers committed in the finally", p._timers.commits == 1)

    p = make_pipeline(STREAM)
    await p._run_turn(
        TurnRequest(lane="web", text="hi"), _TextSink(p, confirm_send=None)
    )
    check("web: label user-web", rows(p)[0] == ("user-web", "hi"))
    check("web: same golden tail", rows(p)[1:] == golden[1:])

    link = FakeRemoteLink()
    p = make_pipeline(STREAM, remote=link)

    async def send_here(t):
        await link.send(t, None)

    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, send_here)
    )
    check("discord: label user-remote", rows(p)[0] == ("user-remote", "hi"))
    check(
        "discord: reply buffered into one text",
        link.sent == [("First. Second.", None)],
    )

    p = make_pipeline(STREAM)
    player = FakePlayer()
    await p._run_turn(TurnRequest(lane="voice", text="hi"), _VoiceSink(p, player))
    check("voice: label user", rows(p)[0] == ("user", "hi"))
    check("voice: replies played, thinking shown not spoken",
          player.played == ["First.", "Second."])
    check("voice: _spoke opens the follow-up window", p._spoke)


# ── prompt composition ───────────────────────────────────────────────────────


async def probe_public_sessions() -> None:
    import tempfile
    from unittest.mock import patch
    from contextlib import aclosing
    from claude_agent_sdk import ResultMessage
    from ciel.brain.agent import Brain
    from ciel.turn import owner_origin
    instances = []
    class Client:
        def __init__(self, options=None):
            self.options = options
            self.cost = 0.0
            self.closed = False
            instances.append(self)
        async def connect(self): pass
        async def disconnect(self): self.closed = True
        async def query(self, text): self.cost += 0.1
        async def receive_response(self):
            yield ResultMessage(subtype='success', duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id='public-fixture' if self.options else 'private-fixture', total_cost_usd=self.cost)
    async def ask(brain, **kwargs):
        async with aclosing(brain.ask('hello', **kwargs)) as stream:
            async for _ in stream: pass
    with tempfile.TemporaryDirectory() as tmp, patch.object(Path, 'home', return_value=Path(tmp)):
        cfg = Config()
        brain = Brain(cfg, memory_index_provider=lambda: 'private memory')
        private = Client()
        brain._client = private
        with patch('ciel.brain.agent.ClaudeSDKClient', Client):
            await ask(brain, origin=owner_origin(cfg.tasks.owner, 'typed'))
            await ask(brain, public_audience='channel-one')
            public = brain._public_brain._client
            check('a public turn uses a distinct client without private tools or resume', public is not private and public.options.mcp_servers == {} and public.options.resume is None and 'private memory' not in public.options.system_prompt)
            await ask(brain, public_audience='channel-one')
            check('one public audience can reuse its own warm client', brain._public_brain._client is public)
            await ask(brain, public_audience='channel-two')
            check('another public audience cannot inherit the first history', public.closed and brain._public_brain._client is not public)
            check('public replies leave the private session resume record alone', brain._session_id == 'private-fixture' and 'public-fixture' not in cfg.session_file.read_text())
            await ask(brain, origin=owner_origin(cfg.tasks.owner, 'typed'))
            check('returning to private conversation retains client and cost accounting', brain._client is private and abs(brain.last_turn_cost_usd - 0.1) < 0.0001)
            last_public = brain._public_brain._client
            await brain.close()
            check('shutdown closes both public and private clients', last_public.closed and private.closed)
    p = make_pipeline(STREAM)
    origin = owner_origin(p._config.tasks.owner, 'web', 'fixture-message', namespace='chart')
    await p._run_turn(TurnRequest(lane='web', text='hi', origin=origin), _TextSink(p))
    check('the turn skeleton forwards immutable admitted identity to the Brain', p._brain.context['origin'] == origin)


async def probe_prompts() -> None:
    print("\nprompt notes and held notes")

    p = make_pipeline(STREAM)
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check("typed: bare prompt, no note", p._brain.prompts == ["hi"])

    p = make_pipeline(STREAM)
    await p._run_turn(TurnRequest(lane="web", text="hi"), _TextSink(p))
    check("web: the GUI note", p._brain.prompts[0] == _WEB_NOTE + "hi")

    p = make_pipeline(STREAM, muted=True)
    await p._run_turn(TurnRequest(lane="web", text="hi"), _TextSink(p))
    check("web muted: the muted variant", p._brain.prompts[0] == _WEB_MUTED_NOTE + "hi")

    link = FakeRemoteLink()
    p = make_pipeline(STREAM, remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check("discord DM: the remote note", p._brain.prompts[0] == _REMOTE_NOTE + "hi")

    events = FakeEvents([FakeNote("Your 2pm moved.")])
    p = make_pipeline(STREAM, events=events, remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check(
        "discord DM: held notes ride inside the note prefix",
        p._brain.prompts[0].startswith(_REMOTE_NOTE)
        and "Your 2pm moved." in p._brain.prompts[0]
        and p._brain.prompts[0].endswith("hi"),
    )

    events = FakeEvents([FakeNote("Your 2pm moved.")])
    link = FakeRemoteLink()
    p = make_pipeline(STREAM, events=events, remote=link)
    chan = FakeChannel()
    await p._run_turn(
        TurnRequest(lane="discord", text="hi", channel=chan, public=True),
        _DiscordSink(p, link.send),
    )
    check(
        "discord public: discretion note, held notes withheld",
        p._brain.prompts[0] == _REMOTE_PUBLIC_NOTE + "hi" and events.takes == 0,
    )

    events = FakeEvents([FakeNote("Your 2pm moved.")])
    p = make_pipeline(STREAM, events=events)
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check(
        "typed: held notes consumed and prefixed",
        events.takes == 1 and "Your 2pm moved." in p._brain.prompts[0],
    )
    check(
        "held-note delivery leaves an event row",
        ("event", "held notes delivered (1)") in rows(p),
    )


# ── confirm routing ──────────────────────────────────────────────────────────


async def probe_confirm_contexts() -> None:
    print("\nconfirm contexts")

    p = make_pipeline(STREAM)
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check("typed: confirmations stay voiced", p._confirm.remotes == [])

    p = make_pipeline(STREAM)
    await p._run_turn(TurnRequest(lane="web", text="hi"), _TextSink(p))
    check("web: broker enters remote mode as web", p._confirm.remotes == ["web"])

    link = FakeRemoteLink()
    p = make_pipeline(STREAM, remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check("discord: broker enters remote mode as discord",
          p._confirm.remotes == ["discord"])

    p = make_pipeline(STREAM)
    await p._run_turn(
        TurnRequest(lane="voice", text="hi"), _VoiceSink(p, FakePlayer())
    )
    check("voice: confirmations stay voiced", p._confirm.remotes == [])


# ── escalation ───────────────────────────────────────────────────────────────


async def probe_escalation() -> None:
    print("\nescalation")
    script = [("escalation", ""), ("reply", "Answer.")]

    p = make_pipeline(script)
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check(
        "typed: escalation leaves an event row",
        ("event", "deep thought engaged") in rows(p),
    )

    link = FakeRemoteLink()
    p = make_pipeline(script, remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check(
        "discord: interim text covers the silence",
        link.sent[0][0].startswith("Give me a moment")
        and link.sent[1] == ("Answer.", None),
    )

    p = make_pipeline(script)
    player = FakePlayer()
    await p._run_turn(TurnRequest(lane="voice", text="hi"), _VoiceSink(p, player))
    check(
        "voice: announcement spoken before the quiet",
        player.played[0].startswith("Give me a moment")
        and player.played[1] == "Answer.",
    )
    check(
        "voice: the announcement is recorded",
        ("ciel", "Give me a moment. I want to think this through properly.")
        in rows(p),
    )


# ── voice specifics ──────────────────────────────────────────────────────────


async def probe_voice() -> None:
    print("\nthe voice sink")

    script = [("reply", "First."), ("reply", "Second."), ("reply", "Third.")]
    p = make_pipeline(script)
    player = FakePlayer(complete_after=1)  # the second play is barged in on
    await p._run_turn(TurnRequest(lane="voice", text="hi"), _VoiceSink(p, player))
    check(
        "barge-in abandons the rest of the turn",
        player.played == ["First.", "Second."],  # Third. never plays
    )
    check("the interruption is recorded", ("event", "interrupted") in rows(p))
    check("the pending confirmation is cancelled",
          p._confirm.cancels == ["interrupted"])
    check("the brain is interrupted", p._brain.interrupts == 1)

    p = make_pipeline(STREAM, ack=True)
    player = FakePlayer()
    sink = _VoiceSink(p, player)
    await p._run_turn(TurnRequest(lane="voice", text="hi"), sink)
    check("the ack filler never leaks past the turn", sink._ack_task is None)

    p = make_pipeline([("thinking", "Only reasoning.")])
    await p._run_turn(
        TurnRequest(lane="voice", text="hi"), _VoiceSink(p, FakePlayer())
    )
    check(
        "a turn with no audio opens no follow-up window",
        not p._spoke and not p._conversed,
    )

    p = make_pipeline([("thinking", "Only reasoning.")])
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check("...but a typed thinking-only turn still counts as conversation",
          p._conversed and p._brain_conversed)


# ── local commands ───────────────────────────────────────────────────────────


async def probe_local_commands() -> None:
    print("\nthe local-command bypass")

    p = make_pipeline([])
    await p._run_turn(TurnRequest(lane="typed", text="reload"), _TextSink(p))
    check("typed reload: pending, silent", p._reload_pending
          and rows(p) == [("user", "reload")])

    link = FakeRemoteLink()
    p = make_pipeline([], remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="reload"), _DiscordSink(p, link.send)
    )
    check("discord reload: acknowledged over the lane",
          link.sent == [("Reloading.", None)])

    p = make_pipeline([])
    await p._run_turn(TurnRequest(lane="web", text="reload"), _TextSink(p))
    check("web reload: acknowledged as a row",
          ("ciel", "Reloading.") in rows(p))

    p = make_pipeline([])
    player = FakePlayer()
    await p._run_turn(
        TurnRequest(lane="voice", text="ten minute timer"),
        _VoiceSink(p, player),
    )
    check(
        "voice timer command: spoken, no brain",
        len(player.played) == 1
        and player.played[0].endswith("timer — starting now.")
        and p._timers.armed == [600.0]
        and p._brain.prompts == [],
    )
    check("voice local command opens the follow-up window", p._spoke)

    p = make_pipeline([])
    await p._run_turn(TurnRequest(lane="typed", text="never mind"), _TextSink(p))
    check("a dismissal is not a conversation", not p._conversed)


# ── failure shapes ───────────────────────────────────────────────────────────


async def probe_failures() -> None:
    print("\nfailure shapes")

    p = make_pipeline(STREAM, error=RuntimeError("boom"))
    await p._run_turn(TurnRequest(lane="typed", text="hi"), _TextSink(p))
    check("typed failure: event row, loop survives",
          ("event", "typed turn failed") in rows(p))
    check("typed failure: timers still committed", p._timers.commits == 1)

    p = make_pipeline(STREAM, error=RuntimeError("boom"))
    await p._run_turn(TurnRequest(lane="web", text="hi"), _TextSink(p))
    check("web failure: the page shows what happened",
          ("event", "web turn failed — see the log") in rows(p))

    link = FakeRemoteLink()
    p = make_pipeline(STREAM, error=RuntimeError("boom"), remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check("discord failure: apology texted",
          link.sent[-1][0].startswith("Sorry"))

    link = FakeRemoteLink(fail_send=True)
    p = make_pipeline(STREAM, remote=link)
    await p._run_turn(
        TurnRequest(lane="discord", text="hi"), _DiscordSink(p, link.send)
    )
    check("discord undeliverable: logged as an event, not a crash",
          ("event", "remote reply undeliverable") in rows(p))

    p = make_pipeline(STREAM, error=RuntimeError("boom"))
    raised = False
    try:
        await p._run_turn(
            TurnRequest(lane="voice", text="hi"), _VoiceSink(p, FakePlayer())
        )
    except RuntimeError:
        raised = True
    check("voice failure re-raises to the spoken apology's guard", raised)

    # The hub's lane: the apology after a failed turn is best-effort, and
    # a crash *inside* the failure handling is one turn's crash, not the
    # loop's (2026-09-05: a NameError in the apology took the hub down).
    from types import SimpleNamespace

    from ciel.pipeline import _WireSink

    p = make_pipeline(STREAM)

    async def speak_fails(*args, **kwargs):
        raise RuntimeError("the spoke is gone")

    sink = _WireSink(p, SimpleNamespace(speak=speak_fails), "t1")
    await sink.apologize()
    check("wire apology on a dead spoke is swallowed, not raised", True)

    async def crashing_turn() -> None:
        raise NameError("name 'contextlib' is not defined")

    p._turn = asyncio.create_task(crashing_turn())
    await asyncio.sleep(0)
    check("a crashed turn task is a logged event, not the loop's end",
          p._turn_crashed() and ("event", "turn crashed") in rows(p))
    p._turn = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0.01)
    check("...and a clean one is nothing", not p._turn_crashed())


async def probe_stream_death() -> None:
    """The SDK's reader dying mid-turn (2026-09-05: a two-display
    screenshot past its one-megabyte line buffer) must evict the client,
    or every later turn ends at once with nothing said."""
    import contextlib

    from ciel.brain.agent import Brain, StreamDied
    from ciel.config import load_config

    print("\nthe stream dying under a turn")

    class DeadReader:
        def __init__(self) -> None:
            self.disconnected = 0

        async def query(self, text: str) -> None:
            return None

        async def receive_response(self):
            from claude_agent_sdk import StreamEvent

            yield StreamEvent(uuid="u", session_id="s", event={
                "type": "content_block_delta", "delta": {"type": "text_delta", "text": "Looking now. "},
            })
            raise Exception("Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes...")

        async def disconnect(self) -> None:
            self.disconnected += 1

    brain = Brain(load_config())
    fake = DeadReader()
    brain._client = fake
    heard: list[str] = []
    died = None
    try:
        async with contextlib.aclosing(brain.ask("look at my screen")) as stream:
            async for _kind, sentence in stream:
                heard.append(sentence)
    except StreamDied as exc:
        died = exc
    check("what was said before the reader died was heard", heard == ["Looking now."])
    check("the turn fails as a transport death", died is not None and "buffer size" in str(died))
    check("the dead client is evicted, so the next turn reconnects", brain._client is None and fake.disconnected == 1)
    check("the turn lock is released", not brain._turn_lock.locked())
    from ciel.brain import agent as agent_module
    check("the SDK's line buffer is raised past a screenshot",
          agent_module._MAX_MESSAGE_BYTES >= 16 * 1024 * 1024)


# ── the registry itself ──────────────────────────────────────────────────────


def probe_registry() -> None:
    print("\nthe registry, byte for byte")
    check("voice", lane_spec(TurnRequest(lane="voice", text="")).label == "user")
    check("typed", lane_spec(TurnRequest(lane="typed", text="")).label == "user")
    check("web", lane_spec(TurnRequest(lane="web", text="")).label == "user-web")
    spec = lane_spec(TurnRequest(lane="discord", text=""))
    check("discord", spec.label == "user-remote" and spec.origin == "discord")
    spec = lane_spec(
        TurnRequest(lane="discord", text="", channel=FakeChannel("dev"), public=True)
    )
    check(
        "discord public: channel-named origin, notes withheld",
        spec.origin == "discord #dev" and not spec.held_notes,
    )
    check(
        "console tags",
        lane_spec(TurnRequest(lane="voice", text="")).console_tag == "you"
        and lane_spec(TurnRequest(lane="typed", text="")).console_tag
        == "you (typed)",
    )


async def probe_speak_back() -> None:
    print("\nspeak back: typed replies spoken in the room")
    from ciel.pipeline import _SpokenTextSink, _TextSink

    p = make_pipeline(STREAM)
    p._player = FakePlayer()
    check("off by default: the Chart's sink is text alone", isinstance(p._web_sink(), _TextSink))
    await p._run_turn(TurnRequest(lane="web", text="speak back on"), p._web_sink())
    check("the typed command flips the switch and answers in text",
          p._speak_back and p._web_link.speak_back == [True]
          and rows(p)[-1] == ("ciel", "Speaking back on.") and p._player.played == [])
    sink = p._web_sink()
    check("on, with a player: the spoken text sink", isinstance(sink, _SpokenTextSink))
    await p._run_turn(TurnRequest(lane="web", text="hi"), sink)
    check("reply sentences play, thinking stays text",
          p._player.played == ["First.", "Second."]
          and [r for r in rows(p) if r[0] == "ciel"][-2:] == [("ciel", "First."), ("ciel", "Second.")])
    check("no follow-up window is earned by a typed turn", p._spoke is False)

    p._muted = True
    check("muted wins: text alone", isinstance(p._web_sink(), _TextSink))
    p._muted = False
    p._player = None
    check("no player (the frame loop not up): text alone", isinstance(p._web_sink(), _TextSink))

    p._player = FakePlayer(complete_after=1)
    sink = p._web_sink()
    await p._run_turn(TurnRequest(lane="web", text="hi"), sink)
    check("a sentence that will not play makes the rest text only, never cuts the reply",
          p._player.played == ["First.", "Second."]
          and [r for r in rows(p) if r[0] == "ciel"][-2:] == [("ciel", "First."), ("ciel", "Second.")]
          and sink._silent)

    await p._run_turn(TurnRequest(lane="web", text="voice off"), p._web_sink())
    check("...and off again, by another phrasing",
          not p._speak_back and p._web_link.speak_back == [True, False]
          and rows(p)[-1] == ("ciel", "Speaking back off."))
    p._set_speak_back(False)
    check("setting what is already set is not news", p._web_link.speak_back == [True, False])

    # The hub: the spoke's speakers, one hop away — or nobody's.
    class FakeServer(FakeWebLink):
        spoke_connected = True

        def __init__(self):
            super().__init__()
            self.frames: list[dict] = []
            self.spoken: list[tuple[str, int, str]] = []

        def send_spoke(self, frame):
            self.frames.append(frame)
            return True

        async def speak(self, turn_id, n, kind, text, timeout):
            self.spoken.append((turn_id, n, text))
            return True

    p = make_pipeline(STREAM)
    p._role = "hub"
    server = FakeServer()
    p._web_link = server
    p._speak_back = True
    sink = _SpokenTextSink(p, confirm_send=server.send, server=server)
    await p._run_turn(TurnRequest(lane="web", text="hi"), sink)
    check("on the hub the sentences go down the wire as a web-lane turn",
          [f["type"] for f in server.frames] == ["turn.begin", "turn.end"]
          and server.frames[0]["lane"] == "web"
          and [(n, t) for _, n, t in server.spoken] == [(1, "First."), (2, "Second.")])
    check("...bracketed once", sum(f["type"] == "turn.end" for f in server.frames) == 1)


async def main() -> int:
    probe_registry()
    await probe_labels_and_rows()
    await probe_prompts()
    await probe_public_sessions()
    await probe_confirm_contexts()
    await probe_escalation()
    await probe_voice()
    await probe_local_commands()
    await probe_failures()
    await probe_stream_death()
    await probe_speak_back()
    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
