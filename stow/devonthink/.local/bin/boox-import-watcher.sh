#!/bin/bash
# Watch the Maestral-synced Boox "Notebooks" folder for new .pdf exports and
# hand each one to boox-import.sh. Launched by com.user.boox-import-watcher.plist.
#
# Replaces the former Hazel rule "Convert Boox PDFs to TIFFs and Import to
# DEVONthink". fswatch emits one NUL-terminated event per filesystem change;
# we act on "Created" and "Renamed" events for .pdf files, wait ~2s for the
# write to settle, then invoke the importer. The importer deletes the source
# PDF on success and quarantines failures, so the folder stays clear of
# already-processed exports.

set -euo pipefail

WATCH_DIR="$HOME/Dropbox (Maestral)/onyx/Go103/Notebooks"
IMPORTER="$HOME/.local/bin/boox-import.sh"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"

log() {
    "$PIPELINE_LOG" boox-import-watcher INFO "$*"
}

warn() {
    "$PIPELINE_LOG" boox-import-watcher WARN "$*"
}

# Record startup time, never alert (interval 0): this only runs on
# (re)start, so the recorded gap is the previous instance's healthy uptime
# and any threshold would false-alert after every multi-day run. A watcher
# that dies and stays dead is caught by dt-watchdog's liveness check.
"$HOME/.local/bin/pipeline-record-run" boox-import-watcher 0 || true

# Runtime role gate, for a follower that still has this agent loaded from an
# older bootstrap. A follower must never touch the synced Notebooks folder —
# import_pdf deletes untitled exports before the driver can import them.
# Exiting would churn launchd's KeepAlive throttle loop, so wait for
# promotion instead.
if ! "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; then
    log "follower role: import disabled until this Mac becomes the driver"
    until "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; do
        sleep 300
    done
    log "driver role detected: enabling watcher"
fi

# Poll a file's size until it has stayed identical for 5 consecutive samples
# (~2.5s of quiescence), capped at 30s total. Echoes "stable", "gone" if the
# file vanished mid-wait, or "unstable:<last-size>" if the cap is reached.
# Maestral writes a synced file incrementally, so this avoids handing the
# importer a half-downloaded PDF. Always returns 0: under set -e a non-zero
# return through the callers' command-substitution assignment would kill the
# watcher, and KeepAlive would loop it against the same file forever.
wait_for_stable_size() {
    local path=$1
    local prev=-1 stable=0 cur
    for _ in $(seq 1 60); do
        if [[ ! -e "$path" ]]; then
            echo "gone"
            return 0
        fi
        cur=$(stat -f%z "$path" 2>/dev/null || echo 0)
        if [[ "$cur" == "$prev" && "$cur" -gt 0 ]]; then
            stable=$((stable + 1))
            if [[ $stable -ge 5 ]]; then
                echo "stable"
                return 0
            fi
        else
            stable=0
        fi
        prev=$cur
        sleep 0.5
    done
    echo "unstable:$prev"
    return 0
}

# An unnamed notebook on the Boox is exported as "Notebook-<n>.pdf", where <n> is
# the device's incrementing counter. These are throwaway quick notes the user
# never titled, so the watcher drops them instead of importing — naming a note on
# the device is the deliberate signal that it should enter DEVONthink.
is_untitled_notebook() {
    [[ "$(basename "$1" .pdf)" =~ ^Notebook-[0-9]+$ ]]
}

# Import one .pdf: wait for quiescence, then hand off to boox-import.sh. Skips
# truncated files so the next event (or backlog sweep) can retry. Untitled
# Notebook-<n> exports are deleted rather than imported.
import_pdf() {
    local path=$1 origin=$2 stability
    if ! "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; then
        log "skipping (follower role): $path ($origin)"
        return 0
    fi
    stability=$(wait_for_stable_size "$path")
    if [[ "$stability" == "gone" ]]; then
        log "file disappeared before import, skipping: $path ($origin)"
        return 0
    fi
    if [[ "$stability" != "stable" ]]; then
        local size=${stability#unstable:}
        warn "file size never stabilized after 30s, skipping: $path (last size=$size, origin=$origin)"
        return 0
    fi
    if [[ -f "$path" ]]; then
        if is_untitled_notebook "$path"; then
            log "ignoring untitled Boox note, deleting: $path ($origin)"
            rm -f "$path"
            return 0
        fi
        log "importing ($origin) $path"
        "$IMPORTER" "$path" || log "importer exited non-zero for $path ($origin)"
    fi
}

# Wait for the watch dir instead of exiting: KeepAlive relaunches on any
# exit status, so an early exit here churns through launchd's throttle loop
# (observed: 22 warns in ~3.5 minutes) until Maestral creates the folder.
if [[ ! -d "$WATCH_DIR" ]]; then
    warn "watch directory not found, waiting for it to appear (is Maestral set up?): $WATCH_DIR"
    until [[ -d "$WATCH_DIR" ]]; do
        sleep 60
    done
    log "watch directory appeared: $WATCH_DIR"
fi

log "starting, watching $WATCH_DIR"

# One-time backlog sweep: any .pdf already present when the watcher starts
# (after a crash, re-bootstrap, or PDFs that synced down while the agent was
# down) would otherwise be ignored until rewritten. find recurses — Boox
# organizes notebooks into category subfolders under Notebooks.
while IFS= read -r -d '' backlog_path; do
    import_pdf "$backlog_path" backlog
done < <(find "$WATCH_DIR" -type f -name '*.pdf' -print0)

# --event Created --event Renamed: catch PDFs written directly into the folder
#   *and* PDFs that arrive via rename. Maestral can finalize a synced file by
#   renaming a temp file into place — FSEvents reports that as Renamed, not
#   Created, so without Renamed here a freshly-synced export is missed until
#   the next watcher restart's backlog sweep (same fix as singlefile-watcher).
#   fswatch's FSEvents monitor is recursive, so subfolder exports are covered.
# -0: NUL-separated output so filenames with newlines don't break us.
/opt/homebrew/bin/fswatch -0 --event Created --event Renamed "$WATCH_DIR" | while IFS= read -r -d '' path; do
    case "$path" in
        *.pdf)
            import_pdf "$path" fswatch
            ;;
    esac
done
