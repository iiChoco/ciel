#!/usr/bin/env python
"""Standalone snap, clap, and double-clap tester using Ciel's microphone input.

    uv run --no-sync python scripts/listen_gestures.py
    uv run --no-sync python scripts/listen_gestures.py --csv /tmp/gestures.csv
    uv run --no-sync python scripts/listen_gestures.py --wav /tmp/hands.wav --json

**Measured cues.** The detector is Ciel's own, ``ciel.audio.gestures``, the
one the spoke listens with when ``wake.snap`` or ``wake.double_clap`` is on;
its boundaries are the ``[gestures]`` config, read off one MacBook microphone
on 2026-09-06, not from a new live evaluation. Both hand sounds can be very
short; duration alone did not separate them in that room.

**Visible misses.** Every impulse clearing the onset gate gets a candidate
row, even when rejected, with the failed boundaries. The first clap prints
immediately as pending. A second clap 60-800 ms later may be quieter and more
reverberant. It completes one double_clap gesture; otherwise one clap gesture
is released after the window. Candidate rows are observations, not controls.
--events-only hides candidate rows. --csv appends measurements, never audio.

**The same ears, a separate program.** Capture, the detector, and its
thresholds are Ciel's; nothing here changes Ciel's settings. Every
``[gestures]`` field is also a flag, so a boundary can be tried here before
it is written to config. Processing, printing, and CSV writes run outside
PortAudio's callback. --device overrides the configured mic. All detector timing uses audio samples, including replay;
wall-clock delays cannot turn one pair into another. Filtering retains state
across frames. Stay quiet for warmup, then try gestures, typing, and speech.

**Limits to compare.** Loud knocks can resemble claps; these thresholds are
specific to the supplied measurements. A diagnostic is not a promise of
recognition accuracy. WAVs must be 16 kHz mono int16 PCM with room sound for
warmup. A clipped final analysis window is discarded. Ctrl-C stops capture.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import wave
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import TextIO

from ciel.audio.gestures import GestureDetector, Observation
from ciel.config import FRAME_BYTES, SAMPLE_RATE, Config, GestureConfig, load_config

_FRAME = FRAME_BYTES // 2


class Reporter:
    """Print every gated impulse; optionally append owner-only CSV diagnostics."""

    def __init__(self, json_output: bool = False, events_only: bool = False, csv_file: TextIO | None = None) -> None:
        self.json_output, self.events_only = json_output, events_only
        self.csv_file = csv_file
        self.writer = csv.DictWriter(csv_file, fieldnames=[f.name for f in fields(Observation)]) if csv_file else None
        if self.writer and csv_file.tell() == 0:
            self.writer.writeheader()

    def emit(self, rows: list[Observation]) -> None:
        for row in rows:
            data = asdict(row)
            if self.writer:
                self.writer.writerow(data)
                self.csv_file.flush()
            if self.events_only and row.type != 'gesture':
                continue
            if self.json_output:
                print(json.dumps(data), flush=True)
            else:
                label = f'{row.type:9s} {row.kind:12s}'
                detail = row.reason
                if row.second_onset_s is not None:
                    detail = f'gap {row.second_onset_s - row.onset_s:.3f}s'
                print(f'{row.onset_s:8.3f}s  {label} peak {row.peak:.3f}  width {row.width_ms:5.2f} ms  '
                      f'tilt {row.tilt:6.2f}  fall {row.fall_db:6.1f} dB  {detail}', flush=True)


def replay(path: Path, detector: GestureDetector, reporter: Reporter) -> None:
    with wave.open(str(path), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (SAMPLE_RATE, 1, 2, 'NONE'):
            raise ValueError('WAV must be uncompressed 16000 Hz mono 16-bit PCM')
        while frame := source.readframes(_FRAME):
            if len(frame) == FRAME_BYTES:
                reporter.emit(detector.push(frame))
        reporter.emit(detector.finish())


async def listen(config: Config, detector: GestureDetector, reporter: Reporter) -> None:
    from ciel.audio.input import MicStream

    async with MicStream(config.audio) as mic:
        device = config.audio.input_device if config.audio.input_device is not None else 'system default'
        print(f"Ciel's MicStream ({device}); stay quiet for {detector.config.warmup_ms / 1000:g}s. Ctrl-C stops.", file=sys.stderr)
        announced = False
        async for frame in mic.frames():
            reporter.emit(detector.push(frame))
            if detector.ready and not announced:
                print('Ready: snap, clap, clap twice, and compare rejected sounds.', file=sys.stderr)
                announced = True
    raise RuntimeError('microphone capture ended; check the input device and restart')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, help="audio settings TOML; defaults to Ciel's config")
    parser.add_argument('--device', help='input device index or name fragment')
    parser.add_argument('--wav', type=Path, help='replay 16 kHz mono int16 PCM WAV')
    parser.add_argument('--json', action='store_true', help='JSON lines with candidate or gesture type')
    parser.add_argument('--events-only', action='store_true', help='print only completed gestures')
    parser.add_argument('--csv', type=Path, help='append all candidates and gestures as CSV (no audio)')
    defaults = GestureConfig()
    for field in fields(defaults):
        value = getattr(defaults, field.name)
        parser.add_argument('--' + field.name.replace('_', '-'), type=type(value), default=value,
                            help=f'gesture threshold (default: {value})')
    args = parser.parse_args(argv)
    csv_file = None
    try:
        config = load_config(args.config) if not args.wav else Config()
        if args.device is not None:
            device = int(args.device) if args.device.isdecimal() else args.device
            config = replace(config, audio=replace(config.audio, input_device=device))
        # A flag left at its default defers to the configured ear, so the
        # tester hears with the room's own thresholds unless told otherwise.
        overrides = {f.name: getattr(args, f.name) for f in fields(defaults)
                     if getattr(args, f.name) != getattr(defaults, f.name)}
        detector = GestureDetector(replace(config.gestures, **overrides))
        if args.csv:
            descriptor = os.open(args.csv, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            os.fchmod(descriptor, 0o600)
            csv_file = os.fdopen(descriptor, 'a', newline='')
        reporter = Reporter(args.json, args.events_only, csv_file)
        if args.wav:
            replay(args.wav, detector, reporter)
        else:
            asyncio.run(listen(config, detector, reporter))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, wave.Error) as exc:
        print(f'gesture listener: {exc}', file=sys.stderr)
        return 1
    finally:
        if csv_file:
            csv_file.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
