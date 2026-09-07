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
URI; and the music door passes the URI as an argument, never as script.
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
from ciel.audio.gestures import GestureDetector, Measurement, Observation, classify
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
    check('a snap breaks a pending pair', kinds([(1, 'clap'), (1.2, 'snap'), (1.4, 'clap')]) == ['clap', 'snap', 'clap'])
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

    said = asyncio.run(music.play(URI, run=fake_osascript))
    check('the URI reaches AppleScript as an argument, never spliced into the script',
          calls[0][0] == 'osascript' and calls[0][-1] == URI and URI not in calls[0][2])
    check('what played comes back as a sentence', said == 'Bruno Mars — Uptown Funk')
    said = asyncio.run(music.play('not a uri', run=fake_osascript))
    check('a bad URI never starts a process', len(calls) == 1 and 'not a Spotify URI' in said)

    async def failing(argv: list[str]) -> tuple[int, str]:
        return 1, 'Spotify got an error'

    said = asyncio.run(music.play(URI, run=failing))
    check('a refusal from Spotify is reported, not raised', said.startswith('Spotify would not play'))
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
