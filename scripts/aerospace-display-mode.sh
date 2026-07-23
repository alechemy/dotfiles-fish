#!/usr/bin/env bash
# Apply the per-display-mode window-management baseline on dock transitions.
#
# Caller (aerospace-auto-gaps.sh) passes the current steady-state mode:
# "portable" (no DELL — laptop alone or laptop plus a travel secondary) or
# "docked" (DELL as the only display). Transient configs (DELL + built-in
# mid-dock) are never passed, so a transition fires exactly once per flip.
#
# On undock every workspace root becomes h_accordion — full-width windows with
# the accordion padding as the "more windows here" cue — because side-by-side
# tiles laid out for the ultrawide are unusable at 14". On re-dock roots return
# to h_tiles and auto-gaps recomputes the constant-width columns. Only the root
# layout is touched: nested containers (join-with groups) survive the round
# trip, so a docked column pairing is intact after undock + re-dock. A
# workspace empty at flip time has no tree to flip — its next window births a
# fresh root from default-root-container-layout, which auto-gaps bakes per
# mode into the runtime config so late-arriving workspaces still match.
#
# JankyBorders width is mode-dependent via bordersrc, which reads the mode file
# this script writes; re-running bordersrc against a live instance applies the
# new width in place (same mechanism as the on-mode-changed recolor).

set -e

export PATH="/opt/homebrew/bin:$PATH"

MODE="${1:?usage: aerospace-display-mode.sh docked|portable}"
STATE_DIR="$HOME/.cache/aerospace-gaps"
mkdir -p "$STATE_DIR"
MODE_FILE="$STATE_DIR/display-mode"
LOG_FILE="$STATE_DIR/gaps.log"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE"; }

case "$MODE" in
    portable) root_layout=h_accordion ;;
    docked)   root_layout=h_tiles ;;
    *) echo "usage: aerospace-display-mode.sh docked|portable" >&2; exit 2 ;;
esac

# Own lock, not the gap lock: a transition must not queue behind a gap rebuild,
# and the re-read below collapses concurrent callers into one apply.
exec 8>"$STATE_DIR/mode-lock"
flock 8

last=$(cat "$MODE_FILE" 2>/dev/null || true)
[ "$MODE" = "$last" ] && exit 0

workspaces=$(aerospace list-workspaces --all)
[ -n "$workspaces" ]

# The mode file is written only after every layout call succeeds (set -e), so
# a transition that hits a transient CLI failure retries on the next event.
while IFS= read -r ws; do
    aerospace layout --workspace "$ws" --root "$root_layout"
done <<<"$workspaces"

printf '%s\n' "$MODE" >"$MODE_FILE"

# Bare bordersrc with no live instance would become the daemon and block here.
if pgrep -xq borders; then
    bash "$HOME/.config/borders/bordersrc" >/dev/null 2>&1 || true
fi

log "display-mode ${last:-unset}->$MODE root=$root_layout"
