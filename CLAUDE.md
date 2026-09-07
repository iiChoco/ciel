@AGENTS.md

Claude-specific notes:

- The deploy apply in `scripts/push_hub.sh` is denied by the auto-mode
  permission classifier. Do not retry it; hand the user the one-line
  command and offer to verify through the hub's journal.
- Persistent memory for this project already exists and is loaded each
  session; the relocation from `~/jarvis` to `~/Projects/ciel` is recorded
  there.
