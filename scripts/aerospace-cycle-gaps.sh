#!/bin/bash
# Cycle outer gap presets on the main (ultrawide) monitor.
# Targets the actual dotfiles source to avoid macOS sed breaking the stow symlink.

CONFIG_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"

PRESETS=(8 600 1220)
LABELS=("full" "split" "centered")

# Read current gap from config
current_gap=$(grep 'outer\.left' "$CONFIG_FILE" | grep -o 'monitor\.main = [0-9]*' | grep -o '[0-9]*')

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

# Patch the config in place (editing source file, symlink stays intact)
sed -i '' "s/outer\.left = \[{ monitor\.main = [0-9]* }/outer.left = [{ monitor.main = $gap }/" "$CONFIG_FILE"
sed -i '' "s/outer\.right = \[{ monitor\.main = [0-9]* }/outer.right = [{ monitor.main = $gap }/" "$CONFIG_FILE"

aerospace reload-config

# osascript -e "display notification \"$label (${gap}px)\" with title \"AeroSpace Gaps\""
