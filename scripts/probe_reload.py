"""Probe Analytic Continuation's watcher — the source watcher and its deadline.

A temporary source tree, a short poll, no process replaced. Pins: an edit
under a watched root is seen and named; a touched sentinel file is seen;
nothing is seen when nothing changed; the change is stamped; with a grace
and a stuck callback the callback is called once after the grace and not
before; without a grace it is never called; closing the watcher inside the
grace cancels the call.

    uv run --no-sync python scripts/probe_reload.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ciel.reload import SourceWatcher

CHECKS: list[str] = []


def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)


async def settle(watcher: SourceWatcher, seconds: float = 0.3) -> None:
    await asyncio.sleep(seconds)


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ciel-reload-probe-") as tmp:
        root = Path(tmp)
        package = root / "pkg"
        package.mkdir()
        module = package / "a.py"
        module.write_text("x = 1\n")
        sentinel = root / "reload"

        print("seeing a change")
        watcher = SourceWatcher([package, sentinel], poll_s=0.05)
        await watcher.start()
        await settle(watcher)
        check("nothing changed, nothing seen", watcher.changed is None and watcher.changed_at is None)
        module.write_text("x = 2\n")
        # A same-second rewrite keeps the mtime on some filesystems; move it.
        later = time.time() + 5
        import os
        os.utime(module, (later, later))
        await settle(watcher)
        check("an edit under a watched root is seen and named", watcher.changed == module)
        check("the change is stamped on the monotonic clock", watcher.changed_at is not None and watcher.changed_at <= time.monotonic())
        await watcher.close()

        watcher = SourceWatcher([package, sentinel], poll_s=0.05)
        await watcher.start()
        sentinel.touch()
        await settle(watcher)
        check("a touched sentinel is seen", watcher.changed == sentinel)
        await watcher.close()

        print("\nthe deadline")
        calls: list[float] = []
        watcher = SourceWatcher([package], poll_s=0.05, grace_s=0.4, stuck=lambda: calls.append(time.monotonic()))
        await watcher.start()
        module.write_text("x = 3\n")
        later = time.time() + 10
        os.utime(module, (later, later))
        await settle(watcher, 0.2)
        seen_at = watcher.changed_at
        check("inside the grace the callback has not been called", watcher.changed == module and calls == [])
        await settle(watcher, 0.5)
        check("past the grace it is called once, after the grace", len(calls) == 1 and seen_at is not None and calls[0] - seen_at >= 0.4)
        await settle(watcher, 0.5)
        check("and never again", len(calls) == 1)
        await watcher.close()

        calls.clear()
        watcher = SourceWatcher([package], poll_s=0.05, stuck=lambda: calls.append(time.monotonic()))
        await watcher.start()
        module.write_text("x = 4\n")
        later = time.time() + 15
        os.utime(module, (later, later))
        await settle(watcher, 0.6)
        check("without a grace the callback is never called", watcher.changed == module and calls == [])
        await watcher.close()

        watcher = SourceWatcher([package], poll_s=0.05, grace_s=0.3, stuck=lambda: calls.append(time.monotonic()))
        await watcher.start()
        module.write_text("x = 5\n")
        later = time.time() + 20
        os.utime(module, (later, later))
        await settle(watcher, 0.15)
        await watcher.close()
        await settle(watcher, 0.5)
        check("closing the watcher inside the grace cancels the call", watcher.changed == module and calls == [])

    print(f"\nall {len(CHECKS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
