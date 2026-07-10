#!/bin/bash
set -euo pipefail

CADDY=/opt/homebrew/bin/caddy
if [[ ! -x "$CADDY" ]]; then
    echo "caddy not installed; agent staying dormant" >&2
    exit 0
fi
exec "$CADDY" run --config "$HOME/.config/caddy/Caddyfile"
