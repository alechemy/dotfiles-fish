#!/usr/bin/env bash

# Optional work overlay (only present on work machines via stow-work)
WORK_OVERLAY="$CONFIG_DIR/plugins/vpn_click_work.sh"
[ -f "$WORK_OVERLAY" ] && source "$WORK_OVERLAY"

# If NordVPN is active, open the NordVPN app
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  open -a 'NordVPN'
  exit 0
fi
