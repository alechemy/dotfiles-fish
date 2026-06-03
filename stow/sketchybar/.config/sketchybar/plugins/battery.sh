#!/usr/bin/env bash

source "$CONFIG_DIR/plugins/lib/display_mode.sh"

# Only relevant on the MacBook built-in display (undocked, usually on battery).
# Docked is clamshell on AC, so the indicator is just noise there.
if [ "$(display_mode)" = "spacious" ]; then
  sketchybar --set "$NAME" drawing=off
  exit 0
fi

BATT=$(pmset -g batt)
PERCENT=$(echo "$BATT" | grep -Eo '[0-9]+%' | head -1 | tr -d '%')
[ -z "$PERCENT" ] && PERCENT=0

CHARGING=false
echo "$BATT" | grep -q "AC Power" && CHARGING=true

# Nerd-font (Material Design Icons) battery glyphs; charging overrides level.
if [ "$CHARGING" = true ]; then
  ICON="󰂄"
else
  case "$PERCENT" in
  100) ICON="󰁹" ;;
  9[0-9]) ICON="󰂂" ;;
  8[0-9]) ICON="󰂁" ;;
  7[0-9]) ICON="󰂀" ;;
  6[0-9]) ICON="󰁿" ;;
  5[0-9]) ICON="󰁾" ;;
  4[0-9]) ICON="󰁽" ;;
  3[0-9]) ICON="󰁼" ;;
  2[0-9]) ICON="󰁻" ;;
  1[0-9]) ICON="󰁺" ;;
  *) ICON="󰂎" ;;
  esac
fi

# Warn (red) when draining and at/under 20%.
if [ "$CHARGING" = false ] && [ "$PERCENT" -le 20 ]; then
  COLOR=0xffff5f5f
else
  COLOR=0xffffffff
fi

# Leaves label.drawing untouched so battery_click.sh's reveal toggle persists.
sketchybar --set "$NAME" drawing=on icon="$ICON" icon.color="$COLOR" label="$PERCENT%"
