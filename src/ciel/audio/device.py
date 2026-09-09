"""Choose the room's paired microphone and speaker.

**One choice owns both directions.** The pair is decided here and nowhere
else, so a microphone and a speaker that were not meant for each other cannot
meet by accident. With Apple capture the speaker may be PortAudio on the same
default output: the canceller's reference is taken at the device, and the room
showed on 2026-09-07 that the engine's own far end gives Ciel a lisp. Imports
stay inside the factory so the hub can keep running without any Mac audio
library. The WebRTC pair uses the same speaker with a nonmuting device tap
and a separate echo processor; Apple voice processing is never opened there.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from ciel.config import AudioConfig

if TYPE_CHECKING:
    from ciel.audio.input import MicStream
    from ciel.audio.output import Player


def build_audio(config: AudioConfig, sample_rate: int, muted: Callable[[], bool] | None = None) -> tuple[MicStream, Player]:
    if config.backend == "webrtc":
        if sys.platform != "darwin":
            raise RuntimeError("audio.backend = webrtc requires macOS 14.2 or later")
        from ciel.audio.webrtc import WebRTCMic
        from ciel.audio.apple import SplitPlayer
        return WebRTCMic(config), SplitPlayer(config, sample_rate, muted)
    if config.backend == "apple":
        if sys.platform != "darwin":
            raise RuntimeError("audio.backend = apple requires macOS")
        if config.apple_playback not in ("portaudio", "engine"):
            raise ValueError("audio.apple_playback must be portaudio or engine")
        from ciel.audio.apple import AppleMic, ApplePlayer, AppleSession, SplitPlayer
        session = AppleSession(config, sample_rate)
        if config.apple_playback == "engine":
            return AppleMic(session), ApplePlayer(session, muted)
        return AppleMic(session), SplitPlayer(config, sample_rate, muted)
    if config.backend != "portaudio":
        raise ValueError("audio.backend must be portaudio, apple, or webrtc")
    from ciel.audio.input import MicStream
    from ciel.audio.output import Player
    return MicStream(config), Player(config, sample_rate, muted)
