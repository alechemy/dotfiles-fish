#!/usr/bin/env bash

# Optional work overlay (only present on work machines via stow-work)
WORK_OVERLAY="$CONFIG_DIR/plugins/vpn_work.sh"
[ -f "$WORK_OVERLAY" ] && source "$WORK_OVERLAY"

# Check for NordVPN tunnel (assigns 10.5.x.x on a utun interface)
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  sketchybar --set "$NAME" icon="🌐" label="NordVPN" drawing=on
  exit 0
fi

# Nothing active. On the MacBook display (undocked) the proxy/VPN toggle is
# irrelevant, so hide it entirely to reclaim space — the active branches above
# still draw it as a "you forgot to turn the proxy off" reminder. Docked, keep
# the idle red dot so it stays clickable.
source "$CONFIG_DIR/plugins/lib/display_mode.sh"
if [ "$(display_mode)" = "compact" ]; then
  sketchybar --set "$NAME" drawing=off
else
  sketchybar --set "$NAME" icon="🔴" label="" drawing=on
fi
