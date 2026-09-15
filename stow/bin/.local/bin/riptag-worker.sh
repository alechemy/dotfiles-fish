#!/bin/sh

# Worker script for the riptag pipeline.
# Downloads an album, tags it, and organizes it into the music library.
#
# Called by the `riptag` fish function. In remote (NAS) mode, riptag deploys
# this script to /tmp via scp and runs it — no permanent copy on the NAS.
#
# Usage:
#   riptag-worker.sh [--compilation] [--playlist-mode] [--year YYYY] [--replaces PATH] [--local] <url> <genre>
#   riptag-worker.sh [--compilation] [--playlist-mode] [--year YYYY] [--replaces PATH] [--local] --resume <session-id> <genre>
#
# --replaces PATH enables guarded re-download mode: PATH (relative to the library
# root) is the existing album folder this download should replace. music-organize.py
# replaces it only if the new download is no worse on track count and quality;
# otherwise the existing folder is kept and the download discarded.
#
# --playlist-mode unifies metadata across the downloaded folder so the tracks
# are filed as one album: forces albumartist="Various Artists" and embeds the
# first track's cover art into every track.
# --year sets the same year on every track.
#
# Exit codes:
#   0  All tracks downloaded successfully; album tagged and organized.
#   1  Hard error (crash, bad args, etc.)
#   2  Some tracks failed; session ID written to $RIPTAG_RESULT_DIR/resume-id.
#      Metadata stays on this host at ~/.local/state/riptag/sessions/<id>.meta.
#   3  Existing library copy kept; the new download was not an improvement.
#   4  Album organized, but the NAS permission fix (local mode) couldn't reach
#      the NAS over SSH. Files are in the library; perms need a manual retry.
#
# Environment variables:
#   TAGGER_SCRIPT        Path to tagger.py (auto-set by riptag in remote mode)
#   ORGANIZER_SCRIPT     Path to music-organize.py (auto-set by riptag remotely)
#   STREAMRIP_DOWNLOADS  Local download dir (default: ~/StreamripDownloads)
#   LOCAL_RIP            Path to local rip command (default: rip)
#   RIPTAG_RESULT_DIR    Private caller-owned result directory (default: run dir)
#   RIPTAG_STATE_DIR     Session metadata directory (default: ~/.local/state/riptag)
#   RIPTAG_LOCK_DIR      Host-wide lock override (default: /tmp/riptag-worker.lock)
#
# To upgrade streamrip on the NAS:
#   sudo /share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/pip install --upgrade \
#     https://github.com/nathom/streamrip/archive/refs/tags/v2.2.0.tar.gz


# --- CONFIGURATION (NAS defaults) ---
INBOX_DIR="/share/Media/Music-Inbox"
LIBRARY_DIR="/share/Media/Music"
RIP_CONFIG="/share/CACHEDEV1_DATA/streamrip/config.toml"
STATE_DIR="${RIPTAG_STATE_DIR:-$HOME/.local/state/riptag}"
SESSION_DIR="$STATE_DIR/sessions"
NAS_HOST="admin@192.168.50.54"
NAS_TS_HOST="admin@100.89.43.9"

PYTHON_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/python"
RIP_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip"

# --- ARGUMENT PARSING ---
COMPILATION_FLAG=""
PLAYLIST_MODE=0
LOCAL_MODE=0
RESUME_ID=""
URL=""
GENRE=""
REPLACES=""

YEAR=""

while [ $# -gt 0 ]; do
  case $1 in
    --compilation) COMPILATION_FLAG="--compilation"; shift ;;
    --no-compilation) COMPILATION_FLAG="--no-compilation"; shift ;;
    --playlist-mode) PLAYLIST_MODE=1; shift ;;
    --year) YEAR="$2"; shift 2 ;;
    --local) LOCAL_MODE=1; shift ;;
    --resume) RESUME_ID="$2"; shift 2 ;;
    --replaces) REPLACES="$2"; shift 2 ;;
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
  LIBRARY_DIR="/Volumes/Media/Music"
  PYTHON_CMD="${LOCAL_PYTHON:-python3}"
  RIP_CMD="${LOCAL_RIP:-rip}"
  : "${TAGGER_SCRIPT:=$HOME/.local/bin/tagger.py}"
  : "${ORGANIZER_SCRIPT:=$HOME/.local/bin/music-organize.py}"
  RIP_CONFIG=""
else
  : "${TAGGER_SCRIPT:=/share/CACHEDEV1_DATA/python-apps/tagger.py}"
  : "${ORGANIZER_SCRIPT:=/share/CACHEDEV1_DATA/python-apps/music-organize.py}"
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

if [ $LOCAL_MODE -eq 1 ] && [ ! -d "$LIBRARY_DIR" ]; then
  if [ -x "$HOME/.local/bin/mount-nas.sh" ]; then
    printf "%s\n" "--> Music library is not mounted; trying the NAS mount helper..."
    "$HOME/.local/bin/mount-nas.sh"
  fi
fi

if [ ! -d "$LIBRARY_DIR" ]; then
  printf "%s\n" "ERROR: Music library is unavailable: $LIBRARY_DIR"
  if [ $LOCAL_MODE -eq 1 ]; then
    printf "%s\n" "Mount the NAS Media share in Finder or run ~/.local/bin/mount-nas.sh, then retry."
  fi
  printf "%s\n" "No download was started. Existing downloads are unchanged."
  exit 1
fi

if [ ! -r "$LIBRARY_DIR" ] || [ ! -w "$LIBRARY_DIR" ] || [ ! -x "$LIBRARY_DIR" ]; then
  printf "%s\n" "ERROR: Music library requires read, write, and directory access: $LIBRARY_DIR"
  printf "%s\n" "No download was started. Existing downloads are unchanged."
  exit 1
fi

# Serialize workers on this host. Never guess that an existing lock is stale.
umask 077
mkdir -p "$SESSION_DIR" || exit 1
LOCK_DIR="${RIPTAG_LOCK_DIR:-/tmp/riptag-worker.lock}"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf "%s\n" "ERROR: another riptag worker holds $LOCK_DIR. If interrupted, verify no worker is running before removing that directory."
  exit 1
fi
printf "%s\n" "$$" > "$LOCK_DIR/pid"
trap 'rm -f "$LOCK_DIR/pid"; rmdir "$LOCK_DIR"' EXIT
trap 'exit 1' HUP INT TERM

# Each download owns a private directory. Resume reuses only its recorded one.
if [ -n "$RESUME_ID" ]; then
  case "$RESUME_ID" in *[!a-f0-9]*) printf "%s\n" "ERROR: invalid session ID"; exit 1 ;; esac
  META_FILE="$SESSION_DIR/$RESUME_ID.meta"
  if [ ! -f "$META_FILE" ]; then
    printf "%s\n" "ERROR: no resume metadata on this host for $RESUME_ID"
    exit 1
  fi
  RUN_DIR=$(sed -n '6p' "$META_FILE")
  if ! "$PYTHON_CMD" -c 'import os, sys
root, run = map(os.path.realpath, sys.argv[1:])
assert os.path.dirname(run) == root and os.path.basename(run).startswith(".riptag-run.") and os.path.isdir(run)
' "$INBOX_DIR" "$RUN_DIR"; then
    printf "%s\n" "ERROR: resume directory is not an owned inbox run"
    exit 1
  fi
else
  RUN_DIR=$(mktemp -d "$INBOX_DIR/.riptag-run.XXXXXX") || exit 1
fi
DOWNLOAD_DIR="$RUN_DIR/downloads"
mkdir -p "$DOWNLOAD_DIR" || exit 1
RIP_LOG_FILE="$RUN_DIR/rip-download.log"
RIP_EXIT_FILE="$RUN_DIR/rip-exit-status.txt"
MANIFEST_FILE="$RUN_DIR/organize-manifest.txt"
RESULT_DIR="${RIPTAG_RESULT_DIR:-$RUN_DIR}"
mkdir -p "$RESULT_DIR" || exit 1
RESUME_FILE="$RESULT_DIR/resume-id"
rm -f "$RESUME_FILE"
printf "%s\n" "Run files: $RUN_DIR"

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
      "$RIP_CMD" -f "$DOWNLOAD_DIR" url "$URL" 2>&1
    else
      "$RIP_CMD" --config-path "$RIP_CONFIG" -f "$DOWNLOAD_DIR" url "$URL" 2>&1
    fi
  fi
  printf "%s\n" "$?" > "$RIP_EXIT_FILE"
} | tee "$RIP_LOG_FILE"
RIP_EXIT=$(cat "$RIP_EXIT_FILE")
rm -f "$RIP_EXIT_FILE"

# --- CHECK FOR FAILED TRACKS ---
# Extract session ID from streamrip's "rip resume <id>" output. Rich wraps at
# the terminal width it probes from stdin even when stdout is piped, and its
# log table interleaves a file:line column — flatten whitespace and drop the
# location tokens so matching never depends on the rendered line layout.
FLAT_LOG=$(tr -s '[:space:]' ' ' < "$RIP_LOG_FILE" | sed 's/[a-zA-Z_]*\.py:[0-9]* //g')
SESSION_ID=$(printf "%s\n" "$FLAT_LOG" | grep -o 'rip resume [a-f0-9]*' | tail -1 | awk '{print $3}')

if [ -n "$SESSION_ID" ]; then
  printf "\n"
  printf "%s\n" "Failed tracks:"
  printf "%s\n" "$FLAT_LOG" | grep -o "Persistent error downloading track '[^']*'" | sed "s/Persistent error downloading track '\(.*\)'/  - \1/" | sort -u
  printf "\n"

  # Keep partial download for resume — don't delete it
  printf "%s" "$SESSION_ID" > "$RESUME_FILE"
  # Save genre + compilation + playlist-mode + year so resume doesn't need them re-specified
  PLAYLIST_FLAG_SAVED=""
  if [ $PLAYLIST_MODE -eq 1 ]; then PLAYLIST_FLAG_SAVED="--playlist-mode"; fi
  printf "%s\n%s\n%s\n%s\n%s\n%s\n" "$GENRE" "$COMPILATION_FLAG" "$PLAYLIST_FLAG_SAVED" "$YEAR" "$REPLACES" "$RUN_DIR" > "$SESSION_DIR/$SESSION_ID.meta"
  printf "%s\n" "Resume on this host: riptag --resume=$SESSION_ID"
  exit 2
fi

if [ "$RIP_EXIT" -ne 0 ]; then
  printf "%s\n" "ERROR: streamrip download failed; run files retained at $RUN_DIR"
  exit 1
fi

# --- STEP 2: FIND THIS RUN'S ALBUM ---
printf "%s\n" "--> Step 2: Finding the owned download..."
ALBUM_COUNT=$(find "$DOWNLOAD_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
if [ "$ALBUM_COUNT" -ne 1 ]; then
  printf "%s\n" "ERROR: expected exactly one album in $DOWNLOAD_DIR; found $ALBUM_COUNT. No files tagged."
  exit 1
fi
ALBUM_PATH=$(find "$DOWNLOAD_DIR" -mindepth 1 -maxdepth 1 -type d)

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
TAG_EXIT=$?
if [ "$TAG_EXIT" -ne 0 ]; then
  printf "%s\n" "ERROR: Tagging failed; files left at: $ALBUM_PATH"
  exit 1
fi

# --- STEP 4: ORGANIZE INTO THE LIBRARY ---
printf "%s\n" "--> Step 4: Organizing album into the library..."
rm -f "$MANIFEST_FILE"
if [ -n "$REPLACES" ]; then
  printf "%s\n" "    Re-download mode: replaces '$REPLACES' only if the new download is no worse."
  "$PYTHON_CMD" "$ORGANIZER_SCRIPT" \
    --library-root "$LIBRARY_DIR" \
    --on-collision replace \
    --manifest "$MANIFEST_FILE" \
    --replaces "$REPLACES" \
    "$ALBUM_PATH"
  ORGANIZE_EXIT=$?
else
  "$PYTHON_CMD" "$ORGANIZER_SCRIPT" \
    --library-root "$LIBRARY_DIR" \
    --on-collision replace \
    --manifest "$MANIFEST_FILE" \
    "$ALBUM_PATH"
  ORGANIZE_EXIT=$?
fi
if [ "$ORGANIZE_EXIT" -eq 3 ]; then
  # Guard kept the existing library copy; music-organize.py discarded the download.
  # Exit 3 propagates so riptag reports "kept" rather than "organized".
  printf "%s\n" "--> Kept the existing library copy; the new download was not an improvement."
  rm -f "$MANIFEST_FILE"
  exit 3
elif [ "$ORGANIZE_EXIT" -ne 0 ]; then
  printf "%s\n" "ERROR: Organizing failed; files left at: $ALBUM_PATH"
  rm -f "$MANIFEST_FILE"
  exit 1
fi

# --- STEP 5: FIX PERMISSIONS ---
# NAS mode: music-organize.py already set permissions on the native filesystem.
# Local mode: it wrote across the SMB mount, where chmod does not stick — redo
# it on the NAS side, one organized album folder (and its artist folder) at a time.
PERM_FAILED=0
FAILED_CMDS=""
if [ $LOCAL_MODE -eq 1 ] && [ -f "$MANIFEST_FILE" ]; then
  printf "%s\n" "--> Step 5: Setting permissions on the NAS..."
  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$NAS_HOST" true 2>/dev/null \
      && ssh -o ConnectTimeout=5 -o BatchMode=yes "$NAS_TS_HOST" true 2>/dev/null; then
    printf "%s\n" "    LAN unreachable — using Tailscale ($NAS_TS_HOST)."
    NAS_HOST="$NAS_TS_HOST"
  fi
  while IFS= read -r album_dir; do
    [ -z "$album_dir" ] && continue
    nas_dir=$(printf "%s" "$album_dir" | sed 's#^/Volumes/Media#/share/Media#')
    artist_dir=$(dirname "$nas_dir")
    esc_album=$(printf "%s" "$nas_dir" | sed "s/'/'\\\\''/g")
    esc_artist=$(printf "%s" "$artist_dir" | sed "s/'/'\\\\''/g")
    remote_cmd="chmod 775 '$esc_artist'; chmod -R 775 '$esc_album'"
    # Retry once: a fresh SSH to the LAN can fail transiently (VPN race, or a
    # first-run macOS Local Network permission prompt that returns EHOSTUNREACH
    # — "No route to host" — until granted).
    if ! ssh -o ConnectTimeout=10 "$NAS_HOST" "$remote_cmd"; then
      sleep 3
      if ! ssh -o ConnectTimeout=10 "$NAS_HOST" "$remote_cmd"; then
        PERM_FAILED=1
        FAILED_CMDS="${FAILED_CMDS}  ssh $NAS_HOST \"$remote_cmd\"
"
      fi
    fi
  done < "$MANIFEST_FILE"
fi

if [ "$PERM_FAILED" -eq 1 ]; then
  printf "\n"
  printf "%s\n" "WARNING: The album is downloaded, tagged, and organized into the library,"
  printf "%s\n" "but setting permissions on the NAS failed (couldn't reach $NAS_HOST"
  printf "%s\n" "over SSH). Navidrome may not be able to read the files until you fix this."
  printf "%s\n" "Once the NAS is reachable, re-run:"
  printf "\n"
  printf "%s" "$FAILED_CMDS"
  # Keep the manifest so the failed paths aren't lost before a retry.
  exit 4
fi

# --- STEP 6: RUNNABILITY SCORING ---
# Local mode only: the NAS can't run essentia. NAS-mode rips are scored by the
# nightly runnability-sync launchd job instead.
if [ $LOCAL_MODE -eq 1 ] && [ -f "$MANIFEST_FILE" ]; then
  printf "%s\n" "--> Step 6: Scoring runnability..."
  while IFS= read -r album_dir; do
    [ -z "$album_dir" ] && continue
    if ! { "$HOME/.local/bin/runnability.py" analyze --force "$album_dir" \
        && "$HOME/.local/bin/runnability.py" write --force "$album_dir"; }; then
      printf "%s\n" "    WARNING: runnability scoring failed; the nightly sync will retry."
    fi
  done < "$MANIFEST_FILE"
fi

rm -f "$MANIFEST_FILE"
if [ -n "$RESUME_ID" ]; then
  rm -f "$SESSION_DIR/$RESUME_ID.meta"
fi
