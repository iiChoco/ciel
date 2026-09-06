#!/bin/sh
# Deployment is maintained by the sibling infrastructure repository.
# No flags: preview. --dry-run: compare with server. --apply: deploy.
set -eu
SOURCE="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
INFRA="${INFRASTRUCTURE_DIR:-$HOME/Projects/infrastructure}"
if [ ! -f "$INFRA/scripts/deploy.py" ]; then
  echo "Infrastructure checkout not found at $INFRA; set INFRASTRUCTURE_DIR." >&2
  exit 1
fi
exec python3 "$INFRA/scripts/deploy.py" ciel --source "$SOURCE" "$@"
