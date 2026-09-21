"""Probe the hub's imports — would it start on a machine with no Mac in it?

    uv run --no-sync python scripts/probe_hub_imports.py

The hub is meant for a Linux server: no sound device, no VAD library,
no Metal, no pyobjc, no EventKit. This runs a child interpreter with an
import hook that refuses every one of those modules by name — the
audio stack, the Mac frameworks, the speech models — and then imports
the hub's modules and constructs ``Pipeline(config, role="hub")`` with a
throwaway state directory. Construction opens no task store; a separate async fixture opens and closes
the enabled store and retains the disabled case. Nutrition and learning use
one background runner; nutrition's namespace remains registered when that
runner is paused, and photo drafts still open. Every default home path,
including memory, resolves inside the temporary fixture. Construction builds: it builds
the tool registry, the brain (unconnected), the broker, the server, and
Vigil's queue and policy, which is everything the hub touches before
it opens a socket. A module-level import of anything Mac-shaped fails
here before it fails on the server.

The spoke is expected to fail the same test, and is checked to.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BLOCKED = [
    "sounddevice", "webrtcvad", "_sounddevice", "mlx", "mlx_whisper",
    "faster_whisper", "openwakeword", "onnxruntime", "sherpa_onnx", "piper",
    "objc", "AppKit", "Foundation", "Quartz", "EventKit", "Contacts",
    "CoreLocation", "Cocoa",
]

CHILD = r'''
import importlib.abc, sys, tempfile
BLOCKED = set(%r)

class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is not installed on this machine (probe)")
        return None

sys.modules.pop("numpy", None)
sys.meta_path.insert(0, Refuse())
sys.path.insert(0, %r)

what = sys.argv[1]
if what == "hub":
    import ciel.wire, ciel.schedule, ciel.turn, ciel.confirm
    import ciel.hub.server, ciel.hub.rpc
    import ciel.pipeline
    from dataclasses import replace
    from pathlib import Path
    from ciel.config import Config, ProactiveConfig, ShellConfig, FilesConfig
    from ciel.pipeline import Pipeline
    import asyncio
    from unittest.mock import patch
    from ciel.config import TasksConfig
    with tempfile.TemporaryDirectory(prefix="ciel-hub-imports-") as fixture:
        tmp = Path(fixture)
        with patch.object(Path, "home", return_value=tmp):
            cfg = replace(
                Config(), state_dir=tmp,
                proactive=replace(ProactiveConfig(), enabled=True, state_file=tmp / "p.json",
                                  watches_file=tmp / "w.json", brief_time="08:30"),
                shell=replace(ShellConfig(), enabled=True),
                files=replace(FilesConfig(), enabled=True, workspace=tmp / "ws"),
                tasks=TasksConfig(enabled=True, directory=tmp / "tasks"),
            )
            assert cfg.memory.dir == tmp / ".ciel" / "memory"
            p = Pipeline(cfg, role="hub")
            assert p._stt is None and p._tts is None and p._wake is None
            assert p._remote is not None and p._presence is p._remote.presence
            assert p._task_controller.store is None and not cfg.tasks.directory.exists()
            print("HUB CONSTRUCTION HAS NO TASK STORE")
            async def lifecycle():
                await p._task_controller.start()
                assert p._task_controller.store is not None
                await p._task_controller.close()
                assert p._task_controller.store is None
                print("HUB TASK STORE OPENS AND CLOSES")
                disabled = Pipeline(replace(cfg, tasks=replace(cfg.tasks, enabled=False)), role="hub")
                await disabled._task_controller.start()
                assert disabled._task_controller.store is None
                await disabled._task_controller.close()
                print("HUB TASK STORE DISABLED")
                import socket
                from ciel.nutrition_photos import OPERATION, NAMESPACE
                for learning in (False,True):
                    for running in (False,True):
                        local=tmp / ("photos-" + str(learning) + "-" + str(running))
                        variant=replace(cfg,state_dir=local,
                            hub=replace(cfg.hub,require_token=True,token="fixture"),
                            tasks=replace(cfg.tasks,runner=running,directory=local/"tasks"),
                            nutrition=replace(cfg.nutrition,enabled=True,owner_host=socket.gethostname(),state_dir=local/"nutrition-state"),
                            learning=replace(cfg.learning,enabled=learning,background=True),
                            projects=replace(cfg.projects,enabled=True))
                        photo=Pipeline(variant,role="hub")
                        assert NAMESPACE in photo._task_controller.namespaces
                        assert photo._nutrition.photos.tasks is photo._task_controller
                        assert (photo._background_runner is not None)==running
                        if running:
                            served=photo._background_runner.served
                            assert OPERATION in served and set(served)<=set(photo._task_runner._excluded)
                            assert any(op.startswith("learning.") for op in served)==learning
                            assert len([r for r in (photo._task_runner,photo._background_runner) if r.background])==1
                            await photo._background_runner.close()
                            await photo._task_runner.close()
                        await photo._task_controller.start()
                        await photo._nutrition.start()
                        assert photo._nutrition.store is not None
                        assert photo._nutrition.photos.capabilities()["analysis"]==running
                        await photo._nutrition.close()
                        await photo._task_controller.close()
                print("PHOTO BACKGROUND SHARED WITH LEARNING")
                print("PHOTO NAMESPACE SURVIVES A PAUSED RUNNER")
            asyncio.run(lifecycle())
            names = [t.name for t in __import__("ciel.brain.tools", fromlist=["TOOLS"]).TOOLS]
            print("HUB FIXTURE MEMORY IS PRIVATE TO THE PROBE")
            print("HUB OK", len(names), "tools registered in the catalog")

else:
    try:
        import ciel.spoke.frontend
    except ImportError as exc:
        print("SPOKE REFUSED", exc)
        raise SystemExit(0)
    print("SPOKE IMPORTED (unexpected)")
    raise SystemExit(1)
''' % (BLOCKED, str(ROOT / "src"))


def run(what: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, what], capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def main() -> None:
    print("the hub, with every Mac and audio module refused")
    code, out = run("hub")
    tail = out.strip().splitlines()[-12:]
    for line in tail:
        print("   ", line[:160])
    ok = code == 0 and "HUB OK" in out
    print(f"  {'ok  ' if ok else 'FAIL'} the hub imports and constructs with no Mac in it")
    if not ok:
        sys.exit(1)
    for marker in ("HUB CONSTRUCTION HAS NO TASK STORE", "HUB TASK STORE OPENS AND CLOSES", "HUB TASK STORE DISABLED", "HUB FIXTURE MEMORY IS PRIVATE TO THE PROBE", "PHOTO BACKGROUND SHARED WITH LEARNING", "PHOTO NAMESPACE SURVIVES A PAUSED RUNNER"):
        if marker not in out:
            print("FAIL", marker)
            sys.exit(1)
        print("  ok  ", marker.lower())
    print("\nthe spoke, same test")
    code, out = run("spoke")
    ok = code == 0 and "SPOKE REFUSED" in out
    print("   ", out.strip().splitlines()[-1][:160] if out.strip() else "(no output)")
    print(f"  {'ok  ' if ok else 'FAIL'} the spoke needs the room's modules, as it should")
    if not ok:
        sys.exit(1)
    print("\nall 8 checks passed")


if __name__ == "__main__":
    main()
