#!/usr/bin/env bash
# Auto-select outer gap preset based on tiled window count on focused workspace.
# Triggered from Hammerspoon (window create/destroy) and Aerospace's
# exec-on-workspace-change callback.
#
# Source of truth: the dotfiles file. Runtime: a regenerated copy at
# ~/.aerospace.toml with the active gap baked in. The runtime file is rebuilt
# from source whenever source is newer or the gap target changes, so any edits
# to the dotfiles config propagate on the next workspace event without manual
# resync.

set -e

# Hammerspoon-launched tasks don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

# When true, manual hyper-g cycles suppress auto-mode for the active workspace
# until you leave it. Set to false to disable suppression entirely.
SUPPRESSION_ENABLED=true

SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"
SUPPRESS_FILE="/tmp/aerospace-gaps-suppressed-workspace"

# Skip when the focused monitor is the laptop's built-in display, or when more
# than one monitor is connected (e.g. clamshell + lid open). The presets are
# tuned for an external ultrawide; slamming 600–1000 px gaps onto a 13" panel
# looks broken. Manual cycling via Hyper+G applies the same guard.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ]; then
    exit 0
fi
case "$(jq -r '.[0]["monitor-name"] // ""' <<<"$mons_json" 2>/dev/null)" in
    *Built-in*|*"Built In"*|*MacBook*) exit 0 ;;
esac

# Serialize concurrent runs (Hammerspoon window events + Aerospace workspace
# changes can fire this script in rapid succession). Non-blocking: if another
# instance holds the lock, exit — the next event will retrigger.
exec 9>/tmp/aerospace-gaps.lock
flock -n 9 || exit 0

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

read_gap() {
    grep -m1 'outer\.left' "$1" 2>/dev/null \
        | grep -oE 'monitor\.main = [0-9]+' \
        | grep -oE '[0-9]+'
}

# Decide whether the runtime needs rebuilding from source.
needs_rebuild=false
if [ ! -f "$RUNTIME_FILE" ] || [ -L "$RUNTIME_FILE" ] || [ "$SOURCE_FILE" -nt "$RUNTIME_FILE" ]; then
    needs_rebuild=true
fi

current=$(read_gap "$RUNTIME_FILE")
[ -z "$current" ] && current=$(read_gap "$SOURCE_FILE")

if [ "$needs_rebuild" = false ] && [ "$current" = "$target" ]; then
    exit 0
fi

# Stage to a sibling temp file and atomically rename into place. mv on the
# same filesystem uses rename(2), so $RUNTIME_FILE never appears truncated
# even if concurrent invocations race or a process is killed mid-write.
TMP=$(mktemp "$RUNTIME_FILE.XXXXXX")
trap 'rm -f "$TMP"' EXIT
cp "$SOURCE_FILE" "$TMP"
sed -i '' "s/outer\.left = \[{ monitor\.main = [0-9]* }/outer.left = [{ monitor.main = $target }/" "$TMP"
sed -i '' "s/outer\.right = \[{ monitor\.main = [0-9]* }/outer.right = [{ monitor.main = $target }/" "$TMP"
chmod 0644 "$TMP"
mv "$TMP" "$RUNTIME_FILE"

aerospace reload-config
