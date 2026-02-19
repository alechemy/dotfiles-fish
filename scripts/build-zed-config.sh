#!/usr/bin/env bash
#
# Merges settings.base.json (JSONC, tracked) + settings.secrets.json (gitignored)
# into settings.json (gitignored, symlinked by stow).
#
# Note: sed strips // comments at the beginning of lines so jq can parse the JSONC.
# This avoids breaking URLs (e.g., https://) but assumes no inline comments at
# the end of lines containing values.
#
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZED_DIR="$DOTFILES/stow/zed/.config/zed"
BASE="$ZED_DIR/settings.base.json"
SECRETS="$ZED_DIR/settings.secrets.json"
OUT="$ZED_DIR/settings.json"

strip_comments() {
    sed -E 's|^[[:space:]]*//.*$||' "$1"
}

if [ ! -f "$BASE" ]; then
    echo "Error: $BASE not found"
    exit 1
fi

if [ ! -f "$SECRETS" ]; then
    strip_comments "$BASE" | jq '.' > "$OUT"
    echo "Warning: No secrets file found at $SECRETS"
    echo "  Using base config only. Create it to add API keys."
else
    # Deep merge: base + secrets overlay (secrets wins on conflicts)
    strip_comments "$BASE" | jq -s '.[0] * .[1]' - "$SECRETS" > "$OUT"
fi
