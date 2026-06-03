#!/usr/bin/env bash

source "$CONFIG_DIR/plugins/lib/display_mode.sh"

# Docked, connectivity comes through the dock (and clamshell is reliably online),
# so a Wi-Fi warning there is just noise.
if [ "$(display_mode)" = "spacious" ]; then
  sketchybar --set "$NAME" drawing=off
  exit 0
fi

# Undocked, Wi-Fi is the only link. Detect "connected" by whether the Wi-Fi
# device holds an IPv4 address — networksetup -getairportnetwork is unreliable on
# recent macOS (reports "not associated" even when online), but ipconfig is not.
WIFI_DEV=$(networksetup -listallhardwareports | awk '/Wi-Fi|AirPort/{getline; print $2; exit}')
[ -z "$WIFI_DEV" ] && WIFI_DEV=en0

if ipconfig getifaddr "$WIFI_DEV" >/dev/null 2>&1; then
  # Online — stay invisible.
  sketchybar --set "$NAME" drawing=off
else
  # Offline reminder.
  sketchybar --set "$NAME" icon="󰖪" icon.color=0xfff5a623 label="No Wi-Fi" drawing=on
fi
