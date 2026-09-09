# 2026-09-08 — The switch names an older Ciel

## Input Monitoring rejects the rebuilt launcher

**Impact.** Neither quick-note hotkey reaches the keyboard listener even
though System Settings shows Input Monitoring enabled for Ciel.

**Boundary.** `src/ciel/shortcuts.py`, `GlobalShortcuts._listen`: the
`CGPreflightListenEventAccess` check refuses startup. The infrastructure
launcher is ad-hoc signed with a designated requirement tied to its code hash.

**Reproduced.** Restarting the launchd spoke did not restore the listener.
macOS TCC diagnostics attributed the request to `ai.ciel.launcher`, rather
than independently to Python, and explicitly rejected the stored code
requirement because it differed from the current launcher signature.
The current signature was independently checked with `codesign -dr -`.
No private Ciel state or note content was inspected for this diagnosis.

**Fix.** In Input Monitoring, remove the stale Ciel entry and add the current
`~/Projects/infrastructure/rendered/Ciel.app`, then enable it and restart the
spoke. The user must renew the grant; the old visible switch is insufficient.
The permission refresh and an actual hotkey press remain unverified.

**Follow-through.** Future launcher rebuilds need a permission check against
the running build. Changes to launcher signing belong in the infrastructure
repository; no signing or permission database was changed here.
