"""Probe nonducking echo capture without a microphone or runtime state.

Pins the signed native helper, paired channel mapping and resampling, atomic
build reuse and failed-build preservation; actual AEC3 removal of delayed stereo
echo, retention of independent speech, partial-block framing, and continuous
adaptation; configuration and split-speaker selection; real subprocess protocol,
startup denial, malformed frames, stopped reference, helper death, and cleanup;
paired gap recovery after callback contention, overflow, and timestamp jumps.
All executable fixtures and state live in a temporary directory. Acoustic room
quality and playback volume require a separate explicit live experiment.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ciel.audio import webrtc
from ciel.audio.apple import SplitPlayer
from ciel.audio.device import build_audio
from ciel.audio.webrtc import EchoProcessor, WebRTCMic, ensure_built
from ciel.config import AudioConfig, FRAME_BYTES, load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}", flush=True)
    if not ok:
        sys.exit(1)


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a.astype(float) ** 2)))


def process(a: np.ndarray, delay: int = 40, piece: int = 160) -> np.ndarray:
    processor = EchoProcessor(delay)
    frames: list[bytes] = []
    for start in range(0, len(a), piece):
        frames.extend(processor.push(a[start:start + piece].astype('<f4').tobytes()))
    return np.frombuffer(b''.join(frames), dtype='<i2').astype(float) / 32767


def dsp() -> None:
    rng = np.random.default_rng(227)
    n = 16000 * 15
    left = rng.normal(0, .13, n).astype(np.float32)
    right = rng.normal(0, .11, n).astype(np.float32)
    echo = np.zeros(n, dtype=np.float32)
    echo[640:] += .35 * left[:-640] + .24 * right[:-640]
    echo[1370:] += .1 * left[:-1370]
    a = np.column_stack((echo, left, right))
    out = process(a)
    attenuation = 20 * np.log10(rms(echo[-32000:]) / max(rms(out[-32000:]), 1e-12))
    check(f"AEC3 removes delayed stereo echo ({attenuation:.1f} dB)", attenuation > 12)
    t = np.arange(n) / 16000
    phase = 2 * np.pi * np.cumsum(140 + 25 * np.sin(2 * np.pi * .7 * t)) / 16000
    near = (sum(np.sin(phase * k) / k for k in range(1, 16)) * .18
            * (.15 + .85 * np.sin(2 * np.pi * 2.3 * t) ** 2)).astype(np.float32)
    near[:16000 * 9] = 0
    mixed = a.copy(); mixed[:, 0] += near
    clean = process(mixed)
    expected = process(np.column_stack((near, np.zeros(n), np.zeros(n))))
    # Compare with the same microphone high-pass path so phase rotation is
    # not mistaken for speech loss. This case has speech louder than echo;
    # it does not qualify whispered double talk under loud media.
    correlation = np.corrcoef(clean[-32000:], expected[-32000:])[0, 1]
    retained = rms(clean[-32000:]) / rms(expected[-32000:])
    check(f"voiced speech above the echo survives double talk (correlation {correlation:.2f})", correlation > .85 and .8 < retained < 1.2)
    check("echo removal improves the double-talk signal", rms(clean[-32000:] - expected[-32000:]) < rms(echo[-32000:]) * .8)
    quiet = process(np.column_stack((near * .01, np.zeros(n), np.zeros(n))))
    check("quiet speech without playback is not gated away", rms(quiet[-32000:]) > rms(near[-32000:] * .01) * .75)
    p = EchoProcessor(40)
    source = mixed[:16000]
    frames: list[bytes] = []
    for start in range(0, len(source), 137):
        frames.extend(p.push(source[start:start+137].astype('<f4').tobytes()))
    check("arbitrary capture chunks become exact thirty-millisecond frames", len(frames) == len(source) // 480 and all(len(f) == FRAME_BYTES for f in frames))
    check("partial chunks do not pad or restart the echo filter", np.array_equal(np.frombuffer(b''.join(frames), dtype='<i2'), (process(source) * 32767).round().astype('<i2')))
    for payload in (b'', b'x', bytes(webrtc.MAX_PAYLOAD + 12), np.full((160, 3), np.nan, dtype='<f4').tobytes()):
        try:
            EchoProcessor(40).push(payload)
        except ValueError:
            check("invalid reference data cannot become microphone audio", True)
        else:
            check("invalid reference data cannot become microphone audio", False)
    for delay in (-10, 5, 210, True):
        try:
            EchoProcessor(delay)
        except ValueError:
            check("capture hold rejects invalid timing", True)
        else:
            check("capture hold rejects invalid timing", False)


FAKE = '''import sys, struct, json, time
mode = MODE
h=struct.Struct('<II')
def send(k,b):
    sys.stdout.buffer.write(h.pack(k,len(b))+b); sys.stdout.buffer.flush()
if mode == 'denied':
    sys.stderr.write('System Audio Recording permission denied'); sys.exit(1)
if mode == 'oversize':
    sys.stdout.buffer.write(h.pack(2,999999)); sys.stdout.buffer.flush(); time.sleep(10)
hello={'rate':16000,'channels':3,'hardware_rate':48000,'nonmuting': mode != 'ducking'}
if mode == 'before_ready': send(2, bytes(5760))
if mode == 'gap_before_ready': send(3, b'discontinuity')
send(1,json.dumps(hello).encode())
if mode == 'duplicate': send(1,json.dumps(hello).encode())
if mode == 'odd': send(2,b'x')
if mode == 'nan': send(2,struct.pack('<f',float('nan'))*480)
if mode == 'unknown': send(9,b'x')
if mode == 'bad_gap': send(3,b'wrong')
if mode == 'stall': time.sleep(10)
send(2,struct.pack('<fff',.02,.0,.0)*480)
if mode == 'die':
    time.sleep(.05); sys.exit(1)
if mode == 'gap':
    send(2, struct.pack('<fff',.2,.1,-.1)*137)
    time.sleep(.1)
    send(3,b'discontinuity')
    send(2,struct.pack('<fff',.02,.0,.0)*960)
if mode == 'clock':
    time.sleep(.1); send(2,struct.pack('<fff',.02,.0,.0)*480)
sys.stdin.buffer.read()
'''


def helper(tmp: Path, mode: str) -> Path:
    p = tmp / ('helper-' + mode)
    p.write_text(f'#!{sys.executable}\n' + FAKE.replace('MODE', repr(mode)))
    p.chmod(0o700)
    return p


async def protocol(tmp: Path) -> None:
    with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=tmp):
        path = tmp / 'config.toml'
        path.write_text('[audio]\nbackend="webrtc"\nwebrtc_capture_delay_ms=60\n')
        config = load_config(path).audio
    check("WebRTC and its hold load through the normal config", config.backend == 'webrtc' and config.webrtc_capture_delay_ms == 60)
    mic, player = build_audio(config, 22050)
    check("the new pair retains the ordinary split speaker", isinstance(mic, WebRTCMic) and type(player) is SplitPlayer and mic.proc is None and player._stream is None)
    with patch.object(webrtc, 'ensure_built', return_value=helper(tmp, 'normal')):
        await mic.__aenter__()
    proc = mic.proc
    frames = mic.frames()
    check("ready requires actual protected frames, not just a format message", len(await asyncio.wait_for(anext(frames), 1)) == FRAME_BYTES)
    check("protected capture owns an active silent output clock", mic._clock.active)
    processor = mic.processor
    mic.drain(); player.stop()
    check("queue drains and Stop retain adaptive state and capture", mic.processor is processor and proc.returncode is None)
    await frames.aclose(); await player.close(); await mic.close(); await mic.close()
    check("closing capture reaps its helper and readers idempotently", proc.returncode is not None and mic.reader is None and mic.stderr is None)
    for mode in ('denied', 'oversize', 'ducking', 'before_ready', 'duplicate', 'odd', 'nan', 'unknown', 'stall', 'gap_before_ready', 'bad_gap'):
        mic = WebRTCMic(config)
        with patch.object(webrtc, 'ensure_built', return_value=helper(tmp, mode)), patch.object(webrtc, '_STARTUP_TIMEOUT', .3 if mode == 'stall' else 30):
            try:
                await mic.__aenter__()
            except (RuntimeError, asyncio.TimeoutError):
                check(f"{mode} startup leaves no unprotected mic or helper", mic._closed and mic.proc is None and mic.reader is None)
            else:
                await mic.close()
                check(f"{mode} startup leaves no unprotected mic or helper", False)
    mic = WebRTCMic(config)
    with patch.object(webrtc, 'ensure_built', return_value=helper(tmp, 'die')):
        await mic.__aenter__()
    await asyncio.wait_for(mic.reader, 1)
    check("helper death closes capture and discards pending audio", mic._closed and bool(mic.error) and not mic._queue)
    await mic.close()
    mic = WebRTCMic(config)
    with patch.object(webrtc, 'ensure_built', return_value=helper(tmp, 'clock')):
        await mic.__aenter__()
    mic._clock.active = False
    await asyncio.wait_for(mic.reader, 1)
    check("a lost output clock cannot leave unprotected capture alive", mic._closed and 'clock stopped' in mic.error)
    await mic.close()
    mic = WebRTCMic(config)
    with patch.object(webrtc, 'ensure_built', return_value=helper(tmp, 'gap')):
        await mic.__aenter__()
    processor, proc = mic.processor, mic.proc
    for _ in range(100):
        if mic.discontinuities:
            break
        await asyncio.sleep(.01)
    check("a paired timing gap retains the helper and open capture", mic.discontinuities == 1 and mic.proc is proc and proc.returncode is None and not mic._closed and mic.error is None)
    check("a timing gap replaces echo adaptation and delayed partial audio", mic.processor is not processor)
    expected = EchoProcessor(config.webrtc_capture_delay_ms).push(np.tile(np.array([.02, 0, 0], dtype='<f4'), (960, 1)).tobytes())
    frames = mic.frames()
    actual = [await asyncio.wait_for(anext(frames), 1) for _ in expected]
    check("post-gap audio matches a fresh protected stream with no queued pre-gap samples", actual == expected and not mic._queue)
    await frames.aclose(); await mic.close()
    for cfg in (AudioConfig(backend='webrtc', input_device=0), AudioConfig(backend='webrtc', output_device='Mac')):
        try: WebRTCMic(cfg)
        except ValueError: check("explicit devices cannot bypass the reference route", True)
        else: check("explicit devices cannot bypass the reference route", False)
    with patch('ciel.audio.device.sys.platform', 'linux'):
        try: build_audio(config, 22050)
        except RuntimeError: check("the hub cannot open a Mac capture backend", True)
        else: check("the hub cannot open a Mac capture backend", False)


class Clock:
    def __init__(self, **kwargs: object) -> None:
        self.active = False
    def start(self) -> None:
        self.active = True
    def stop(self) -> None:
        self.active = False
    def close(self) -> None:
        self.active = False


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-webrtc-probe-') as directory:
        tmp = Path(directory)
        binary = ensure_built(bin_dir=tmp / 'bin')
        check("the nonmuting native helper compiles and is private", binary.stat().st_mode & 0o777 == 0o700)
        inode = binary.stat().st_ino
        ensure_built(bin_dir=tmp / 'bin')
        check("unchanged capture source reuses its signed helper", binary.stat().st_ino == inode)
        result = subprocess.run([str(binary), '--self-test'], capture_output=True, text=True, timeout=10)
        check("native channel mapping and paired 44.1/48 kHz resampling preserve the reference", result.returncode == 0 and 'passed' in result.stdout)
        result = subprocess.run([str(binary), '--self-test-contention'], capture_output=True, text=True, timeout=10)
        check("native contention drops the paired callback and recovers at its timestamp gap", result.returncode == 0 and 'passed' in result.stdout)
        result = subprocess.run([str(binary), '--self-test-overflow'], capture_output=True, text=True, timeout=10)
        check("native overflow preserves channel pairing and marks recovery", result.returncode == 0 and 'passed' in result.stdout)
        result = subprocess.run([str(binary), '--self-test-invalid'], capture_output=True, text=True, timeout=10)
        check("a missing reference still ends capture instead of exposing raw microphone", result.returncode == 1 and 'missing reference' in result.stderr)
        broken = tmp / 'broken'; broken.mkdir()
        source = broken / 'CielCapture.swift'; source.write_text('not valid Swift')
        shutil.copy(webrtc.SOURCE.with_name('CaptureInfo.plist'), broken / 'CaptureInfo.plist')
        original = binary.read_bytes()
        try: ensure_built(source=source, bin_dir=tmp / 'bin')
        except RuntimeError: check("a failed build preserves the running helper", binary.read_bytes() == original)
        else: check("a failed build preserves the running helper", False)
        dsp()
        data = np.ones((64, 2), dtype=np.float32)
        webrtc._silence(data, 64, None, None)
        check("the idle clock renders digital silence", not data.any())
        with patch.object(webrtc.sd, 'query_devices', return_value={'default_samplerate':48000}), patch.object(webrtc.sd, 'OutputStream', side_effect=Clock):
            asyncio.run(protocol(tmp))
    print(f'\nall {len(CHECKS)} checks passed')


if __name__ == '__main__':
    main()
