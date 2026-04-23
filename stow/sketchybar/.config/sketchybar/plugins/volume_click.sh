#!/usr/bin/env bash

# Configurable cycle of audio outputs for right-click.
# Names must match `SwitchAudioSource -a -t output` exactly.
# Unavailable entries are skipped.
OUTPUTS=(
  "BTD 700"
  "USB-C to 3.5mm Headphone Jack Adapter"
  "DELL U4025QW"
)

SWITCH=/opt/homebrew/bin/SwitchAudioSource

if [ "$BUTTON" = "right" ]; then
  AVAILABLE=$("$SWITCH" -a -t output)
  CURRENT=$("$SWITCH" -c -t output)

  # Build the list of configured outputs that are currently available.
  candidates=()
  for out in "${OUTPUTS[@]}"; do
    if grep -Fxq "$out" <<<"$AVAILABLE"; then
      candidates+=("$out")
    fi
  done

  [ "${#candidates[@]}" -eq 0 ] && exit 0

  # Find current position; default to -1 so next becomes index 0.
  idx=-1
  for i in "${!candidates[@]}"; do
    if [ "${candidates[$i]}" = "$CURRENT" ]; then
      idx=$i
      break
    fi
  done

  next="${candidates[$(( (idx + 1) % ${#candidates[@]} ))]}"
  "$SWITCH" -s "$next" -t output >/dev/null

  # Refresh the sketchybar label.
  VOL=$(osascript -e 'output volume of (get volume settings)')
  sketchybar --trigger volume_change INFO="$VOL"
  exit 0
fi

# Default (left / other): open SoundSource menu.
osascript -e '
tell application "System Events"
    tell process "SoundSource"
        click menu bar item 1 of menu bar 2
    end tell
end tell
' 2>/dev/null
