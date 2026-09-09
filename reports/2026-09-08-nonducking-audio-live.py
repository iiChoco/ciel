"""Compare unchanged speaker gain and live AEC3 on the Mac, only with --live.

Temporarily stops the launchd spoke and restores it in finally. Plays a quiet
repeatable signal and compares its measured microphone amplitude with and
without the tap. Checks the real paired capture and echo processing in the
same run, then pauses the helper to verify timing-gap recovery. Captured audio stays in memory and is discarded; no runtime config,
recordings, tokens, or models are read. This cannot qualify the owner's voice,
whispered double talk, gestures, or arbitrary audio accessories.
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
import os
import signal as signals
from pathlib import Path
from unittest.mock import patch

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ciel.audio import webrtc
from ciel.audio.webrtc import WebRTCMic
from ciel.config import AudioConfig


def tone_level(samples: np.ndarray, rate: int) -> float:
    y = samples[-rate*2:].reshape(-1)
    t = np.arange(len(y)) / rate
    return float(2 * abs(np.mean(y * np.exp(-2j * np.pi * 1300 * t))))


async def run(binary: Path) -> None:
    rate, seconds = 48000, 8
    t = np.arange(rate * seconds) / rate
    rng = np.random.default_rng(332)
    signal = (.022 * np.sin(2 * np.pi * 1300 * t)
              + np.convolve(rng.normal(0, .05, len(t)), np.ones(5)/5, mode='same')).astype(np.float32)
    signal[:9600] *= np.arange(9600) / 9600
    # The tap must start in a genuinely idle room, before a playback stream
    # gives the output device a clock. This was missed by the first A/B test.
    with patch.object(webrtc, 'ensure_built', return_value=binary):
        async with WebRTCMic(AudioConfig(backend='webrtc')) as cold:
            frames = cold.frames()
            if len(await asyncio.wait_for(anext(frames), 3)) != 960:
                raise RuntimeError('Idle capture did not produce a full protected frame')
            await frames.aclose()
    print('Cold start before playback passed', flush=True)
    baseline = await asyncio.to_thread(sd.playrec, signal, rate, channels=1, blocking=True)
    raw: list[np.ndarray] = []
    clean: list[bytes] = []
    mic = WebRTCMic(AudioConfig(backend='webrtc'))
    original = webrtc.EchoProcessor.push
    def observe(processor: webrtc.EchoProcessor, payload: bytes) -> list[bytes]:
        raw.append(np.frombuffer(payload, dtype='<f4').reshape(-1,3).copy())
        frames = original(processor, payload)
        clean.extend(frames)
        return frames
    async def discard_frames() -> None:
        async for _ in mic.frames():
            pass
    with patch.object(webrtc, 'ensure_built', return_value=binary), patch.object(webrtc.EchoProcessor, 'push', observe):
        async with mic:
            raw.clear(); clean.clear()
            consumer = asyncio.create_task(discard_frames())
            try:
                active = await asyncio.to_thread(sd.playrec, signal, rate, channels=1, blocking=True)
                if mic.error:
                    raise RuntimeError(mic.error)
                # Preserve the acoustic comparison before introducing a gap.
                measured_raw, measured_clean = list(raw), list(clean)
                proc, prior_gaps = mic.proc, mic.discontinuities
                os.kill(proc.pid, signals.SIGSTOP)
                try:
                    await asyncio.sleep(.3)
                finally:
                    os.kill(proc.pid, signals.SIGCONT)
                for _ in range(300):
                    if mic.error or mic.discontinuities > prior_gaps:
                        break
                    await asyncio.sleep(.01)
                if mic.error or mic.discontinuities <= prior_gaps or mic.proc is not proc or proc.returncode is not None:
                    raise RuntimeError('Capture pause did not recover in the same helper')
                before = len(clean)
                await asyncio.sleep(.2)
                if len(clean) <= before:
                    raise RuntimeError('Recovered capture did not resume protected frames')
                print('Forced capture gap recovered in the same helper; protected frames resumed', flush=True)
            finally:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
    change = 20 * np.log10(tone_level(active, rate) / tone_level(baseline, rate))
    a = np.concatenate(measured_raw)
    y = np.frombuffer(b''.join(measured_clean), dtype='<i2').astype(float) / 32767
    attenuation = 20 * np.log10(np.sqrt(np.mean(a[-32000:,0]**2)) / max(np.sqrt(np.mean(y[-32000:]**2)), 1e-12))
    print(f'Speaker amplitude change with tap: {change:+.2f} dB', flush=True)
    print(f'Live raw-to-clean microphone reduction: {attenuation:.1f} dB', flush=True)
    print(f'Capture ended cleanly: {mic.error is None and mic.proc is None}', flush=True)
    if abs(change) > 1.5 or attenuation < 10 or mic.error:
        raise RuntimeError('Live comparison did not meet unchanged playback and useful echo reduction')
    print('All 6 live checks passed; audio discarded', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required: this stops/restores the spoke and plays a quiet test signal')
    with tempfile.TemporaryDirectory(prefix='ciel-nonducking-live-') as directory:
        binary = webrtc.ensure_built(bin_dir=Path(directory) / 'bin')
        domain = f'gui/{os.getuid()}'
        plist = Path.home() / 'Library/LaunchAgents/ai.ciel.spoke.plist'
        subprocess.run(['launchctl', 'bootout', domain + '/ai.ciel.spoke'], check=True)
        try:
            async def settled() -> None:
                await asyncio.sleep(1)
                await run(binary)
            asyncio.run(settled())
        finally:
            sd.stop()
            subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
            print('Spoke service restored', flush=True)


if __name__ == '__main__':
    main()
