#!/usr/bin/env bash

# Right-click: open/focus Feishin.
if [ "$BUTTON" = "right" ]; then
  open -a Feishin
  exit 0
fi

# Default (left / other): toggle the player represented by the item.
ACTIVE_SOURCE=$(head -n 1 "$HOME/.cache/now-playing-source" 2>/dev/null)
if [ "$ACTIVE_SOURCE" = "qobuz" ] && pgrep -xq Qobuz; then
  /usr/bin/osascript "$HOME/.config/sketchybar/plugins/qobuz_toggle.applescript" \
    || /usr/bin/logger -t sketchybar-qobuz "play/pause control not found; Qobuz UI may have changed"
elif pgrep -xq Feishin; then
  /opt/homebrew/bin/nowplaying-cli togglePlayPause
else
  open -a Feishin
fi
