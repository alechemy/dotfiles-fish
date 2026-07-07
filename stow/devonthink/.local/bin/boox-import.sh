#!/bin/bash
# boox-import.sh <pdf-path>
#
# Converts a Boox handwritten-note PDF export to a monochrome TIFF and files it
# into DEVONthink's Lorebook, deduplicating by the SourceFile custom-metadata
# key at the point of import. Invoked once per file by boox-import-watcher.sh.
#
#   - A note never seen before imports into /00_INBOX, stamped Handwritten +
#     SourceFile, and enters the standard pipeline.
#   - A re-export of an existing note (same SourceFile) replaces that record's
#     backing file in place — preserving its UUID, name, tags, and WikiLinks —
#     resets its pipeline flags, and moves it back to /00_INBOX for a fresh pass.
#   - A byte-identical re-export (the Boox re-emits unchanged notebooks on every
#     device sync) is a no-op.
#
# Dedup runs here, before any record exists, so it never depends on smart-rule
# trash or action-ordering semantics. Untitled Notebook-<n> exports are dropped
# upstream by the watcher, so a SourceFile match is always the same
# intentionally-named notebook being updated, never a name collision.
#
# Safeguards:
#   - gtimeout caps the ImageMagick conversion (and its Ghostscript delegate)
#   - oversized TIFFs are quarantined rather than imported
#   - a failed conversion quarantines the source PDF so it is not retried forever
#   - a failed DEVONthink operation leaves the PDF in place for the next retry

set -uo pipefail

export PATH="/opt/homebrew/bin:$PATH"

MAX_SIZE_MB=50
MAX_SIZE_BYTES=$((MAX_SIZE_MB * 1024 * 1024))
TIMEOUT_SECONDS=120
QUARANTINE_DIR="$HOME/Desktop/DT_Import_Errors"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"

INPUT_FILE="${1:?usage: boox-import.sh <pdf-path>}"

log()  { "$PIPELINE_LOG" boox-import INFO "$*" || true; }
warn() { "$PIPELINE_LOG" boox-import WARN "$*" || true; }
notify() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"$2\"" >/dev/null 2>&1 || true
}

if [[ ! -f "$INPUT_FILE" ]]; then
    warn "source PDF vanished before import, skipping: $INPUT_FILE"
    exit 0
fi

# Only the pipeline driver mutates DEVONthink; a follower leaves the synced PDF
# in place for the driver to pick up and delete.
if ! "$HOME/.local/bin/should-run-dt-driver" >/dev/null 2>&1; then
    log "follower role, leaving PDF for the driver: $INPUT_FILE"
    exit 0
fi

mkdir -p "$QUARANTINE_DIR"

# Convert into a temp dir, not alongside the PDF: the Notebooks folder is
# Maestral-synced, so writing the intermediate TIFF there would round-trip an
# upload then a delete through Dropbox on every import. The TIFF keeps the
# PDF's basename so a quarantined oversized file stays identifiable.
WORK_DIR=$(mktemp -d -t boox-import)
trap 'rm -rf "$WORK_DIR"' EXIT
BASENAME=$(basename "${INPUT_FILE%.pdf}")
TIFF_FILE="$WORK_DIR/$BASENAME.tiff"

# Group4 (monochrome) compression at 300 DPI. -background white -alpha remove
# -alpha off flattens layers, which is critical for vector PDFs. gtimeout
# enforces a hard limit on magick and its Ghostscript delegate.
if ! gtimeout "$TIMEOUT_SECONDS" magick -density 300 "$INPUT_FILE" \
        -background white -alpha remove -alpha off \
        -threshold 50% -monochrome -compress Group4 "$TIFF_FILE"; then
    mv "$INPUT_FILE" "$QUARANTINE_DIR/"
    warn "PDF->TIFF conversion failed, quarantined PDF: $INPUT_FILE"
    notify "Failed to convert PDF to TIFF. Moved PDF to Desktop/DT_Import_Errors." "DT Import Error"
    exit 1
fi

FILE_SIZE=$(stat -f%z "$TIFF_FILE" 2>/dev/null || echo 0)
if [[ "$FILE_SIZE" -gt "$MAX_SIZE_BYTES" ]]; then
    mv "$TIFF_FILE" "$QUARANTINE_DIR/"
    mv "$INPUT_FILE" "$QUARANTINE_DIR/"
    warn "TIFF too large (${FILE_SIZE} bytes), quarantined PDF + TIFF: $INPUT_FILE"
    notify "File too large (${FILE_SIZE} bytes). Moved PDF & TIFF to Desktop/DT_Import_Errors." "DT Import Skipped"
    exit 1
fi

# File the TIFF into Lorebook, deduplicating by SourceFile. New notes import to
# /00_INBOX (a first-class direct-arrival entry point); a re-export replaces the
# existing record's file in place and re-primes it. 'tell application id "DNtp"'
# auto-launches DEVONthink if it is not running.
DEDUP_SCRIPT="$WORK_DIR/dedup-import.applescript"
cat > "$DEDUP_SCRIPT" <<'APPLESCRIPT'
on run argv
    set tiffPath to item 1 of argv
    set baseName to item 2 of argv
    tell application id "DNtp"
        set theDatabase to database "Lorebook"
        set inboxGroup to get record at "/00_INBOX" in theDatabase

        -- Unquoted value is deliberate: DEVONthink's mdsourcefile== matches the
        -- multi-word key this way but not when the value is quoted. The loop
        -- below confirms the exact match, so a broad hit set is harmless.
        set existingMatch to missing value
        set searchResults to search "mdsourcefile==" & baseName in (root of theDatabase)
        if searchResults is not missing value then
            repeat with matchedRecord in searchResults
                if (get custom meta data for "SourceFile" from matchedRecord) is baseName then
                    set existingMatch to matchedRecord
                    exit repeat
                end if
            end repeat
        end if

        if existingMatch is missing value then
            set theRecord to import tiffPath to inboxGroup
            add custom meta data baseName for "SourceFile" to theRecord
            add custom meta data 1 for "Handwritten" to theRecord
            add custom meta data 1 for "NeedsProcessing" to theRecord
            return "imported"
        end if

        set existingPath to path of existingMatch
        try
            set newHash to do shell script "shasum -a 256 " & quoted form of tiffPath & " | cut -d' ' -f1"
            set oldHash to do shell script "shasum -a 256 " & quoted form of existingPath & " | cut -d' ' -f1"
            if newHash is oldHash then
                -- Covers a prior run that died after the mv but before re-indexing:
                -- the bytes already match, so re-read them into DT's index.
                synchronize record existingMatch
                return "identical"
            end if
        end try

        -- Stage + atomic mv (same volume) so the record's backing file is always
        -- either the old or the new content — a direct cp truncates in place, and
        -- a mid-write failure would corrupt the record with no undo.
        set stagePath to existingPath & ".dt-replace-tmp"
        try
            do shell script "cp " & quoted form of tiffPath & " " & quoted form of stagePath & " && /bin/mv -f " & quoted form of stagePath & " " & quoted form of existingPath
        on error errMsg
            do shell script "rm -f " & quoted form of stagePath
            error "content replace failed: " & errMsg
        end try
        synchronize record existingMatch

        -- Reset pipeline flags for a fresh pass; NameLocked pins the name so
        -- existing WikiLinks survive the re-enrichment.
        add custom meta data 0 for "Recognized" to existingMatch
        add custom meta data 0 for "Commented" to existingMatch
        add custom meta data 0 for "AIEnriched" to existingMatch
        add custom meta data 1 for "NameLocked" to existingMatch
        add custom meta data 1 for "Handwritten" to existingMatch
        add custom meta data 1 for "NeedsProcessing" to existingMatch
        move record existingMatch to inboxGroup

        return "updated"
    end tell
end run
APPLESCRIPT

STATUS=$(/usr/bin/osascript "$DEDUP_SCRIPT" "$TIFF_FILE" "$BASENAME" 2>"$WORK_DIR/osa.err")
OSA_RC=$?
if [[ $OSA_RC -eq 0 ]]; then
    rm -f "$INPUT_FILE"
    case "$STATUS" in
        imported)  log "imported new notebook to Lorebook 00_INBOX, removed source PDF: $INPUT_FILE" ;;
        updated)   log "re-export replaced existing notebook in place, removed source PDF: $INPUT_FILE" ;;
        identical) log "identical re-export, no change; removed source PDF: $INPUT_FILE" ;;
        *)         log "filed to Lorebook (status='$STATUS'), removed source PDF: $INPUT_FILE" ;;
    esac
else
    warn "DEVONthink dedup/import failed ($(cat "$WORK_DIR/osa.err" 2>/dev/null)), leaving PDF in place for retry: $INPUT_FILE"
    notify "Failed to import/replace note in DEVONthink." "DT Import Error"
    exit 1
fi
