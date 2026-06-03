#!/usr/bin/env bash

source "$CONFIG_DIR/plugins/lib/display_mode.sh"

if [ -z "$FOCUSED_WORKSPACE" ]; then
  FOCUSED_WORKSPACE=$(/opt/homebrew/bin/aerospace list-workspaces --focused)
fi

# Highlight the focused workspace.
if [ "$1" = "$FOCUSED_WORKSPACE" ]; then
  sketchybar --set "$NAME" background.drawing=on
else
  sketchybar --set "$NAME" background.drawing=off
fi

# Emoji icons cost horizontal space; on the MacBook display drop them and keep
# just the workspace number. Recompute only on display changes / startup, not on
# every workspace switch, to avoid a per-space ioreg call on each focus change.
case "$SENDER" in
display_change | forced | routine | "")
  if [ "$(display_mode)" = "compact" ]; then
    sketchybar --set "$NAME" icon.drawing=off
  else
    sketchybar --set "$NAME" icon.drawing=on
  fi
  ;;
esac
