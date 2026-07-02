#!/usr/bin/env bash
# Cycle outer gap presets on the main (ultrawide) monitor.
#
# Source of truth: the dotfiles file. Runtime: a regenerated copy at
# ~/.aerospace.toml with the chosen gap baked in. The runtime is rebuilt from
# source on every invocation, so dotfiles edits propagate without manual resync.

set -e

# aerospace invokes this via exec-and-forget; ensure Homebrew is on PATH for flock.
export PATH="/opt/homebrew/bin:$PATH"

STATE_DIR="$HOME/.cache/aerospace-gaps"
mkdir -p "$STATE_DIR"
SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"

. "$HOME/.dotfiles/scripts/aerospace-gaps-lib.sh"

# Skip when more than one monitor is connected. See aerospace-auto-gaps.sh
# for the rationale; the TOML's named-monitor rule keeps the built-in panel
# on the 4 px fallback regardless.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ]; then
    osascript -e 'display notification "Gap cycling is disabled while multiple monitors are connected." with title "AeroSpace"' >/dev/null 2>&1 || true
    exit 0
fi

# Presets only exist for the ultrawide; on the built-in panel the cycle would
# bake built-in-derived values into the DELL entries and suppress auto-gaps
# with no visible effect until the next dock.
if ! jq -e --arg m 'DELL U4025QW' 'any(.[]; ."monitor-name" | contains($m))' \
        <<<"$mons_json" >/dev/null 2>&1; then
    osascript -e 'display notification "Gap cycling only applies on the ultrawide." with title "AeroSpace"' >/dev/null 2>&1 || true
    exit 0
fi

# Block on the same lock as auto-gaps so a manual cycle and an automatic
# rebuild can't race each other.
exec 9>"$STATE_DIR/lock"
flock 9

if ! compute_gap_presets; then
    osascript -e 'display notification "Cannot determine screen width; gap cycle skipped." with title "AeroSpace"' >/dev/null 2>&1 || true
    exit 0
fi
PRESETS=("$gap_full" "$gap_split" "$gap_centered")
LABELS=("full" "split" "centered")

# Read current gap from runtime if present, else from source.
read_gap() {
    # See aerospace-auto-gaps.sh for rationale.
    sed -nE 's/.*outer\.left = \[\{ monitor\."DELL U4025QW" = ([0-9]+).*/\1/p' "$1" 2>/dev/null | head -n1 || true
}
current_gap=$(read_gap "$RUNTIME_FILE")
[ -z "$current_gap" ] && current_gap=$(read_gap "$SOURCE_FILE")

# Find matching preset index (default to last so next cycle wraps to 0)
current_idx=$(( ${#PRESETS[@]} - 1 ))
for i in "${!PRESETS[@]}"; do
    if [[ "${PRESETS[$i]}" -eq "$current_gap" ]]; then
        current_idx=$i
        break
    fi
done

# Advance to next preset
next_idx=$(( (current_idx + 1) % ${#PRESETS[@]} ))
gap=${PRESETS[$next_idx]}
label=${LABELS[$next_idx]}

# Stage to a sibling temp file and atomically rename into place. mv on the
# same filesystem uses rename(2), so $RUNTIME_FILE never appears truncated.
TMP=$(mktemp "$RUNTIME_FILE.XXXXXX")
trap 'rm -f "$TMP"' EXIT
cp "$SOURCE_FILE" "$TMP"
sed -i '' "s/outer\.left = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.left = [{ monitor.\"DELL U4025QW\" = $gap }/" "$TMP"
sed -i '' "s/outer\.right = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.right = [{ monitor.\"DELL U4025QW\" = $gap }/" "$TMP"
chmod 0644 "$TMP"
mv "$TMP" "$RUNTIME_FILE"

aerospace reload-config

# Mark this workspace as manually overridden so auto-gaps backs off until
# you leave the workspace. Honored only when SUPPRESSION_ENABLED=true in
# aerospace-auto-gaps.sh.
aerospace list-workspaces --focused > "$STATE_DIR/suppressed-workspace"
