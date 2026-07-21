#!/usr/bin/env bash

if [ -z "$FOCUSED_WORKSPACE" ]; then
  FOCUSED_WORKSPACE=$(/opt/homebrew/bin/aerospace list-workspaces --focused)
fi

# Highlight the focused workspace.
if [ "$1" = "$FOCUSED_WORKSPACE" ]; then
  sketchybar --set "$NAME" background.drawing=on
else
  sketchybar --set "$NAME" background.drawing=off
fi

# Docked: emoji + workspace number. Portable: emoji + window count — roots are
# h_accordion there (aerospace-display-mode.sh), so the count marks windows
# stacked behind the front one; an empty workspace keeps just the emoji. The
# mode file replaces an ioreg call. COUNTS ("1:5 3:2", absent workspaces are
# zero) rides the pokes aerospace-auto-gaps.sh fires on window open/close, so
# those refreshes cost no per-item CLI call; workspace-change triggers carry no
# COUNTS and fall back to asking aerospace.
if [ "$(cat "$HOME/.cache/aerospace-gaps/display-mode" 2>/dev/null)" = "portable" ]; then
  if [ -n "$COUNTS" ]; then
    count=0
    for pair in $COUNTS; do
      case "$pair" in "$1":*) count="${pair#*:}" ;; esac
    done
  else
    count=$(/opt/homebrew/bin/aerospace list-windows --workspace "$1" --count 2>/dev/null)
    case "$count" in '' | *[!0-9]*) count=0 ;; esac
  fi
  if [ "$count" -ge 1 ]; then
    sketchybar --set "$NAME" icon.drawing=on label.drawing=on label="$count" \
      label.font="Helvetica Neue:Regular:11.0" label.color=0xb0ffffff \
      label.y_offset=4 label.padding_left=1
  else
    sketchybar --set "$NAME" icon.drawing=on label.drawing=off
  fi
else
  sketchybar --set "$NAME" icon.drawing=on label.drawing=on label="$1" \
    label.font="Helvetica Neue:Regular:14.0" label.color=0xffffffff \
    label.y_offset=0 label.padding_left=7
fi
