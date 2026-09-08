"""Measure a Python stall in Apple's rendered output, with explicit --live.

Builds an instrumented helper in temporary storage, plays a quiet generated
997 Hz tone, and reads only the mixer output written by that helper. Microphone
frames are drained and discarded, never saved. No runtime state or model is
read. The tap's file writing is diagnostic instrumentation, not production
capture code. Compare the former three-buffer window with the current bound
under the same 300 ms Python stall. This establishes a scheduling gap, not a
perceptual diagnosis of the user's reported lisp or acoustic echo rejection.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from ciel.audio import apple
from ciel.audio.device import build_audio
from ciel.config import AudioConfig


async def compare(tmp: Path) -> None:
    source = tmp / 'CielAudio.swift'
    instrumented = apple.SOURCE.read_text().replace('        engine.prepare()', '''        let diagnostic = FileHandle(forWritingAtPath: ProcessInfo.processInfo.environment["CIEL_RENDER_FILE"]!)!
        engine.mainMixerNode.installTap(onBus: 0, bufferSize: 480, format: outputFormat) { buffer, _ in
            diagnostic.write(Data(bytes: buffer.floatChannelData![0], count: Int(buffer.frameLength) * 4))
        }
        engine.prepare()''')
    source.write_text(instrumented)
    shutil.copy(apple.SOURCE.with_name('Info.plist'), tmp / 'Info.plist')
    binary = apple.ensure_built(source, tmp / 'bin')
    rate = 22050
    pcm = (np.sin(2 * np.pi * 997 * np.arange(rate * 3) / rate) * 1000).astype(np.int16).tobytes()
    gaps_by_window: dict[int, list[float]] = {}
    for window in (3, apple._PLAYBACK_WINDOW):
        output = tmp / f'render-{window}.f32'
        output.touch(mode=0o600)
        mic, player = build_audio(AudioConfig(backend='apple'), rate)
        async def chunks() -> AsyncIterator[bytes]:
            yield pcm
        async def consume() -> None:
            async for _ in mic.frames():
                pass
        async def stall() -> None:
            await asyncio.sleep(.4)
            time.sleep(.3)
        with patch.dict(os.environ, CIEL_RENDER_FILE=str(output)), patch.object(apple, 'ensure_built', return_value=binary), patch.object(apple, '_PLAYBACK_WINDOW', window):
            async with mic, player:
                consumer = asyncio.create_task(consume())
                await asyncio.sleep(.2)
                blocked = asyncio.create_task(stall())
                try:
                    completed = await player.play(chunks())
                    await blocked
                    await asyncio.sleep(.2)
                    formats = dict(mic.session.formats)
                    underruns = mic.session.underruns
                finally:
                    consumer.cancel()
                    blocked.cancel()
                    await asyncio.gather(consumer, blocked, return_exceptions=True)
        data = np.fromfile(output, dtype=np.float32)
        nonzero = np.flatnonzero(np.abs(data) > .0001)
        assert completed and len(nonzero), 'the instrumented engine must play the tone'
        core = data[nonzero[0]:nonzero[-1] + 1]
        edges = np.diff(np.r_[False, np.abs(core) < 1e-7, False].astype(int))
        lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
        render_rate = formats['mixer_rate']
        gaps = [round(float(n * 1000 / render_rate), 2) for n in lengths if n > render_rate / 1000]
        gaps_by_window[window] = gaps
        assert underruns == (1 if window == 3 else 0), 'native telemetry must distinguish the stalled window from continuous playback'
        print(f'window={window}, formats={formats}, rendered_seconds={len(core) / render_rate:.3f}, gaps_ms={gaps}, underruns={underruns}', flush=True)
    assert sum(gaps_by_window[3]) > 100, 'the former bound must reproduce an audible gap'
    assert not gaps_by_window[apple._PLAYBACK_WINDOW], 'the deeper bound must bridge the same stall'
    print('The former window gaps; the current window preserves continuous rendered audio.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='open the Mac audio devices and play two quiet tones')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this reproduction opens real audio devices')
    with tempfile.TemporaryDirectory(prefix='ciel-playback-repro-') as directory:
        asyncio.run(compare(Path(directory)))


if __name__ == '__main__':
    main()
