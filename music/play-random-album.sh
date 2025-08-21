#!/bin/bash

# Configuration
NAVIDROME_URL=""
USERNAME=""
PASSWORD=""
MIN_TRACKS=5
MAX_TRACKS=30
EXCLUDE_RECENT_DAYS=3
NAVIDROME_APP_NAME="Navidrome"

# Function to get auth token
get_token() {
  curl -s "$NAVIDROME_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" |
    jq -r '.token // empty'
}

# Function to get random album
get_random_album() {
  local token=$1
  local cutoff_date=$(date -j -v-${EXCLUDE_RECENT_DAYS}d +%Y-%m-%d)

  curl -s "$NAVIDROME_URL/api/album?_limit=1000" \
    -H "X-ND-Authorization: Bearer $token" |
    jq -r --arg cutoff "$cutoff_date" '
        .[] |
        select(.songCount >= '$MIN_TRACKS' and .songCount <= '$MAX_TRACKS') |
        select(.playDate == null or (.playDate | split("T")[0]) < $cutoff) |
        .id' |
    shuf -n 1
}

get_random_album() {
  local token=$1
  local cutoff_date=$(date -j -v-${EXCLUDE_RECENT_DAYS}d +%Y-%m-%d)

  echo "Looking for albums not played in last $EXCLUDE_RECENT_DAYS day(s)..." >&2

  # First try: exclude recently played albums
  ALBUM_ID=$(curl -s "$NAVIDROME_URL/api/album?_limit=1000" \
    -H "X-ND-Authorization: Bearer $token" |
    jq -r --arg cutoff "$cutoff_date" '.[] | select(.songCount >= '$MIN_TRACKS' and .songCount <= '$MAX_TRACKS') | select(.playDate == null or (.playDate | split("T")[0]) < $cutoff) | .id' |
    shuf -n 1)

  # If no results, fall back to any album (without date restriction)
  if [ -z "$ALBUM_ID" ]; then
    echo "No unplayed albums found, trying any album..." >&2
    ALBUM_ID=$(curl -s "$NAVIDROME_URL/api/album?_limit=1000" \
      -H "X-ND-Authorization: Bearer $token" |
      jq -r '.[] | select(.songCount >= '$MIN_TRACKS' and .songCount <= '$MAX_TRACKS') | .id' |
      shuf -n 1)
  fi

  echo "$ALBUM_ID"
}

# Function to get album info
get_album_info() {
  local token=$1
  local album_id=$2
  curl -s "$NAVIDROME_URL/api/album/$album_id" \
    -H "X-ND-Authorization: Bearer $token" |
    jq -r '"\(.name) by \(.albumArtist) (\(.maxYear // "Unknown"))"'
}

# Function to auto-play using Keyboard Maestro
auto_play_album() {
  open "kmtrigger://macro=Click%20Navidrome%20Play%20Button"
}

# Main execution
echo "Getting authentication token..."
TOKEN=$(get_token)
if [ -z "$TOKEN" ]; then
  echo "Failed to authenticate with Navidrome"
  exit 1
fi

echo "Finding random album..."
ALBUM_ID=$(get_random_album "$TOKEN")
if [ -z "$ALBUM_ID" ]; then
  echo "No albums found with $MIN_TRACKS-$MAX_TRACKS tracks"
  exit 1
fi

# # Get album info for feedback
ALBUM_INFO=$(get_album_info "$TOKEN" "$ALBUM_ID")

# Open the webapp to the album page
open -a "$NAVIDROME_APP_NAME" "$NAVIDROME_URL/app/#/album/$ALBUM_ID/show"

# Auto-play the album
auto_play_album
