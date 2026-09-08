"""Play identical generated speech through three routes, with explicit --live.

The fixture uses macOS say once at Piper's usual 22050 Hz rate, then plays the
same attenuated int16 PCM through PortAudio, a temporary helper with voice
processing off, and the production helper with it on. No model or runtime
state is read. Microphone frames are discarded; no microphone audio is saved.
The disabled helper exists only in temporary storage. Production has no option
to report protected capture with voice processing disabled. Listening, not
receipt success, determines whether the three routes sound different.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import AsyncIterator

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from ciel.audio import apple
from ciel.audio.output import Player
from ciel.config import AudioConfig


class DiagnosticSession(apple.AppleSession):
    """Validate an explicitly requested experimental mode outside production."""

    def __init__(self, binary: Path, enabled: bool) -> None:
        super().__init__(AudioConfig(backend='apple'), 22050)
        self.binary = binary
        self.enabled = enabled

    async def start(self) -> None:
        if self.proc is not None:
            self._healthy()
            return
        self.loop = asyncio.get_running_loop()
        self.ready = self.loop.create_future()
        self.proc = await asyncio.create_subprocess_exec(str(self.binary), '22050', 'min', 'false', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._stderr())
        try:
            await asyncio.wait_for(asyncio.shield(self.ready), 20)
            self._healthy()
        except BaseException:
            await self.close()
            raise

    async def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while True:
                kind, ident, count = apple._HEADER.unpack(await self.proc.stdout.readexactly(apple._HEADER.size))
                assert count <= apple._MAX_PAYLOAD
                payload = await self.proc.stdout.readexactly(count)
                if kind == apple._READY:
                    hello = json.loads(payload)
                    assert hello['echo_cancelled'] is self.enabled and hello['rate'] == 16000
                    self.formats = {name: hello[name] for name in ('input_rate', 'output_rate', 'mixer_rate')}
                    self.ready.set_result(None)
                elif kind == apple._CAPTURE:
                    pass
                elif kind == apple._PLAYED:
                    assert payload in (b'\x00', b'\x01')
                    future = self.pending.pop(ident, None)
                    if future is not None and not future.done():
                        future.set_result(payload == b'\x01')
                elif kind == apple._UNDERRUN:
                    self.underruns = json.loads(payload)['count']
                elif kind == apple._ERROR:
                    raise RuntimeError(payload.decode(errors='replace'))
                else:
                    raise RuntimeError('unexpected diagnostic protocol event')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed(str(exc) or 'diagnostic helper exited')


async def compare(tmp: Path) -> None:
    voice = tmp / 'sentence.wav'
    subprocess.run(['say', '-o', str(voice), '--data-format=LEI16@22050', 'Six crisp sentences. Susan sees seven shiny silver shells.'], check=True, timeout=20)
    with wave.open(str(voice), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 22050)
        pcm = (np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').astype(np.float32) * .25).astype('<i2').tobytes()
    print('One shared PCM fixture:', hashlib.sha256(pcm).hexdigest(), flush=True)
    off_dir = tmp / 'off-source'
    off_dir.mkdir()
    source = apple.SOURCE.read_text()
    source = source.replace('try input.setVoiceProcessingEnabled(true)', 'try input.setVoiceProcessingEnabled(false)')
    source = source.replace('input.isVoiceProcessingAGCEnabled = agc', 'if input.isVoiceProcessingEnabled { input.isVoiceProcessingAGCEnabled = agc }')
    source = source.replace('guard input.isVoiceProcessingEnabled && engine.outputNode.isVoiceProcessingEnabled else {', 'guard !input.isVoiceProcessingEnabled && !engine.outputNode.isVoiceProcessingEnabled else {')
    source = source.replace('input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: true, duckingLevel: level)', 'if input.isVoiceProcessingEnabled { input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: true, duckingLevel: level) }')
    source = source.replace('"echo_cancelled": true', '"echo_cancelled": input.isVoiceProcessingEnabled')
    (off_dir / 'CielAudio.swift').write_text(source)
    shutil.copy(apple.SOURCE.with_name('Info.plist'), off_dir / 'Info.plist')
    off = apple.ensure_built(off_dir / 'CielAudio.swift', tmp / 'off-bin')
    on = apple.ensure_built(bin_dir=tmp / 'on-bin')
    async def chunks() -> AsyncIterator[bytes]:
        yield pcm
    print('A: PortAudio', flush=True)
    async with Player(AudioConfig(), 22050) as player:
        assert await player.play(chunks())
    await asyncio.sleep(1)
    for label, binary, enabled in [('B: Apple engine, voice processing OFF', off, False), ('C: Apple engine, voice processing ON', on, True)]:
        session = DiagnosticSession(binary, enabled)
        try:
            async with apple.ApplePlayer(session) as player:
                print(label, session.formats, flush=True)
                assert await player.play(chunks())
                assert session.underruns == 0
                print('played to completion, no reported queue starvation', flush=True)
        finally:
            await session.close()
        await asyncio.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; the comparison opens microphone and speaker devices')
    with tempfile.TemporaryDirectory(prefix='ciel-speech-comparison-') as directory:
        asyncio.run(compare(Path(directory)))


if __name__ == '__main__':
    main()
