# Working on Ciel

This file is read by every coding agent that works here (Codex reads it
directly; Claude Code imports it from `CLAUDE.md`). It describes how the
project is actually built, so that work from any agent looks like it came
from the same hand. Read neighbouring code for conventions this guide does not cover.

## Working together

- One writer per checkout. Parallel implementation needs separate worktrees
  and agreed file ownership.
- Read status and diffs before starting; re-read each file immediately before
  editing and reconcile any changes.
- Preserve other tasks' uncommitted work. Never discard, stash, stage, commit,
  or reformat it as cleanup.
- Reviews provide findings, evidence, and proposed fixes; the assigned writer
  makes the changes.
- Keep settled decisions unless changed requirements or new evidence justify
  revisiting them; record the reason.

Follow this repository's conventions and configured checks; otherwise match
neighbouring code. Google's style guides are optional references, not review
requirements. Permission covers the user's stated task and scope; it does not
carry over to unrelated work.

## What this is

Ciel is a local-first voice assistant: package `ciel` under `src/ciel`,
Python 3.12, `uv`. The checkout lives at `~/Projects/ciel` (`~/jarvis` is a
compatibility symlink). The GitHub remote is `iiChoco/ciel`; it was
`iiChoco/jarvis` until 2026-09-06, and GitHub redirects the old name. The
word "jarvis" survives on purpose in three places that are *features*, not
leftovers: the pretrained
wake model `hey_jarvis`, the persona `personality = "jarvis"`, and the
speaker treatment `effect = "jarvis"`. Do not rename them.

`README.md` is the manual and the map. Its last section, "How it fits
together", names every module and its role; "Extending it" says where a new
capability, connection, or engine goes. Read those two sections before
touching anything you have not touched before.

## Environment and runtime

- Ad hoc commands: `uv run --no-sync ...`. A bare `uv run` or `uv sync`
  re-resolves without the optional extras and silently uninstalls piper,
  speaker verification, and the Discord lane from the live environment.
- After a dependency change: `uv sync --locked --all-extras`. Commit
  `uv.lock` with `pyproject.toml`.
- The spoke runs under launchd (`ai.ciel.spoke`) and **re-execs itself on
  any edit under `src/ciel`**. After editing, confirm the restart landed:
  `tail ~/.ciel/log/ciel-spoke.err.log` should end in `spoke ready`. A
  wedged reload is fixed with `launchctl kickstart -k gui/$(id -u)/ai.ciel.spoke`.
- Runtime state, config, tokens, recordings, and models live in `~/.ciel`.
  Nothing from there is ever committed, copied into a report, or read by a
  probe. Probes build their own state in temporary directories.
- Service definitions (launchd templates, the systemd unit, the deploy
  script) live in the sibling repository `~/Projects/infrastructure`, not
  here. `deploy/README.md` points there.

## Tests are probes

There is no pytest. Each layer has a probe, `scripts/probe_<layer>.py`,
that exercises it end to end without a mic, a model, or the network:

```python
CHECKS: list[str] = []

def check(name: str, ok: bool) -> None:
    CHECKS.append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        sys.exit(1)
...
print(f"\nall {len(CHECKS)} checks passed")
```

A check's name is a sentence stating the invariant in the project's words
("an honestly empty afternoon is a reading"), not a test id. The probe's
module docstring lists, in prose, everything it pins.

Every behaviour change adds checks to the probe for that layer, and the
changelog entry reports the count (`probe_turns.py 63 → 71`). Run the
probes for every layer you touched before calling the work done, and run
`uv run --no-sync python -c "import ciel.hub.server"` when the hub side
changed. Do not ship a change whose probe you did not run.

## Code

- `from __future__ import annotations` at the top of every module; type
  hints on every signature; dataclasses for records; asyncio throughout.
- No formatter or linter is configured. Match the surrounding code and do
  not reformat lines you are not changing. Lines run long when a string or
  a comment wants it; that is fine.
- Module docstrings are essays: what the module is, its codename if it has
  one, why it exists (what was wrong before it), and the invariants it
  keeps, each in bold. Read the top of `src/ciel/world.py` for the shape.
- Comments explain *why*, never what the next line does. A comment that
  restates the code is deleted.
- Subsystems carry mathematical codenames (Phase Space, Vigil, Adjoint,
  Chart, Barn Door, Invariant, Inverse, Atlas, Trace, Analytic
  Continuation, Parallel Transport, Proof Obligation). Use the existing
  ones by name. Do not coin a new one; the user names things.
- Config is one place: every swappable choice is a field on a dataclass in
  `src/ciel/config.py` with a docstring, and documented in the README's
  Configuration section the same day.
- Security posture is part of correctness: files the user alone should
  read are written owner-only; strings written by other people (a
  meeting's title, a message) are quoted in prompts, never vouched for;
  private readings never reach a public lane; secrets are named in
  `FORBIDDEN_NAMES` and never printed.
- No new dependency without asking. Prefer the protocol-and-factory shape
  already used for engines over a new abstraction.
- Every action the user might regret goes through the confirmation broker
  and the action journal. Do not add a side-effecting tool that skips them.

## Visual style: the Instrument

The Chart's `src/ciel/remote/chart.html` `:root` block owns the base tokens.
Reuse its palette, typography, state colors, and components. Keep the Chart
self-contained and usable offline. Token changes also affect the website's
Instrument stylesheet and vendored copies: identify consumers and update their
version notes together. Check narrow layouts, keyboard focus, and changed UI
states; say which visual checks were performed.

## Writing

The project has a voice. Commit titles, changelog entries, docstrings, and
the README are written in it; deployment notes in the register of a
corporate runbook are not.

**Commit titles** state what is now true, as a sentence in the project's
words: "The interview room can use the door's accounts", "A password you
chose", "Two processes: the room and the brain". No type prefixes, no
"Add/Fix/Update", no trailing period. The body, when there is one, says why.

**One change, one commit, one changelog entry.** Never bundle unrelated
work into a checkpoint commit. If the tree holds several changes, commit
them separately with their own titles.

**`CHANGELOG.md`**, newest first, gets an entry for every notable change:

```
## YYYY-MM-DD — title in the same voice as the commit

**Why.** The problem as it was experienced, in prose.

**What.**

- *A short italic thesis.* What changed and the reasoning behind it.

**Probes.** `probe_x.py 12 → 15: what the new checks pin.`
```

**`README.md`** is updated in the same change as the behaviour it
describes: the section for that subsystem, the Configuration block, the
probe list. A change without its README paragraph is unfinished.

**Reviews and investigations** are written to `reports/YYYY-MM-DD-*.md`
with reproduction scripts beside them, findings ordered by impact, each
with the file and line, what was reproduced, and the fix. Fixes then get
their own changelog entry that names the report.

Dates are absolute (`2026-09-06`), never "today" or "yesterday".

## Git

- Work on the branch you were given. Do not create branches, commit, push,
  rebase, or amend unless asked. When asked to commit, the author is the
  repository's local identity, `iiChoco`, never a real name; this
  repository is public under a pseudonym.
- Do not run `git add -A`. Stage the files that belong to the change.
- `reports/`, `design/boards/`, and `.claude/settings.local.json` are
  local working material; leave them alone unless the task is about them.

## Deploying

The brain (hub) runs on an Azure VM as the `ciel-hub` systemd service; the
Mac runs the spoke. `scripts/push_hub.sh` rsyncs this checkout to the hub,
whose autoreloader re-execs on arrival (`--sync` also re-syncs the server's
locked dependencies; `--dry-run` compares; `--preview` prints). Do not deploy
unless asked. Read-only checks over ssh are fine and expected after a push:

```bash
ssh ciel@172.184.253.239 'journalctl -u ciel-hub --since "10 min ago" --no-pager'
```

## Documentation-only work

For guidance, comments, or documentation with no behavior or dependency change,
verify links and commands against the current files and run `git diff --check`.
No new probe or live-service check is needed. Update the README and changelog
when the change is notable. Leave production processes and runtime data alone.

## Done means

1. Behavior changes pass the probes for every touched layer, with relevant
   regression checks; documentation-only work passes the checks above.
2. The changelog entry and README paragraph exist.
3. If edits under `src/ciel` triggered a running spoke to reload, its log shows
   `spoke ready` after the last edit.
4. No new dependency, codename, branch, commit, push, or deploy that was
   not asked for.
5. Your final message says what you verified and what you did not.
