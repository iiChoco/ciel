#!/usr/bin/env python
"""Probe the gesture ear and its tester without a mic, model, network, or runtime state.

Both synthetic hand-shaped impulses here have the same decay: the old width
assumption cannot separate them. These fixtures pin filtering across frame
boundaries, diagnostic measurements, quieter second claps, exclusive pairs,
reset and EOF, and CLI output. Feature records separately pin the boundaries
reported in the supplied comparison script. Real hand-sound accuracy remains
unmeasured; neither tone fixtures nor threshold checks establish it.

The wake half pins how the ear sits beside the wake word: a snap or a double
clap wakes only when its switch is on, a lone clap never wakes, a turn's reset
forgets a half-made pair without going deaf, the wake-word model still sees
the frame a snap fires on, ``always`` mode has nothing to wake, the hotkey's
``arm`` survives the wrapping, the ready line names what is switched on, and
the ``[gestures]`` table loads from TOML and from the environment. In play
mode two claps run their action once, after the frame loop, and never wake;
the snap still wakes beside them; the build refuses anything but a Spotify
URI; the music door passes the URI as an argument, never as script; every play
launches the app hidden and behind the screen; with no active device the API
is aimed at this Mac by device id, launching and waiting for the app when it
is not among the devices; and with
the Spotify connector on, the Web API is tried first and the desktop app
answers when the API has no player or no login.
With ``log_candidates`` on, every gated impulse becomes a log line with its
four numbers and the cue a miss failed; off, the ear says nothing but gestures.
The keyboard's own word: a snap or a clap inside ``keyboard_veto_ms`` of a key
event is a keystroke and says so, one inside it of a mouse button is a click
and says so, the more recent of the two is the one named, a fixture that
supplies one clock is never answered by the Mac's other one, the keyboard is
asked before the model, and without Quartz the veto is inert. Modifier-only
presses and releases also veto hand sounds; event-age diagnostics follow the
candidate logging switch and record no key identities.
A snap is solitary: one after another gated impulse inside ``snap_quiet_ms`` is
typing cadence, claps are not held to it, and zero switches it off.
The veto half pins that a second opinion only says no: it is asked only about
what the rules accepted, shown one AudioSet frame ending just after the
impulse, its answer names what the room was doing, a vetoed second clap breaks
the pair, and the model wrapper vetoes on room classes alone, never on hands.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import wave
from dataclasses import replace
from pathlib import Path
from types import TracebackType
from typing import AsyncIterator, Self
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from listen_gestures import Reporter, listen
from ciel.audio.audioset import AudioSetVeto, VETO_CLASSES, WINDOW_SAMPLES, YAMNET_SHA256
from ciel.audio.gestures import GestureDetector, Measurement, Observation, classify, first_of
from ciel.audio.keys import KeyboardVeto
from ciel import music
from ciel.audio.wake import AnyWake, GestureWake, HotkeyWake, build_wake_detector, wake_phrases
from ciel.config import FRAME_BYTES, SAMPLE_RATE, AudioConfig, Config, GestureConfig, WakeConfig, load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


def pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes()


def recording(sounds: list[tuple[float, str]], seconds: float = 2.5) -> np.ndarray:
    audio = np.zeros(round(seconds * SAMPLE_RATE))
    t = np.arange(640) / SAMPLE_RATE
    for at, kind in sounds:
        freq, gain = {'snap': (4000, .15), 'clap': (1200, .8), 'soft': (1200, .15), 'tick': (1200, .03)}[kind]
        sound = gain * np.sin(2 * np.pi * freq * t) * np.exp(-t / .002)
        start = round(at * SAMPLE_RATE)
        length = min(len(sound), len(audio) - start)
        audio[start:start + length] += sound[:length]
    return audio


def feed(audio: np.ndarray, detector: GestureDetector | None = None, finish: bool = True) -> list[Observation]:
    detector = detector or GestureDetector()
    data = pcm(audio)
    rows = []
    for offset in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES):
        rows.extend(detector.push(data[offset:offset + FRAME_BYTES]))
    if finish:
        rows.extend(detector.finish())
    return rows


def gestures(rows: list[Observation]) -> list[str]:
    return [e.kind for e in rows if e.type == 'gesture']


def kinds(sounds: list[tuple[float, str]]) -> list[str]:
    return gestures(feed(recording(sounds)))


def main() -> None:
    c = GestureConfig()
    snap = Measurement(1, .1, 2, 2.5, 20)
    clap = Measurement(1, .6, 2, 1, 5)
    check('the supplied narrow bright snap qualifies', classify(snap, c)[0] == 'snap')
    check('the supplied equally narrow clap qualifies', classify(clap, c)[0] == 'clap')
    check('a loud clap in the tilt overlap remains a clap', classify(replace(clap, tilt=1.6), c)[0] == 'clap')
    verdict, reason = classify(replace(clap, peak=.02), c)
    check('a quiet keystroke exposes the failed peak boundary', verdict == 'rejected' and 'clap: peak' in reason)
    check('snap width uses the supplied exclusive upper boundary', classify(replace(snap, width_ms=5), c)[0] == 'rejected')
    check('clap fall uses the supplied inclusive lower boundary', classify(replace(clap, fall_db=3), c)[0] == 'clap')
    check('silence emits neither diagnostics nor gestures', feed(recording([])) == [])
    check('warmup ignores early impulses', kinds([(.1, 'snap')]) == [])
    check('the short bright waveform is a snap', kinds([(1, 'snap')]) == ['snap'])
    check('the equally short darker waveform is a clap', kinds([(1, 'clap')]) == ['clap'])
    check('a weak leading ripple does not hide the main clap', all(kinds([(1, 'tick'), (1 + gap, 'clap')]) == ['clap'] for gap in (.001, .0015, .002)))
    snap_rows = feed(recording([(1, 'snap')]))
    check('a recognized sound has a diagnostic and a gesture', [e.type for e in snap_rows] == ['candidate', 'gesture'])
    check('the full decay window costs less than two capture frames here', 0.035 <= snap_rows[-1].detected_s - snap_rows[-1].onset_s < .065)
    check('a rejected gated sound is visible with its reason', any(e.kind == 'rejected' and e.reason for e in feed(recording([(1, 'tick')]))))
    check('a quieter second clap can complete a pair', kinds([(1, 'clap'), (1.2, 'soft')]) == ['double_clap'])
    check('the same quiet clap cannot start a pair alone', kinds([(1, 'soft')]) == [])
    pair = [e for e in feed(recording([(1, 'clap'), (1.2, 'clap')])) if e.type == 'gesture']
    check('a double clap produces no single-clap gestures', [e.kind for e in pair] == ['double_clap'])
    check('both times in the pair come from audio peaks', abs(pair[0].second_onset_s - pair[0].onset_s - .2) < 1 / SAMPLE_RATE)
    check('a fast pair survives the old long analysis lockout', kinds([(1, 'clap'), (1.1, 'clap')]) == ['double_clap'])
    check('a late pair survives the old short pair window', kinds([(1, 'clap'), (1.7, 'clap')]) == ['double_clap'])
    check('a close echo remains one clap', kinds([(1, 'clap'), (1.03, 'clap')]) == ['clap'])
    check('three claps become a pair and a single', kinds([(1, 'clap'), (1.2, 'clap'), (1.4, 'clap')]) == ['double_clap', 'clap'])
    check('four claps become two pairs', kinds([(1, 'clap'), (1.2, 'clap'), (1.4, 'clap'), (1.6, 'clap')]) == ['double_clap', 'double_clap'])
    check('a snap breaks a pending pair', kinds([(1, 'clap'), (1.5, 'snap'), (1.7, 'clap')]) == ['clap', 'snap', 'clap'])
    check('a rejected tick breaks a pending pair', kinds([(1, 'clap'), (1.2, 'tick'), (1.4, 'clap')]) == ['clap', 'clap'])
    for gap, expected in ((.06, ['double_clap']), (.799, ['double_clap']), (.8, ['clap', 'clap'])):
        d = GestureDetector()
        rows = d._accept(clap) + d._accept(replace(clap, onset_s=1 + gap)) + d.finish()
        check(f'the measured pair boundary at {gap:g} seconds is explicit', gestures(rows) == expected)
    d = GestureDetector()
    rows = d._accept(clap) + d._accept(replace(snap, onset_s=1.2, tilt=1.8)) + d.finish()
    check('a recognized snap wins over the relaxed second-clap overlap', gestures(rows) == ['clap', 'snap'])
    offsets = [0, 1, 15, 16, 31, 79, 80, 159, 399, 400, 478, 479]
    for kind in ('snap', 'clap'):
        check(f'{kind} survives capture and envelope boundary offsets', all(kinds([(.9 + n / SAMPLE_RATE, kind)]) == [kind] for n in offsets))
    d = GestureDetector()
    early = feed(recording([(1, 'clap')], 1.2), d, finish=False)
    check('the first clap is visible immediately but its gesture waits', len(early) == 1 and early[0].type == 'candidate' and early[0].reason == 'waiting for second clap')
    later = feed(np.zeros(SAMPLE_RATE), d)
    check('silence releases one pending clap', gestures(later) == ['clap'])
    d = GestureDetector()
    feed(recording([(1, 'clap')], 1.2), d, finish=False)
    d.reset()
    check('reset clears pending gestures and filter history', not d.ready and feed(np.zeros(SAMPLE_RATE), d) == [])
    check('EOF discards a sound without its full decay window', feed(recording([(1, 'clap')], 1.02)) == [])
    d = GestureDetector()
    d.finish()
    try:
        d.push(b'\x00' * FRAME_BYTES)
    except ValueError:
        check('a finished stream requires reset before reuse', True)
    else:
        check('a finished stream requires reset before reuse', False)
    for config in (replace(c, noise_ratio=float('nan')), replace(c, double_min_ms=20), replace(c, clap_max_ms=50)):
        try:
            GestureDetector(config)
        except ValueError:
            pass
        else:
            check('inconsistent configuration is rejected', False)
    check('inconsistent configuration is rejected', True)
    try:
        GestureDetector().push(b'\x00')
    except ValueError:
        check('invalid capture frames are rejected', True)
    else:
        check('invalid capture frames are rejected', False)
    rng = np.random.default_rng(7)
    check('steady ambient hiss produces no gestures', gestures(feed(rng.normal(0, .005, SAMPLE_RATE * 2))) == [])
    check('steady hum produces no gestures', gestures(feed(.05 * np.sin(2 * np.pi * 100 * np.arange(SAMPLE_RATE * 2) / SAMPLE_RATE))) == [])
    check('DC startup does not retrigger at frame boundaries', feed(np.full(SAMPLE_RATE * 2, .2)) == [])
    audio = recording([(1, 'snap')])
    one = GestureDetector()._filter(audio)
    d = GestureDetector()
    chunks = [d._filter(audio[i:i + 480]) for i in range(0, len(audio), 480)]
    check('filter and envelope state are identical across frame boundaries', all(np.array_equal(one[j], np.concatenate([x[j] for x in chunks])) for j in (0, 1)))
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
        root = Path(folder)
        wav_path, csv_path = root / 'pair.wav', root / 'events.csv'
        with wave.open(str(wav_path), 'wb') as output:
            output.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            output.writeframes(pcm(recording([(1, 'clap'), (1.2, 'soft')])))
        command = [sys.executable, str(Path(__file__).with_name('listen_gestures.py')), '--wav', str(wav_path), '--json', '--events-only', '--csv', str(csv_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        lines = [json.loads(line) for line in result.stdout.splitlines()] if result.returncode == 0 else []
        check('WAV replay exposes one exclusive pair as JSON', result.returncode == 0 and [e['kind'] for e in lines] == ['double_clap'])
        with csv_path.open() as source:
            csv_rows = list(csv.DictReader(source))
        check('CSV retains candidates even when console shows only gestures', [r['type'] for r in csv_rows] == ['candidate', 'candidate', 'gesture'])
        check('diagnostic CSV is owner-only', csv_path.stat().st_mode & 0o777 == 0o600)
        result = subprocess.run(command, capture_output=True, text=True)
        with csv_path.open() as source:
            csv_rows = list(csv.DictReader(source))
        check('CSV appends without duplicating its header', result.returncode == 0 and len(csv_rows) == 6)
        result = subprocess.run(command[:-2] + ['--double-max-ms', '150'], capture_output=True, text=True)
        lines = [json.loads(line) for line in result.stdout.splitlines()] if result.returncode == 0 else []
        check('standalone flags tune pairing without editing Ciel', result.returncode == 0 and [e['kind'] for e in lines] == ['clap'])
        with wave.open(str(wav_path), 'wb') as output:
            output.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
        result = subprocess.run(command[:-2], capture_output=True, text=True)
        check('incompatible WAV formats fail clearly', result.returncode == 1 and '16000 Hz mono' in result.stderr)

    class FakeMic:
        chosen: AudioConfig | None = None
        closed = False

        def __init__(self, config: AudioConfig) -> None:
            FakeMic.chosen = config

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, exc_type: type[BaseException] | None,
                            exc: BaseException | None, tb: TracebackType | None) -> None:
            FakeMic.closed = True

        async def frames(self) -> AsyncIterator[bytes]:
            data = pcm(recording([(1, 'snap')]))
            for offset in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES):
                yield data[offset:offset + FRAME_BYTES]

    stdout, stderr = io.StringIO(), io.StringIO()
    config = Config(audio=AudioConfig(input_device=0))
    ended = False
    with patch('ciel.audio.input.MicStream', FakeMic), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            asyncio.run(listen(config, GestureDetector(), Reporter(True, True)))
        except RuntimeError as exc:
            ended = 'capture ended' in str(exc)
    check('live mode uses the exact Ciel capture configuration', FakeMic.chosen is config.audio)
    check('live capture feeds the same detector as WAV replay', [json.loads(line)['kind'] for line in stdout.getvalue().splitlines()] == ['snap'])
    check('readiness and device messages stay off JSON stdout', 'Ready:' in stderr.getvalue() and '(0)' in stderr.getvalue())
    check('capture loss closes the microphone and reports failure', FakeMic.closed and ended)

    # ── the ear beside the wake word ─────────────────────────────────────────

    def wakes(detector, sounds: list[tuple[float, str]], seconds: float = 2.5) -> list[float]:
        data = pcm(recording(sounds, seconds))
        return [offset / FRAME_BYTES * 0.03 for offset in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES)
                if detector.push(data[offset:offset + FRAME_BYTES])]

    ear = GestureWake(GestureDetector(), {'snap', 'double_clap'})
    check('a snap wakes through the gesture ear', len(wakes(ear, [(1, 'snap')])) == 1)
    check('a double clap wakes through the gesture ear', len(wakes(ear, [(1, 'clap'), (1.2, 'clap')])) == 1)
    check('a lone clap never wakes', wakes(ear, [(1, 'clap')]) == [])
    check('a pair fires once, on its second clap, not again when the window closes',
          len(wakes(ear, [(1, 'clap'), (1.2, 'clap')], 4)) == 1)
    snap_only = GestureWake(GestureDetector(), {'snap'})
    check('with only the snap switched on, two claps are just noise', wakes(snap_only, [(1, 'clap'), (1.2, 'clap')]) == [])
    claps_only = GestureWake(GestureDetector(), {'double_clap'})
    check('with only the double clap switched on, a snap is just noise', wakes(claps_only, [(1, 'snap')]) == [])
    ear = GestureWake(GestureDetector(), {'snap', 'double_clap'})
    wakes(ear, [(1, 'clap')], 1.2)
    ear.reset()
    check('a turn\'s reset forgets a half-made pair', wakes(ear, [(0.1, 'clap')], 1.5) == [])
    check('...without going deaf: the next snap still wakes', len(wakes(ear, [(0.1, 'snap')], 1.5)) == 1)

    class Counting:
        def __init__(self) -> None:
            self.frames = 0
            self.armed = False

        async def start(self) -> None:
            pass

        def push(self, frame: bytes) -> bool:
            self.frames += 1
            return False

        def reset(self) -> None:
            pass

        async def close(self) -> None:
            pass

        def arm(self) -> None:
            self.armed = True

    word = Counting()
    both = AnyWake([word, GestureWake(GestureDetector(), {'snap'})])
    fired = wakes(both, [(1, 'snap')])
    check('a snap wakes through the composite', len(fired) == 1)
    check('...and the composite says what addressed it', both.source == 'snap')
    check('a wake word or a hotkey says spoken or hotkey', HotkeyWake().source == 'hotkey' and AnyWake([HotkeyWake()]).source == 'spoken')
    check('the wake-word model still saw every frame, including the one the snap fired on', word.frames == len(pcm(recording([(1, 'snap')]))) // FRAME_BYTES)
    both.arm()
    check('the hotkey\'s arm survives the wrapping', word.armed and hasattr(both, 'arm'))
    check('a composite without a hotkey has nothing to arm', not hasattr(AnyWake([GestureWake(GestureDetector(), {'snap', 'double_clap'})]), 'arm'))
    check('a switched-off ear leaves the wake word alone', not isinstance(build_wake_detector(WakeConfig(mode='hotkey'), GestureConfig()), AnyWake))
    built = build_wake_detector(WakeConfig(mode='hotkey', snap=True), GestureConfig())
    check('a switched-on ear sits beside the hotkey', isinstance(built, AnyWake) and isinstance(built._members[0], HotkeyWake))
    check('always mode has nothing to wake, so the ear is not built', not isinstance(build_wake_detector(WakeConfig(mode='always', snap=True, double_clap='wake')), AnyWake))
    check('the ready line names what is switched on, in order',
          wake_phrases(build_wake_detector(WakeConfig(mode='hotkey', snap=True, double_clap='wake'))) == ('snap', 'clap twice')
          and wake_phrases(build_wake_detector(WakeConfig(mode='hotkey', double_clap='wake'))) == ('clap twice',)
          and wake_phrases(HotkeyWake()) == ())
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'CIEL_GESTURES_CLAP_MIN_PEAK': '0.3'}, clear=True):
        toml = Path(folder) / 'config.toml'
        toml.write_text('[wake]\nsnap = true\n\n[gestures]\ndouble_max_ms = 500\nsnap_min_tilt = 2.5\n')
        loaded = load_config(toml)
        check('the gestures table loads from TOML', loaded.wake.snap and loaded.wake.double_clap == 'off'
              and loaded.gestures.double_max_ms == 500 and loaded.gestures.snap_min_tilt == 2.5)
        check('...and from the environment, like every other section', loaded.gestures.clap_min_peak == 0.3)
        check('the loaded thresholds build a working ear', isinstance(build_wake_detector(loaded.wake, loaded.gestures), AnyWake))

    # ── two claps that act instead of waking ─────────────────────────────────

    played: list[str] = []

    async def fake_play() -> str:
        played.append('bruno')
        return 'playing'

    async def act_run(sounds, seconds=2.5):
        ear = GestureWake(GestureDetector(), {'snap'}, {'double_clap': ('play music', fake_play)})
        fired = wakes(ear, sounds, seconds)
        await asyncio.sleep(0)  # the scheduled action runs on the loop, not in the frame loop
        return ear, fired

    ear, fired = asyncio.run(act_run([(1, 'clap'), (1.2, 'clap')]))
    check('in play mode two claps do not wake', fired == [])
    check('...they run the action once, after the frame loop has moved on', played == ['bruno'])
    check('the ready line says what two claps do', ear.phrases == ('snap', 'clap twice to play music'))
    played.clear()
    _, fired = asyncio.run(act_run([(1, 'snap')]))
    check('the snap still wakes beside an acting double clap', len(fired) == 1 and played == [])
    _, fired = asyncio.run(act_run([(1, 'clap')], 3))
    check('a lone clap neither wakes nor plays', fired == [] and played == [])
    URI = 'spotify:artist:0du5cEVh5yTK9QJze8zA0C'
    built = build_wake_detector(WakeConfig(mode='hotkey', double_clap='play', double_clap_plays=URI))
    check('play mode builds an ear with an action and no wake', isinstance(built, AnyWake)
          and wake_phrases(built) == ('clap twice to play music',))
    try:
        build_wake_detector(WakeConfig(mode='hotkey', double_clap='play', double_clap_plays='rm -rf ~'))
    except ValueError as exc:
        check('play mode refuses anything but a Spotify URI at build time', 'Spotify URI' in str(exc))
    else:
        check('play mode refuses anything but a Spotify URI at build time', False)
    check('a Spotify URI is a kind and a 22-character id, nothing else',
          music.valid_uri(URI) and music.valid_uri('spotify:playlist:37i9dQZF1DXcBWIGoYBM5M')
          and not music.valid_uri('spotify:artist:0du5cEVh5yTK9QJze8zA0C"; do shell script "true')
          and not music.valid_uri('https://open.spotify.com/artist/0du5cEVh5yTK9QJze8zA0C')
          and not music.valid_uri(''))
    calls: list[list[str]] = []

    async def fake_osascript(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, 'Bruno Mars — Uptown Funk'

    said = asyncio.run(music.play(URI, run=fake_osascript, launch=lambda: None))
    check('the URI reaches AppleScript as an argument, never spliced into the script',
          calls[0][0] == 'osascript' and calls[0][-1] == URI and URI not in calls[0][2])
    check('what played comes back as a sentence', said == 'Bruno Mars — Uptown Funk')
    said = asyncio.run(music.play('not a uri', run=fake_osascript, launch=lambda: None))
    check('a bad URI never starts a process', len(calls) == 1 and 'not a Spotify URI' in said)

    async def failing(argv: list[str]) -> tuple[int, str]:
        return 1, 'Spotify got an error'

    said = asyncio.run(music.play(URI, run=failing, launch=lambda: None))
    check('a refusal from Spotify is reported, not raised', said.startswith('Spotify would not play'))

    from ciel.config import SpotifyConfig
    from ciel.spotify import SpotifyUnavailable

    calls.clear()
    said = asyncio.run(music.play(URI, run=fake_osascript, api=lambda uri: f'playing on Kitchen ({uri[-4:]})', launch=lambda: None))
    check('with the connector, two claps play through the Web API on the active device and the desktop app is not scripted',
          said == 'Spotify Connect: playing on Kitchen (zA0C)' and calls == [])

    def no_player(uri: str) -> str:
        raise SpotifyUnavailable('No active device.', 404)

    said = asyncio.run(music.play(URI, run=fake_osascript, api=no_player, launch=lambda: None))
    check('when the API has no player to talk to, the desktop app answers', len(calls) == 1 and said == 'Bruno Mars — Uptown Funk')

    def not_connected(uri: str) -> str:
        raise SpotifyUnavailable('Spotify is not connected.')

    calls.clear()
    said = asyncio.run(music.play(URI, run=fake_osascript, api=not_connected, launch=lambda: None))
    check('...and so it does before the account is authorized', len(calls) == 1 and said == 'Bruno Mars — Uptown Funk')

    class FakeSpotify:
        """The connector's client, scripted: no active device at first."""

        def __init__(self, devices: list[dict], active_after: int = 1) -> None:
            self._devices = devices
            self.calls: list[tuple] = []
            self.active_after = active_after

        def control(self, action: str, uri: str = '', device_id: str = '') -> dict:
            self.calls.append((action, uri, device_id))
            if not device_id and len([c for c in self.calls if c[0] == 'play']) <= self.active_after:
                raise SpotifyUnavailable('Spotify could not find the player or item.', 404)
            return {'accepted': action}

        def devices(self) -> dict:
            return {'devices': self._devices}

        def status(self) -> dict:
            return {'active': True, 'device': {'name': 'Yunhan\u2019s MacBook Pro (10)'}}

    launched: list[str] = []
    fake = FakeSpotify([{'id': 'mac-1', 'name': 'Yunhan\u2019s MacBook Pro (10)', 'type': 'Computer'}, {'id': 'phone', 'name': 'iPhone', 'type': 'Smartphone'}])
    door = music._web_player(fake, launch=lambda: launched.append('open'), wait=lambda s: None)
    check('with no active device the API is aimed at this Mac by its device id, and nothing is launched',
          door(URI).startswith('playing on') and fake.calls[-1] == ('play', URI, 'mac-1') and launched == [])
    fake = FakeSpotify([{'id': 'other', 'name': 'Office iMac', 'type': 'Computer'}])
    door = music._web_player(fake, launch=lambda: launched.append('open'), wait=lambda s: None)
    check('with no device named for this Mac, any computer will do', door(URI) and fake.calls[-1][2] == 'other')
    appearing = FakeSpotify([])
    def appear() -> None:
        launched.append('open')
        appearing._devices.append({'id': 'mac-2', 'name': 'Yunhan\u2019s MacBook Pro (10)', 'type': 'Computer'})
    door = music._web_player(appearing, launch=appear, wait=lambda s: None)
    check('with no device at all the app is launched hidden, waited for, and then aimed at',
          door(URI) and launched == ['open'] and appearing.calls[-1][2] == 'mac-2')
    never = FakeSpotify([])
    ticks: list[float] = []
    door = music._web_player(never, launch=lambda: launched.append('open'), wait=ticks.append)
    try:
        door(URI)
    except SpotifyUnavailable as exc:
        check('an app that never appears gives up after the wait, and says so', 'did not appear' in str(exc) and len(ticks) >= 1)
    else:
        check('an app that never appears gives up after the wait, and says so', False)
    fine = FakeSpotify([], active_after=0)
    door = music._web_player(fine, launch=lambda: launched.append('open'), wait=lambda s: None)
    launched.clear()
    check('with an active device nothing else is asked or launched', door(URI).startswith('playing on') and launched == [] and len(fine.calls) == 1)
    opened: list[str] = []
    calls.clear()
    asyncio.run(music.play(URI, run=fake_osascript, launch=lambda: opened.append('hidden')))
    check('every play launches the app hidden and behind the screen before any door is tried', opened == ['hidden'])

    check('a connector that is off, or has no app, leaves the desktop app alone as the only door',
          music.web_player(SpotifyConfig()) is None and music.web_player(SpotifyConfig(enabled=True)) is None
          and music.web_player(None) is None)
    check('a connector that is on with an app is the first door',
          callable(music.web_player(SpotifyConfig(enabled=True, client_id='abc'))))
    built = build_wake_detector(WakeConfig(mode='hotkey', double_clap='play', double_clap_plays=URI), spotify=SpotifyConfig(enabled=True, client_id='abc'))
    check('the spoke builds the pair action with the connector beside it', isinstance(built, AnyWake))

    # ── the ear can narrate what it hears ────────────────────────────────────

    import logging

    class Catch(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.lines.append(record.getMessage())

    catch = Catch()
    logging.getLogger('ciel.audio.wake').addHandler(catch)
    logging.getLogger('ciel.audio.wake').setLevel(logging.INFO)
    wakes(GestureWake(GestureDetector(), {'snap'}), [(1, 'clap'), (1.5, 'tick')], 3)
    check('by default the ear says nothing about impulses that fire no gesture', not any(l.startswith('ear:') for l in catch.lines))
    catch.lines.clear()
    wakes(GestureWake(GestureDetector(), {'snap'}, log_candidates=True), [(1, 'clap'), (1.5, 'tick')], 3)
    ears = [l for l in catch.lines if l.startswith('ear:')]
    check('with log_candidates on, every gated impulse is a line with its four numbers',
          len(ears) >= 2 and all('peak' in l and 'tilt' in l and 'fall' in l for l in ears))
    check('...and a miss names the cue it failed', any('rejected' in l and ':' in l.split('—')[-1] for l in ears))
    check('...and a lone clap says it is waiting for its pair', any('waiting for second clap' in l for l in ears))
    check('...and says when it stood alone, with the window it waited', any('stood alone' in l and '800 ms' in l for l in ears))
    logging.getLogger('ciel.audio.wake').removeHandler(catch)
    with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
        toml = Path(folder) / 'config.toml'
        toml.write_text('[wake]\nsnap = true\n\n[gestures]\nlog_candidates = true\n')
        loaded = load_config(toml)
        built = build_wake_detector(loaded.wake, loaded.gestures)
        check('the switch reaches the ear the spoke builds', built._members[1]._log_candidates is True)

    # ── a snap is solitary ───────────────────────────────────────────────────

    check('a snap on its own is a snap', kinds([(1, 'snap')]) == ['snap'])
    rows = feed(recording([(1, 'tick'), (1.2, 'snap')]))
    check('a snap 200 ms after a keystroke-shaped tick is typing cadence, and says so',
          gestures(rows) == [] and any('typing cadence' in r.reason for r in rows if r.type == 'candidate'))
    check('a snap 500 ms after the tick is solitary again', kinds([(1, 'tick'), (1.5, 'snap')]) == ['snap'])
    check('the run of keystrokes itself never becomes a snap', kinds([(1, 'tick'), (1.15, 'tick'), (1.3, 'tick'), (1.45, 'snap')]) == [])
    check('claps are not held to the snap\'s solitude', kinds([(1, 'tick'), (1.2, 'clap'), (1.4, 'clap')]) == ['double_clap'])
    quiet_off = GestureDetector(replace(GestureConfig(), snap_quiet_ms=0))
    check('snap_quiet_ms = 0 switches the gate off', gestures(feed(recording([(1, 'tick'), (1.2, 'snap')]), quiet_off)) == ['snap'])

    # ── the keyboard's own word ──────────────────────────────────────────────

    ago = [0.05]
    keys = KeyboardVeto(300, since_key=lambda: ago[0])
    check('a snap 50 ms after a key went down is that key', keys(np.zeros(8)) == 'a keystroke 50 ms ago')
    check('...and so is one 299 ms after', (ago.__setitem__(0, 0.299) or keys(np.zeros(8))) == 'a keystroke 299 ms ago')
    check('a snap 300 ms after the last key is not the keyboard\'s business', (ago.__setitem__(0, 0.3) or keys(np.zeros(8))) is None)
    check('nor one a minute after', (ago.__setitem__(0, 60.0) or keys(np.zeros(8))) is None)
    with patch('ciel.audio.keys._quartz_since_key', lambda: None), patch('ciel.audio.keys._quartz_since_click', lambda: None):
        inert = KeyboardVeto(300)
    check('without Quartz the veto is inert and says so', not inert.available and inert(np.zeros(8)) is None)
    clicked = [0.04]
    ago[0] = 5.0
    desk = KeyboardVeto(300, since_key=lambda: ago[0], since_click=lambda: clicked[0])
    check('a snap 40 ms after a mouse button is that click, and says so', desk(np.zeros(8)) == 'a click 40 ms ago')
    ago[0] = 0.02
    check('with a key and a click both inside the window, the more recent is named', desk(np.zeros(8)) == 'a keystroke 20 ms ago')
    ago[0], clicked[0] = 5.0, 0.5
    check('a click half a second old is not the desk\'s business', desk(np.zeros(8)) is None)
    with patch('ciel.audio.keys._quartz_since_click', lambda: (lambda: 0.0)):
        keys_only = KeyboardVeto(300, since_key=lambda: 5.0)
    check('a fixture that supplies the keyboard is never answered by the Mac\'s mouse', keys_only.available and keys_only(np.zeros(8)) is None)
    ago[0] = 0.04
    rows = feed(recording([(1, 'snap'), (2, 'clap'), (2.2, 'clap')]), GestureDetector(veto=keys))
    check('with a key just pressed, a snap and both claps are keystrokes, and say so',
          gestures(rows) == [] and all('a keystroke 40 ms ago' in r.reason for r in rows if r.type == 'candidate' and r.kind == 'rejected'))
    ago[0] = 5.0
    check('with the keyboard quiet, the same sounds are gestures again', gestures(feed(recording([(1, 'snap'), (2, 'clap'), (2.2, 'clap')]), GestureDetector(veto=keys))) == ['snap', 'double_clap'])
    order: list[str] = []
    both = first_of(lambda w: (order.append('keys'), 'a keystroke 20 ms ago')[1], lambda w: (order.append('model'), 'typing 0.9')[1])
    check('the keyboard is asked first and a certain answer spares the model', both(np.zeros(8)) == 'a keystroke 20 ms ago' and order == ['keys'])
    order.clear()
    both = first_of(lambda w: (order.append('keys'), None)[1], lambda w: (order.append('model'), 'typing 0.9')[1])
    check('when the keyboard has nothing to say the model is asked', both(np.zeros(8)) == 'typing 0.9' and order == ['keys', 'model'])
    check('no opinions at all is no veto', first_of(None, None) is None)
    built = build_wake_detector(WakeConfig(mode='hotkey', snap=True), replace(GestureConfig(), keyboard_veto_ms=0))
    check('keyboard_veto_ms = 0 builds an ear without the keyboard', built._members[1]._detector._veto is None)

    from types import SimpleNamespace
    q = SimpleNamespace(kCGEventSourceStateCombinedSessionState=0, kCGEventKeyDown=10,
                        kCGEventKeyUp=11, kCGEventFlagsChanged=12,
                        kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2,
                        kCGEventRightMouseDown=3, kCGEventRightMouseUp=4,
                        kCGEventOtherMouseDown=25, kCGEventOtherMouseUp=26)
    ages = {10: 5.0, 11: 5.0, 12: .04}
    queried: list[int] = []
    def event_age(state: int, kind: int) -> float:
        queried.append(kind)
        return ages.get(kind, 5.0)
    q.CGEventSourceSecondsSinceLastEventType = event_age
    with patch.dict(sys.modules, {'Quartz': q}):
        modifiers = KeyboardVeto(300)
        check('a modifier-only press is a keystroke even with no ordinary key nearby', modifiers(np.zeros(8)) == 'a keystroke 40 ms ago')
        check('the Mac query includes modifier changes and excludes pointer motion', 12 in queried and 5 not in queried)
        rows = feed(recording([(1, 'snap'), (2, 'clap'), (2.2, 'clap')]), GestureDetector(veto=modifiers))
        check('modifier clicks cannot wake or complete a clap action', gestures(rows) == [])
        ages[12] = .02
        check('a modifier release refreshes the veto', modifiers(np.zeros(8)) == 'a keystroke 20 ms ago')
        ages[12] = .3
        check('a modifier outside the window does not suppress a deliberate snap', gestures(feed(recording([(1, 'snap')]), GestureDetector(veto=modifiers))) == ['snap'])
        ages[10], ages[12] = .01, .04
        check('the latest ordinary or modifier event supplies the keystroke age', modifiers(np.zeros(8)) == 'a keystroke 10 ms ago')
        key_log = logging.getLogger('ciel.audio.keys')
        key_log.addHandler(catch); key_log.setLevel(logging.INFO); catch.lines.clear()
        modifiers(np.zeros(8))
        check('input timing diagnostics are silent by default', not catch.lines)
        diagnostic = build_wake_detector(WakeConfig(mode='hotkey', snap=True), replace(GestureConfig(), log_candidates=True))
        diagnostic._members[1]._detector._veto(np.zeros(8))
        check('candidate logging includes event ages and the veto window', any('a keystroke 10 ms ago' in line and 'veto window 300 ms' in line for line in catch.lines))
        key_log.removeHandler(catch)

    # ── a second opinion that only says no ───────────────────────────────────

    asked: list[np.ndarray] = []

    def typing_veto(window: np.ndarray) -> str | None:
        asked.append(window)
        return 'typing 0.82'

    rows = feed(recording([(1, 'snap'), (1.5, 'tick'), (2, 'clap')]), GestureDetector(veto=typing_veto))
    vetoed = [r for r in rows if r.type == 'candidate' and r.kind == 'rejected' and 'sounds like typing 0.82' in r.reason]
    check('a vetoed snap and a vetoed clap are rejections that say what the room was doing',
          gestures(rows) == [] and len(vetoed) == 2)
    check('the veto is asked only about what the rules accepted, never about the tick', len(asked) == 2)
    check('the veto is shown one AudioSet frame of raw audio', all(len(w) == WINDOW_SAMPLES for w in asked))
    peak_at = int(np.abs(asked[0]).argmax())
    check('...ending 35 ms after the impulse, so the room around it is in the picture',
          WINDOW_SAMPLES - 35 * 16 - 80 <= peak_at <= WINDOW_SAMPLES - 35 * 16 + 80)
    rows = feed(recording([(1, 'snap'), (2, 'clap'), (2.2, 'clap')]), GestureDetector(veto=lambda w: None))
    check('a veto that hears nothing changes nothing', gestures(rows) == ['snap', 'double_clap'])
    second = iter([None, 'speech 0.95'])
    rows = feed(recording([(1, 'clap'), (1.2, 'clap')]), GestureDetector(veto=lambda w: next(second)))
    check('a vetoed second clap breaks the pair instead of completing it', gestures(rows) == ['clap'])
    check('the veto never finds a gesture the rules did not',
          gestures(feed(recording([(1, 'tick')]), GestureDetector(veto=lambda w: None))) == [])

    class FakeSession:
        def __init__(self, scores: dict[int, float]) -> None:
            self.scores = scores
            self.seen: list[int] = []

        def get_inputs(self):
            return [type('I', (), {'name': 'waveform'})()]

        def run(self, _outputs, feeds):
            self.seen.append(len(feeds['waveform']))
            row = np.zeros(521, dtype=np.float32)
            for i, v in self.scores.items():
                row[i] = v
            return [np.stack([row * 0.5, row])]  # two frames; the best one counts

    loud_typing = AudioSetVeto('yamnet', Path('/nonexistent'), 0.3, session=FakeSession({378: 0.82, 57: 0.4}))
    loud_typing.load()
    check('the model vetoes with the loudest of its room classes, by name and score',
          loud_typing(np.zeros(WINDOW_SAMPLES)) == 'typing 0.82' and loud_typing._session.seen == [WINDOW_SAMPLES])
    quiet = AudioSetVeto('yamnet', Path('/nonexistent'), 0.3, session=FakeSession({378: 0.29, 0: 0.1}))
    quiet.load()
    check('below the threshold it says nothing', quiet(np.zeros(WINDOW_SAMPLES)) is None)
    snappy = AudioSetVeto('yamnet', Path('/nonexistent'), 0.3, session=FakeSession({57: 0.9, 485: 0.9}))
    snappy.load()
    check('finger snapping and clicking are never grounds for a veto', snappy(np.zeros(WINDOW_SAMPLES)) is None)
    check('the veto classes are the room, not hands',
          set(VETO_CLASSES.values()) == {'speech', 'conversation', 'music', 'typing', 'computer keyboard'})
    with tempfile.TemporaryDirectory() as folder:
        bogus = Path(folder) / 'bogus.onnx'
        bogus.write_bytes(b'not a model')
        try:
            AudioSetVeto(str(bogus), Path(folder), 0.3).load()
        except Exception:  # noqa: BLE001 - onnxruntime's own refusal
            check('a custom path that is not a model is refused', True)
        else:
            check('a custom path that is not a model is refused', False)
        try:
            AudioSetVeto(str(Path(folder) / 'absent.onnx'), Path(folder), 0.3).load()
        except FileNotFoundError as exc:
            check('a custom path that does not exist says so', 'absent.onnx' in str(exc))
        else:
            check('a custom path that does not exist says so', False)
    check('the pretrained model is pinned by hash', len(YAMNET_SHA256) == 64)
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
