"""Probe Apple's audio boundary without a microphone, model, or account.

Pins compiler-aware atomic builds, stable signing, cache cleanup, underrun
reporting, dependency errors, and the native bounded capture handoff; paired backend
selection, the default split pair (Apple capture, PortAudio speaker) and the
selectable engine route;
processed PCM repacking and draining; bounded playback through real subprocess
pipes, last-buffer receipts, stop and late receipts, cancellation and mute;
synthesis failures that leave capture alive; negotiated device rates;
invalid helper frames, denied startup, helper death and timeout cleanup; and
both voice loops' rejection of non-speech during Apple barge-in. All helper
builds and executable fixtures live in temporary storage. --live is a separate
opt-in device smoke check: processed capture and a quiet tone, never recordings.
It does not establish real-room echo reduction or user recognition accuracy.
"""
from __future__ import annotations

import argparse
import builtins
import json
import shutil
import asyncio
import contextlib
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.audio import apple
from ciel.audio.apple import AppleMic, ApplePlayer, AppleSession, SplitPlayer, ensure_built
from ciel.audio.device import build_audio
from ciel.audio.input import MicStream
from ciel.audio.output import Player
from ciel.config import AudioConfig, FRAME_BYTES, SAMPLE_RATE, load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


FAKE = r'''
import json, os, struct, sys, threading, time
mode = MODE
reported = False
header = struct.Struct('<BII')
lock = threading.Lock()
def send(kind, ident=0, data=b''):
    with lock:
        sys.stdout.buffer.write(header.pack(kind, ident, len(data)) + data)
        sys.stdout.buffer.flush()
if mode == 'error':
    send(6, 0, b'Microphone permission denied')
    sys.exit(1)
if mode == 'oversize':
    sys.stdout.buffer.write(header.pack(3, 0, 65537))
    sys.stdout.buffer.flush()
    time.sleep(10)
if mode == 'badready':
    send(5, 0, b'{"rate":48000,"echo_cancelled":false}')
    time.sleep(10)
if mode == 'badformats':
    send(5, 0, b'{"rate":16000,"echo_cancelled":true,"input_rate":48000,"output_rate":48000,"mixer_rate":0}')
    time.sleep(10)
if mode == 'unknown':
    send(99)
    time.sleep(10)
send(5, 0, b'{"rate":16000,"echo_cancelled":true,"input_rate":48000,"output_rate":48000,"mixer_rate":48000}')
if mode == 'odd':
    send(3, 0, b'x')
    time.sleep(10)
if mode == 'capture':
    send(3, 0, b'\x01\x00' * 600)
    send(3, 0, b'\x02\x00' * 360)
while True:
    raw = sys.stdin.buffer.read(9)
    if len(raw) != 9:
        break
    kind, ident, size = header.unpack(raw)
    payload = sys.stdin.buffer.read(size)
    if kind == 1:
        if mode in ('underrun', 'badunderrun') and not reported:
            reported = True
            send(9, ident, b'{"count":1,"gap_ms":150}' if mode == 'underrun' else b'{"count":1,"gap_ms":-1}')
        if mode == 'die':
            sys.exit(1)
        if mode != 'hang':
            timer = threading.Timer(.02, lambda ident=ident: send(4, ident, b'\x01'))
            timer.daemon = True
            timer.start()
'''


def helper(tmp: Path, mode: str) -> Path:
    path = tmp / f"helper-{mode}"
    path.write_text(f"#!{sys.executable}\n" + FAKE.replace("MODE", repr(mode)))
    path.chmod(0o700)
    return path


async def chunks(pcm: bytes, pieces: int = 1) -> AsyncIterator[bytes]:
    for _ in range(pieces):
        yield pcm


async def opened(tmp: Path, mode: str = 'normal') -> tuple[AppleMic, ApplePlayer]:
    mic, player = build_audio(AudioConfig(backend='apple', apple_playback='engine'), 16000)
    with patch.object(apple, 'ensure_built', return_value=helper(tmp, mode)):
        await mic.__aenter__()
        await player.__aenter__()
    return mic, player


async def probe(tmp: Path) -> None:
    config_path = tmp / 'audio.toml'
    config_path.write_text('[audio]\nbackend="apple"\napple_playback="engine"\napple_ducking="mid"\napple_agc=true\n')
    with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=tmp):
        configured = load_config(config_path).audio
        check('Apple settings load from the normal configuration file', configured.backend == 'apple' and configured.apple_playback == 'engine' and configured.apple_ducking == 'mid' and configured.apple_agc)
        check('the old speaker is the default Apple playback', AudioConfig().apple_playback == 'portaudio')
        with patch.dict(os.environ, {'CIEL_AUDIO_BACKEND': 'portaudio', 'CIEL_AUDIO_APPLE_AGC': 'false'}):
            overridden = load_config(config_path).audio
        check('environment overrides preserve typed Apple settings', overridden.backend == 'portaudio' and overridden.apple_agc is False)
    mic, player = build_audio(AudioConfig(), 16000)
    check('the existing backend remains the default', type(mic) is MicStream and type(player) is Player)
    mic, player = build_audio(AudioConfig(backend='apple'), 22050)
    check('the default Apple pair is the canceller hearing and the old speaker speaking', isinstance(mic, AppleMic) and type(player) is SplitPlayer)
    check('the split speaker owns no helper and opens no device', not hasattr(player, 'session') and player._stream is None and mic.session.proc is None)
    check('the split speaker still gates barge-in on processed speech', not player.accepts_barge(bytes(FRAME_BYTES)) and not player.accepts_barge(b'x'))
    mic, player = build_audio(AudioConfig(backend='apple', apple_playback='engine'), 22050)
    check('the engine route remains selectable for comparison', isinstance(mic, AppleMic) and isinstance(player, ApplePlayer) and mic.session is player.session)
    check('constructing the pair opens no device', mic.session.proc is None)
    check('Apple barge-in rejects silence before the energy gate can admit it', not player.accepts_barge(bytes(FRAME_BYTES)))
    check('Apple barge-in rejects malformed frames', not player.accepts_barge(b'x'))
    importer = builtins.__import__
    def without_vad(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == 'webrtcvad':
            raise ImportError('missing fixture')
        return importer(name, *args, **kwargs)
    with patch('builtins.__import__', side_effect=without_vad):
        try:
            build_audio(AudioConfig(backend='apple'), 16000)
            check('missing VAD names the spoke dependency and its repair command', False)
        except RuntimeError as exc:
            check('missing VAD names the spoke dependency and its repair command', 'webrtcvad-wheels' in str(exc) and 'uv sync --locked --all-extras' in str(exc))
    for config in (AudioConfig(backend='invalid'), AudioConfig(backend='apple', input_device=0), AudioConfig(backend='apple', output_device='Mac'), AudioConfig(backend='apple', apple_ducking='invalid'), AudioConfig(backend='apple', apple_playback='invalid')):
        try:
            build_audio(config, 16000)
            check('invalid audio choices are refused', False)
        except ValueError:
            check('invalid audio choices are refused', True)
    with patch('ciel.audio.device.sys.platform', 'linux'):
        try:
            build_audio(AudioConfig(backend='apple'), 16000)
            check('Apple audio cannot silently become raw audio on Linux', False)
        except RuntimeError:
            check('Apple audio cannot silently become raw audio on Linux', True)

    mic, player = await opened(tmp, 'capture')
    proc = mic.session.proc
    try:
        frames = mic.frames()
        a = await asyncio.wait_for(anext(frames), 1)
        b = await asyncio.wait_for(anext(frames), 1)
        check('arbitrary native buffers become exact 30 ms microphone frames', len(a) == len(b) == FRAME_BYTES)
        check('repacking preserves every sample in order', a + b == b'\x01\x00' * 600 + b'\x02\x00' * 360)
        mic.receive(b'\x03\x00' * 500)
        mic.drain()
        check('drain discards whole frames and the partial echo tail', not mic._queue and not mic.partial)
        for _ in range(105):
            mic.receive(bytes(FRAME_BYTES))
        check('a slow Python consumer has a bounded microphone queue', len(mic._queue) == 100 and mic._dropped == 5)
        mic.drain()
        with patch('ciel.audio.input.log.warning') as warning:
            for _ in range(180):
                mic.receive(bytes(FRAME_BYTES))
                await anext(frames)
        check('echo-cancelled silence does not report a broken microphone', not warning.called)
        await frames.aclose()
    finally:
        await player.close()
        await mic.close()
    check('closing the pair reaps the helper and its readers', proc.returncode is not None and mic.session.reader is None and mic.session.stderr is None)

    mic, player = build_audio(AudioConfig(backend='apple'), 16000)
    with patch.object(apple, 'ensure_built', return_value=helper(tmp, 'capture')):
        await mic.__aenter__()
    proc = mic.session.proc
    try:
        frames = mic.frames()
        check('the split pair captures processed frames without a player node', len(await asyncio.wait_for(anext(frames), 1)) == FRAME_BYTES)
        player.stop()
        check('the split speaker never sends the helper a playback command', mic.session.next_id == 0 and not mic.session.pending)
        await frames.aclose()
    finally:
        await player.close()
        await mic.close()
    check('closing the split pair reaps the helper', proc.returncode is not None and player._stream is None)

    mic, player = await opened(tmp)
    try:
        submitted: list[int] = []
        original = player.session.submit
        async def submit(pcm: bytes) -> asyncio.Future[bool]:
            future = await original(pcm)
            submitted.append(len(player.session.pending))
            return future
        player.session.submit = submit
        check('streaming playback waits for audible receipts including its short tail', await player.play(chunks(bytes(64002))))
        check('one second of playback is buffered within the twenty-chunk bound', max(submitted) == apple._PLAYBACK_WINDOW == 20 and not player.session.pending)
        check('the helper reports input and output rates independently', player.session.formats == {'input_rate': 48000, 'output_rate': 48000, 'mixer_rate': 48000})
        check('playback is no longer active after the final receipt', not player.is_playing)
        before = player.session.next_id
        player._muted = lambda: True
        check('mute reports completion without scheduling sound', await player.play(chunks(bytes(1600))) and before == player.session.next_id)
        player._muted = None
        check('an empty utterance needs no playback receipt', await player.play(chunks(b'')))
        task = asyncio.create_task(player.play(chunks(bytes(1600), 100)))
        await asyncio.sleep(.01)
        player.stop()
        check('stop releases pending playback without waiting for its receipts', not await asyncio.wait_for(task, 1) and not player.is_playing)
        check('a new utterance after stop survives late old receipts', await player.play(chunks(bytes(3200))))
        task = asyncio.create_task(player.play(chunks(bytes(1600), 100)))
        await asyncio.sleep(.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        check('task cancellation stops playback and leaves no pending buffers', not player.is_playing and not player.session.pending)
        check('the next utterance works after task cancellation', await player.play(chunks(bytes(1600))))
        entered = asyncio.Event()
        closed = asyncio.Event()
        async def stalled() -> AsyncIterator[bytes]:
            try:
                entered.set()
                await asyncio.Event().wait()
                yield b''
            finally:
                closed.set()
        task = asyncio.create_task(player.play(stalled()))
        await entered.wait()
        player.stop()
        check('stop also releases a synthesizer that has not produced audio', not await asyncio.wait_for(task, 1) and closed.is_set())
        try:
            await player.play(chunks(b'x'))
            check('partial PCM samples cannot corrupt the next utterance', False)
        except ValueError:
            check('partial PCM samples cannot corrupt the next utterance', not player.is_playing and not player.session.pending)
        check('a valid utterance works after malformed PCM', await player.play(chunks(bytes(1600))))
        for error_type in (RuntimeError, OSError, TimeoutError):
            failure = error_type("the synthesizer failed")
            async def broken() -> AsyncIterator[bytes]:
                yield bytes(1600)
                raise failure
            try:
                await player.play(broken())
                check(f'{error_type.__name__} from synthesis reaches the caller', False)
            except error_type as exc:
                check(f'{error_type.__name__} from synthesis reaches the caller', exc is failure)
            check(f'{error_type.__name__} from synthesis leaves the microphone alive', not mic._closed and player.session.error is None and not player.device_lost and not player.session.pending)
            check(f'playback recovers after a synthesis {error_type.__name__}', await player.play(chunks(bytes(1600))))
        await player._play_lock.acquire()
        queued = asyncio.create_task(player.play(chunks(bytes(1600))))
        await asyncio.sleep(0)
        player.stop()
        player._play_lock.release()
        check('a stop also cancels speech queued behind another utterance', not await queued)
    finally:
        await player.close()
        await mic.close()

    mic, player = await opened(tmp, 'underrun')
    try:
        with patch.object(apple.log, 'warning') as warning:
            completed = await player.play(chunks(bytes(3200)))
        check('an underrun is reported without ending protected listening', completed and not mic._closed and player.session.underruns == 1 and any('underrun' in call.args[0] for call in warning.call_args_list))
        check('another utterance starts with a fresh underrun count', await player.play(chunks(bytes(1600))) and player.session.underruns == 0)
    finally:
        await player.close()
        await mic.close()
    mic, player = await opened(tmp, 'badunderrun')
    try:
        check('malformed underrun telemetry cannot be accepted as healthy audio', not await player.play(chunks(bytes(1600))) and mic._closed)
    finally:
        await player.close()
        await mic.close()

    for mode in ('error', 'oversize', 'badready', 'badformats', 'unknown'):
        mic, player = build_audio(AudioConfig(backend='apple'), 16000)
        with patch.object(apple, 'ensure_built', return_value=helper(tmp, mode)):
            try:
                await mic.__aenter__()
                check(f'{mode} startup cannot open unprotected listening', False)
            except RuntimeError:
                check(f'{mode} startup cannot open unprotected listening', mic.session.proc is None and mic._closed)
    for mode in ('die', 'hang'):
        mic, player = await opened(tmp, mode)
        proc = mic.session.proc
        with patch.object(apple, '_IO_TIMEOUT', .05):
            check(f'{mode} playback fails visibly', not await player.play(chunks(bytes(1600))) and player.device_lost)
        check(f'{mode} failure closes capture rather than falling back', mic._closed and mic.session.error is not None)
        await player.close()
        await mic.close()
        check(f'{mode} helper is reaped on close', proc.returncode is not None)

    from probe_spoke import make_spoke
    from probe_turns import make_pipeline
    for label, state in [('spoke', make_spoke()), ('local', make_pipeline([]))]:
        if isinstance(state, tuple):
            state = state[0]
        state._config = replace(state._config, audio=replace(state._config.audio, barge_in=True))
        state._noise_floor = 0
        state._barge_run = 0
        class SpeechPlayer:
            is_playing = True
            stopped = False
            speech = False
            def accepts_barge(self, frame: bytes) -> bool:
                return self.speech
            def stop(self) -> None:
                self.stopped = True
        output = SpeechPlayer()
        loud = np.full(480, 25000, dtype=np.int16).tobytes()
        for _ in range(state._config.audio.barge_in_frames + 1):
            state._check_barge_in(loud, output)
        check(f'{label} ignores loud non-speech in processed audio', not output.stopped)
        output.speech = True
        for _ in range(state._config.audio.barge_in_frames):
            state._check_barge_in(loud, output)
        check(f'{label} still interrupts on sustained processed speech', output.stopped)


async def live(binary: Path) -> None:
    tone = (np.sin(2 * np.pi * 440 * np.arange(4410) / 22050) * 1000).astype(np.int16).tobytes()
    for playback in ('portaudio', 'engine'):
        mic, player = build_audio(AudioConfig(backend='apple', apple_playback=playback), 22050)
        with patch.object(apple, 'ensure_built', return_value=binary):
            async with mic, player:
                frames = mic.frames()
                frame = await asyncio.wait_for(anext(frames), 10)
                check(f'{playback}: the real Apple engine returns processed microphone frames', len(frame) == FRAME_BYTES)
                check(f'{playback}: a quiet tone plays to completion', await player.play(chunks(tone)))
                await frames.aclose()


def probe_builds(tmp: Path, binary: Path) -> None:
    bin_dir = binary.parent
    old = bin_dir / 'cielaudio-0123456789abcdef'
    unrelated = bin_dir / 'cielaudio-not-ours'
    linked = bin_dir / 'cielaudio-fedcba9876543210'
    old.write_bytes(b'obsolete')
    unrelated.write_bytes(b'keep')
    linked.symlink_to(unrelated)
    ensure_built(bin_dir=bin_dir)
    check('cache cleanup removes only owned hash-named files', not old.exists() and unrelated.read_bytes() == b'keep' and linked.is_symlink())
    stamp = bin_dir / 'cielaudio.build.json'
    check('the executable path and build manifest are stable and private', binary.name == 'cielaudio' and stamp.stat().st_mode & 0o777 == 0o600)
    signature = subprocess.run(['codesign', '-d', '--verbose=2', str(binary)], capture_output=True, text=True, timeout=10)
    check('rebuilds use the same explicit code-signing identifier', signature.returncode == 0 and 'Identifier=ai.ciel.audio' in signature.stderr)
    original_run = subprocess.run
    builds: list[list[str]] = []
    def newer_compiler(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = original_run(command, **kwargs)
        if command[1:] == ['--version']:
            result.stdout += '\nprobe toolchain revision'
        if '-O' in command:
            builds.append(command)
        return result
    original_inode = binary.stat().st_ino
    with patch.object(apple.subprocess, 'run', side_effect=newer_compiler):
        rebuilt = ensure_built(bin_dir=bin_dir)
        check('a compiler change atomically replaces the executable at the same path', rebuilt == binary and len(builds) == 1 and binary.stat().st_ino != original_inode)
        ensure_built(bin_dir=bin_dir)
        check('the new compiler fingerprint is reused on the next startup', len(builds) == 1)
    broken = tmp / 'broken'
    broken.mkdir()
    source = broken / 'CielAudio.swift'
    source.write_text('this is not valid Swift')
    shutil.copy(apple.SOURCE.with_name('Info.plist'), broken / 'Info.plist')
    previous = binary.read_bytes(), stamp.read_bytes()
    try:
        ensure_built(source=source, bin_dir=bin_dir)
        check('a failed rebuild preserves the last working executable and manifest', False)
    except RuntimeError:
        check('a failed rebuild preserves the last working executable and manifest', previous == (binary.read_bytes(), stamp.read_bytes()))
    check('a failed rebuild leaves no partial compiler output', not list(bin_dir.glob('.cielaudio-*')) or {p.name for p in bin_dir.glob('.cielaudio-*')} == {'.cielaudio-build.lock'})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='ciel-apple-probe-') as directory:
        tmp = Path(directory)
        binary = ensure_built(bin_dir=tmp / 'bin')
        check('the Apple helper compiles using only the system toolchain', binary.is_file())
        check('the compiled helper is owner-only', binary.stat().st_mode & 0o777 == 0o700)
        check('unchanged native code reuses its binary', ensure_built(bin_dir=tmp / 'bin') == binary)
        result = subprocess.run([str(binary), '--self-test'], capture_output=True, text=True, timeout=10)
        check('native capture storage and 44.1/48 kHz conversion preserve the microphone contract', result.returncode == 0 and 'passed' in result.stdout)
        contention = subprocess.run([str(binary), '--self-test-contention'], capture_output=True, timeout=10)
        check('native lock contention fails visibly instead of dropping microphone audio', contention.returncode == 1 and b'lock contention' in contention.stdout)
        check('native underrun accounting distinguishes starvation from end and restart', b'playback continuity' in result.stdout.encode())
        if args.live:
            asyncio.run(live(binary))
        else:
            probe_builds(tmp, binary)
            asyncio.run(probe(tmp))
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
