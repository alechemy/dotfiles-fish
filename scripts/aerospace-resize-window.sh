#!/usr/bin/env bash
# Hyper +/- resize. With 2+ tiled windows this defers to AeroSpace's native
# `resize smart`, which redistributes width among siblings. With a single tiled
# window on the ultrawide, native resize is a no-op — the window fills the
# tiling area and the constant-width outer gaps (see aerospace-gaps-lib.sh) pin
# it to one third of the screen — so instead this widens/narrows those outer
# gaps, letting the lone window break out past the 1/3 cap. The workspace is
# marked suppressed (same file aerospace-cycle-gaps.sh uses) so auto-gaps backs
# off until you leave it.
#
# Usage: aerospace-resize-window.sh grow|shrink

set -e

# AeroSpace callbacks don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

dir="${1:-grow}"

SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"
SUPPRESS_FILE="/tmp/aerospace-gaps-suppressed-workspace"

. "$HOME/.dotfiles/scripts/aerospace-gaps-lib.sh"

# Per-side gap delta. Both sides move, so a step of 50 changes window width by
# ~100 pt, matching the `resize smart +/-100` this key used to run.
STEP=50
# Tightest edge for a fully broken-out single window.
MIN_GAP=4

native_resize() {
    case "$dir" in
        grow)   aerospace resize smart +100 || true ;;
        shrink) aerospace resize smart -100 || true ;;
    esac
}

# Gap breakout only applies to the ultrawide-only single-monitor config that
# auto-gaps manages. Any other topology falls through to native resize.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ] \
   || ! jq -e --arg m 'DELL U4025QW' 'any(.[]; ."monitor-name" | contains($m))' \
        <<<"$mons_json" >/dev/null 2>&1; then
    native_resize
    exit 0
fi

ws=$(aerospace list-workspaces --focused)

# Count tiled windows (exclude floating and hidden-app placeholders).
count=$(aerospace list-windows --workspace "$ws" --format "%{window-layout}" \
    | grep -vE '^(floating|macos_native_window_of_hidden_app)$' \
    | wc -l \
    | tr -d ' ')

[ "$count" -lt 1 ] && exit 0
if [ "$count" -gt 1 ]; then
    native_resize
    exit 0
fi

# Single tiled window: adjust the outer gaps instead of resizing. Block on the
# same lock as auto-gaps/cycle-gaps so a manual resize and an automatic rebuild
# can't race.
exec 9>/tmp/aerospace-gaps.lock
flock 9

compute_gap_presets || { native_resize; exit 0; }

read_gap() {
    # See aerospace-auto-gaps.sh for the digits-in-name rationale.
    sed -nE 's/.*outer\.left = \[\{ monitor\."DELL U4025QW" = ([0-9]+).*/\1/p' "$1" 2>/dev/null \
        | head -n1 || true
}
current=$(read_gap "$RUNTIME_FILE")
[ -z "$current" ] && current=$(read_gap "$SOURCE_FILE")
[ -z "$current" ] && current=$gap_centered

# grow shrinks the gap (widens the window); shrink grows it, capped at the
# centered 1/3 default so this key never narrows past the auto-gaps baseline.
case "$dir" in
    grow)   gap=$(( current - STEP )); [ "$gap" -lt "$MIN_GAP" ] && gap=$MIN_GAP ;;
    shrink) gap=$(( current + STEP )); [ "$gap" -gt "$gap_centered" ] && gap=$gap_centered ;;
    *)      exit 0 ;;
esac

[ "$gap" = "$current" ] && exit 0

# Stage to a sibling temp file and atomically rename into place (rename(2), so
# $RUNTIME_FILE never appears truncated if invocations race or are killed).
TMP=$(mktemp "$RUNTIME_FILE.XXXXXX")
trap 'rm -f "$TMP"' EXIT
cp "$SOURCE_FILE" "$TMP"
sed -i '' "s/outer\.left = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.left = [{ monitor.\"DELL U4025QW\" = $gap }/" "$TMP"
sed -i '' "s/outer\.right = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.right = [{ monitor.\"DELL U4025QW\" = $gap }/" "$TMP"
chmod 0644 "$TMP"
mv "$TMP" "$RUNTIME_FILE"

aerospace reload-config

# Back auto-gaps off this workspace until you leave it (honored only when
# SUPPRESSION_ENABLED=true in aerospace-auto-gaps.sh).
echo "$ws" > "$SUPPRESS_FILE"
