# Deployment

Canonical service definitions and deployment tooling live in the independent
`~/Projects/infrastructure` repository. See its README and `docs/operations.md`.

- Server units: `infrastructure/services/systemd/ciel-hub.service`.
- Mac templates: `infrastructure/services/launchd/`; render them with
  `python3 ~/Projects/infrastructure/scripts/render_launchagents.py`.
- `scripts/push_hub.sh` forwards this source checkout to the infrastructure
  deploy command and deploys by default. `--sync` adds locked server dependency
  synchronization; `--dry-run` compares with the server; `--preview` only prints.

The server runs `/home/ciel/ciel` (moved from `/home/ciel/jarvis` on
2026-09-06; a symlink keeps the old name resolving). Runtime state stays
under `~/.ciel`.
