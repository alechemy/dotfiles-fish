#!/bin/bash
# apptamer-watchdog.sh
#
# Ensures App Tamer is running. macOS silently terminates UIElement apps
# under memory pressure (LSApplicationWouldBeTerminatedByTALKey), which
# leaves no crash report — so a poll-based watchdog is the right fit.
# Launched every 60 seconds by launchd (com.user.apptamer-watchdog.plist).
# Logs to ~/Library/Logs/apptamer-watchdog.log.

APP_NAME="App Tamer"
LAUNCH_TIMEOUT=30
LOG_FILE="$HOME/Library/Logs/apptamer-watchdog.log"

log() {
    printf '%s [apptamer-watchdog] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

if pgrep -qx "$APP_NAME"; then
    exit 0
fi

log "$APP_NAME not running — launching"
open -a "$APP_NAME"

waited=0
while ! pgrep -qx "$APP_NAME"; do
    sleep 2
    waited=$((waited + 2))
    if [ "$waited" -ge "$LAUNCH_TIMEOUT" ]; then
        log "ERROR: '$APP_NAME' did not appear within ${LAUNCH_TIMEOUT}s"
        exit 1
    fi
done

log "$APP_NAME relaunched"
