#!/bin/bash
# boox-stage.sh <pdf-path>
#
# Stages a named Boox notebook PDF export for local processing by
# boox-process.py. Invoked per file by boox-import-watcher.sh. Handwritten
# notebooks never enter 00_INBOX unprocessed and never touch the
# cloud-backed smart-rule stages (DT OCR, comment formatting, chat
# enrichment); transcription happens on-device only.
#
# This script stays dumb and fast: byte-hash short-circuits (the Boox
# re-emits unchanged notebooks on every device sync), an atomic copy into
# the staging directory, and removal of the Maestral-synced source. All
# heavy work (rendering, page diffing, OCR, DEVONthink writes) belongs to
# boox-process.py, which runs on its own launchd schedule behind
# battery/idle gates.

set -uo pipefail

BOOX_DIR="$HOME/.local/state/devonthink/boox"
STAGING_DIR="$BOOX_DIR/staging"
DONE_DIR="$BOOX_DIR/done"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"

INPUT_FILE="${1:?usage: boox-stage.sh <pdf-path>}"

log()  { "$PIPELINE_LOG" boox-stage INFO "$*" || true; }
warn() { "$PIPELINE_LOG" boox-stage WARN "$*" || true; }

if [[ ! -f "$INPUT_FILE" ]]; then
    warn "source PDF vanished before staging, skipping: $INPUT_FILE"
    exit 0
fi

# Only the pipeline driver consumes the synced Notebooks folder; a follower
# leaves the PDF in place for the driver to pick up and delete.
if ! "$HOME/.local/bin/should-run-dt-driver" >/dev/null 2>&1; then
    log "follower role, leaving PDF for the driver: $INPUT_FILE"
    exit 0
fi

mkdir -p "$STAGING_DIR" "$DONE_DIR"

BASENAME=$(basename "$INPUT_FILE")
STAGED_FILE="$STAGING_DIR/$BASENAME"
DONE_MARKER="$DONE_DIR/$BASENAME.sha256"

INPUT_SHA=$(shasum -a 256 "$INPUT_FILE" | cut -d' ' -f1)

if [[ -f "$DONE_MARKER" && "$(cat "$DONE_MARKER")" == "$INPUT_SHA" ]]; then
    rm -f "$INPUT_FILE"
    log "identical re-export (already processed), removed source: $INPUT_FILE"
    exit 0
fi

if [[ -f "$STAGED_FILE" ]] && \
   [[ "$(shasum -a 256 "$STAGED_FILE" | cut -d' ' -f1)" == "$INPUT_SHA" ]]; then
    rm -f "$INPUT_FILE"
    log "identical re-export (already staged), removed source: $INPUT_FILE"
    exit 0
fi

# Stage + atomic mv so boox-process never reads a half-copied PDF.
TMP_FILE="$STAGED_FILE.tmp"
if ! cp "$INPUT_FILE" "$TMP_FILE"; then
    rm -f "$TMP_FILE"
    warn "failed to copy into staging, leaving source for retry: $INPUT_FILE"
    exit 1
fi
mv -f "$TMP_FILE" "$STAGED_FILE"
rm -f "$INPUT_FILE"
log "staged for local processing, removed source: $BASENAME"
