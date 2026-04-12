#!/usr/bin/env bash

# If NordVPN is active, open the NordVPN app
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  open -a 'NordVPN'
  exit 0
fi

# Toggle work proxy
if ssh -O check workbridge 2>/dev/null; then
  fish -c "proxy off"
else
  # Pause polling so vpn.sh doesn't overwrite our status
  sketchybar --set vpn update_freq=0 icon="⏳" label="…"
  if ! fish -c "proxy on"; then
    sketchybar --set vpn icon="⚠️" label="Failed"
    # Show error for 3s, then restore polling
    (sleep 3 && sketchybar --set vpn update_freq=10 icon="🔴" label="" drawing=on) &
  else
    sketchybar --set vpn update_freq=10
  fi
fi
