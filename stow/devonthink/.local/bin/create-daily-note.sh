#!/bin/bash
# create-daily-note.sh
# Creates a daily note in DEVONthink's 10_DAILY group.
# Designed to be run by launchd every morning on a headless Mac mini.
#
# Features:
#   - Idempotent: skips creation if a note already exists for a given date
#   - Backfills any gap since the last existing daily note (no-arg mode)
#   - Triggers a DEVONthink cloud sync after each new note
#   - Generates the full formatted heading (e.g., "Wednesday, January 21, 2026")
#   - Logs to ~/Library/Logs/dt-daily-note.log
#
# Usage:
#   ./create-daily-note.sh              # backfill from last note through today
#   ./create-daily-note.sh 2026-03-15   # create a note for a specific date

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_NAME="Lorebook"
GROUP_PATH="/10_DAILY"
LOG_FILE="$HOME/Library/Logs/dt-daily-note.log"

# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [daily-note] $*" >> "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Temp AppleScript files — written once, cleaned up on exit
# ---------------------------------------------------------------------------
FIND_SCRIPT=$(mktemp /tmp/dt-daily-find.XXXXXX.scpt)
CREATE_SCRIPT=$(mktemp /tmp/dt-daily-create.XXXXXX.scpt)
trap 'rm -f "$FIND_SCRIPT" "$CREATE_SCRIPT"' EXIT

# Returns the most recent YYYY-MM-DD note name found in the group, or "none".
cat > "$FIND_SCRIPT" <<'FIND_APPLESCRIPT'
on run argv
    set dbName to item 1 of argv
    set groupPath to item 2 of argv

    tell application id "DNtp"
        try
            set targetDB to database dbName
        on error
            return "error: database " & dbName & " not found"
        end try

        set destGroup to get record at groupPath in targetDB
        if destGroup is missing value then
            return "none"
        end if

        set latestDate to ""
        repeat with aRecord in children of destGroup
            set recName to name of aRecord
            -- Match YYYY-MM-DD (length 10, dashes at positions 5 and 8)
            if length of recName is 10 and character 5 of recName is "-" and character 8 of recName is "-" then
                if recName > latestDate then
                    set latestDate to recName
                end if
            end if
        end repeat

        if latestDate is "" then
            return "none"
        end if
        return latestDate
    end tell
end run
FIND_APPLESCRIPT

# Creates a single daily note. Idempotent — returns "skip:…" if it already exists.
cat > "$CREATE_SCRIPT" <<'CREATE_APPLESCRIPT'
on run argv
    set dbName to item 1 of argv
    set groupPath to item 2 of argv
    set targetDate to item 3 of argv
    set noteFilename to item 4 of argv
    set headingDate to item 5 of argv

    set noteContent to "# " & headingDate & return & return & "- " & return

    tell application id "DNtp"
        try
            set targetDB to database dbName
        on error
            return "error: database " & dbName & " not found"
        end try

        -- Locate (or create) the destination group
        set destGroup to get record at groupPath in targetDB
        if destGroup is missing value then
            set destGroup to create record with {name:"10_DAILY", type:group} in root of targetDB
        end if

        -- Idempotency: look for an existing note with the same filename
        set existingChildren to children of destGroup
        repeat with aRecord in existingChildren
            if filename of aRecord is noteFilename then
                return "skip: note already exists"
            end if
        end repeat

        -- Create the markdown record, then set its content
        set newRecord to create record with {name:targetDate, type:markdown} in destGroup
        set plain text of newRecord to noteContent

        -- Tag the record for smart group filtering
        set tags of newRecord to {"type/daily"}

        -- Trigger cloud sync so the note is available on other devices
        try
            synchronize database targetDB
        on error errMsg
            return "ok: created " & noteFilename & " (sync failed: " & errMsg & ")"
        end try

        return "ok: created and synced " & noteFilename
    end tell
end run
CREATE_APPLESCRIPT

# ---------------------------------------------------------------------------
# Helper: create one note for a given YYYY-MM-DD string
# ---------------------------------------------------------------------------
create_note_for_date() {
  local date_str="$1"
  local date_arg heading_date note_filename as_output exit_code

  date_arg=$(echo "$date_str" | tr -d '-')
  heading_date=$(date -j -f "%Y%m%d" "$date_arg" "+%A, %B %-d, %Y")
  note_filename="${date_str}.md"

  as_output=$(/usr/bin/osascript "$CREATE_SCRIPT" \
    "$DATABASE_NAME" \
    "$GROUP_PATH" \
    "$date_str" \
    "$note_filename" \
    "$heading_date")
  exit_code=$?

  log "${as_output}"

  if [[ $exit_code -ne 0 ]]; then
    log "AppleScript exited with status ${exit_code} for ${date_str}"
  fi

  return $exit_code
}

# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
if [[ ${1:-} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  # Explicit date argument: create that single note (original behaviour)
  log "Creating note for ${1}"
  create_note_for_date "$1"
  exit $?
fi

# No argument: backfill from last existing note through today.
log "Querying last daily note in ${GROUP_PATH}"
LAST_DATE=$(/usr/bin/osascript "$FIND_SCRIPT" "$DATABASE_NAME" "$GROUP_PATH")

if [[ "$LAST_DATE" == "none" || "$LAST_DATE" == error:* ]]; then
  log "No prior daily note found; nothing to backfill (${LAST_DATE})"
  exit 0
fi

TODAY=$(date "+%Y-%m-%d")

if [[ "$LAST_DATE" == "$TODAY" ]]; then
  log "Last note is today (${TODAY}); nothing to backfill"
  exit 0
fi

# Build the list of dates from LAST_DATE+1 through TODAY.
# Uses date -v+1d to avoid DST-related second-counting errors.
FILL_DATES=()
current=$(date -j -v+1d -f "%Y-%m-%d" "$LAST_DATE" "+%Y-%m-%d")
while [[ ! "$current" > "$TODAY" ]]; do
  FILL_DATES+=("$current")
  current=$(date -j -v+1d -f "%Y-%m-%d" "$current" "+%Y-%m-%d")
done

log "Backfilling ${#FILL_DATES[@]} note(s): ${FILL_DATES[0]} → ${TODAY}"

for d in "${FILL_DATES[@]}"; do
  create_note_for_date "$d" || log "WARNING: failed to create note for ${d}"
done
