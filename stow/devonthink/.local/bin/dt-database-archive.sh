#!/bin/bash
# dt-database-archive.sh
# Weekly verified archive of the Lorebook database, with rotation.
#
# CloudKit sync and Time Machine both exist, but neither yields a
# consistency-guaranteed restore point: Time Machine can capture the
# .dtBase2 package mid-write, and a sync-store loss plus a dead machine has
# no automated recovery path. This produces a DEVONthink-native archive
# (verify, then compress) the way File > Export > Database Archive does.
#
# Driven by launchd DAILY (com.user.dt-database-archive.plist) but archives
# at most once per INTERVAL_DAYS: a battery or asleep-at-03:30 skip
# self-heals the next night instead of waiting a full week.
#
# Usage:
#   dt-database-archive.sh            # launchd-driven (gated, cadence-limited)
#   dt-database-archive.sh --force    # archive now, bypassing gates + cadence
#
# Archives land in ~/Backups/DEVONthink/ (KEEP most recent are retained).
# The archive is only rotated in after `verify database` reports zero
# errors and the zip passes a CRC check, so a corrupt database can never
# push the last good archive out of the rotation.

set -euo pipefail

DB_NAME="Lorebook"
DEST_DIR="$HOME/Backups/DEVONthink"
KEEP=4
INTERVAL_DAYS=7
STATE_DIR="$HOME/.local/state/devonthink"
SUCCESS_FILE="$STATE_DIR/dt-database-archive.last-success"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"

log()  { "$PIPELINE_LOG" dt-database-archive INFO "$*"; }
warn() { "$PIPELINE_LOG" dt-database-archive WARN "$*"; }
err()  { "$PIPELINE_LOG" dt-database-archive ERROR "$*"; }

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

"$HOME/.local/bin/pipeline-record-run" dt-database-archive 0 || true

if [[ "$FORCE" -ne 1 ]]; then
    if [[ -f "$SUCCESS_FILE" ]]; then
        LAST=$(cat "$SUCCESS_FILE" 2>/dev/null || echo 0)
        if [[ "$LAST" =~ ^[0-9]+$ ]] && \
           (( $(date +%s) - LAST < INTERVAL_DAYS * 86400 )); then
            exit 0
        fi
    fi
    "$HOME/.local/bin/should-run-background-job" || exit 0
    "$HOME/.local/bin/should-run-dt-driver" || exit 0
fi

if ! pgrep -qx DEVONthink; then
    log "skip: DEVONthink not running"
    exit 0
fi

mkdir -p "$DEST_DIR" "$STATE_DIR"
DEST="$DEST_DIR/${DB_NAME}-$(date +%Y-%m-%d).dtBase2.zip"

TMPSCRIPT=$(mktemp /tmp/dt-archive.XXXXXX.scpt)
trap 'rm -f "$TMPSCRIPT"' EXIT
cat > "$TMPSCRIPT" << 'APPLESCRIPT'
on run argv
    set dbName to item 1 of argv
    set destPath to item 2 of argv
    tell application id "DNtp"
        try
            set theDB to database dbName
        on error
            return "error: database not open: " & dbName
        end try
        -- Compressing a multi-GB package outruns the 2-minute AppleEvent
        -- default; verify is not instant either.
        with timeout of 3600 seconds
            set errCount to verify database theDB
            if errCount is not 0 then
                return "verify-failed: " & errCount & " error(s)/orphan(s)"
            end if
            set ok to compress database theDB to destPath
            if not ok then return "error: compress returned false"
        end timeout
        return "ok"
    end tell
end run
APPLESCRIPT

log "archiving ${DB_NAME} to $DEST"
AS_OUTPUT=$(/usr/bin/osascript "$TMPSCRIPT" "$DB_NAME" "$DEST" 2>&1) || {
    err "archive AppleScript failed: $AS_OUTPUT"
    exit 1
}
case "$AS_OUTPUT" in
    ok) ;;
    verify-failed:*)
        err "database verify failed, archive skipped: ${AS_OUTPUT#verify-failed: } — repair via Tools > Verify & Repair, prior archives retained"
        exit 1
        ;;
    *)
        err "archive failed: $AS_OUTPUT"
        exit 1
        ;;
esac

if [[ ! -s "$DEST" ]]; then
    err "archive missing or empty: $DEST"
    exit 1
fi
if ! unzip -tq "$DEST" >/dev/null 2>&1; then
    err "archive failed CRC check, deleting: $DEST"
    rm -f "$DEST"
    exit 1
fi

date +%s > "$SUCCESS_FILE"
SIZE=$(du -h "$DEST" | cut -f1)
log "archived ${DB_NAME} (${SIZE}) to $DEST"

ls -1t "$DEST_DIR/${DB_NAME}-"*.dtBase2.zip 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
    rm -f "$old"
    log "rotated out $old"
done
