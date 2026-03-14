#!/bin/bash
# dt-watchdog.sh
#
# Ensures DEVONthink and Maestral (Dropbox sync) are running, and the
# Lorebook database is open.
# Launched every 5 minutes by launchd (com.user.dt-watchdog.plist).
# Logs to ~/Library/Logs/dt-watchdog.log.

# ── Configuration ─────────────────────────────────────────────────────────────
DB_NAME="Lorebook"
# Full path to the .dtBase2 package — adjust to match your system if needed
DB_PATH="$HOME/Databases/Lorebook.dtBase2"
LOG_FILE="$HOME/Library/Logs/dt-watchdog.log"
DT_APP_NAME="DEVONthink"      # must match the name shown in Activity Monitor
DT_LAUNCH_TIMEOUT=60           # seconds to wait for the process to appear
DT_INIT_WAIT=8                 # seconds to let DT auto-open its databases after launch
MAESTRAL_APP_NAME="Maestral"   # Dropbox sync client — must match Activity Monitor name
MAESTRAL_LAUNCH_TIMEOUT=30     # seconds to wait for the process to appear
# ──────────────────────────────────────────────────────────────────────────────

log() {
    printf '%s [dt-watchdog] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

# ── 1. Ensure DEVONthink is running ──────────────────────────────────────────
if ! pgrep -qx "$DT_APP_NAME"; then
    log "DEVONthink not running — launching '$DT_APP_NAME'"
    open -a "$DT_APP_NAME"

    waited=0
    while ! pgrep -qx "$DT_APP_NAME"; do
        sleep 2
        waited=$((waited + 2))
        if [ "$waited" -ge "$DT_LAUNCH_TIMEOUT" ]; then
            log "ERROR: '$DT_APP_NAME' did not appear within ${DT_LAUNCH_TIMEOUT}s"
            exit 1
        fi
    done

    # Let DT finish initialising and auto-open its remembered databases before
    # we query them — without this the database list may appear empty.
    log "DEVONthink process appeared — waiting ${DT_INIT_WAIT}s for initialisation"
    sleep "$DT_INIT_WAIT"
    log "DEVONthink launched"
fi

# ── 2. Ensure Maestral is running ─────────────────────────────────────────────
if ! pgrep -qx "$MAESTRAL_APP_NAME"; then
    log "Maestral not running — launching '$MAESTRAL_APP_NAME'"
    open -a "$MAESTRAL_APP_NAME"

    waited=0
    while ! pgrep -qx "$MAESTRAL_APP_NAME"; do
        sleep 2
        waited=$((waited + 2))
        if [ "$waited" -ge "$MAESTRAL_LAUNCH_TIMEOUT" ]; then
            log "ERROR: '$MAESTRAL_APP_NAME' did not appear within ${MAESTRAL_LAUNCH_TIMEOUT}s"
            exit 1
        fi
    done
    log "Maestral launched"
else
    log "OK: Maestral running"
fi

# ── 3. Ensure the target database is open ────────────────────────────────────
# Write the AppleScript to a temp file to avoid heredoc quoting issues.
TMPSCRIPT=$(mktemp /tmp/dt-watchdog.XXXXXX.scpt)
trap 'rm -f "$TMPSCRIPT"' EXIT

cat > "$TMPSCRIPT" << 'APPLESCRIPT'
on run argv
    set dbName to item 1 of argv
    set dbPath to item 2 of argv
    -- Resolve POSIX path to a file reference OUTSIDE the tell block.
    -- Inside a tell application block, POSIX file is dispatched to the target
    -- app rather than Standard Additions, causing "Can't get POSIX file" errors.
    set dbFile to POSIX file dbPath

    tell application id "DNtp"
        if dbName is in (name of every database) then
            return "open"
        end if

        -- Database not open — attempt to open it from disk
        try
            open database dbFile
            return "opened"
        on error errMsg
            return "error: " & errMsg
        end try
    end tell
end run
APPLESCRIPT

AS_OUTPUT=$(/usr/bin/osascript "$TMPSCRIPT" "$DB_NAME" "$DB_PATH" 2>&1)
AS_STATUS=$?

case "$AS_OUTPUT" in
    open)
        log "OK: DEVONthink running, '$DB_NAME' open"
        ;;
    opened)
        log "Database '$DB_NAME' was closed — opened successfully"
        ;;
    error:*)
        log "ERROR opening '$DB_NAME': ${AS_OUTPUT#error: }"
        exit 1
        ;;
    *)
        if [ "$AS_STATUS" -ne 0 ]; then
            log "ERROR: osascript failed (exit $AS_STATUS): $AS_OUTPUT"
            exit 1
        fi
        log "WARNING: unexpected response from DT: $AS_OUTPUT"
        ;;
esac
