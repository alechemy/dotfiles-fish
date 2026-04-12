#!/usr/bin/env bash

if [ "$SENDER" = "volume_change" ]; then
  VOLUME="$INFO"

  case "$VOLUME" in
    [6-9][0-9]|100) ICON="󰕾" ;;
    [3-5][0-9]) ICON="󰖀" ;;
    [1-9]|[1-2][0-9]) ICON="󰕿" ;;
    *) ICON="󰖁" ;;
  esac

  OUTPUT=$(/opt/homebrew/bin/SwitchAudioSource -c)

  # Friendly names for audio outputs (add more lines as needed)
  case "$OUTPUT" in
    "USB-C to 3.5mm Headphone Jack Adapter") OUTPUT="Headphones" ;;
  esac

  sketchybar --set "$NAME" icon="$ICON" label="$VOLUME% $OUTPUT"
fi
