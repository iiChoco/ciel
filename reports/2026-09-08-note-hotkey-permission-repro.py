"""Read-only diagnostics for a stale Ciel Input Monitoring grant.

Inspect only the public application signature and OS permission mismatch
messages. No Ciel runtime directory, keyboard events, or note contents are read.
Run after the spoke has attempted to start its shortcut listener.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    app = Path.home() / "Projects/infrastructure/rendered/Ciel.app"
    subprocess.run(["codesign", "-dr", "-", str(app)], check=False)
    predicate = ('subsystem == "com.apple.TCC" AND '
                 'eventMessage CONTAINS "Failed to match existing code requirement" AND '
                 'eventMessage CONTAINS "ai.ciel.launcher" AND '
                 'eventMessage CONTAINS "kTCCServiceListenEvent"')
    subprocess.run(["/usr/bin/log", "show", "--last", "10m", "--info", "--debug",
                    "--style", "compact", "--predicate", predicate], check=False)


if __name__ == "__main__":
    main()
