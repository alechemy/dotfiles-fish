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

notify() {
    /usr/bin/osascript -e 'on run argv' \
        -e 'display notification (item 1 of argv) with title (item 2 of argv)' \
        -e 'end run' -- "$1" "$2" >/dev/null 2>&1 || true
}

# ── 0. Record run time + alert on missed runs ────────────────────────────────
# launchd's StartInterval (300s) doesn't fire during sleep. The pseudo-interval
# is 3600, not 300: on this hardware every lid-close nap exceeds the 2×300s
# threshold, and a dozen expected sleep-gap ALERTs a week buries real ones.
# Only 2h+ gaps alert.
"$HOME/.local/bin/pipeline-record-run" dt-watchdog 3600 || true

# ── 1. Surface new failures from the pipeline logs ───────────────────────────
# Apart from Granola's failure records, every pipeline component logs
# failures to files only — nothing pushes them to the user, so breakage sits
# unnoticed until a manual log grep. Scan the log regions written since the
# previous watchdog run and raise one macOS notification per new failure
# signature. Signatures are digit-stripped (gap sizes, dates, and byte counts
# vary between occurrences of the same failure) and re-notify at most daily.
SCAN_STATE_DIR="$HOME/.local/state/devonthink/watchdog-scan"
NOTIFIED_FILE="$SCAN_STATE_DIR/notified.txt"
FAILURE_PATTERN=' ERROR | WARN |WARNING:|ALERT:'
MAX_NOTIFY_PER_LOG=5
mkdir -p "$SCAN_STATE_DIR"
touch "$NOTIFIED_FILE"

surface_line() {
    local line=$1 now sig last
    now=$(date +%s)
    sig=$(printf '%s' "$line" | sed -E 's/[0-9]+/N/g' | md5 -q)
    last=$(awk -v s="$sig" '$1 == s { t = $2 } END { print t }' "$NOTIFIED_FILE")
    if [[ -n "$last" && $((now - last)) -lt 86400 ]]; then
        return 0
    fi
    printf '%s %s\n' "$sig" "$now" >> "$NOTIFIED_FILE"
    log "surfacing: $line"
    notify "$line" "DT pipeline failure"
}

scan_log() {
    local logfile=$1 offset_file size offset count=0 line
    [[ -f "$logfile" ]] || return 0
    offset_file="$SCAN_STATE_DIR/$(basename "$logfile").offset"
    size=$(stat -f%z "$logfile" 2>/dev/null) || return 0
    if [[ ! -f "$offset_file" ]]; then
        # First run: start at the current end so history isn't replayed.
        echo "$size" > "$offset_file"
        return 0
    fi
    offset=$(cat "$offset_file" 2>/dev/null)
    [[ "$offset" =~ ^[0-9]+$ ]] || offset=0
    [[ "$size" -lt "$offset" ]] && offset=0
    echo "$size" > "$offset_file"
    [[ "$size" -le "$offset" ]] && return 0
    while IFS= read -r line; do
        count=$((count + 1))
        if [[ "$count" -le "$MAX_NOTIFY_PER_LOG" ]]; then
            surface_line "$line"
        fi
    done < <(tail -c +"$((offset + 1))" "$logfile" | grep -E --color=never "$FAILURE_PATTERN" || true)
    if [[ "$count" -gt "$MAX_NOTIFY_PER_LOG" ]]; then
        surface_line "$(basename "$logfile"): $((count - MAX_NOTIFY_PER_LOG)) further failure line(s) since last check"
    fi
}

# Prune notified signatures older than a week so the state file stays small.
PRUNE_NOW=$(date +%s)
awk -v cutoff=$((PRUNE_NOW - 604800)) '$2 >= cutoff' "$NOTIFIED_FILE" > "$NOTIFIED_FILE.tmp" \
    && mv "$NOTIFIED_FILE.tmp" "$NOTIFIED_FILE"

# dt-watchdog.log is deliberately not scanned: surfaced lines echo into it,
# which would feed the scanner its own output. The watchdog's own failure
# paths call notify directly instead.
scan_log "$HOME/Library/Logs/devonthink-pipeline.log"
scan_log "$HOME/Library/Logs/dt-daily-note.log"
scan_log "$HOME/Library/Logs/github-stars-import.log"

# Stuck captures: a .html still in the staging folder after 15 minutes was
# either missed by the watcher or failed ingest (failures stay in place by
# design). The daily re-notify doubles as a cleanup reminder.
while IFS= read -r stuck; do
    surface_line "stuck capture awaiting ingest: $(basename "$stuck")"
done < <(find "$HOME/Downloads/SingleFile" -name '*.html' -mmin +15 2>/dev/null || true)

# ── 2. Ensure DEVONthink is running ──────────────────────────────────────────
if ! pgrep -qx "$DT_APP_NAME"; then
    log "DEVONthink not running — launching '$DT_APP_NAME'"
    open -a "$DT_APP_NAME"

    waited=0
    while ! pgrep -qx "$DT_APP_NAME"; do
        sleep 2
        waited=$((waited + 2))
        if [ "$waited" -ge "$DT_LAUNCH_TIMEOUT" ]; then
            log "ERROR: '$DT_APP_NAME' did not appear within ${DT_LAUNCH_TIMEOUT}s"
            notify "DEVONthink did not launch within ${DT_LAUNCH_TIMEOUT}s" "DT pipeline failure"
            exit 1
        fi
    done

    # Let DT finish initialising and auto-open its remembered databases before
    # we query them — without this the database list may appear empty.
    log "DEVONthink process appeared — waiting ${DT_INIT_WAIT}s for initialisation"
    sleep "$DT_INIT_WAIT"
    log "DEVONthink launched"
fi

# ── 3. Ensure Maestral is running (skip on battery) ───────────────────────────
# A Shortcuts automation quits Maestral when the laptop unplugs; relaunching it
# here every 5 min would fight that automation. Defer to the same battery gate.
if "$HOME/.local/bin/should-run-background-job"; then
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
else
    log "skip: Maestral check (on battery)"
fi

# ── 4. Ensure the fswatch watcher agents are alive ───────────────────────────
# KeepAlive restarts a crashed watcher, but if launchd gives up (crash loop)
# or the agent was booted out, nothing else notices: the watchers record-run
# only at startup, so the missed-run tracker can never flag one that stays
# dead — the one failure mode it exists for.
for WATCHER_LABEL in com.user.singlefile-watcher com.user.boox-import-watcher; do
    if launchctl list "$WATCHER_LABEL" 2>/dev/null | grep -q --color=never '"PID"'; then
        continue
    fi
    log "ALERT: $WATCHER_LABEL has no running process — kickstarting"
    if launchctl kickstart "gui/$(id -u)/$WATCHER_LABEL" 2>> "$LOG_FILE"; then
        log "kickstarted $WATCHER_LABEL"
        notify "$WATCHER_LABEL was down — kickstarted" "DT pipeline failure"
    else
        log "ERROR: kickstart failed for $WATCHER_LABEL (agent not loaded?)"
        notify "$WATCHER_LABEL is down and kickstart failed" "DT pipeline failure"
    fi
done

# ── 5. Ensure the target database is open ────────────────────────────────────
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
        notify "Could not open '$DB_NAME': ${AS_OUTPUT#error: }" "DT pipeline failure"
        exit 1
        ;;
    *)
        if [ "$AS_STATUS" -ne 0 ]; then
            log "ERROR: osascript failed (exit $AS_STATUS): $AS_OUTPUT"
            exit 1
        fi
        log "WARNING: unexpected response from DT: $AS_OUTPUT"
        exit 1
        ;;
esac
