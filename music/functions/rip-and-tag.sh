#!/bin/sh

# --- CONFIGURATION ---
INBOX_DIR="/share/Media/Music/Inbox"
AUTO_ADD_DIR="/share/Media/Music/Music/Media.localized/Automatically Add to Music.localized"
RIP_CONFIG="/share/CACHEDEV1_DATA/streamrip/config.toml"

# --- COMMANDS ---
PYTHON_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/python"
RIP_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip"
TAGGER_SCRIPT="/share/CACHEDEV1_DATA/python-apps/tagger.py"

# --- ALLOWED GENRES ---
ALLOWED_GENRES="Ambient Bluegrass Classical Country Electronic Experimental Folk Hip-Hop Jazz Lo-Fi Mashup Pop R&B Reggae Rock Soundtrack Unknown"

# --- ARGUMENT PARSING ---
COMPILATION_FLAG=""
URL=""
GENRE=""

while [ $# -gt 0 ]; do
  case $1 in
  --compilation)
    COMPILATION_FLAG="--compilation"
    shift
    ;;
  *)
    if [ -z "$URL" ]; then
      URL="$1"
    elif [ -z "$GENRE" ]; then
      GENRE="$1"
    else
      echo "ERROR: Too many arguments"
      echo "Usage: $0 [--compilation] \"<album_url>\" \"<Genre>\""
      exit 1
    fi
    shift
    ;;
  esac
done

# --- INPUT VALIDATION ---
if [ -z "$URL" ] || [ -z "$GENRE" ]; then
  echo "Usage: $0 [--compilation] \"<album_url>\" \"<Genre>\""
  echo ""
  echo "Options:"
  echo "  --compilation    Mark the album as a compilation"
  echo ""
  echo "Allowed genres:"
  echo "  $ALLOWED_GENRES"
  exit 1
fi

# --- GENRE VALIDATION ---
GENRE_VALID=0
for allowed_genre in $ALLOWED_GENRES; do
  if [ "$GENRE" = "$allowed_genre" ]; then
    GENRE_VALID=1
    break
  fi
done

if [ $GENRE_VALID -eq 0 ]; then
  echo "ERROR: Invalid genre '$GENRE'"
  echo ""
  echo "Allowed genres are:"
  for allowed_genre in $ALLOWED_GENRES; do
    echo "  $allowed_genre"
  done
  exit 1
fi

# --- STEP 1: DOWNLOAD ---
echo "--> Step 1: Downloading album from URL..."
"$RIP_CMD" --config-path "$RIP_CONFIG" url "$URL"
if [ $? -ne 0 ]; then
  echo "ERROR: streamrip download failed."
  exit 1
fi

# --- STEP 2: FIND THE NEW ALBUM ---
echo "--> Step 2: Finding the newly downloaded album directory..."
ALBUM_PATH=$(ls -td1 "$INBOX_DIR"/*/ | head -n 1)

if [ -z "$ALBUM_PATH" ]; then
  echo "ERROR: Could not find a newly downloaded album directory in $INBOX_DIR"
  exit 1
fi

ALBUM_PATH=$(echo "$ALBUM_PATH" | sed 's:/*$::')
echo "    Found: $ALBUM_PATH"

# --- STEP 3: TAG THE FILES ---
echo "--> Step 3: Tagging files with genre '$GENRE'..."
if [ -n "$COMPILATION_FLAG" ]; then
  echo "    Also marking as compilation..."
  "$PYTHON_CMD" "$TAGGER_SCRIPT" --genre "$GENRE" $COMPILATION_FLAG "$ALBUM_PATH"
else
  "$PYTHON_CMD" "$TAGGER_SCRIPT" --genre "$GENRE" "$ALBUM_PATH"
fi

# --- STEP 4: MOVE TO AUTO-ADD FOLDER ---
echo "--> Step 4: Moving album to Apple Music folder..."
ALBUM_NAME=$(basename "$ALBUM_PATH")
rsync -av --remove-source-files "$ALBUM_PATH/" "$AUTO_ADD_DIR/$ALBUM_NAME/"

# --- STEP 5: FIX PERMISSIONS ---
echo "--> Step 5: Setting final permissions..."
chmod -R 775 "$AUTO_ADD_DIR/$ALBUM_NAME"

# --- STEP 6: TRIGGER THE IMPORT ---
echo "--> Step 6: Touching files to ensure import..."
find "$AUTO_ADD_DIR/$ALBUM_NAME" -type f -exec touch {} +

echo ""
echo "Workflow complete! Album should now be importing into Music.app."
