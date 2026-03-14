#!/usr/bin/env bash

# If Feishin is not running, show "nothing playing"
if ! pgrep -xq "Feishin"; then
  sketchybar --set "$NAME" icon="" label=" Nothing playing"
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

LAST_SONG_FILE="$HOME/.cache/navidrome-last-song"
AUTH_CACHE="$HOME/.cache/navidrome-auth"
AUTH_MAX_AGE=300 # re-auth every 5 minutes

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

  printf 'SUBSONIC_TOKEN=%s\nSUBSONIC_SALT=%s\n' "$SUBSONIC_TOKEN" "$SUBSONIC_SALT" > "$AUTH_CACHE"
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

set_track() {
  local icon_prefix="$1" artist="$2" title="$3"
  sketchybar --set "$NAME" icon="$icon_prefix $artist –" label=" $title"
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
