#!/usr/bin/env bash

# Optional work overlay (only present on work machines via stow-work)
WORK_OVERLAY="$CONFIG_DIR/plugins/vpn_work.sh"
[ -f "$WORK_OVERLAY" ] && source "$WORK_OVERLAY"

# Check for NordVPN tunnel (assigns 10.5.x.x on a utun interface)
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  sketchybar --set "$NAME" icon="🌐" label="NordVPN" drawing=on
  exit 0
fi

source "$CONFIG_DIR/plugins/lib/display_mode.sh"
if [ ! -f "$WORK_OVERLAY" ] && [ "$(display_mode)" = "compact" ]; then
  sketchybar --set "$NAME" drawing=off
else
  sketchybar --set "$NAME" icon="🔴" label="" drawing=on
fi
