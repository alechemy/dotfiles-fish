#!/usr/bin/env bash

# Right-click: open/focus Feishin.
if [ "$BUTTON" = "right" ]; then
  open -a Feishin
  exit 0
fi

# Default (left / other): toggle play/pause, launching Feishin if it's not running.
if pgrep -xq Feishin; then
  /opt/homebrew/bin/nowplaying-cli togglePlayPause
else
  open -a Feishin
fi
