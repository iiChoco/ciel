"""Review fixture: regex work is confined to a child killed after two seconds.

Updated 2026-09-05: the search now runs in its own killable worker, so the
heartbeat prints while it runs and the fixture completes."""
import asyncio, sys, tempfile
from pathlib import Path
from ciel.brain.permissions import WorkspaceGuard
from ciel.brain.tools import files
async def main():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); (root/'line.txt').write_text('a'*29+'!')
        files.bind_files(WorkspaceGuard(root))
        async def heartbeat():
            await asyncio.sleep(.05)
            print('HEARTBEAT',flush=True)
        task=asyncio.create_task(heartbeat())
        print('STARTED_SEARCH',flush=True)
        await files.search_files.handler({'pattern':'^(a+)+$'})
        await task
if __name__ != '__main__':
    pass  # the search's own spawned worker re-imports this file; nothing to do
elif '--child' in sys.argv:
    asyncio.run(main())
else:
    import subprocess
    try:
        result = subprocess.run([sys.executable, __file__, '--child'], capture_output=True, text=True, timeout=2)
        print('completed:', result.stdout)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
        print('Search exceeded two seconds; event-loop output:', output)

