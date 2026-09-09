"""The keyboard controls the room without becoming a second microphone.

Physical chords match exactly, aliases agree, repeats fire once, and invalid
bindings fail closed. Note chords and timed backslash pairs are independent of
voice controls, ignore repeats, and break on intervening typing. A fake Mac tap pins passive event delivery, permission
requests, denial and request failures, thread handoff, and shutdown without
monitoring the real keyboard. Disabled or invalid shortcuts never prompt;
already granted permission is reused and a new grant opens the listener.
The real local and spoke controls interrupt turns, deny confirmations, respect
mute, discard held words, and open one listening window. Late hub sentences
and confirmations cannot undo Stop. All state and speech are fixtures.

    uv run --no-sync python scripts/probe_shortcuts.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from ciel.config import NotesConfig, ShortcutsConfig, load_config
from ciel.schedule import State
from ciel.shortcuts import GlobalShortcuts, Matcher, parse_chord
from probe_spoke import make_spoke, FakeEndpointer, FakeWake, FakeMic, FakePlayer
from probe_turns import make_pipeline

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def probe_matching() -> None:
    cfg = ShortcutsConfig()
    check("the keyboard listener is opt in", not cfg.enabled)
    check("the default controls have stable physical positions", [parse_chord(getattr(cfg, a)).key for a in ('talk', 'stop', 'mute')] == [49, 53, 46])
    check("modifier aliases describe the same chord", parse_chord('Control + Alt + Esc') == parse_chord(cfg.stop))
    for value in ('space', 'shift+a', 'ctrl+ctrl+m', 'ctrl+unknown', 'cmd++m'):
        try:
            parse_chord(value)
        except ValueError:
            check(f"the invalid chord {value!r} cannot watch the keyboard", True)
        else:
            check(f"the invalid chord {value!r} cannot watch the keyboard", False)
    try:
        Matcher(replace(cfg, mute=cfg.talk))
    except ValueError:
        check("two actions cannot claim the same key", True)
    else:
        check("two actions cannot claim the same key", False)
    m = Matcher(cfg)
    chord = parse_chord(cfg.mute)
    check("ordinary typing produces no action", m.feed(0, 0, down=True) is None and not m._held)
    check("extra command modifiers do not match", m.feed(chord.key, chord.modifiers | (1 << 20), down=True) is None)
    check("caps lock does not change a shortcut", m.feed(chord.key, chord.modifiers | (1 << 16), down=True) == 'mute')
    check("holding mute cannot toggle it repeatedly", m.feed(chord.key, chord.modifiers, down=True) is None and m.feed(chord.key, chord.modifiers, down=True, repeat=True) is None)
    m.feed(chord.key, 0, down=False)
    check("releasing the key rearms mute", m.feed(chord.key, chord.modifiers, down=True) == 'mute')
    m.reset()
    check("a resumed tap does not retain a held key", m.feed(chord.key, chord.modifiers, down=True) == 'mute')
    with patch.dict('os.environ', {'CIEL_SHORTCUTS_ENABLED': 'true', 'CIEL_SHORTCUTS_TALK': 'cmd+option+t'}):
        cfg = load_config().shortcuts
    check("shortcuts use the ordinary configuration loader", cfg.enabled and cfg.talk == 'cmd+option+t')


def probe_notes() -> None:
    notes = NotesConfig()
    m = Matcher(ShortcutsConfig(), notes)
    chord = parse_chord(notes.shortcut)
    check("Command backslash opens a note with voice controls disabled", m.feed(42, chord.modifiers, down=True) == 'note')
    check("holding the note chord opens only one window", m.feed(42, chord.modifiers, down=True, repeat=True) is None)
    m.reset()
    with patch('ciel.shortcuts.time.monotonic', side_effect=[1.0, 1.2]):
        first = m.feed(42, 0, down=True)
        held = m.feed(42, 0, down=True, repeat=True)
        m.feed(42, 0, down=False)
        second = m.feed(42, 0, down=True)
    check("two distinct backslashes open one note", first is None and held is None and second == 'note')
    m.reset()
    with patch('ciel.shortcuts.time.monotonic', side_effect=[1.0, 2.0]):
        m.feed(42, 0, down=True); m.feed(42, 0, down=False)
        check("a slow pair remains ordinary typing", m.feed(42, 0, down=True) is None)
    m.reset()
    with patch('ciel.shortcuts.time.monotonic', side_effect=[1.0, 1.1]):
        m.feed(42, 0, down=True); m.feed(42, 0, down=False)
        m.feed(0, 0, down=True)
        check("a letter between backslashes breaks the gesture", m.feed(42, 0, down=True) is None)
    m.reset()
    check("shifted backslash is never the note gesture", m.feed(42, 1 << 17, down=True) is None)
    m = Matcher(ShortcutsConfig(), replace(notes, double_backslash=False))
    check("the pair can be disabled independently", m.feed(42, 0, down=True) is None and m.feed(42, chord.modifiers, down=True) == 'note')
    m = Matcher(ShortcutsConfig(), replace(notes, enabled=False))
    check("disabled notes have no keyboard action", not m.bindings and m.feed(42, 0, down=True) is None)
    m = Matcher(replace(ShortcutsConfig(), talk='invalid'), notes)
    check("an unused voice binding cannot disable notes", m.feed(42, chord.modifiers, down=True) == 'note')
    try:
        Matcher(replace(ShortcutsConfig(), enabled=True), replace(notes, shortcut='ctrl+option+m'))
    except ValueError:
        check("note and voice actions cannot share a chord", True)
    else:
        check("note and voice actions cannot share a chord", False)
    with patch.dict('os.environ', {'CIEL_NOTES_SHORTCUT': 'cmd+option+n', 'CIEL_NOTES_DOUBLE_BACKSLASH': 'false'}):
        cfg = load_config().notes
    check("note choices use the ordinary config loader", cfg.shortcut == 'cmd+option+n' and not cfg.double_backslash)


async def probe_controls() -> None:
    s = make_spoke()
    await s._shortcut('talk')
    check("Talk opens the spoke's existing listening window", s._take_shortcut_talk() and s._state is State.LISTENING and s._wake_source == 'hotkey')
    check("a single press opens only one window", not s._take_shortcut_talk())
    s._pending_text = 'a held thought'
    await s._shortcut('stop')
    check("Stop closes listening and discards held words", s._state is State.WAITING and s._pending_text is None)
    await s._shortcut('mute')
    check("Mute uses the persisted and relayed switch", s._muted and s._mute_sentinel.exists() and s._link.of('mute')[-1]['muted'])
    await s._shortcut('talk')
    check("Talk respects the mute switch", not s._talk_requested and not s._take_shortcut_talk())
    await s._shortcut('mute')
    check("unmuting does not secretly start listening", not s._muted and not s._mute_sentinel.exists() and s._state is State.WAITING)
    s._on_frame({'type': 'turn.begin', 'lane': 'voice', 'turn_id': 'old'})
    s._confirm = ('question', 100000)
    await s._shortcut('talk')
    check("a shortcut denies a pending confirmation", s._confirm is None and s._link.of('confirm.answer')[-1]['text'] == 'no')
    check("Talk cancels the old hub turn before taking the room", s._link.of('turn.cancel')[-1]['turn_id'] == 'old' and not s._take_shortcut_talk())
    s._on_frame({'type': 'turn.sentence', 'turn_id': 'old', 'n': 1, 'kind': 'reply', 'text': 'late'})
    check("late speech is receipted as interrupted", s._link.of('turn.played')[-1]['completed'] is False)
    s._on_frame({'type': 'confirm.request', 'confirm_id': 'late-question', 'text': 'Proceed?', 'listen': True})
    check("a late confirmation cannot reopen the answer window", s._link.of('confirm.answer')[-1]['text'] == 'no' and s._confirm is None)
    s._on_frame({'type': 'turn.end', 'turn_id': 'old'})
    s._finish_turn(s._mic)
    check("Talk resumes after the old turn ends", s._take_shortcut_talk() and s._state is State.LISTENING)
    await s._shortcut('stop')
    s._spoke = True
    s._finish_turn(s._mic)
    check("Stop does not leave a follow-up window", s._state is State.WAITING)
    s._awaiting_hub = True
    s._state = State.BUSY
    await s._shortcut('stop')
    s._on_frame({'type': 'turn.begin', 'lane': 'voice', 'turn_id': 'pending'})
    check("Stop before turn begin cancels it when its identity arrives", s._link.of('turn.cancel')[-1]['turn_id'] == 'pending' and s._shortcut_quiet)
    before = len(s._player.played)
    await s._play_sentence('pending', 2, 'reply', 'queued before Stop')
    check("a previously queued playback cannot restart speech", len(s._player.played) == before and not s._link.of('turn.played')[-1]['completed'])
    await s._ask('queued', 'Proceed?', True)
    check("a previously queued question cannot restart speech", len(s._player.played) == before and s._confirm is None)

    s = make_spoke()
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed_transcription(pcm: object) -> str:
        entered.set()
        await release.wait()
        return 'yes'
    s._stt.transcribe = delayed_transcription
    answer = asyncio.create_task(s._answer('in-flight', None))
    await entered.wait()
    await s._shortcut('stop')
    s._enter_listening()
    release.set()
    await answer
    check("an in-flight yes cannot survive Stop and a new listening window", not s._link.of('confirm.answer'))

    old_epoch = s._shortcut_epoch
    await s._shortcut('stop')
    s._enter_listening()
    before = len(s._player.played)
    await s._ask('old-queued', 'Proceed?', True, epoch=old_epoch)
    check("a queued question remembers the press that cancelled it", len(s._player.played) == before and s._confirm is None)
    await s._play_sentence('old-queued', 1, 'reply', 'stale', epoch=old_epoch)
    check("a queued sentence remembers the press that cancelled it", len(s._player.played) == before and not s._link.of('turn.played')[-1]['completed'])

    s = make_spoke()
    s._timers.set_local(1, 0.0)
    due = s._timers.due(2.0, hub_connected=False)
    entered, release = asyncio.Event(), asyncio.Event()
    plays = []
    async def held_ring(stream: object) -> bool:
        plays.append('ring')
        entered.set()
        await release.wait()
        return False
    s._player.play = held_ring
    ring = asyncio.create_task(s._ring_locally(due))
    await entered.wait()
    await s._shortcut('stop')
    release.set()
    await ring
    check("Stop during a ring prevents its following announcement", plays == ['ring'] and s._player.stops == 1)
    check("a stopped offline timer cannot immediately ring again", bool(due) and not s._timers.due(10.0, hub_connected=False))

    p = make_pipeline([])
    p._state = State.WAITING
    p._turn = None
    p._player, p._mic = FakePlayer(), FakeMic()
    p._endpointer, p._wake = FakeEndpointer(), FakeWake()
    p._mute_sentinel = s._mute_sentinel
    p._web_link.note_muted = lambda muted: None
    await p._shortcut('talk')
    check("Talk uses the same listening window in local mode", p._take_shortcut_talk() and p._state is State.LISTENING)
    p._turn = asyncio.create_task(asyncio.Event().wait())
    old_turn = p._turn
    p._state = State.BUSY
    p._pending_text = 'held'
    await p._shortcut('stop')
    check("local Stop cancels generation and denies confirmation", old_turn.cancelled() and p._brain.interrupts == 2 and p._confirm.cancels[-1] == 'keyboard shortcut')
    check("local Stop returns the microphone without held words", p._state is State.WAITING and p._pending_text is None and p._turn is None)
    await p._shortcut('mute')
    await p._shortcut('talk')
    check("local Talk cannot lift mute", p._muted and not p._take_shortcut_talk())
    await p._shortcut('mute')
    check("local unmute persists the same switch", not p._muted and not p._mute_sentinel.exists())


class FakeQuartz:
    kCGEventTapDisabledByTimeout = -2
    kCGEventTapDisabledByUserInput = -1
    kCGEventKeyDown, kCGEventKeyUp = 10, 11
    kCGKeyboardEventKeycode, kCGKeyboardEventAutorepeat = 'key', 'repeat'
    kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionListenOnly = 1, 0, 1
    kCFRunLoopDefaultMode = 'default'

    def __init__(self) -> None:
        self.created = False
        self.invalidated = False
        self.sent = False
        self.permission = True
        self.requests = 0
        self.grant_on_request = False
        self.request_error = False

    def CGPreflightListenEventAccess(self) -> bool:
        return self.permission

    def CGRequestListenEventAccess(self) -> bool:
        self.requests += 1
        if self.request_error:
            raise RuntimeError('simulated permission request failure')
        if self.grant_on_request:
            self.permission = True
        return self.permission

    def CGEventTapCreate(self, tap: int, place: int, options: int, mask: int, callback: object, context: object) -> object:
        self.created = True
        self.options = options
        self.callback = callback
        return self

    def CFMachPortCreateRunLoopSource(self, allocator: object, tap: object, order: int) -> object:
        return self

    def CFRunLoopGetCurrent(self) -> object:
        return self

    def CFRunLoopAddSource(self, loop: object, source: object, mode: object) -> None:
        pass

    def CFRunLoopRemoveSource(self, loop: object, source: object, mode: object) -> None:
        pass

    def CGEventTapEnable(self, tap: object, enabled: bool) -> None:
        pass

    def CFMachPortInvalidate(self, tap: object) -> None:
        self.invalidated = True

    def CGEventGetIntegerValueField(self, event: dict, field: str) -> int:
        return event[field]

    def CGEventGetFlags(self, event: dict) -> int:
        return event['flags']

    def CFRunLoopRunInMode(self, mode: object, seconds: float, return_after: bool) -> None:
        if not self.sent:
            self.sent = True
            event = {'key': 46, 'repeat': 0, 'flags': 786432}
            self.passed_through = self.callback(None, 10, event, None) is event
        # The fake thread yields without ever opening a real event tap.
        import time
        time.sleep(0.001)


async def probe_listener() -> None:
    actions = []
    ready = asyncio.Event()
    async def handle(action: str) -> None:
        actions.append(action)
        ready.set()
    q = FakeQuartz()
    with patch('sys.platform', 'darwin'), patch.dict(sys.modules, {'Quartz': q}):
        disabled = GlobalShortcuts(ShortcutsConfig(), handle)
        await disabled.start()
        check("disabled shortcuts open no native listener", not q.created and disabled._thread is None)
        check("disabled shortcuts never ask for keyboard permission", q.requests == 0)
        q.permission = False
        denied = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True), handle)
        await denied.start()
        await asyncio.to_thread(denied._thread.join, 2)
        check("denied permission leaves the voice loop available", not q.created and not denied._thread.is_alive())
        check("Ciel requests its own missing keyboard permission", q.requests == 1)
        await denied.start()
        check("starting the same listener twice cannot repeat the prompt", q.requests == 1)
        await denied.close()
        q.permission = True
        listener = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True), handle)
        await listener.start()
        await asyncio.wait_for(ready.wait(), 2)
        check("native callbacks deliver only action names to asyncio", actions == ['mute'])
        check("an existing permission grant does not prompt again", q.requests == 1)
        check("the native tap passes the original event through", q.options == q.kCGEventTapOptionListenOnly and q.passed_through)
        await listener.close()
        check("shutdown removes the tap and its worker", q.invalidated and listener._thread is None and listener._worker is None)
        listener._enqueue('talk')
        check("callbacks arriving after shutdown cannot enqueue controls", listener._queue.empty())
        failed = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True), handle)
        with patch.object(q, 'CGEventTapCreate', return_value=None):
            await failed.start()
            await failed.close()
        check("an unavailable native tap still shuts down cleanly", failed._thread is None and failed._worker is None)

        invalid = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True, talk='space'), handle)
        requests_before = q.requests
        await invalid.start()
        check("invalid bindings cannot prompt for permission", q.requests == requests_before and invalid._thread is None)

        q.permission = False
        q.grant_on_request = True
        q.sent = False
        q.created = False
        ready.clear()
        granted = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True), handle)
        await granted.start()
        await asyncio.wait_for(ready.wait(), 2)
        check("a grant available after the request starts the listener", q.permission and q.created and q.requests == requests_before + 1)
        await granted.close()

        q.permission = False
        q.request_error = True
        q.created = False
        broken = GlobalShortcuts(replace(ShortcutsConfig(), enabled=True), handle)
        await broken.start()
        await asyncio.to_thread(broken._thread.join, 2)
        check("a failed system request cannot open a tap or crash startup", not q.created and not broken._thread.is_alive())
        await broken.close()


async def main() -> None:
    with tempfile.TemporaryDirectory() as home, patch.object(Path, "home", return_value=Path(home)), patch("tempfile.tempdir", home):
        probe_matching()
        probe_notes()
        await probe_controls()
        await probe_listener()
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == '__main__':
    asyncio.run(main())
