#!/usr/bin/env bash

# Toggle the exact battery percentage label on click. The icon is always shown;
# the numeric label is opt-in so the bar stays compact by default.
STATE="${TMPDIR:-/tmp}/sketchybar_battery_show_pct"
if [ -f "$STATE" ]; then
  rm -f "$STATE"
  sketchybar --set "$NAME" label.drawing=off
else
  : >"$STATE"
  sketchybar --set "$NAME" label.drawing=on
fi
