#!/usr/bin/env bash

# Optional work overlay (only present on work machines via stow-work)
WORK_OVERLAY="$CONFIG_DIR/plugins/vpn_work.sh"
[ -f "$WORK_OVERLAY" ] && source "$WORK_OVERLAY"

# Check for NordVPN tunnel (assigns 10.5.x.x on a utun interface)
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  sketchybar --set "$NAME" icon="🌐" label="NordVPN" drawing=on
  exit 0
fi

# Nothing active — show idle state so it's still clickable
sketchybar --set "$NAME" icon="🔴" label="" drawing=on
