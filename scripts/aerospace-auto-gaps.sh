#!/bin/bash
# Auto-select outer gap preset based on tiled window count on focused workspace.
# Triggered from Hammerspoon (window create/destroy) and Aerospace's
# exec-on-workspace-change callback.

set -e

# Hammerspoon-launched tasks don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

# When true, manual hyper-g cycles suppress auto-mode for the active workspace
# until you leave it. Set to false to disable suppression entirely.
SUPPRESSION_ENABLED=true

CONFIG_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
SUPPRESS_FILE="/tmp/aerospace-gaps-suppressed-workspace"

ws=$(aerospace list-workspaces --focused)

if [ "$SUPPRESSION_ENABLED" = true ] && [ -f "$SUPPRESS_FILE" ]; then
    suppressed_ws=$(cat "$SUPPRESS_FILE")
    if [ "$suppressed_ws" = "$ws" ]; then
        exit 0
    fi
    rm -f "$SUPPRESS_FILE"
fi

# Count tiled windows (exclude floating and hidden-app placeholders).
count=$(aerospace list-windows --workspace "$ws" --format "%{window-layout}" \
    | grep -vE '^(floating|macos_native_window_of_hidden_app)$' \
    | wc -l \
    | tr -d ' ')

# Map count to outer-left/right preset value (px) on the main monitor.
case "$count" in
    0|1) target=1000 ;;
    2)   target=600 ;;
    *)   target=8 ;;
esac

current=$(grep -m1 'outer\.left' "$CONFIG_FILE" \
    | grep -oE 'monitor\.main = [0-9]+' \
    | grep -oE '[0-9]+')

[ "$current" = "$target" ] && exit 0

sed -i '' "s/outer\.left = \[{ monitor\.main = [0-9]* }/outer.left = [{ monitor.main = $target }/" "$CONFIG_FILE"
sed -i '' "s/outer\.right = \[{ monitor\.main = [0-9]* }/outer.right = [{ monitor.main = $target }/" "$CONFIG_FILE"

aerospace reload-config
