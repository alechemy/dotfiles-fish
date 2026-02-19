#!/bin/sh

# NAS Location: /share/CACHEDEV1_DATA/python-apps/rip-and-tag.sh

# --- CONFIGURATION ---
INBOX_DIR="/share/Media/Music/Inbox"
AUTO_ADD_DIR="/share/Media/Music/Music/Media.localized/Automatically Add to Music.localized"
RIP_CONFIG="/share/CACHEDEV1_DATA/streamrip/config.toml"
SEARCH_RESULTS_FILE="/tmp/rip-search-results.json"
RIP_LOG_FILE="/tmp/rip-download.log"
RIP_EXIT_FILE="/tmp/rip-exit-status.txt"

# --- COMMANDS ---
PYTHON_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/python"
RIP_CMD="/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip"
TAGGER_SCRIPT="/share/CACHEDEV1_DATA/python-apps/tagger.py"

# --- ALLOWED GENRES ---
ALLOWED_GENRES="Ambient Bluegrass Classical Country Electronic Experimental Folk Hip-Hop Jazz Lo-Fi Mashup Pop R&B Reggae Rock Soundtrack Unknown"

# --- ARGUMENT PARSING ---
COMPILATION_FLAG=""
URL_OR_QUERY=""
GENRE=""

while [ $# -gt 0 ]; do
  case $1 in
  --compilation)
    COMPILATION_FLAG="--compilation"
    shift
    ;;
  *)
    if [ -z "$URL_OR_QUERY" ]; then
      URL_OR_QUERY="$1"
    elif [ -z "$GENRE" ]; then
      GENRE="$1"
    else
      echo "ERROR: Too many arguments"
      echo "Usage: $0 [--compilation] \"<album_url|search_query>\" \"<Genre>\""
      exit 1
    fi
    shift
    ;;
  esac
done

# --- INPUT VALIDATION (URL/QUERY REQUIRED) ---
if [ -z "$URL_OR_QUERY" ]; then
  echo "Usage: $0 [--compilation] \"<album_url|search_query>\" \"<Genre>\""
  echo ""
  echo "Options:"
  echo "  --compilation    Mark the album as a compilation"
  echo ""
  echo "Arguments:"
  echo "  album_url        A Qobuz URL (https://play.qobuz.com/album/...)"
  echo "  search_query     An album name to search for"
  echo ""
  echo "Allowed genres:"
  echo "  $ALLOWED_GENRES"
  exit 1
fi

# --- GENRE PROMPT (IF OMITTED) ---
if [ -z "$GENRE" ]; then
  echo ""
  echo "Select a genre:"
  i=1
  for allowed_genre in $ALLOWED_GENRES; do
    printf "  [%d] %s\n" "$i" "$allowed_genre"
    i=$((i + 1))
  done

  max=$((i - 1))

  while :; do
    printf "\nEnter selection (1-%d) or 0 to cancel: " "$max"
    IFS= read -r choice

    case "$choice" in
      0)
        echo "Cancelled."
        exit 1
        ;;
      ''|*[!0-9]*)
        echo "Invalid selection. Enter a number."
        ;;
      *)
        if [ "$choice" -ge 1 ] 2>/dev/null && [ "$choice" -le "$max" ] 2>/dev/null; then
          idx=1
          for allowed_genre in $ALLOWED_GENRES; do
            if [ "$idx" -eq "$choice" ]; then
              GENRE="$allowed_genre"
              break
            fi
            idx=$((idx + 1))
          done
          break
        else
          echo "Invalid selection. Choose 1-$max or 0 to cancel."
        fi
        ;;
    esac
  done

  echo ""
  echo "✅ Genre selected: $GENRE"
  echo ""
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

# --- DETERMINE IF URL OR SEARCH QUERY ---
# Check if input starts with http:// or https://
case "$URL_OR_QUERY" in
http://*)
  URL="$URL_OR_QUERY"
  ;;
https://*)
  URL="$URL_OR_QUERY"
  ;;
*)
  # It's a search query - perform search
  echo "🔍 Searching for: \"$URL_OR_QUERY\"..."
  echo ""

  # Run the search command
  "$RIP_CMD" --config-path "$RIP_CONFIG" search -o "$SEARCH_RESULTS_FILE" -n 5 qobuz album "$URL_OR_QUERY"
  if [ $? -ne 0 ]; then
    echo "ERROR: Search failed."
    exit 1
  fi

  # Parse and display results using Python
  SELECTED_URL=$("$PYTHON_CMD" -c "
import json
import sys

try:
    with open('$SEARCH_RESULTS_FILE', 'r') as f:
        results = json.load(f)
except Exception as e:
    print(f'ERROR: Could not read search results: {e}', file=sys.stderr)
    sys.exit(1)

if not results:
    print('No results found.', file=sys.stderr)
    sys.exit(1)

print('', file=sys.stderr)
print('Search Results:', file=sys.stderr)
print('=' * 60, file=sys.stderr)

for i, item in enumerate(results, 1):
    album_id = item.get('id', 'unknown')
    desc = item.get('desc', 'Unknown Album')
    url = f'https://play.qobuz.com/album/{album_id}'
    print(f'', file=sys.stderr)
    print(f'  [{i}] {desc}', file=sys.stderr)
    print(f'      {url}', file=sys.stderr)

print('', file=sys.stderr)
print('=' * 60, file=sys.stderr)
print('', file=sys.stderr)
print('Enter selection (1-{}) or 0 to cancel: '.format(len(results)), file=sys.stderr, end='', flush=True)

choice_raw = sys.stdin.readline().strip()
try:
    choice = int(choice_raw)
except ValueError:
    print('ERROR: Invalid selection (not a number).', file=sys.stderr)
    sys.exit(1)

if choice == 0:
    print('', file=sys.stderr)
    print('Cancelled.', file=sys.stderr)
    sys.exit(1)

if choice < 1 or choice > len(results):
    print('ERROR: Selection out of range.', file=sys.stderr)
    sys.exit(1)

selected = results[choice - 1]
album_id = selected.get('id', '')
url = f'https://play.qobuz.com/album/{album_id}'
print(url)
")

  PYTHON_EXIT=$?
  if [ $PYTHON_EXIT -ne 0 ]; then
    exit 1
  fi

  if [ -z "$SELECTED_URL" ]; then
    echo "ERROR: No URL selected."
    exit 1
  fi

  URL="$SELECTED_URL"
  echo ""
  echo "✅ Selected: $URL"
  echo ""

  # Clean up search results file
  rm -f "$SEARCH_RESULTS_FILE"
  ;;
esac

# --- STEP 1: DOWNLOAD ---
echo "--> Step 1: Downloading album from URL..."
{ "$RIP_CMD" --config-path "$RIP_CONFIG" url "$URL" 2>&1; echo $? > "$RIP_EXIT_FILE"; } | tee "$RIP_LOG_FILE"
RIP_EXIT=$(cat "$RIP_EXIT_FILE")
rm -f "$RIP_EXIT_FILE"

if [ $RIP_EXIT -ne 0 ]; then
  echo "ERROR: streamrip download failed."
  rm -f "$RIP_LOG_FILE"
  exit 1
fi

# --- CHECK FOR FAILED TRACKS ---
if grep -q "Persistent error downloading track" "$RIP_LOG_FILE"; then
  echo ""
  echo "❌ DOWNLOAD INCOMPLETE: One or more tracks failed to download!"
  echo ""
  echo "Failed tracks:"
  grep "Persistent error downloading track" "$RIP_LOG_FILE" | sed "s/.*Persistent error downloading track '\([^']*\)'.*/  - \1/" | sort -u
  echo ""

  # Clean up the partial download from inbox
  PARTIAL_ALBUM=$(ls -td1 "$INBOX_DIR"/*/ 2>/dev/null | head -n 1)
  if [ -n "$PARTIAL_ALBUM" ]; then
    PARTIAL_NAME=$(basename "$PARTIAL_ALBUM")
    echo "🗑️  Removing partial download: $PARTIAL_NAME"
    rm -rf "$PARTIAL_ALBUM"
  fi

  rm -f "$RIP_LOG_FILE"
  echo ""
  echo "Please retry the download later, or try a different source."
  exit 1
fi

rm -f "$RIP_LOG_FILE"

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
