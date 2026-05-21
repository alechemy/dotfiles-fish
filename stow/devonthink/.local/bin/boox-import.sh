#!/bin/bash
# boox-import.sh <pdf-path>
#
# Converts a Boox handwritten-note PDF export to a monochrome TIFF and imports
# it into DEVONthink's Lorebook inbox with the Handwritten custom-metadata flag
# set at the point of provenance. Invoked once per file by boox-import-watcher.sh.
#
# Formerly the action of a Hazel rule (devonthink/utils/hazel-boox-import.sh);
# the file-path argument is now a plain positional rather than Hazel's $1.
#
# Safeguards:
#   - gtimeout caps the ImageMagick conversion (and its Ghostscript delegate)
#   - oversized TIFFs are quarantined rather than imported
#   - a failed conversion quarantines the source PDF so it is not retried forever

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

mkdir -p "$QUARANTINE_DIR"

# Convert into a temp dir, not alongside the PDF: the Notebooks folder is
# Maestral-synced, so writing the intermediate TIFF there would round-trip an
# upload then a delete through Dropbox on every import. The TIFF keeps the
# PDF's basename so a quarantined oversized file stays identifiable.
WORK_DIR=$(mktemp -d -t boox-import)
trap 'rm -rf "$WORK_DIR"' EXIT
TIFF_FILE="$WORK_DIR/$(basename "${INPUT_FILE%.pdf}").tiff"

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

# Import the TIFF into Lorebook's inbox and flag it as a handwritten Boox note.
# 'tell application id "DNtp"' auto-launches DEVONthink if it is not running.
if /usr/bin/osascript \
    -e 'on run argv' \
    -e '    set theFilePath to item 1 of argv' \
    -e '    tell application id "DNtp"' \
    -e '        set theRecord to import theFilePath to incoming group of database "Lorebook"' \
    -e '        add custom meta data 1 for "Handwritten" to theRecord' \
    -e '    end tell' \
    -e 'end run' \
    "$TIFF_FILE" >/dev/null; then
    rm -f "$INPUT_FILE"
    log "imported to Lorebook, removed source PDF: $INPUT_FILE"
else
    warn "DEVONthink import failed, leaving PDF in place for retry: $INPUT_FILE"
    notify "Failed to import TIFF into DEVONthink." "DT Import Error"
    exit 1
fi
