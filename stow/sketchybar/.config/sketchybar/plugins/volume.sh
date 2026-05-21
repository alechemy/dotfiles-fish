#!/usr/bin/env bash

source "$CONFIG_DIR/plugins/lib/display_mode.sh"

OUTPUT=$(/opt/homebrew/bin/SwitchAudioSource -c)

# DELL U4025QW exposes a fixed 0% to macOS audio. The real, audible volume is
# its DDC volume, which m1ddc reads/writes. Query that instead of trusting the
# system value (or the $INFO from volume_change events, which would just be 0).
if [ "$OUTPUT" = "DELL U4025QW" ]; then
  VOLUME=$(/opt/homebrew/bin/m1ddc get volume 2>/dev/null)
  [ -z "$VOLUME" ] && VOLUME=0
elif [ "$SENDER" = "volume_change" ] && [ -n "$INFO" ]; then
  VOLUME="$INFO"
else
  VOLUME=$(osascript -e 'output volume of (get volume settings)')
fi

case "$VOLUME" in
[6-9][0-9] | 100) ICON="󰕾" ;;
[3-5][0-9]) ICON="󰖀" ;;
[1-9] | [1-2][0-9]) ICON="󰕿" ;;
*) ICON="󰖁" ;;
esac

if [ "$(display_mode)" = "compact" ]; then
  # On the MacBook display, drop the output-device name to reclaim space
  # near the notch; the volume percentage alone is enough.
  LABEL="$VOLUME%"
else
  # Friendly names for audio outputs (add more lines as needed)
  case "$OUTPUT" in
  "USB-C to 3.5mm Headphone Jack Adapter") OUTPUT="Beyerdynamic DT 770 Pro X" ;;
  "BTD 700") OUTPUT="Bowers & Wilkins Px8 S2" ;;
  "DELL U4025QW") OUTPUT="DELL Monitor" ;;
  esac

  LABEL="$VOLUME% $OUTPUT"
fi

sketchybar --set "$NAME" icon="$ICON" label="$LABEL"
