"""Barn Door can measure its decisions without taking away the conversation.

Synthetic embeddings pin parity with the enforcing policy, duration and grace
thresholds, and the difference between bypassing and earning trust. Missing
profiles, unavailable models, inference failures, invalid measurements, and
failed diagnostic writes cannot silence the user. Records are bounded, private,
strict JSON without speech or embeddings; diagnostic rejections save no clips
and never alter enrollment. The actual local and spoke handlers carry a
would-reject utterance to transcription and onward only in diagnostic mode.
All state is temporary; no microphone, model, account, or network is used.

    uv run --no-sync python scripts/probe_speaker.py
"""
from __future__ import annotations

import asyncio
import json
import math
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ciel.audio.speaker import SpeakerGate, build_speaker_gate, save_profile
from ciel.config import SAMPLE_RATE, VoiceConfig, load_config

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


class Encoder:
    def __init__(self) -> None:
        self.score = 0.0
        self.error: Exception | None = None
        self.cancel = False
        self.nan = False
        self.calls = 0

    async def warm_up(self) -> None:
        pass

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        self.calls += 1
        if self.cancel:
            raise asyncio.CancelledError()
        if self.error is not None:
            raise self.error
        if self.nan:
            return np.array([float('nan'), 0.0], dtype=np.float32)
        return np.array([self.score, math.sqrt(1 - self.score ** 2)], dtype=np.float32)

    async def close(self) -> None:
        pass


def readings(path: Path) -> list[dict]:
    def invalid(value: str) -> None:
        raise ValueError(value)
    return [json.loads(line, parse_constant=invalid) for line in path.read_text().splitlines()]


async def gate_for(cfg: VoiceConfig) -> tuple[SpeakerGate, Encoder]:
    encoder = Encoder()
    gate = SpeakerGate(cfg, encoder)
    await gate.warm_up()
    return gate, encoder


async def probe_policy(tmp: Path, cfg: VoiceConfig) -> None:
    print("one judgement, two delivery modes")
    diagnostic, encoder = await gate_for(cfg)
    enforcing, other = await gate_for(replace(cfg, diagnostic=False, keep_rejected=0))
    saved_profile = cfg.profile.read_bytes()
    long = np.zeros(3 * SAMPLE_RATE, dtype=np.float32)
    short = np.zeros(int(0.6 * SAMPLE_RATE), dtype=np.float32)
    blip = np.zeros(int(0.2 * SAMPLE_RATE), dtype=np.float32)
    for label, score, pcm in (("a stranger", 0.0, long), ("a borderline long command", 0.6, long),
                              ("a borderline short command", 0.6, short), ("a brief follow-up", 0.0, blip),
                              ("the enrolled voice", 1.0, long), ("another speaker mid-conversation", 0.0, long)):
        encoder.score = other.score = score
        allowed, _ = await diagnostic.check(pcm)
        enforced, _ = await enforcing.check(pcm)
        check(f"{label} has the same judgement in either mode",
              allowed and diagnostic.last_decision == enforcing.last_decision
              and enforced == diagnostic.last_decision.would_accept)
    rows = readings(cfg.diagnostic_file)
    check("a bypassed rejection does not open the grace window", not rows[1]['recent'])
    check("the short command records the effective threshold", abs(rows[2]['threshold'] - 0.58) < 1e-8)
    check("a scored acceptance opens the diagnostic grace window", rows[3]['recent'] and rows[3]['reason'] == 'short_grace')
    check("the grace margin is recorded on a long follow-up", abs(rows[4]['threshold'] - 0.65) < 1e-8)
    check("a short contextual acceptance has no invented similarity", rows[3]['similarity'] is None)
    check("the best reference is named without exposing its embedding", rows[4]['best_reference'] == 'take 1')
    check("zero samples are visible as capture measurements",
          rows[0]['duration_ms'] == 3000 and rows[0]['rms'] == 0 and rows[0]['zero_fraction'] == 1)
    check("diagnostic mode neither saves rejected clips nor alters enrollment",
          not (tmp / 'rejected').exists() and cfg.profile.read_bytes() == saved_profile)
    prior = diagnostic._last_pass
    await diagnostic.check(blip)
    check("contextual permission does not renew the verified grace clock", diagnostic._last_pass == prior)
    diagnostic._last_pass = time.monotonic() - cfg.recent_window_s - 1
    allowed, score = await diagnostic.check(blip)
    check("a cold blip continues but records that the gate would reject it",
          allowed and math.isnan(score) and diagnostic.last_decision.reason == 'too_short'
          and diagnostic.last_decision.would_accept is False)
    encoder.score = 1.0
    await diagnostic.check(long)
    encoder.score = 0.0
    await diagnostic.check(short)
    row = readings(cfg.diagnostic_file)[-1]
    check("short and recent leniencies do not add together",
          row['recent'] and abs(row['threshold'] - 0.58) < 1e-8)
    await diagnostic.check(np.tile(np.array([0.0, 0.5, 1.0, -1.0], dtype=np.float32), SAMPLE_RATE))
    row = readings(cfg.diagnostic_file)[-1]
    check("clipping and peak measurements expose a saturated capture",
          row['peak'] == 1.0 and row['clipped_fraction'] == 0.5 and row['zero_fraction'] == 0.25)
    check("diagnostic records contain no audio, transcript, or profile vectors",
          set(row) == {'at', 'mode', 'allowed', 'would_accept', 'reason', 'similarity', 'base_threshold',
                       'threshold', 'short_discount', 'recent_discount', 'recent', 'best_reference',
                       'duration_ms', 'rms', 'peak', 'clipped_fraction', 'zero_fraction', 'finite_audio'})
    await diagnostic.close()
    await enforcing.close()


async def probe_failures(tmp: Path, cfg: VoiceConfig) -> None:
    print("an unavailable measurement never means recognized")
    pcm = np.zeros(SAMPLE_RATE, dtype=np.float32)
    gate, encoder = await gate_for(cfg)
    encoder.error = RuntimeError('fixture private error detail')
    allowed, score = await gate.check(pcm)
    row = readings(cfg.diagnostic_file)[-1]
    check("an inference failure lets speech through as unavailable",
          allowed and math.isnan(score) and row['would_accept'] is None and row['reason'] == 'encoder_error')
    check("error details stay out of diagnostic readings", 'fixture private error detail' not in cfg.diagnostic_file.read_text())
    check("an unavailable measurement cannot earn grace", gate._last_pass is None)
    encoder.error = None
    encoder.nan = True
    await gate.check(pcm)
    row = readings(cfg.diagnostic_file)[-1]
    check("non-finite embeddings produce strict JSON with no score", row['reason'] == 'invalid_embedding' and row['similarity'] is None)
    encoder.nan = False
    before = encoder.calls
    await gate.check(np.full(SAMPLE_RATE, float('nan'), dtype=np.float32))
    row = readings(cfg.diagnostic_file)[-1]
    check("invalid audio is reported without running the encoder",
          encoder.calls == before and row['reason'] == 'invalid_audio' and not row['finite_audio'] and row['rms'] is None)
    missing, _ = await gate_for(replace(cfg, profile=tmp / 'missing.npz'))
    allowed, score = await missing.check(pcm)
    check("a missing profile records unavailability rather than a match",
          allowed and math.isnan(score) and missing.last_decision.reason == 'no_profile'
          and readings(cfg.diagnostic_file)[-1]['would_accept'] is None)
    with patch.dict(sys.modules, {'sherpa_onnx': None}):
        missing_model = build_speaker_gate(cfg)
        await missing_model.warm_up()
        allowed, _ = await missing_model.check(pcm)
        check("a missing voice dependency still leaves diagnostic readings",
              allowed and missing_model.last_decision.reason == 'model_unavailable'
              and not cfg.model.exists())
        check("the disabled factory stays disabled even with diagnostics selected",
              build_speaker_gate(replace(cfg, enabled=False)) is None)
        await missing_model.close()
    await missing.close()
    encoder.cancel = True
    cancelled = False
    try:
        await gate.check(pcm)
    except asyncio.CancelledError:
        cancelled = True
    check("diagnostics preserve cancellation", cancelled)
    await gate.close()
    enforcing, encoder = await gate_for(replace(cfg, diagnostic=False, keep_rejected=0))
    encoder.error = RuntimeError('fixture')
    raised = False
    try:
        await enforcing.check(pcm)
    except RuntimeError:
        raised = True
    check("enforcing mode keeps its existing inference-failure behavior", raised)
    await enforcing.close()


async def probe_storage(tmp: Path, cfg: VoiceConfig) -> None:
    print("private and bounded readings")
    gate, _ = await gate_for(replace(cfg, diagnostic_max_bytes=4096))
    cfg.diagnostic_file.chmod(0o644)
    for _ in range(12):
        await gate.check(np.zeros(SAMPLE_RATE, dtype=np.float32))
    check("the diagnostic file is owner-only even when it already existed", cfg.diagnostic_file.stat().st_mode & 0o777 == 0o600)
    check("a full file starts a bounded window of complete JSON records",
          cfg.diagnostic_file.stat().st_size <= 4096 and 0 < len(readings(cfg.diagnostic_file)) < 12)
    await gate.close()
    target = tmp / 'untouched.txt'
    target.write_text('untouched')
    link = tmp / 'linked.jsonl'
    link.symlink_to(target)
    bad, _ = await gate_for(replace(cfg, diagnostic_file=link))
    allowed, _ = await bad.check(np.zeros(SAMPLE_RATE, dtype=np.float32))
    check("a diagnostic symlink cannot overwrite its target or block speech", allowed and target.read_text() == 'untouched')
    await bad.close()
    bad, _ = await gate_for(replace(cfg, diagnostic_file=target / 'readings.jsonl'))
    allowed, _ = await bad.check(np.zeros(SAMPLE_RATE, dtype=np.float32))
    check("an unwritable diagnostic destination does not block speech", allowed and bad.last_decision.would_accept is False)
    await bad.close()


async def probe_lanes(cfg: VoiceConfig) -> None:
    from probe_spoke import FakeMic, FakeSTT, make_spoke
    from probe_turns import FakePlayer, make_pipeline

    print("the real voice handlers use diagnostic permission")
    for diagnostic in (False, True):
        gate, _ = await gate_for(replace(cfg, diagnostic=diagnostic, keep_rejected=0))
        spoke = make_spoke(stt='A complete fixture sentence.')
        try:
            spoke._speaker = gate
            await spoke._handle_utterance(np.zeros(SAMPLE_RATE, dtype=np.float32))
            check(f"the spoke {'sends' if diagnostic else 'drops'} a would-reject utterance",
                  bool(spoke._link.says) == diagnostic and gate.last_decision.would_accept is False)
        finally:
            shutil.rmtree(spoke._config.state_dir)
        pipeline = make_pipeline([('reply', 'Fixture reply.')])
        pipeline._speaker = gate
        pipeline._stt = FakeSTT('A complete fixture sentence.')
        await pipeline._handle_turn(np.zeros(SAMPLE_RATE, dtype=np.float32), FakePlayer(), FakeMic())
        check(f"the local pipeline {'answers' if diagnostic else 'drops'} a would-reject utterance",
              bool(pipeline._brain.prompts) == diagnostic and gate.last_decision.would_accept is False)
        await gate.close()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix='ciel-speaker-probe-') as directory:
        tmp = Path(directory)
        profile = tmp / 'profile.npz'
        save_profile(profile, [np.array([1.0, 0.0], dtype=np.float32)], threshold=0.7)
        config_path = tmp / 'config.toml'
        config_path.write_text('[voice]\nenabled = true\ndiagnostic = true\n'
                               f'diagnostic_file = "{tmp / "diagnostics.jsonl"}"\n'
                               f'profile = "{profile}"\nmodel = "{tmp / "unused.onnx"}"\n'
                               'diagnostic_max_bytes = 100000\n')
        cfg = load_config(config_path).voice
        check("diagnostic mode is opt-in", not VoiceConfig().diagnostic)
        check("diagnostic configuration loads paths and limits from TOML",
              cfg.enabled and cfg.diagnostic and cfg.diagnostic_file == tmp / 'diagnostics.jsonl'
              and cfg.diagnostic_max_bytes == 100000)
        await probe_policy(tmp, cfg)
        await probe_failures(tmp, cfg)
        await probe_storage(tmp, cfg)
        await probe_lanes(cfg)
    print(f"\nall {len(CHECKS)} checks passed")


if __name__ == '__main__':
    asyncio.run(main())
