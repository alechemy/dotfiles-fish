#!/usr/bin/env bash
# Merge portable Pi preferences into Pi's app-owned settings file.

set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRAGMENT="$DOTFILES/stow/pi/.pi/agent/settings.fragment.json"
TARGET="$HOME/.pi/agent/settings.json"
TARGET_DIR="$(dirname "$TARGET")"

if ! command -v jq >/dev/null 2>&1; then
    echo "merge-pi-settings: jq is required; skipping" >&2
    exit 1
fi

if [ ! -f "$FRAGMENT" ]; then
    echo "merge-pi-settings: missing fragment: $FRAGMENT" >&2
    exit 1
fi

if [ -L "$TARGET" ]; then
    echo "merge-pi-settings: refusing to replace symlink: $TARGET" >&2
    exit 1
fi

jq empty "$FRAGMENT"
if [ -e "$TARGET" ]; then
    jq empty "$TARGET"
fi

mkdir -p "$TARGET_DIR"
tmp=$(mktemp "$TARGET_DIR/.settings.json.XXXXXX")
trap 'rm -f "$tmp"' EXIT

if [ -e "$TARGET" ]; then
    jq -S -s '.[0] * .[1]' "$TARGET" "$FRAGMENT" >"$tmp"
else
    jq -S -s '.[0] * .[1]' <(printf '{}\n') "$FRAGMENT" >"$tmp"
fi
chmod 644 "$tmp"

if [ -e "$TARGET" ] && cmp -s "$tmp" "$TARGET"; then
    echo "Pi settings already up to date: $TARGET"
    exit 0
fi

mv "$tmp" "$TARGET"
trap - EXIT
echo "Merged Pi settings: $TARGET"
