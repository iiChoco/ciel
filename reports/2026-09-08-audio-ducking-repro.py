"""Reproduce the split pair's mute lifecycle without opening audio devices.

**The microphone outlives the speaker.** A temporary protocol helper stands in
for Apple, and the real spoke mute method operates on temporary state. This
pins that Stop, mute, and speaker close leave the helper alive until microphone
close. It cannot measure macOS attenuation or reproduce an audible dip.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from probe_apple_audio import helper
from ciel.audio import apple
from ciel.audio.apple import AppleMic, SplitPlayer
from ciel.audio.device import build_audio
from ciel.config import AudioConfig, FRAME_BYTES
from ciel.spoke.frontend import Spoke

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def reproduce(tmp: Path) -> None:
    mic, player = build_audio(AudioConfig(backend="apple"), 22050)
    check("the split still owns an Apple microphone", isinstance(mic, AppleMic) and type(player) is SplitPlayer)
    with patch.object(apple, "ensure_built", return_value=helper(tmp, "capture")):
        await mic.__aenter__()
    proc = mic.session.proc
    assert proc is not None
    frames = mic.frames()
    try:
        check("capture starts before Ciel plays anything", len(await asyncio.wait_for(anext(frames), 1)) == FRAME_BYTES and mic.session.next_id == 0)
        player.stop()
        check("Stop leaves the microphone helper running", proc.returncode is None and not mic.session.closing)
        spoke = SimpleNamespace(_muted=False, _mute_sentinel=tmp / "muted", _player=player, _link=Mock())
        Spoke._set_muted(spoke, True)
        check("the actual spoke mute leaves the microphone helper running", spoke._muted and spoke._mute_sentinel.exists() and proc.returncode is None and not mic.session.closing)
        mic.drain()
        mic.receive(bytes(FRAME_BYTES))
        check("the microphone still delivers frames while Ciel is muted", len(await asyncio.wait_for(anext(frames), 1)) == FRAME_BYTES)
        await player.close()
        check("closing the split speaker does not release Apple voice processing", proc.returncode is None and not mic.session.closing)
    finally:
        await frames.aclose()
        await player.close()
        await mic.close()
    check("closing the microphone finally reaps the helper", proc.returncode is not None)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ciel-ducking-repro-") as directory:
        asyncio.run(reproduce(Path(directory)))
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == "__main__":
    main()
