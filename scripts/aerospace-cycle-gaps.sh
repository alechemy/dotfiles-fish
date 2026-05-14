#!/usr/bin/env bash
# Cycle outer gap presets on the main (ultrawide) monitor.
#
# Source of truth: the dotfiles file. Runtime: a regenerated copy at
# ~/.aerospace.toml with the chosen gap baked in. The runtime is rebuilt from
# source on every invocation, so dotfiles edits propagate without manual resync.

set -e

# aerospace invokes this via exec-and-forget; ensure Homebrew is on PATH for flock.
export PATH="/opt/homebrew/bin:$PATH"

SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"

# Skip when the focused monitor is the laptop's built-in display, or when more
# than one monitor is connected. See aerospace-auto-gaps.sh for the rationale.
# Notifies briefly so the user knows the Hyper+G didn't do nothing for free.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
should_skip=false
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ]; then
    should_skip=true
fi
case "$(jq -r '.[0]["monitor-name"] // ""' <<<"$mons_json" 2>/dev/null)" in
    *Built-in*|*"Built In"*|*MacBook*) should_skip=true ;;
esac
if [ "$should_skip" = true ]; then
    osascript -e 'display notification "Gap cycling is disabled on the built-in display." with title "AeroSpace"' >/dev/null 2>&1 || true
    exit 0
fi

# Block on the same lock as auto-gaps so a manual cycle and an automatic
# rebuild can't race each other.
exec 9>/tmp/aerospace-gaps.lock
flock 9

PRESETS=(8 600 1220)
LABELS=("full" "split" "centered")

# Read current gap from runtime if present, else from source.
read_gap() {
    grep -m1 'outer\.left' "$1" 2>/dev/null | grep -o 'monitor\.main = [0-9]*' | grep -o '[0-9]*'
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
sed -i '' "s/outer\.left = \[{ monitor\.main = [0-9]* }/outer.left = [{ monitor.main = $gap }/" "$TMP"
sed -i '' "s/outer\.right = \[{ monitor\.main = [0-9]* }/outer.right = [{ monitor.main = $gap }/" "$TMP"
chmod 0644 "$TMP"
mv "$TMP" "$RUNTIME_FILE"

aerospace reload-config

# Mark this workspace as manually overridden so auto-gaps backs off until
# you leave the workspace. Honored only when SUPPRESSION_ENABLED=true in
# aerospace-auto-gaps.sh.
aerospace list-workspaces --focused > /tmp/aerospace-gaps-suppressed-workspace
