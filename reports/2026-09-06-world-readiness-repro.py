"""Reproduce the world-readiness assessment's fixture checks.

Every child receives a temporary home before importing Ciel. A filesystem
audit refuses access to the real runtime directory; no live service is used.
Run with: python3 reports/2026-09-06-world-readiness-repro.py
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    runtime = str(Path.home() / ".ciel")
    with tempfile.TemporaryDirectory(prefix="ciel-readiness-") as directory:
        fixture = Path(directory)
        guard = """import os, pathlib, sys
fixture_home = pathlib.Path(os.environ["CIEL_ASSESSMENT_FIXTURE"]) / str(os.getpid())
fixture_home.mkdir(exist_ok=True)
pathlib.Path.home = classmethod(lambda cls: fixture_home)
os.environ["HOME"] = str(fixture_home)
def guard(event, args):
    if event in ("open", "os.listdir", "os.scandir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.abspath(os.fsdecode(args[0]))
        runtime = os.environ["CIEL_ASSESSMENT_RUNTIME"]
        if path == runtime or path.startswith(runtime + os.sep):
            raise RuntimeError("probe attempted to access live runtime state")
sys.addaudithook(guard)
"""
        (fixture / "sitecustomize.py").write_text(guard)
        env = dict(os.environ, CIEL_ASSESSMENT_FIXTURE=directory,
                   CIEL_ASSESSMENT_RUNTIME=runtime,
                   PYTHONPATH=directory + os.pathsep + str(root / "src"))
        failed = False
        for name in ("world", "vigil", "closure", "turns"):
            result = subprocess.run(
                ["uv", "run", "--no-sync", "python", f"scripts/probe_{name}.py"],
                cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=120,
            )
            lines = result.stdout.strip().splitlines()
            print(f"{name}: " + (lines[-1] if result.returncode == 0 and lines else result.stdout), flush=True)
            failed = failed or result.returncode != 0
        return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
