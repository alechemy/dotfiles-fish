#!/usr/bin/env bash

# Check for NordVPN tunnel (assigns 10.5.x.x on a utun interface)
if ifconfig 2>/dev/null | grep -q 'inet 10\.5\.'; then
  sketchybar --set "$NAME" icon="🔒" label="VPN" drawing=on
else
  sketchybar --set "$NAME" drawing=off
fi
