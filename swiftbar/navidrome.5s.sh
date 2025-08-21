#!/bin/bash

# <swiftbar.title>Navidrome Now Playing</swiftbar.title>

NAVIDROME_URL=""
USERNAME=""
PASSWORD=""

# Function to get subsonic auth credentials
get_subsonic_auth() {
  curl -s "$NAVIDROME_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" 2>/dev/null
}

# Function to get current playing info using Subsonic API
get_now_playing() {
  local auth_info="$1"
  local subsonic_token=$(echo "$auth_info" | jq -r '.subsonicToken' 2>/dev/null)
  local subsonic_salt=$(echo "$auth_info" | jq -r '.subsonicSalt' 2>/dev/null)

  if [ -z "$subsonic_token" ] || [ "$subsonic_token" = "null" ]; then
    return
  fi

  # Use Subsonic getNowPlaying endpoint
  curl -s "$NAVIDROME_URL/rest/getNowPlaying" \
    -d "u=$USERNAME" \
    -d "t=$subsonic_token" \
    -d "s=$subsonic_salt" \
    -d "v=1.8.0" \
    -d "c=SwiftBar" \
    -d "f=json" 2>/dev/null |
    jq -r '.["subsonic-response"].nowPlaying.entry[0] // empty' 2>/dev/null
}

# Function to escape markdown special characters
escape_markdown() {
  local text="$1"
  # Escape characters that have special meaning in markdown
  echo "$text" | sed 's/\\/\\\\/g; s/\*/\\*/g; s/_/\\_/g; s/\[/\\[/g; s/\]/\\]/g; s/(/\\(/g; s/)/\\)/g; s/#/\\#/g; s/`/\\`/g'
}

# Function to format song from Subsonic response
format_song_info() {
  local song_data="$1"

  if [ -n "$song_data" ] && [ "$song_data" != "null" ]; then
    local artist=$(echo "$song_data" | jq -r '.artist // "Unknown Artist"' 2>/dev/null)
    local title=$(echo "$song_data" | jq -r '.title // "Unknown Title"' 2>/dev/null)

    # Escape markdown characters
    artist=$(escape_markdown "$artist")
    title=$(escape_markdown "$title")

    echo "$artist – **$title**" # Using en dash
  fi
}

# Function to truncate for menu bar (with smart Unicode handling)
format_for_menubar() {
  local track_info="$1"
  local max_length=50

  # Use character count instead of byte count for Unicode safety
  local char_count=$(echo "$track_info" | wc -m | tr -d ' ')

  if [ $char_count -gt $max_length ]; then
    # Extract artist and title using the en dash
    if [[ "$track_info" =~ ^(.+)\ –\ \*\*(.+)\*\*$ ]]; then
      local artist="${BASH_REMATCH[1]}"
      local title="${BASH_REMATCH[2]}"
      local prefix="$artist – **"
      local suffix="**"

      # Calculate available space for title (in characters)
      local prefix_length=$(echo "$prefix" | wc -m | tr -d ' ')
      local suffix_length=$(echo "$suffix" | wc -m | tr -d ' ')
      local available=$((max_length - prefix_length - suffix_length - 3)) # -3 for "..."

      if [ $available -gt 0 ]; then
        # Use cut to safely truncate Unicode
        local truncated_title=$(echo "$title" | cut -c1-$available)
        echo "$prefix$truncated_title...$suffix"
      else
        # Fallback: truncate whole string safely
        echo "$track_info" | cut -c1-$max_length | sed 's/$/.../'
      fi
    else
      # Fallback: truncate whole string safely
      echo "$track_info" | cut -c1-$max_length | sed 's/$/.../'
    fi
  else
    echo "$track_info"
  fi
}

# Main execution
AUTH_INFO=$(get_subsonic_auth)

if [ -z "$AUTH_INFO" ] || [ "$AUTH_INFO" = "null" ]; then
  echo "🎵 Navidrome (offline)"
  echo "---"
  echo "Connection failed"
  exit 0
fi

CURRENT_SONG=$(get_now_playing "$AUTH_INFO")

if [ -n "$CURRENT_SONG" ] && [ "$CURRENT_SONG" != "null" ]; then
  SONG_INFO=$(format_song_info "$CURRENT_SONG")

  if [ -n "$SONG_INFO" ] && [ "$SONG_INFO" != "null - null" ]; then
    # Format for menu bar display
    FORMATTED_INFO=$(format_for_menubar "$SONG_INFO")
    echo "$FORMATTED_INFO | md=true"
    echo "---"
    echo "Now Playing: $SONG_INFO"
    echo "Open Navidrome | href=$NAVIDROME_URL/app/"
    echo "---"
    echo "Refresh | refresh=true"
  else
    echo "🎵 Loading..."
    echo "---"
    echo "Getting track info..."
  fi
else
  echo "🎵 Not playing"
  echo "---"
  echo "No music playing"
  echo "Open Navidrome | href=$NAVIDROME_URL/app/"
  echo "---"
  echo "Refresh | refresh=true"
fi
