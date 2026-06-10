#!/usr/bin/env bash
# Recompute outer gaps from the tiled window count on the focused workspace so
# every window keeps a constant width (one third of the monitor; see
# aerospace-gaps-lib.sh for the math).
# Triggered from AeroSpace callbacks: on-window-detected (new windows),
# on-focus-changed (closing the focused window shifts focus, so this catches
# cmd-W), and exec-on-workspace-change (workspace navigation).
#
# Source of truth: the dotfiles file. Runtime: a regenerated copy at
# ~/.aerospace.toml with the active gap baked in. The runtime file is rebuilt
# from source whenever source is newer or the gap target changes, so any edits
# to the dotfiles config propagate on the next workspace event without manual
# resync.

set -e

# AeroSpace callbacks don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

# When true, manual hyper-g cycles suppress auto-mode for the active workspace
# until you leave it. Set to false to disable suppression entirely.
SUPPRESSION_ENABLED=true

SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"
SUPPRESS_FILE="/tmp/aerospace-gaps-suppressed-workspace"

. "$HOME/.dotfiles/scripts/aerospace-gaps-lib.sh"

# Skip when more than one monitor is connected (e.g. clamshell + lid open) so
# the manual + automatic gap states don't fight during transient configs.
# The TOML's named-monitor gap rule already keeps the laptop's built-in panel
# on the 8 px fallback regardless of what's written here.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ]; then
    exit 0
fi

# Serialize concurrent runs (AeroSpace's window/focus/workspace callbacks can
# fire this script in rapid succession). Non-blocking: if another instance
# holds the lock, exit and let the next event retrigger.
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

# Map count to the outer-left/right value that keeps window width constant.
compute_gap_presets || exit 0
case "$count" in
    0|1) target=$gap_centered ;;
    2)   target=$gap_split ;;
    *)   target=$gap_full ;;
esac

read_gap() {
    # Extracts the gap integer assigned to the named monitor on outer.left,
    # ignoring the digits embedded in the monitor name itself ("U4025QW").
    # Always exits 0 so set -e doesn't kill the script when the pattern is
    # absent (e.g. during a TOML format migration). Caller treats empty as
    # "fall back to source".
    sed -nE 's/.*outer\.left = \[\{ monitor\."DELL U4025QW" = ([0-9]+).*/\1/p' "$1" 2>/dev/null \
        | head -n1 || true
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
sed -i '' "s/outer\.left = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.left = [{ monitor.\"DELL U4025QW\" = $target }/" "$TMP"
sed -i '' "s/outer\.right = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.right = [{ monitor.\"DELL U4025QW\" = $target }/" "$TMP"
chmod 0644 "$TMP"
mv "$TMP" "$RUNTIME_FILE"

aerospace reload-config
