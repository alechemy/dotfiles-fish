#!/usr/bin/env bash

mkdir -p "$HOME/.cache"
LAST_SONG_FILE="$HOME/.cache/now-playing-last-song"
QOBUZ_SONG_FILE="$HOME/.cache/qobuz-now-playing"
ACTIVE_SOURCE_FILE="$HOME/.cache/now-playing-source"
AUTH_CACHE="$HOME/.cache/navidrome-auth"
AUTH_MAX_AGE=300

set_track() {
  local icon_prefix="$1" artist="$2" title="$3"
  sketchybar --set "$NAME" \
    icon="$icon_prefix $artist –" label=" $title" \
    label.font="Helvetica Neue:Bold:14.0" \
    icon.color="$TEXT_COLOR" label.color="$TEXT_COLOR"
}

FEISHIN_RUNNING=0
QOBUZ_RUNNING=0
pgrep -xq "Feishin" && FEISHIN_RUNNING=1
pgrep -xq "Qobuz" && QOBUZ_RUNNING=1

if (( ! FEISHIN_RUNNING && ! QOBUZ_RUNNING )); then
  rm -f "$ACTIVE_SOURCE_FILE"
  sketchybar --set "$NAME" icon="" label=" Nothing playing" \
    label.font="Helvetica Neue:Regular:14.0" \
    icon.color="0xffffffff" label.color="0xffffffff"
  exit 0
fi

# Qobuz does not reliably claim macOS's global media session. Its local player
# state identifies an active stream, and its cache database holds the metadata.
ACTIVE_SOURCE=$(head -n 1 "$ACTIVE_SOURCE_FILE" 2>/dev/null)
QOBUZ_CURRENT_TRACK_ID=""
QOBUZ_PLAYING_TRACK_ID=""
QOBUZ_POSITION_RECENT=0
QOBUZ_TRACK_CHANGED=0
QOBUZ_ARTIST=""
QOBUZ_TITLE=""
CACHE_TRACK_ID=""
CACHE_ARTIST=""
CACHE_TITLE=""
if (( QOBUZ_RUNNING )); then
  QOBUZ_DIR="$HOME/Library/Application Support/Qobuz"
  QOBUZ_PLAYER="$QOBUZ_DIR/player-0.json"
  QOBUZ_REPORT="$QOBUZ_DIR/reportingQueue.json"
  QOBUZ_DB="$QOBUZ_DIR/qobuz.db"

  if [ -f "$QOBUZ_PLAYER" ] && [ -f "$QOBUZ_DB" ]; then
    IFS=$'\034' read -r QOBUZ_CURRENT_TRACK_ID QOBUZ_POSITION_TIME < <(
      jq -jr '
        .playqueue.data as $queue
        | ($queue.items[$queue.currentIndex].trackId // ""), "\u001c",
          ((.player.data.position.timestamp // 0) / 1000 | floor), "\n"
      ' "$QOBUZ_PLAYER" 2>/dev/null
    )
    if [[ "$QOBUZ_POSITION_TIME" =~ ^[0-9]+$ ]] \
        && (( $(date +%s) - QOBUZ_POSITION_TIME < 30 )); then
      QOBUZ_POSITION_RECENT=1
    fi

    QOBUZ_REPORT_TRACK_ID=$(jq -r '.currentEvent.trackId // empty' "$QOBUZ_REPORT" 2>/dev/null)
    if [ "$QOBUZ_REPORT_TRACK_ID" = "$QOBUZ_CURRENT_TRACK_ID" ] \
        && (( QOBUZ_POSITION_RECENT )); then
      QOBUZ_PLAYING_TRACK_ID="$QOBUZ_CURRENT_TRACK_ID"
    fi

    if [[ "$QOBUZ_CURRENT_TRACK_ID" =~ ^[0-9]+$ ]]; then
      if [ -f "$QOBUZ_SONG_FILE" ]; then
        IFS=$'\034' read -r CACHE_TRACK_ID CACHE_ARTIST CACHE_TITLE < "$QOBUZ_SONG_FILE"
      fi

      if [ "$CACHE_TRACK_ID" = "$QOBUZ_CURRENT_TRACK_ID" ]; then
        QOBUZ_ARTIST="$CACHE_ARTIST"
        QOBUZ_TITLE="$CACHE_TITLE"
      else
        QOBUZ_METADATA=$(/usr/bin/sqlite3 -readonly -noheader -separator $'\034' -cmd '.timeout 250' "$QOBUZ_DB" "
          SELECT
            coalesce(json_extract(data, '$.performer.name'), json_extract(data, '$.album.artist.name'), ''),
            coalesce(title, json_extract(data, '$.title'), '')
          FROM L_Track
          WHERE track_id = '$QOBUZ_CURRENT_TRACK_ID'
          LIMIT 1;
        " 2>/dev/null)
        IFS=$'\034' read -r QOBUZ_ARTIST QOBUZ_TITLE <<< "$QOBUZ_METADATA"

        if [ -n "$QOBUZ_ARTIST" ] || [ -n "$QOBUZ_TITLE" ]; then
          QOBUZ_ARTIST=${QOBUZ_ARTIST:-Unknown}
          QOBUZ_TITLE=${QOBUZ_TITLE:-Unknown}
          CACHE_TRACK_ID="$QOBUZ_CURRENT_TRACK_ID"
          CACHE_ARTIST="$QOBUZ_ARTIST"
          CACHE_TITLE="$QOBUZ_TITLE"
          QOBUZ_TRACK_CHANGED=1
          printf '%s\034%s\034%s\n' "$CACHE_TRACK_ID" "$CACHE_ARTIST" "$CACHE_TITLE" > "$QOBUZ_SONG_FILE"
        fi
      fi

      if [ -n "$QOBUZ_PLAYING_TRACK_ID" ] && { [ -n "$QOBUZ_ARTIST" ] || [ -n "$QOBUZ_TITLE" ]; }; then
        QOBUZ_ARTIST=${QOBUZ_ARTIST:-Unknown}
        QOBUZ_TITLE=${QOBUZ_TITLE:-Unknown}
        printf '%s\t%s\n' "$QOBUZ_ARTIST" "$QOBUZ_TITLE" > "$LAST_SONG_FILE"
        printf 'qobuz\n' > "$ACTIVE_SOURCE_FILE"
        TEXT_COLOR="0xffffffff"
        set_track "" "$QOBUZ_ARTIST" "$QOBUZ_TITLE"
        exit 0
      fi

      if [ "$ACTIVE_SOURCE" = "qobuz" ] && (( QOBUZ_POSITION_RECENT )) \
          && (( QOBUZ_TRACK_CHANGED )) \
          && { [ -n "$QOBUZ_ARTIST" ] || [ -n "$QOBUZ_TITLE" ]; }; then
        printf '%s\t%s\n' "$QOBUZ_ARTIST" "$QOBUZ_TITLE" > "$LAST_SONG_FILE"
        TEXT_COLOR="0xffffffff"
        set_track "" "$QOBUZ_ARTIST" "$QOBUZ_TITLE"
        exit 0
      fi

      if [ "$ACTIVE_SOURCE" = "qobuz" ] && (( QOBUZ_POSITION_RECENT )) \
          && [ "$CACHE_TRACK_ID" != "$QOBUZ_CURRENT_TRACK_ID" ] \
          && { [ -n "$CACHE_ARTIST" ] || [ -n "$CACHE_TITLE" ]; }; then
        TEXT_COLOR="0xffffffff"
        set_track "" "${CACHE_ARTIST:-Unknown}" "${CACHE_TITLE:-Unknown}"
        exit 0
      fi
    fi
  fi
fi

NOW_PLAYING=$(/opt/homebrew/bin/nowplaying-cli get --json title artist playbackRate 2>/dev/null)
IFS=$'\034' read -r PLAYBACK_RATE MEDIA_ARTIST MEDIA_TITLE < <(
  printf '%s' "$NOW_PLAYING" | jq -jr \
    '(.playbackRate // ""), "\u001c", (.artist // ""), "\u001c", (.title // ""), "\n"' 2>/dev/null
)

if [ "$PLAYBACK_RATE" = "0" ]; then
  TEXT_COLOR="0x80ffffff"
else
  TEXT_COLOR="0xffffffff"
fi

if { [ -z "$PLAYBACK_RATE" ] || [ "$PLAYBACK_RATE" = "0" ]; } \
    && [ "$ACTIVE_SOURCE" = "qobuz" ] \
    && [ "$CACHE_TRACK_ID" = "$QOBUZ_CURRENT_TRACK_ID" ] \
    && { [ -n "$QOBUZ_ARTIST" ] || [ -n "$QOBUZ_TITLE" ]; }; then
  QOBUZ_ARTIST=${QOBUZ_ARTIST:-Unknown}
  QOBUZ_TITLE=${QOBUZ_TITLE:-Unknown}
  TEXT_COLOR="0x80ffffff"
  set_track "" "$QOBUZ_ARTIST" "$QOBUZ_TITLE"
  exit 0
fi

if [ -n "$MEDIA_ARTIST" ] || [ -n "$MEDIA_TITLE" ]; then
  ARTIST=${MEDIA_ARTIST:-Unknown}
  TITLE=${MEDIA_TITLE:-Unknown}
  printf '%s\t%s\n' "$ARTIST" "$TITLE" > "$LAST_SONG_FILE"
  printf 'feishin\n' > "$ACTIVE_SOURCE_FILE"
  set_track "" "$ARTIST" "$TITLE"
  exit 0
fi

if (( ! FEISHIN_RUNNING )); then
  sketchybar --set "$NAME" icon=" Not playing" label="" icon.color="0xffffffff" label.color="0xffffffff"
  exit 0
fi

# Feishin normally publishes metadata through macOS Now Playing. Fall back to
# Navidrome for versions that fail to do so, but keep network work off battery.
if ! "$HOME/.local/bin/should-run-background-job" >/dev/null 2>&1; then
  if [ -f "$LAST_SONG_FILE" ]; then
    ARTIST=$(cut -f1 "$LAST_SONG_FILE")
    TITLE=$(cut -f2 "$LAST_SONG_FILE")
    TEXT_COLOR="0x80ffffff"
    set_track "" "$ARTIST" "$TITLE"
  else
    sketchybar --set "$NAME" icon=" Battery" label="" icon.color="0xffffffff" label.color="0xffffffff"
  fi
  exit 0
fi

NAVIDROME_ENV="$HOME/.config/navidrome/env"
if [ ! -f "$NAVIDROME_ENV" ]; then
  sketchybar --set "$NAME" icon=" No config" label="" icon.color="0xffffffff" label.color="0xffffffff"
  exit 0
fi
source "$NAVIDROME_ENV"

USERNAME="${NAVIDROME_USERNAME:-alec}"

# Fast reachability gate: when Navidrome is unreachable (off home network or
# NAS down), skip the curl block entirely. Without this, the plugin pays a
# 3-second curl timeout every 5 seconds whenever Wi-Fi isn't on the home LAN.
ND_HOSTPORT="${NAVIDROME_URL#*://}"   # strip scheme
ND_HOSTPORT="${ND_HOSTPORT%%/*}"      # strip any path
ND_HOST="${ND_HOSTPORT%:*}"
ND_PORT="${ND_HOSTPORT##*:}"
case "$NAVIDROME_URL" in
  https://*) ND_PORT_DEFAULT=443 ;;
  *) ND_PORT_DEFAULT=80 ;;
esac
[ "$ND_HOST" = "$ND_PORT" ] && ND_PORT=$ND_PORT_DEFAULT
if ! /usr/bin/nc -zw1 "$ND_HOST" "$ND_PORT" >/dev/null 2>&1; then
  sketchybar --set "$NAME" icon=" Offline" label="" icon.color="0xffffffff" label.color="0xffffffff"
  exit 0
fi

# Keep only valid token fields. Cache contents are JSON data, never shell code.
navidrome_auth_fields() {
  jq -ce 'select(type == "object")
    | select(.subsonicToken | type == "string" and length > 0)
    | select(.subsonicSalt | type == "string" and length > 0)
    | {subsonicToken, subsonicSalt}' 2>/dev/null
}

AUTH_INFO=""
if [ -f "$AUTH_CACHE" ] && [ ! -L "$AUTH_CACHE" ] \
    && [ "$(stat -f %Lp "$AUTH_CACHE" 2>/dev/null)" = 600 ]; then
  AUTH_AGE=$(( $(date +%s) - $(stat -f %m "$AUTH_CACHE") ))
  if [ "$AUTH_AGE" -ge 0 ] && [ "$AUTH_AGE" -lt "$AUTH_MAX_AGE" ]; then
    AUTH_INFO=$(navidrome_auth_fields < "$AUTH_CACHE")
  fi
fi

# Authenticate if no valid cached token. Legacy shell caches fail validation.
if [ -z "$AUTH_INFO" ]; then
  # The sentinel preserves password newlines through command substitution.
  # security -w appends one output newline, which is not part of the password.
  if PASSWORD=$(security find-generic-password -s 'Navidrome' -a "$USERNAME" -w 2>/dev/null && printf '\001'); then
    PASSWORD=${PASSWORD%$'\001'}
    PASSWORD=${PASSWORD%$'\n'}
  else
    PASSWORD=""
  fi
  if [ -z "$PASSWORD" ]; then
    sketchybar --set "$NAME" icon=" No keychain" label="" icon.color="0xffffffff" label.color="0xffffffff"
    exit 0
  fi
  # Encode through stdin, so credentials never appear on jq or curl argv.
  AUTH_INFO=$(printf '%s\0%s' "$USERNAME" "$PASSWORD" |
    jq -Rs 'split("\u0000") | {username: .[0], password: .[1]}' |
    curl -s --max-time 3 "$NAVIDROME_URL/auth/login" \
      -H "Content-Type: application/json" \
      --data @- 2>/dev/null)

  if [ -z "$AUTH_INFO" ] || [ "$AUTH_INFO" = "null" ]; then
    sketchybar --set "$NAME" icon=" Offline" label="" icon.color="0xffffffff" label.color="0xffffffff"
    exit 0
  fi

  AUTH_INFO=$(printf '%s' "$AUTH_INFO" | navidrome_auth_fields)
  if [ -z "$AUTH_INFO" ]; then
    sketchybar --set "$NAME" icon=" Auth failed" label="" icon.color="0xffffffff" label.color="0xffffffff"
    exit 0
  fi

  # Replace atomically with a private data file, without following old symlinks.
  (
    umask 077
    CACHE_TMP=$(mktemp "${AUTH_CACHE}.XXXXXX") || exit 1
    trap 'rm -f "$CACHE_TMP"' EXIT
    # macOS mv -h replaces a final symlink, even when it points to a directory.
    # An actual directory is not a cache file and must never receive token files.
    if [ -d "$AUTH_CACHE" ] && [ ! -L "$AUTH_CACHE" ]; then
      exit 1
    fi
    printf '%s\n' "$AUTH_INFO" > "$CACHE_TMP" && mv -fh "$CACHE_TMP" "$AUTH_CACHE"
  )
fi

# Encode every form value, including cached token/salt, without shell evaluation.
CURRENT_SONG=$(printf '%s\0%s' "$USERNAME" "$AUTH_INFO" |
  jq -Rsr 'split("\u0000") | .[0] as $user | (.[1] | fromjson) as $auth
    | "u=\($user | @uri)&t=\($auth.subsonicToken | @uri)&s=\($auth.subsonicSalt | @uri)&v=1.8.0&c=SketchyBar&f=json"' |
  curl -s --max-time 3 "$NAVIDROME_URL/rest/getNowPlaying" --data @- 2>/dev/null |
  jq -r '.["subsonic-response"].nowPlaying.entry[0] // empty' 2>/dev/null)

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
    sketchybar --set "$NAME" icon=" Not playing" label="" icon.color="0xffffffff" label.color="0xffffffff"
  fi
fi
