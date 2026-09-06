# Deployment

Canonical service definitions and deployment tooling live in the independent
`~/Projects/infrastructure` repository. See its README and `docs/operations.md`.

- Server units: `infrastructure/services/systemd/ciel-hub.service`.
- Mac templates: `infrastructure/services/launchd/`; render them with
  `python3 ~/Projects/infrastructure/scripts/render_launchagents.py`.
- `scripts/push_hub.sh` forwards this source checkout to the infrastructure
  deploy command. It previews by default; use `--dry-run` for a server comparison
  or `--apply` to deploy. `--sync` adds locked server dependency synchronization.

The server still runs `/home/ciel/jarvis`. Moving the Mac checkout does not
change server paths. Runtime state stays under `~/.ciel`.
