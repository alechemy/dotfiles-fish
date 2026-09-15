#!/usr/bin/env bash
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/worktrunk/config.toml"
SEED="$DOTFILES/stow/worktrunk/_seed/.config/worktrunk/config.toml"

if [ -e "$CONFIG" ] || [ -L "$CONFIG" ]; then
    if [ ! -f "$CONFIG" ] || [ -L "$CONFIG" ]; then
        echo "Worktrunk config must be a regular, app-owned file." >&2
        exit 1
    fi
else
    mkdir -p "$(dirname "$CONFIG")"
    (umask 077; set -o noclobber; printf '%s\n' "$(<"$SEED")" > "$CONFIG")
fi

if ! (cd "$HOME" && wt --config "$CONFIG" config show --format=json >/dev/null 2>&1); then
    echo "Worktrunk config validation failed. Inspect it locally with wt config show." >&2
    exit 1
fi
