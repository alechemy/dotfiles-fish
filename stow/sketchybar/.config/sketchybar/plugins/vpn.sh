#!/usr/bin/env bash

# Check for NordVPN tunnel (assigns 10.5.x.x on a utun interface)
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  sketchybar --set "$NAME" icon="🌐" label="NordVPN" drawing=on
  exit 0
fi

# Check for work proxy tunnel (SSH SOCKS proxy over Thunderbolt bridge)
if ssh -O check workbridge 2>/dev/null; then
  sketchybar --set "$NAME" icon="🔌" label="Proxy" drawing=on
  exit 0
fi

# Neither active — show idle state so it's still clickable
sketchybar --set "$NAME" icon="🔴" label="" drawing=on
