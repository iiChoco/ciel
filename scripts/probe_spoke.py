"""Accepted speech carries owner-input policy; diagnostic bypass does not.

Probe the spoke — the room's half, with a fake hub on the other end.

    uv run scripts/probe_spoke.py

The spoke is the pipeline's audio state machine with the thinking
taken out: an utterance becomes a ``say``, sentences come back as
``turn.sentence`` frames and are played and receipted, questions
arrive as ``confirm.request`` and are spoken and listened for. This
drives the frontend with fakes for every component that touches
hardware (mic, player, STT, TTS, wake, endpointer, speaker gate, HUD)
and a fake hub link that records what went up: a full voice turn with
its receipts and the follow-up window after; the speaker gate, the
Cauchy hold, and a dismissal before the merge; barge-in reported as
an unfinished receipt; a hub that is down (said once, then a chime);
a confirmation spoken and answered, and one that times out; a
delivery rung and receipted; mute from the hub and from the sentinel, and
the microphone closed by it and opened by the unmute (a quick flip ends
shut; a microphone that will not reopen is tried again);
the state machine's reports; the ear that will not open tried again with
a doubling wait, left for a reload, and the old one-attempt door on a zero
ceiling; and a model load past its deadline treated as a warm-up failure.
"""
from __future__ import annotations


import asyncio
import contextlib
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.config import AudioConfig, BrainConfig, Config
from ciel.schedule import State
from ciel.spoke.frontend import Spoke

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeLink:
    def __init__(self, connected=True):
        self.connected = connected
        self.frames: list[dict] = []
        self.says: list[tuple[str, str]] = []

    def send(self, frame):
        if not self.connected:
            return False
        self.frames.append(frame)
        return True

    def say(self, text, lane="voice", *, owner_input=False):
        self.owner_input = owner_input
        self.says.append((text, lane))
        return self.connected

    def of(self, kind):
        return [f for f in self.frames if f["type"] == kind]


class FakeSTT:
    def __init__(self, text="hello there"):
        self.text = text

    async def transcribe(self, pcm):
        return self.text


class FakeTTS:
    sample_rate = 16000

    def stream(self, text):
        async def chunks():
            yield b"TTS:" + text.encode()

        return chunks()


class FakePlayer:
    def __init__(self, complete_after=None):
        self.played: list[str] = []
        self.complete_after = complete_after
        self.device_lost = False
        self.stops = 0
        self.is_playing = False

    async def play(self, stream):
        text = b""
        async for chunk in stream:
            text += chunk
        label = text[4:].decode() if text.startswith(b"TTS:") else "<pcm>"
        self.played.append(label)
        if self.complete_after is not None and len(self.played) > self.complete_after:
            return False
        return True

    def stop(self):
        self.stops += 1


class FakeMic:
    def __init__(self):
        self.drains = 0
        self.paused = False
        self.moves: list[str] = []
        self.refuse = False

    def drain(self):
        self.drains += 1

    async def pause(self):
        await asyncio.sleep(0)  # closing a device waits; the switch can move meanwhile
        self.paused = True
        self.moves.append("closed")

    async def resume(self):
        await asyncio.sleep(0)
        if self.refuse:
            raise RuntimeError("no such device")
        self.paused = False
        self.moves.append("opened")


class FakeGate:
    def __init__(self, recognized=True, similarity=0.9):
        self.recognized = recognized
        self.similarity = similarity

    async def check(self, utterance):
        return self.recognized, self.similarity


class FakeEndpointer:
    def __init__(self, utterance_at=None):
        self.speaking = False
        self.pushes = 0
        self.resets = 0
        self.utterance_at = utterance_at

    def push(self, frame):
        self.pushes += 1
        if self.utterance_at is not None and self.pushes == self.utterance_at:
            return np.zeros(16000, dtype=np.float32)
        return None

    def reset(self):
        self.resets += 1


class FakeWake:
    def reset(self):
        pass


class FakeIndicator:
    def __init__(self):
        self.states: list[str] = []

    def set_state(self, state):
        self.states.append(state)


def make_spoke(*, stt="hello there.", connected=True, complete_after=None,
               recognized=True, followup_ms=1500, ack=False):
    tmp = Path(tempfile.mkdtemp())
    cfg = replace(
        Config(),
        state_dir=tmp,
        audio=replace(AudioConfig(), followup_ms=followup_ms, filler_extend_ms=1200),
        brain=replace(
            BrainConfig(),
            ack_phrases=("Hmm.",) if ack else (),
            ack_delay_s=0.01 if ack else 0.6,
            speak_thinking=False,
            thinking_chime=False,
        ),
    )
    s = Spoke.__new__(Spoke)
    s._config = cfg
    s._stt = FakeSTT(stt)
    s._tts = FakeTTS()
    s._wake = FakeWake()
    s._endpointer = FakeEndpointer()
    s._confirm_endpointer = FakeEndpointer()
    s._speaker = FakeGate(recognized)
    s._indicator = FakeIndicator()
    s._link = FakeLink(connected)
    s._mute_sentinel = tmp / "mute"
    s._muted = False
    s._next_mute_check = 0.0
    s._player = FakePlayer(complete_after)
    s._mic = FakeMic()
    s._ear_task = None
    s._next_ear_try = 0.0
    s._state = State.BUSY
    s._interrupted = False
    s._barge_run = 0
    s._noise_floor = None
    s._followup_until = None
    s._spoke = False
    s._pending_text = None
    s._continue_listening = False
    s._prelude = None
    s._awaiting_hub = False
    s._hub_turn = None
    s._turn_deadline = 0.0
    s._playing = None
    s._prev_kind = None
    s._ack_task = None
    s._ack_speaking = False
    s._confirm = None
    s._delivering = False
    s._hub_lost_said = False
    s._reported = None
    s._watcher = None
    s.reload_requested = False

    class _Outbox:
        def __init__(self):
            self.acked_ids = []
            self.resends = 0

        def acked(self, publish_id):
            self.acked_ids.append(publish_id)

        def resend(self):
            self.resends += 1
            return 0

    class _Beat:
        def __init__(self):
            self.beats = 0

        def beat(self, *, force=False):
            self.beats += 1
            return True

    class _Exec:
        def __init__(self):
            self.frames = []

        def handle(self, frame):
            self.frames.append(frame)

    s._publish = _Outbox()
    s._heartbeat = _Beat()
    s._executor = _Exec()
    s._watchers = []
    from ciel.spoke.timers import TimerMirror
    s._timers = TimerMirror(tmp / "spoke-timers.json", grace_s=20.0)
    s._ringing = False
    return s


UTT = np.zeros(16000, dtype=np.float32)


async def settle():
    for _ in range(3):
        await asyncio.sleep(0.01)


# ── the probes ───────────────────────────────────────────────────────────────


async def probe_voice_turn() -> None:
    print("a voice turn, end to end")
    s = make_spoke()
    await s._handle_utterance(UTT)
    check("the utterance went up as a voice say", s._link.says == [("hello there.", "voice")])
    check("the spoke waits for the hub to begin", s._awaiting_hub and s._state is State.BUSY)
    s._on_frame({"type": "turn.begin", "turn_id": "t1", "lane": "voice"})
    check("turn.begin claims the turn", s._hub_turn == "t1" and not s._awaiting_hub)
    s._on_frame({"type": "turn.sentence", "turn_id": "t1", "n": 1, "kind": "reply", "text": "First."})
    await settle()
    s._on_frame({"type": "turn.sentence", "turn_id": "t1", "n": 2, "kind": "reply", "text": "Second."})
    await settle()
    check("sentences play in order", s._player.played == ["First.", "Second."])
    check(
        "each is receipted as completed",
        [(f["n"], f["completed"]) for f in s._link.of("turn.played")] == [(1, True), (2, True)],
    )
    s._on_frame({"type": "turn.sentence", "turn_id": "other", "n": 1, "kind": "reply", "text": "Stray."})
    await settle()
    check("a sentence for another turn is ignored", "Stray." not in s._player.played)
    s._on_frame({"type": "turn.end", "turn_id": "t1", "status": "done"})
    check("turn.end releases the turn", s._hub_turn is None)
    s._finish_turn(s._mic)
    check(
        "after a spoken reply the follow-up window opens, the mic drained",
        s._state is State.LISTENING and s._mic.drains == 1 and s._followup_until is not None,
    )
    check("the hub's idle is ignored while a window is open",
          (s._on_frame({"type": "state", "state": "idle"}) or s._indicator.states[-1]) == "listening")
    s._on_frame({"type": "state", "state": "thinking"})
    check("other hub states drive the HUD", s._indicator.states[-1] == "thinking")


async def probe_prelude() -> None:
    s = make_spoke()
    await s._handle_utterance(UTT)
    check('ordinary accepted speech carries the owner-input policy to the hub', s._link.owner_input)
    s = make_spoke()
    s._config = replace(s._config, voice=replace(s._config.voice, diagnostic=True))
    await s._handle_utterance(UTT)
    check('diagnostic speaker bypass never claims task owner authority', not s._link.owner_input)
    s = make_spoke()
    s._config = replace(s._config, voice=replace(s._config.voice, enabled=True))
    s._speaker = None
    await s._handle_utterance(UTT)
    check('an enabled speaker gate missing its profile cannot grant task authority', not s._link.owner_input)

    print("\nthe prelude: gate, hold, dismissal")
    s = make_spoke(recognized=False)
    await s._handle_utterance(UTT)
    check("a stranger's sentence never leaves the machine", s._link.says == [])

    s = make_spoke(stt="check my")
    await s._handle_utterance(UTT)
    check(
        "a trailing-off transcript is held, not sent",
        s._link.says == [] and s._pending_text == "check my" and s._continue_listening,
    )
    s._stt = FakeSTT("calendar for today.")
    await s._handle_utterance(UTT)
    check("the continuation merges onto the held thought and goes up",
          s._link.says == [("check my calendar for today.", "voice")] and s._pending_text is None)

    s = make_spoke(stt="never mind")
    s._pending_text = "set a timer for"
    await s._handle_utterance(UTT)
    check(
        "a dismissal clears the held thought and goes up alone",
        s._pending_text is None and s._link.says == [("never mind", "voice")],
    )

    s = make_spoke(stt="")
    await s._handle_utterance(UTT)
    check("nothing intelligible sends nothing", s._link.says == [])


async def probe_barge_in() -> None:
    print("\nbarge-in as an unfinished receipt")
    s = make_spoke(complete_after=1)
    s._on_frame({"type": "turn.begin", "turn_id": "t2", "lane": "voice"})
    s._on_frame({"type": "turn.sentence", "turn_id": "t2", "n": 1, "kind": "reply", "text": "One."})
    await settle()
    s._on_frame({"type": "turn.sentence", "turn_id": "t2", "n": 2, "kind": "reply", "text": "Two."})
    await settle()
    check(
        "the stopped sentence is receipted as not completed, and the turn marked interrupted",
        [(f["n"], f["completed"]) for f in s._link.of("turn.played")] == [(1, True), (2, False)]
        and s._interrupted,
    )


async def probe_hub_down() -> None:
    print("\nthe hub is down")
    s = make_spoke(connected=False)
    await s._handle_utterance(UTT)
    await settle()
    check(
        "the say is held in the ledger and the loss is said once",
        s._link.says == [("hello there.", "voice")]
        and s._player.played == ["I can't reach the hub right now."]
        and not s._awaiting_hub,
    )
    s._stt = FakeSTT("still there?")
    await s._handle_utterance(UTT)
    await settle()
    check("the second time is a chime", s._player.played[-1] == "<pcm>" and len(s._player.played) == 2)
    s._on_connect({"type": "hello", "muted": False})
    check("reconnecting resets the notice, resends the outbox, beats at once",
          not s._hub_lost_said and s._publish.resends == 1 and s._heartbeat.beats == 1)
    s._on_frame({"type": "tool.request", "rpc_id": "r1", "tool": "screen.capture", "args": {}})
    s._on_frame({"type": "event.ack", "publish_id": "p-mac-1"})
    check("tool requests go to the executor, event acks to the outbox",
          s._executor.frames[0]["rpc_id"] == "r1" and s._publish.acked_ids == ["p-mac-1"])

    s = make_spoke()
    s._on_frame({"type": "turn.begin", "turn_id": "t3", "lane": "voice"})
    s._on_disconnect()
    check("a drop mid-turn releases the turn", s._hub_turn is None and not s._awaiting_hub)


async def probe_confirm() -> None:
    print("\na confirmation, spoken and answered")
    s = make_spoke(stt="yes")
    s._on_frame({"type": "turn.begin", "turn_id": "t4", "lane": "voice"})
    s._on_frame({"type": "confirm.request", "confirm_id": "c1", "text": "Run: git push — okay?",
                 "deadline_wall": 0.0, "listen": True})
    await settle()
    check(
        "the question is spoken, the mic drained, the answer window open",
        s._player.played == ["Run: git push — okay?"] and s._mic.drains == 1
        and s._confirm is not None and s._confirm[0] == "c1",
    )
    s._confirm_endpointer = FakeEndpointer(utterance_at=2)
    s._feed_confirm(b"\x00" * 640)
    check("frames go to the answer endpointer", s._confirm is not None)
    s._feed_confirm(b"\x00" * 640)
    await settle()
    check(
        "the utterance is transcribed and sent as the answer",
        s._confirm is None and s._link.of("confirm.answer") == [
            {"type": "confirm.answer", "confirm_id": "c1", "text": "yes"}
        ],
    )
    s._on_frame({"type": "confirm.request", "confirm_id": "c2", "text": "Okay, skipping it.",
                 "deadline_wall": 0.0, "listen": False})
    await settle()
    check("a closing line is spoken without opening the window",
          s._player.played[-1] == "Okay, skipping it." and s._confirm is None)

    s = make_spoke()
    s._on_frame({"type": "confirm.request", "confirm_id": "c3", "text": "Sure?",
                 "deadline_wall": 0.0, "listen": True})
    await settle()
    s._confirm = ("c3", 0.0)  # the deadline already passed
    s._feed_confirm(b"\x00" * 640)
    check(
        "the window timing out sends an empty answer",
        s._confirm is None and s._link.of("confirm.answer")[-1]["text"] == "",
    )
    s._on_frame({"type": "confirm.request", "confirm_id": "c4", "text": "Sure?",
                 "deadline_wall": 0.0, "listen": True})
    await settle()
    s._on_frame({"type": "confirm.cancel", "confirm_id": "c4"})
    check("confirm.cancel closes the window", s._confirm is None)


async def probe_delivery() -> None:
    print("\na delivery at idle")
    s = make_spoke()
    s._state = State.WAITING
    s._on_frame({"type": "deliver.speak", "event_id": "timer:1", "text": "Your ten minute timer is done.",
                 "meta": {"ring": True, "kind": "timer"}})
    await settle()
    check(
        "ring, then the line, then the receipt, mic drained",
        s._player.played == ["<pcm>", "Your ten minute timer is done."]
        and s._link.of("deliver.result") == [{"type": "deliver.result", "event_id": "timer:1", "ok": True}]
        and s._mic.drains == 1 and not s._delivering,
    )
    check("the HUD returns to idle", s._indicator.states[-1] == "idle")

    s = make_spoke(complete_after=1)
    s._state = State.WAITING
    s._on_frame({"type": "deliver.speak", "event_id": "ev", "text": "Nudge.", "meta": {}})
    await settle()
    check("a nudge barged in on still counts as delivered",
          s._link.of("deliver.result")[-1]["ok"] is True)
    s = make_spoke(complete_after=0)
    s._player.device_lost = True
    s._state = State.WAITING
    s._on_frame({"type": "deliver.speak", "event_id": "ev", "text": "Nudge.", "meta": {}})
    await settle()
    check("a dead device does not", s._link.of("deliver.result")[-1]["ok"] is False)


async def probe_mute_and_state() -> None:
    print("\nmute and the state reports")
    s = make_spoke()
    s._on_frame({"type": "muted", "muted": True})
    check(
        "the hub's mute touches the sentinel, stops the player, sends no echo",
        s._muted and s._mute_sentinel.exists() and s._player.stops == 1
        and s._link.of("mute") == [],
    )
    s._set_muted(False)
    check(
        "a local flip clears the sentinel and tells the hub",
        not s._mute_sentinel.exists() and s._link.of("mute") == [{"type": "mute", "muted": False}],
    )
    s._on_connect({"type": "hello", "muted": True})
    check("the hub's hello carries the switch", s._muted)

    s = make_spoke()
    s._set_muted(True)
    await s._ear_task
    check("muting closes the microphone, not just the wake word", s._mic.paused and s._mic.moves == ["closed"])
    s._set_muted(False)
    await s._ear_task
    check("unmuting opens it again", not s._mic.paused and s._mic.moves == ["closed", "opened"])
    s._set_muted(True)
    s._set_muted(False)
    s._set_muted(True)
    await s._ear_task
    check("a quick mute-unmute-mute ends shut", s._mic.paused and s._muted)
    s._mic.refuse = True
    s._set_muted(False)
    await s._ear_task
    check(
        "an unmute whose microphone will not open leaves the ear shut and books another try",
        s._mic.paused and not s._muted and s._next_ear_try > time.time(),
    )
    s._mic.refuse = False
    s._sync_ear()  # what the loop's tick does once the wait is over
    await s._ear_task
    check("...and the next try opens it", not s._mic.paused)

    s = make_spoke()
    s._state = State.WAITING
    s._report_voice_state()
    s._report_voice_state()
    s._enter_listening()
    s._report_voice_state()
    s._endpointer.speaking = True
    s._report_voice_state()
    s._report_voice_state()
    check(
        "voice.state goes up once per transition",
        [(f["listening"], f["speaking"]) for f in s._link.of("voice.state")]
        == [(False, False), (True, False), (True, True)],
    )
    s = make_spoke()
    s._state = State.WAITING
    s._report_voice_state()
    s._wake_source = "snap"  # what the frame loop records when the ear fires
    s._enter_listening()
    s._report_voice_state()
    s._enter_followup()
    s._report_voice_state()
    s._enter_waiting()
    s._report_voice_state()
    check(
        "a window opened by a snap says so; a follow-up window says nothing; idle says nothing",
        [(f["listening"], f.get("source")) for f in s._link.of("voice.state")]
        == [(False, None), (True, "snap"), (True, None), (False, None)],
    )


async def probe_offline() -> None:
    print("\nthe hub away: the mirror and the grammar")
    s = make_spoke(stt="ten minute timer", connected=False)
    s._state = State.BUSY
    await s._handle_utterance(UTT)
    await settle()
    check(
        "an offline 'ten minute timer' arms a local timer and is answered here",
        s._link.says == [] and len(s._timers.active(0.0)) == 1
        and s._player.played[-1].startswith("10 minute timer — starting now"),
    )
    s._stt = FakeSTT("what timers are running")
    await s._handle_utterance(UTT)
    await settle()
    check("the offline listing answers from the mirror",
          s._player.played[-1].startswith("Your 10 minute timer with"))
    s._stt = FakeSTT("cancel the timer")
    await s._handle_utterance(UTT)
    await settle()
    check("the offline cancel clears it",
          s._player.played[-1] == "Cancelled the 10 minute timer." and s._timers.active(0.0) == [])
    s._stt = FakeSTT("what time is it?")
    await s._handle_utterance(UTT)
    await settle()
    check("anything else still goes to the ledger and the loss is said",
          s._link.says == [("what time is it?", "voice")]
          and "I can't reach the hub" in s._player.played[-1])

    s = make_spoke()
    s._on_frame({"type": "timers.sync", "timers": [
        {"id": "t1", "kind": "timer", "due_at": 5.0, "label": "", "duration_s": 60.0, "pending": False}
    ]})
    check("timers.sync fills the mirror", [x.id for x in s._timers.active(0.0)] == ["t1"])
    s._state = State.WAITING
    due = s._timers.due(100.0, hub_connected=False)
    await s._ring_locally(due)
    check(
        "the mirror rings a due timer itself: ring, announcement, rung, mic drained",
        s._player.played == ["<pcm>", "Your 1 minute timer is done."]
        and s._timers.rung_already("t1") and s._mic.drains == 1,
    )
    s._on_frame({"type": "deliver.speak", "event_id": "timer:t1", "text": "Your 1 minute timer is done.", "meta": {"ring": True}})
    await settle()
    check(
        "the hub's late delivery of the same timer is receipted silently",
        len(s._player.played) == 2
        and s._link.of("deliver.result") == [{"type": "deliver.result", "event_id": "timer:t1", "ok": True}],
    )

    s = make_spoke(connected=False)
    s._state = State.WAITING
    s._set_muted(True)
    s._timers.set_local(1, 0.0)
    due = s._timers.due(100.0, hub_connected=False)
    await s._ring_locally(due)
    check(
        "a due timer in a muted room stays silent and stays due",
        s._player.played == [] and not s._ringing
        and [t.id for t in s._timers.due(100.0, hub_connected=False)] == ["local-1"],
    )
    s._set_muted(False)
    await s._ring_locally(s._timers.due(100.0, hub_connected=False))
    check(
        "...and rings on the unmute, late, once",
        s._player.played == ["<pcm>", "Your 1 second timer is done."]
        and s._timers.due(100.0, hub_connected=False) == [],
    )


async def probe_ack_filler() -> None:
    print("\nthe ack filler")
    s = make_spoke(ack=True)
    s._on_frame({"type": "turn.begin", "turn_id": "t5", "lane": "voice"})
    await asyncio.sleep(0.05)
    s._on_frame({"type": "turn.sentence", "turn_id": "t5", "n": 1, "kind": "reply", "text": "Late."})
    await settle()
    check("a slow first sentence is preceded by the filler",
          s._player.played == ["Hmm.", "Late."])
    s = make_spoke(ack=True)
    s._on_frame({"type": "turn.begin", "turn_id": "t6", "lane": "voice"})
    s._on_frame({"type": "turn.sentence", "turn_id": "t6", "n": 1, "kind": "reply", "text": "Quick."})
    await settle()
    check("a quick first sentence cancels it unspoken", s._player.played == ["Quick."])


async def probe_speak_back_turn() -> None:
    print("\na web-lane turn (speak back)")
    s = make_spoke()
    before = s._state
    s._on_frame({"type": "turn.begin", "turn_id": "w1", "lane": "web"})
    check("a web turn is claimed without changing state or arming the filler",
          s._hub_turn == "w1" and s._hub_lane == "web" and s._state is before
          and s._ack_task is None)
    s._on_frame({"type": "turn.sentence", "turn_id": "w1", "n": 1, "kind": "reply", "text": "Typed."})
    await settle()
    check("the sentence plays and is receipted",
          s._player.played == ["Typed."]
          and [(f["n"], f["completed"]) for f in s._link.of("turn.played")] == [(1, True)])
    check("...with the room held for it and released after",
          not s._delivering and s._indicator.states[-1] == "idle")
    check("no follow-up window is earned", s._spoke is False)
    s._on_frame({"type": "turn.end", "turn_id": "w1", "status": "done"})
    check("turn.end releases the turn and its lane", s._hub_turn is None and s._hub_lane is None)
    s._on_frame({"type": "turn.begin", "turn_id": "x", "lane": "typed"})
    check("other lanes are still ignored", s._hub_turn is None)


async def probe_forced_reload() -> None:
    print("\nthe reload has a deadline")
    s = make_spoke()
    s._state = State.BUSY
    s._reload_forced = False
    s._run_task = None
    s._reload_stuck()
    check("with no run task on record nothing is forced", not s.reload_requested and not s._reload_forced)

    async def loop():
        await asyncio.sleep(10)

    task = asyncio.create_task(loop())
    s._run_task = task
    await asyncio.sleep(0)
    s._reload_stuck()
    check("a busy room past the grace is cancelled and marked as a forced reload",
          s.reload_requested and s._reload_forced and task.cancelling())
    try:
        await task
    except asyncio.CancelledError:
        pass
    s = make_spoke()
    s.reload_requested = True
    task = asyncio.create_task(loop())
    s._run_task = task
    await asyncio.sleep(0)
    s._reload_stuck()
    check("a loop already leaving for its reload is left alone", not task.cancelling() and not s._reload_forced)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def probe_ear_retry() -> None:
    print("\nthe ear that will not open is tried again")

    class Mic:
        def __init__(self, fails):
            self.fails = fails
            self.closed = False

        async def __aenter__(self):
            attempts.append(self.fails)
            if self.fails:
                raise RuntimeError("no paired audio")
            return self

        async def __aexit__(self, *exc):
            self.closed = True

    class Player:
        def __init__(self):
            self.entered = False
            self.closed = False

        async def __aenter__(self):
            self.entered = True
            return self

        async def __aexit__(self, *exc):
            self.closed = True

    attempts: list[bool] = []
    built: list[tuple] = []
    waits: list[float] = []
    real_sleep = asyncio.sleep

    def builder(plan):
        it = iter(plan)

        def build(config, rate, muted=None):
            pair = (Mic(next(it)), Player())
            built.append(pair)
            return pair

        return build

    async def sleep(delay):
        waits.append(delay)
        await real_sleep(0)

    def spoke(ceiling):
        s = make_spoke()
        s._config = replace(s._config, audio=replace(s._config.audio, open_retry_max_s=ceiling))
        s._player = None
        return s

    s = spoke(3.0)
    with patch("ciel.spoke.frontend.build_audio", builder([True, True, True, False])), patch("ciel.spoke.frontend.asyncio.sleep", sleep):
        async with contextlib.AsyncExitStack() as room:
            opened = await s._open_audio(room)
            check("a pair that will not open is built afresh and tried again, the wait doubling to its ceiling",
                  opened is not None and attempts == [True, True, True, False] and waits == [1.0, 2.0, 3.0])
            mic, player = opened
            check("the pair that opened is the room's; the ones that did not were never played through",
                  s._player is player and player.entered and mic is built[-1][0] and not any(p.entered for _, p in built[:-1]))
    check("the pair closes with the run", mic.closed and player.closed)

    attempts.clear(); built.clear(); waits.clear()
    s = spoke(3.0)

    async def sleep_then_ask(delay):
        waits.append(delay)
        s.reload_requested = True
        await real_sleep(0)

    with patch("ciel.spoke.frontend.build_audio", builder([True, True])), patch("ciel.spoke.frontend.asyncio.sleep", sleep_then_ask):
        async with contextlib.AsyncExitStack() as room:
            opened = await s._open_audio(room)
    check("a reload asked for during the wait ends the attempts, and the run leaves for it",
          opened is None and s.reload_requested and attempts == [True] and s._player is None)

    attempts.clear(); waits.clear()
    s = spoke(3.0)

    class Changed:
        changed = Path("frontend.py")

    s._watcher = Changed()
    with patch("ciel.spoke.frontend.build_audio", builder([True, True])), patch("ciel.spoke.frontend.asyncio.sleep", sleep):
        async with contextlib.AsyncExitStack() as room:
            opened = await s._open_audio(room)
    check("a source edit during the wait is a reload request", opened is None and s.reload_requested and attempts == [True])

    attempts.clear(); waits.clear()
    s = spoke(0.0)
    with patch("ciel.spoke.frontend.build_audio", builder([True, False])), patch("ciel.spoke.frontend.asyncio.sleep", sleep):
        try:
            async with contextlib.AsyncExitStack() as room:
                await s._open_audio(room)
        except RuntimeError:
            check("a ceiling of zero is the old door: one attempt, and the spoke leaves", attempts == [True] and not waits)
        else:
            check("a ceiling of zero is the old door: one attempt, and the spoke leaves", False)


async def probe_warm_up_deadline() -> None:
    print("\na model load that never finishes does not hold startup")

    class FakeWhisper:
        def __init__(self, config=None, hang=False):
            self.hang = hang
            self.warmed = 0

        async def warm_up(self):
            if self.hang:
                await asyncio.sleep(10)
            self.warmed += 1

    class Hung:
        async def warm_up(self):
            await asyncio.sleep(10)

    def spoke(timeout):
        s = make_spoke()
        s._config = replace(s._config, stt=replace(s._config.stt, warm_up_timeout_s=timeout))
        return s

    s = spoke(0.05)
    s._stt = Hung()
    started = asyncio.get_running_loop().time()
    with patch("ciel.stt.local_whisper.WhisperSTT", FakeWhisper), patch("ciel.stt.gate_stt", lambda engine, config: engine):
        await s._warm_up_stt()
    check("past the deadline the engine is a failure like any other: faster-whisper takes over, warmed",
          isinstance(s._stt, FakeWhisper) and s._stt.warmed == 1 and asyncio.get_running_loop().time() - started < 1)

    s = spoke(0.05)
    s._stt = FakeWhisper(hang=True)
    with patch("ciel.stt.local_whisper.WhisperSTT", FakeWhisper):
        try:
            await s._warm_up_stt()
        except RuntimeError as exc:
            check("a fallback past its deadline ends startup and says how long it waited", "did not finish within" in str(exc))
        else:
            check("a fallback past its deadline ends startup and says how long it waited", False)

    s = spoke(0.0)
    s._stt = Hung()
    task = asyncio.create_task(s._warm_up_stt())
    await asyncio.sleep(0.1)
    check("a deadline of zero waits, as before", not task.done())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def main() -> None:
    await probe_voice_turn()
    await probe_prelude()
    await probe_barge_in()
    await probe_hub_down()
    await probe_confirm()
    await probe_delivery()
    await probe_mute_and_state()
    await probe_offline()
    await probe_ack_filler()
    await probe_speak_back_turn()
    await probe_forced_reload()
    await probe_ear_retry()
    await probe_warm_up_deadline()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    asyncio.run(main())
