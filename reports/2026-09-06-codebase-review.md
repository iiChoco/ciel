# Ciel codebase review — 2026-09-06

Reviewed commit `9e9bad10b43be32467d6a3bf01707aee6a5eec01` and the existing uncommitted gesture-candidate logging change. The review concentrated on confirmation and file guards, action recording, hub/spoke RPC, web admission, and turn routing. It is not an exhaustive audit of every module or dependency.

Four actionable findings were reproduced. The first two expose gaps in enforced security boundaries; the other two break cancellation and undo guarantees. No production code was changed.

## 1. [P1] Check mutating options before granting a shell prefix quiet access

**Location.** [brain/shellguard.py:309](/Users/choco/Projects/ciel/src/ciel/brain/shellguard.py:309), with the default Git prefixes in [config.py:525](/Users/choco/Projects/ciel/src/ciel/config.py:525).

**What was reproduced.** With shell access enabled and its default allowlist, `git diff --no-index --output=DEST LEFT RIGHT` classifies as `quiet`. A real `ShellGuard` accepted it without calling a confirmer that would decline every question. The spoke executor then wrote the diff to a temporary destination outside its configured file workspace, with `confirmed` left false. The file guard independently refused that same destination for writing.

**Impact.** Matching only the leading tokens treats every option of an approved Git command as read-only. Once shell access is enabled, a model can create or overwrite files without the promised confirmation; the shell also bypasses the file workspace. The explicit opt-in to shell access does not opt into unconfirmed writes. Other mutating forms such as branch creation/deletion deserve coverage in the same classifier fix, though this reproduction executes only the diff write.

**Proposed fix.** Require argument-aware checks for the built-in quiet prefixes, allowing only verified read-only forms. Escalate output-writing options and mutating Git subcommands/options to confirmation. A smaller immediate fix is to remove ambiguous prefixes from the default quiet tier until that validation exists. Add shellguard probe checks that exercise actual temporary-file side effects and verify that the confirmer is called.

## 2. [P1] Put the signup session cookie on the credential blocklist

**Location.** [brain/permissions.py:94](/Users/choco/Projects/ciel/src/ciel/brain/permissions.py:94). The omitted filename is defined by [config.py:968](/Users/choco/Projects/ciel/src/ciel/config.py:968).

**What was reproduced.** A synthetic `.ciel/sections-cookie` file, mode `0600`, was placed beneath a temporary workspace. `WorkspaceGuard.permits()` allowed it, the spoke's real file-read handler returned its synthetic contents, and `classify('cat …/sections-cookie', ShellConfig())` returned `quiet`.

**Impact.** The configured cookie is an authenticated website session credential, as its own config documentation states. When file access includes the home directory, or allows reads outside a narrower workspace, ordinary file tools can expose it to the model. With shell access enabled, a quiet `cat` reaches it independently of the workspace. Owner-only filesystem permissions do not constrain Ciel, which runs as that owner. This differs from the protected Discord, Oura, hub, and interview credentials.

**Proposed fix.** Add `sections-cookie` to `FORBIDDEN_NAMES` so both file and shell gates inherit the restriction. Also account for explicitly configured credential paths if alternate filenames are supported. Add regressions for direct reads, recursive searches, and quiet shell classification, using synthetic credentials only.

## 3. [P2] Stop and reap the shell process when its RPC is canceled

**Location.** [spoke/executor.py:219](/Users/choco/Projects/ciel/src/ciel/spoke/executor.py:219), reached from the cancellation handler at [spoke/executor.py:93](/Users/choco/Projects/ciel/src/ciel/spoke/executor.py:93).

**What was reproduced.** A real `tool.request` started a temporary Python helper through `shell.run`. After the helper signaled that it had started, a real `tool.cancel` canceled and removed the executor task. The helper nevertheless wrote a second marker file afterward. No result frame was emitted for the canceled call.

**Impact.** Canceling the coroutine does not terminate the OS process. `_shell_run` kills its immediate process only for its own `TimeoutError`; an outer RPC deadline, `tool.cancel`, or executor shutdown injects cancellation and skips that cleanup. Work can continue changing files after the call has been abandoned. The existing timeout path also does not explicitly reap the killed process or terminate its descendants.

**Proposed fix.** Give each shell invocation an owned process group/session, and terminate and reap it on cancellation as well as timeout. Preserve cancellation after cleanup. Have executor shutdown await canceled tasks so cleanup completes. Add an RPC probe that checks that a delayed marker is never written after cancellation, rather than checking only that a cancel frame was sent.

## 4. [P2] Save the Mac file's previous contents before overwriting it

**Location.** [brain/recorder.py:31](/Users/choco/Projects/ciel/src/ciel/brain/recorder.py:31) and [spoke/executor.py:248](/Users/choco/Projects/ciel/src/ciel/spoke/executor.py:248).

**What was reproduced.** The real recorder's before/after hooks wrapped a real executor overwrite of an existing temporary file, using `mcp__ciel__mac_write_file`. The action appeared in the journal, but `snapshot` and `note` were both null, the snapshot directory was empty, and the old contents had been replaced.

**Impact.** The brain includes Mac writes in the recorder's watched set, but `_FILE_MUTATORS` recognizes only the local SDK file tools. The spoke does not snapshot before writing either. Consequently the hub deployment cannot reliably honor “put it back how it was” for a file overwritten through its normal Mac write tool, despite the action journal's documented guarantee. Recording the new contents does not recover the old ones.

**Proposed fix.** Capture the previous contents on the spoke, where the target actually exists, and return a snapshot reference or explicit failure note that the hub journal and guarded undo path can use. Simply adding the remote tool name to the local snapshot map would read the hub's filesystem and is insufficient. Add an RPC/journal regression that overwrites and then restores a temporary Mac file.

## Reproduction

[2026-09-06-codebase-review-repro.py](/Users/choco/Projects/ciel/reports/2026-09-06-codebase-review-repro.py) builds disposable fixtures and runs all four reproductions:

```bash
uv run --no-sync python reports/2026-09-06-codebase-review-repro.py
```

Observed output:

```text
REPRODUCED: git diff --output writes outside the workspace with zero confirmation questions
REPRODUCED: owner-only sections-cookie is readable through the file guard and quiet shell
REPRODUCED: tool.cancel clears the task but the shell writes a file afterward
REPRODUCED: Mac overwrite is journaled with no snapshot and no explanation
All four findings reproduced; temporary fixtures removed.
```

The script asserts the observed defects, so a corrected implementation should fail the corresponding assertion. Its commands and files are confined to its temporary fixture directory; it loads no user configuration or production runtime state, uses no model or external service, and removes its fixtures afterward.

## Verification and limits

Existing probes passed with `uv run --no-sync python scripts/probe_<name>.py`:

| Probe | Result |
|---|---|
| `probe_shellguard.py` | 148 checks passed |
| `probe_files.py` | All checks passed; this probe does not print a count |
| `probe_tool_rpc.py` | 32 checks passed |
| `probe_wire.py` | 70 checks passed |
| `probe_confirm_wire.py` | 13 checks passed |
| `probe_hub_arbiter.py` | 50 checks passed |
| `probe_turns.py` | 71 checks passed |
| `probe_web.py` | 35 checks passed |
| `probe_gestures.py` | 88 checks passed |
| `probe_world.py` | 121 checks passed |
| `probe_closure.py` | 56 checks passed |

The first turns-probe run used its existing `load_config()` call in the stream-death check. After noticing that this reads user configuration, I reran it with `load_config` replaced by a temporary `Config` and an audit hook rejecting opens under the real `~/.ciel`; all 71 checks passed again. No runtime contents are included in this report.

`git diff --check` passed. The uncommitted gesture changes were preserved, and this review found no confirmed regression specific to that change. The defects above are not covered by the passing existing probes.

No source, README, or changelog edits, commits, deployments, live microphone/model tests, real message sends, or remote-service checks were performed. The interview UI and its live account/session flows were not tested. Only the local report and its reproduction script were added; no spoke reload was triggered by these additions.
