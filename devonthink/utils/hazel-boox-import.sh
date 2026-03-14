#!/bin/bash
# hazel-boox-import.sh
# Hazel rule action: converts Boox PDF exports to monochrome TIFFs and imports
# them into DEVONthink's Lorebook database with the Handwritten flag set.
#
# Safeguards:
#   - Timeout on ImageMagick conversion (gtimeout)
#   - File-size cap — oversized TIFFs are quarantined
#   - Failed conversions quarantined so Hazel doesn't retry endlessly

export PATH="/opt/homebrew/bin:$PATH"

# Configuration
MAX_SIZE_MB=50
MAX_SIZE_BYTES=$((MAX_SIZE_MB * 1024 * 1024))
TIMEOUT_SECONDS=120
QUARANTINE_DIR="$HOME/Desktop/DT_Import_Errors"

INPUT_FILE="$1"
TIFF_FILE="${INPUT_FILE%.pdf}.tiff"

# Ensure quarantine directory exists
mkdir -p "$QUARANTINE_DIR"

# Convert PDF to TIFF
# Using Group4 compression (Monochrome) for maximum space savings + 300 DPI
# Added: -background white -alpha remove -alpha off (flattens layers, critical for vector PDFs)
# Safeguard: Bail if conversion takes longer than TIMEOUT_SECONDS
# Using gtimeout to enforce hard limit on magick and its delegates (ghostscript)
if /opt/homebrew/bin/gtimeout "$TIMEOUT_SECONDS" /opt/homebrew/bin/magick -density 300 "$INPUT_FILE" -background white -alpha remove -alpha off -threshold 50% -monochrome -compress Group4 "$TIFF_FILE"; then

    # Get file size in bytes
    FILE_SIZE=$(stat -f%z "$TIFF_FILE")

    if [ "$FILE_SIZE" -gt "$MAX_SIZE_BYTES" ]; then
        # File is too big - Quarantine it
        mv "$TIFF_FILE" "$QUARANTINE_DIR/"
        mv "$INPUT_FILE" "$QUARANTINE_DIR/" # Move original PDF too
        # Notify user
        osascript -e "display notification \"File too large (${FILE_SIZE} bytes). Moved PDF & TIFF to Desktop/DT_Import_Errors.\" with title \"DT Import Skipped\""
    else
        # File size is OK — Import to DEVONthink via AppleScript and
        # flag as a handwritten Boox note at the point of provenance.
        # 'tell application id "DNtp"' will auto-launch DT if needed.
        if osascript \
            -e 'on run argv' \
            -e '    set theFilePath to item 1 of argv' \
            -e '    tell application id "DNtp"' \
            -e '        set theRecord to import theFilePath to incoming group of database "Lorebook"' \
            -e '        add custom meta data 1 for "Handwritten" to theRecord' \
            -e '    end tell' \
            -e 'end run' \
            "$TIFF_FILE"; then
            # Import succeeded — clean up source files
            rm "$TIFF_FILE"
            rm "$INPUT_FILE"
        else
            osascript -e "display notification \"Failed to import TIFF into DEVONthink.\" with title \"DT Import Error\""
        fi
    fi

else
    # Conversion failed - Quarantine the original PDF so Hazel doesn't retry endlessly
    mv "$INPUT_FILE" "$QUARANTINE_DIR/"
    osascript -e "display notification \"Failed to convert PDF to TIFF. Moved PDF to Desktop/DT_Import_Errors.\" with title \"DT Import Error\""
fi
