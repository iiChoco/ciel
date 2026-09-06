# Repository relocation audit — September 6, 2026

Scope: the current local working trees at `/Users/choco/jarvis` and `/Users/choco/yunhan.me`, including uncommitted source, repository-local launch/settings files, lockfiles, selected generated virtual-environment metadata, and local Git configuration/hooks. No repositories, services, or runtime data were moved or modified. This is a static audit, not a successful relocation test or a check of deployed server state.

## Findings that affect relocation

| Location | Dependency | Required handling |
|---|---|---|
| `jarvis/deploy/ai.ciel.plist:35` | Changes directory to `$HOME/jarvis` before starting Ciel. | Update the local checkout path and reinstall/reload the installed launch agent when moving the checkout. |
| `jarvis/deploy/ai.ciel.spoke.plist:30` | Changes directory to `$HOME/jarvis`; starts with `uv run --no-sync`. | Update the path and prepare the environment before restarting the spoke. |
| `jarvis/deploy/ciel-hub.service:23,38` | Working directory and interpreter are under `/home/ciel/jarvis`. | Update only if the server checkout moves. A local folder rename does not require a server rename. |
| `jarvis/scripts/push_hub.sh:18,21` | Remote rsync destination and dependency-sync directory are `~/jarvis`. | Keep aligned with the hub service's server path. |
| `yunhan.me/door/deploy/door.service:16,19` | Working directory and executable are under `/home/ciel/yunhan.me/door`. | Update only if the server checkout moves. |
| `yunhan.me/scripts/push.sh:15,18` | Remote destination is `~/yunhan.me`; dependency sync runs in its `door` directory. | Keep aligned with the Door service's server path. |
| Both deployment scripts, `ROOT` assignment | The application source root is inferred from the script's parent directory. | Moving these scripts into a separate infrastructure repository without changing source selection would upload the wrong tree. Both use `rsync --delete`, which could then remove application files at the destination. Require an explicit source checkout and validate its expected project files before syncing. |
| `jarvis/.venv` and `yunhan.me/door/.venv` | Generated entry points, activation scripts, and editable-install metadata contain absolute checkout paths. | Recreate environments from the respective lockfiles after relocation; do not treat them as portable or hand-edit generated files. |

The targeted environment scan found 40 text files referencing the Ciel checkout and 9 referencing the website checkout. These counts describe scanned launcher/install metadata, not every dependency file. Both repositories have ordinary `.git` directories, not worktree pointer files. No old checkout-path matches were found in the inspected Git configuration, non-sample hooks, or alternates file.

## Machine-specific paths that are independent of the checkout

- Both Ciel launch-agent templates hardcode `/Users/choco/.ciel/log/...` in their stdout/stderr fields. Moving the source on the same Mac does not invalidate those paths, but installation on another account would. Generate absolute log paths when installing; launchd path fields do not become shell expressions just by writing `$HOME` in them.
- The hub unit explicitly sets `/home/ciel`, `/home/ciel/.local/bin`, and `/home/ciel/.ciel/hub.env`; Door uses `/home/ciel/.door/config.toml`. These describe the server account and runtime state, not repository ownership.
- Ciel's `Path.home() / '.ciel'` defaults and Door's `Path.home() / '.door'` defaults keep runtime state outside the source tree. Preserve them during the source move.
- macOS tool paths, Homebrew search paths, Messages/Find My locations, and `/tmp` probe outputs are platform or temporary-file assumptions. They do not require changes for a checkout rename.
- Hostnames and cookie domains such as `ciel.yunhan.me` and `.yunhan.me` are not filesystem paths and can stay unchanged.
- `jarvis` in wake-word models, persona names, speech examples, and voice effects is not a reference to the repository directory. Do not globally replace the word.

## Relative layout assumptions

These survive moving the entire repository intact, but matter if its internal layout changes:

- Ciel locates the Chart, Interview pages/cases, native Swift source, and reload-watch root relative to Python module files.
- Several probes insert `<repository>/src` into `sys.path`; review reproduction scripts similarly locate probes relative to their own files.
- Ciel's `.claude/launch.json` uses relative script/design paths and assumes it is launched from the project root. It contains no hardcoded old checkout path. Its `--no-sync` commands need a rebuilt environment after a move.
- Door's stylesheet default in `door/door/config.py:38` resolves `<website-repository>/instrument` through `__file__.parents[2]`. Moving Instrument into another repository requires an explicit configured location or a different asset-distribution mechanism.
- Door pages are loaded relative to its package. Its static-site roots are supplied by configuration.
- Both lockfiles identify their own project as `editable = '.'`, rather than an absolute checkout path. The installed environments, unlike the lockfiles, contain absolute paths.
- Documentation commands generally assume the current directory is the applicable project root. The Ciel README's `~/jarvis` example at line 291 should be updated when the folder is renamed. Historical review paths can remain historical.

## Outside these repositories: check before moving

The following were identified from the repository code/docs but were not inspected in this audit:

1. Installed copies in `~/Library/LaunchAgents`, including the documented `es.datastructur.sections-refresh.plist` job. Editing a repository template does not update its installed copy.
2. Live `~/.ciel/config.toml` on the Mac and hub: especially file-workspace paths, MCP command arguments, sections `refresh_cmd`, and custom source-watch/model paths. Runtime state can also contain user-saved file references.
3. Live `~/.door/config.toml`: `instrument` and `[sites]` roots. The example in `door/door/config.py:11,14` uses `~/yunhan.me/...`; the actual configured values need checking on the machine running Door.
4. Installed server service units and tunnel configuration, shell aliases, saved editor/workspace locations, and any external automation invoking a checkout script.

## Suggested migration boundary

First separate infrastructure ownership while retaining existing server paths. Give deployment scripts explicit application-source inputs before relocating them. Then relocate local checkouts, recreate their environments, and update installed local launchers and saved workspaces. Move server checkouts only as a separate deployment change with matching service/config updates. Verify imports/assets, launchers, and deployment source/destination selection before invoking any deployment script.
