#!/usr/bin/env bash

# If Feishin is not running, show "nothing playing"
if ! pgrep -xq "Feishin"; then
  sketchybar --set "$NAME" icon="" label=" Nothing playing" \
    label.font="Helvetica Neue:Regular:14.0"
  exit 0
fi

# Cache paths — defined before any expensive work so the battery gate below
# can short-circuit with a dimmed last-known-song readout without touching
# the keychain, sourcing the env file, or opening any sockets.
mkdir -p "$HOME/.cache"
LAST_SONG_FILE="$HOME/.cache/navidrome-last-song"
AUTH_CACHE="$HOME/.cache/navidrome-auth"
AUTH_MAX_AGE=300 # re-auth every 5 minutes

# Battery gate: on battery, skip the keychain lookup, the `nc` reachability
# probe (would wake Wi-Fi every 5s), and the curl auth + getNowPlaying calls.
# Must come before any of those — sketchybar fires this plugin under
# `update_freq=5`, so anything above this line is paid 12 times a minute.
if ! "$HOME/.local/bin/should-run-background-job" >/dev/null 2>&1; then
  if [ -f "$LAST_SONG_FILE" ]; then
    ARTIST=$(cut -f1 "$LAST_SONG_FILE")
    TITLE=$(cut -f2 "$LAST_SONG_FILE")
    sketchybar --set "$NAME" \
      icon=" $ARTIST –" label=" $TITLE" \
      label.font="Helvetica Neue:Bold:14.0" \
      icon.color="0x80ffffff" label.color="0x80ffffff"
  else
    sketchybar --set "$NAME" icon=" Battery" label=""
  fi
  exit 0
fi

NAVIDROME_ENV="$HOME/.config/navidrome/env"
if [ ! -f "$NAVIDROME_ENV" ]; then
  sketchybar --set "$NAME" icon=" No config" label=""
  exit 0
fi
source "$NAVIDROME_ENV"

USERNAME="${NAVIDROME_USERNAME:-alec}"
PASSWORD=$(security find-generic-password -s 'Navidrome' -a "$USERNAME" -w 2>/dev/null)
if [ -z "$PASSWORD" ]; then
  sketchybar --set "$NAME" icon=" No keychain" label=""
  exit 0
fi

# Fast reachability gate: when Navidrome is unreachable (off home network or
# NAS down), skip the curl block entirely. Without this, the plugin pays a
# 3-second curl timeout every 5 seconds whenever Wi-Fi isn't on the home LAN.
ND_HOSTPORT="${NAVIDROME_URL#*://}"   # strip scheme
ND_HOSTPORT="${ND_HOSTPORT%%/*}"      # strip any path
ND_HOST="${ND_HOSTPORT%:*}"
ND_PORT="${ND_HOSTPORT##*:}"
[ "$ND_HOST" = "$ND_PORT" ] && ND_PORT=80
if ! /usr/bin/nc -zw1 "$ND_HOST" "$ND_PORT" >/dev/null 2>&1; then
  sketchybar --set "$NAME" icon=" Offline" label=""
  exit 0
fi

# Load cached auth if fresh enough
if [ -f "$AUTH_CACHE" ]; then
  AUTH_AGE=$(( $(date +%s) - $(stat -f %m "$AUTH_CACHE") ))
  if [ "$AUTH_AGE" -lt "$AUTH_MAX_AGE" ]; then
    source "$AUTH_CACHE"
  fi
fi

# Authenticate if no cached token
if [ -z "$SUBSONIC_TOKEN" ] || [ -z "$SUBSONIC_SALT" ]; then
  AUTH_INFO=$(curl -s --max-time 3 "$NAVIDROME_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" 2>/dev/null)

  if [ -z "$AUTH_INFO" ] || [ "$AUTH_INFO" = "null" ]; then
    sketchybar --set "$NAME" icon=" Offline" label=""
    exit 0
  fi

  SUBSONIC_TOKEN=$(echo "$AUTH_INFO" | jq -r '.subsonicToken // empty' 2>/dev/null)
  SUBSONIC_SALT=$(echo "$AUTH_INFO" | jq -r '.subsonicSalt // empty' 2>/dev/null)

  if [ -z "$SUBSONIC_TOKEN" ]; then
    sketchybar --set "$NAME" icon=" Auth failed" label=""
    exit 0
  fi

  # The cache holds bearer-equivalent material (subsonic token + salt).
  # umask 077 ensures the create call uses 0600; chmod 600 covers the case
  # where the file already existed at a wider mode.
  (
    umask 077
    printf 'SUBSONIC_TOKEN=%s\nSUBSONIC_SALT=%s\n' "$SUBSONIC_TOKEN" "$SUBSONIC_SALT" > "$AUTH_CACHE"
  )
  chmod 600 "$AUTH_CACHE" 2>/dev/null || true
fi

# Get now playing
CURRENT_SONG=$(curl -s --max-time 3 "$NAVIDROME_URL/rest/getNowPlaying" \
  -d "u=$USERNAME" \
  -d "t=$SUBSONIC_TOKEN" \
  -d "s=$SUBSONIC_SALT" \
  -d "v=1.8.0" \
  -d "c=SketchyBar" \
  -d "f=json" 2>/dev/null |
  jq -r '.["subsonic-response"].nowPlaying.entry[0] // empty' 2>/dev/null)

PLAYBACK_RATE=$(/opt/homebrew/bin/nowplaying-cli get playbackRate 2>/dev/null)
if [ "$PLAYBACK_RATE" = "0" ]; then
  TEXT_COLOR="0x80ffffff"
else
  TEXT_COLOR="0xffffffff"
fi

set_track() {
  local icon_prefix="$1" artist="$2" title="$3"
  sketchybar --set "$NAME" \
    icon="$icon_prefix $artist –" label=" $title" \
    label.font="Helvetica Neue:Bold:14.0" \
    icon.color="$TEXT_COLOR" label.color="$TEXT_COLOR"
}

if [ -n "$CURRENT_SONG" ] && [ "$CURRENT_SONG" != "null" ]; then
  ARTIST=$(echo "$CURRENT_SONG" | jq -r '.artist // "Unknown"')
  TITLE=$(echo "$CURRENT_SONG" | jq -r '.title // "Unknown"')

  # Persist for paused state (tab-separated)
  printf '%s\t%s\n' "$ARTIST" "$TITLE" > "$LAST_SONG_FILE"

  set_track "" "$ARTIST" "$TITLE"
else
  if [ -f "$LAST_SONG_FILE" ]; then
    ARTIST=$(cut -f1 "$LAST_SONG_FILE")
    TITLE=$(cut -f2 "$LAST_SONG_FILE")
    set_track "" "$ARTIST" "$TITLE"
  else
    sketchybar --set "$NAME" icon=" Not playing" label=""
  fi
fi
