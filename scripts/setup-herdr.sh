#!/usr/bin/env bash
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$HOME/.config/herdr/config.toml"
SEED="$DOTFILES/stow/herdr/_seed/.config/herdr/config.toml"

if [ ! -e "$CONFIG" ] && [ ! -L "$CONFIG" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    cp -p "$SEED" "$CONFIG"
fi

HERDR_CONFIG_PATH="$CONFIG" herdr config check
mkdir -p "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
herdr integration install pi
"$DOTFILES/scripts/install-herdr-hunk-diff.sh"
