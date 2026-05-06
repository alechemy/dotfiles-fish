#!/bin/sh

# Worker script for the riptag pipeline.
# Downloads an album, tags it, and moves it to Apple Music's auto-add folder.
#
# Called by the `riptag` fish function. In remote (NAS) mode, riptag deploys
# this script to /tmp via scp and runs it — no permanent copy on the NAS.
#
# Usage:
#   riptag-worker.sh [--compilation] [--playlist-mode] [--year YYYY] [--local] <url> <genre>
#   riptag-worker.sh [--compilation] [--playlist-mode] [--year YYYY] [--local] --resume <session-id> <genre>
#
# --playlist-mode unifies metadata across the downloaded folder so Apple Music
# treats the tracks as one album: forces albumartist="Various Artists" and
# embeds the first track's cover art into every track.
# --year sets the same year on every track (Music.app uses year as part of
# album identity, so unifying it stops compilations from being split).
#
# Exit codes:
#   0  All tracks downloaded successfully; album tagged and copied.
#   1  Hard error (crash, bad args, etc.)
#   2  Some tracks failed; session ID written to /tmp/riptag-resume-id.
#
# Environment variables:
#   TAGGER_SCRIPT        Path to tagger.py (auto-set by riptag in remote mode)
#   STREAMRIP_DOWNLOADS  Local download dir (default: ~/StreamripDownloads)
#   LOCAL_RIP            Path to local rip command (default: rip)
#
# To upgrade streamrip on the NAS:
#   sudo /share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/pip install --upgrade \
#     https://github.com/nathom/streamrip/archive/refs/tags/v2.2.0.tar.gz


# --- CONFIGURATION (NAS defaults) ---
INBOX_DIR="/share/Media/Music/Inbox"
AUTO_ADD_DIR="/share/Media/Music/Music/Media.localized/Automatically Add to Music.localized"
RIP_CONFIG="/share/CACHEDEV1_DATA/streamrip/config.toml"
RIP_LOG_FILE="/tmp/rip-download.log"
RIP_EXIT_FILE="/tmp/rip-exit-status.txt"
RESUME_FILE="/tmp/riptag-resume-id"
NAS_HOST="admin@192.168.50.54"

PYTHON_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/python"
RIP_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip"

# --- ARGUMENT PARSING ---
COMPILATION_FLAG=""
PLAYLIST_MODE=0
LOCAL_MODE=0
RESUME_ID=""
URL=""
GENRE=""

YEAR=""

while [ $# -gt 0 ]; do
  case $1 in
    --compilation) COMPILATION_FLAG="--compilation"; shift ;;
    --playlist-mode) PLAYLIST_MODE=1; shift ;;
    --year) YEAR="$2"; shift 2 ;;
    --local) LOCAL_MODE=1; shift ;;
    --resume) RESUME_ID="$2"; shift 2 ;;
    *)
      if [ -n "$RESUME_ID" ]; then
        # Resume mode: only genre is positional
        if [ -z "$GENRE" ]; then GENRE="$1"
        else printf "%s\n" "ERROR: Too many arguments"; exit 1
        fi
      else
        # Normal mode: url then genre
        if [ -z "$URL" ]; then URL="$1"
        elif [ -z "$GENRE" ]; then GENRE="$1"
        else printf "%s\n" "ERROR: Too many arguments"; exit 1
        fi
      fi
      shift ;;
  esac
done

# --- LOCAL MODE OVERRIDES ---
if [ $LOCAL_MODE -eq 1 ]; then
  INBOX_DIR="${STREAMRIP_DOWNLOADS:-$HOME/StreamripDownloads}"
  PYTHON_CMD="${LOCAL_PYTHON:-python3}"
  RIP_CMD="${LOCAL_RIP:-rip}"
  : "${TAGGER_SCRIPT:=$HOME/.local/bin/tagger.py}"
  RIP_CONFIG=""
else
  : "${TAGGER_SCRIPT:=/share/CACHEDEV1_DATA/python-apps/tagger.py}"
fi

# --- VALIDATION ---
if [ -n "$RESUME_ID" ]; then
  if [ -z "$GENRE" ]; then
    printf "%s\n" "Usage: riptag-worker.sh --resume <session-id> [--local] [--compilation] <genre>"
    exit 1
  fi
else
  if [ -z "$URL" ] || [ -z "$GENRE" ]; then
    printf "%s\n" "Usage: riptag-worker.sh [--compilation] [--local] <url> <genre>"
    printf "%s\n" "Both URL and genre are required. Use 'riptag' for interactive mode."
    exit 1
  fi
fi

# --- STEP 1: DOWNLOAD ---
if [ -n "$RESUME_ID" ]; then
  printf "%s\n" "--> Step 1: Resuming download (session $RESUME_ID)..."
else
  printf "%s\n" "--> Step 1: Downloading album..."
fi
{
  if [ -n "$RESUME_ID" ]; then
    if [ $LOCAL_MODE -eq 1 ]; then
      "$RIP_CMD" resume "$RESUME_ID" 2>&1
    else
      "$RIP_CMD" --config-path "$RIP_CONFIG" resume "$RESUME_ID" 2>&1
    fi
  else
    if [ $LOCAL_MODE -eq 1 ]; then
      "$RIP_CMD" url "$URL" 2>&1
    else
      "$RIP_CMD" --config-path "$RIP_CONFIG" url "$URL" 2>&1
    fi
  fi
  printf "%s\n" "$?" > "$RIP_EXIT_FILE"
} | tee "$RIP_LOG_FILE"
RIP_EXIT=$(cat "$RIP_EXIT_FILE")
rm -f "$RIP_EXIT_FILE"

if [ "$RIP_EXIT" -ne 0 ]; then
  printf "%s\n" "ERROR: streamrip download failed."
  rm -f "$RIP_LOG_FILE"
  exit 1
fi

# --- CHECK FOR FAILED TRACKS ---
# Extract session ID from streamrip's "rip resume <id>" output
SESSION_ID=$(grep -o 'rip resume [a-f0-9]*' "$RIP_LOG_FILE" | tail -1 | awk '{print $3}')

if [ -n "$SESSION_ID" ]; then
  printf "\n"
  printf "%s\n" "Failed tracks:"
  grep "Persistent error downloading track" "$RIP_LOG_FILE" | sed "s/.*Persistent error downloading track '\([^']*\)'.*/  - \1/" | sort -u
  printf "\n"

  # Keep partial download for resume — don't delete it
  printf "%s" "$SESSION_ID" > "$RESUME_FILE"
  # Save genre + compilation + playlist-mode + year so resume doesn't need them re-specified
  PLAYLIST_FLAG_SAVED=""
  if [ $PLAYLIST_MODE -eq 1 ]; then PLAYLIST_FLAG_SAVED="--playlist-mode"; fi
  printf "%s\n%s\n%s\n%s\n" "$GENRE" "$COMPILATION_FLAG" "$PLAYLIST_FLAG_SAVED" "$YEAR" > "/tmp/riptag-$SESSION_ID.meta"
  rm -f "$RIP_LOG_FILE"
  exit 2
fi

rm -f "$RIP_LOG_FILE" "$RESUME_FILE"
# Clean up resume metadata on success
if [ -n "$RESUME_ID" ]; then
  rm -f "/tmp/riptag-$RESUME_ID.meta"
fi

# --- STEP 2: FIND THE NEW ALBUM ---
printf "%s\n" "--> Step 2: Finding the newly downloaded album..."
ALBUM_PATH=$(ls -td1 "$INBOX_DIR"/*/ | head -n 1)

if [ -z "$ALBUM_PATH" ]; then
  printf "%s\n" "ERROR: Could not find a newly downloaded album in $INBOX_DIR"
  exit 1
fi

ALBUM_PATH=$(printf "%s\n" "$ALBUM_PATH" | sed 's:/*$::')
printf "%s\n" "    Found: $ALBUM_PATH"

# --- STEP 3: TAG THE FILES ---
printf "%s\n" "--> Step 3: Tagging with genre '$GENRE'..."
if [ -n "$COMPILATION_FLAG" ]; then
  printf "%s\n" "    Also marking as compilation..."
fi
YEAR_ARGS=""
if [ -n "$YEAR" ]; then
  YEAR_ARGS="--year $YEAR"
  printf "%s\n" "    Setting year to $YEAR..."
fi
# shellcheck disable=SC2086
if [ $PLAYLIST_MODE -eq 1 ]; then
  printf "%s\n" "    Playlist mode: unifying albumartist + cover art..."
  "$PYTHON_CMD" "$TAGGER_SCRIPT" --genre "$GENRE" $COMPILATION_FLAG $YEAR_ARGS \
    --album-artist "Various Artists" --unify-cover "$ALBUM_PATH"
else
  "$PYTHON_CMD" "$TAGGER_SCRIPT" --genre "$GENRE" $COMPILATION_FLAG $YEAR_ARGS "$ALBUM_PATH"
fi

# --- STEP 4: MOVE TO AUTO-ADD FOLDER ---
printf "%s\n" "--> Step 4: Moving album to Apple Music folder..."
ALBUM_NAME=$(basename "$ALBUM_PATH")
if [ $LOCAL_MODE -eq 1 ]; then
  rsync -av "$ALBUM_PATH/" "$NAS_HOST:$AUTO_ADD_DIR/$ALBUM_NAME/"
  if [ $? -eq 0 ]; then
    rm -rf "$ALBUM_PATH"
  else
    printf "%s\n" "ERROR: Failed to copy album to NAS. Files kept at: $ALBUM_PATH"
    exit 1
  fi
else
  rsync -av --remove-source-files "$ALBUM_PATH/" "$AUTO_ADD_DIR/$ALBUM_NAME/"
fi

# --- STEP 5: FIX PERMISSIONS ---
printf "%s\n" "--> Step 5: Setting permissions..."
if [ $LOCAL_MODE -eq 1 ]; then
  ESCAPED_PATH=$(printf "%s\n" "$AUTO_ADD_DIR/$ALBUM_NAME" | sed "s/'/'\\\\''/g")
  ssh "$NAS_HOST" "chmod -R 775 '$ESCAPED_PATH'"
else
  chmod -R 775 "$AUTO_ADD_DIR/$ALBUM_NAME"
fi
