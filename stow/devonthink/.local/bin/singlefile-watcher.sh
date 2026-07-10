#!/bin/bash
# Watch ~/Downloads/SingleFile/ for new .html files and hand each one to
# ingest-singlefile-html.py. Launched by com.user.singlefile-watcher.plist.
#
# fswatch emits one NUL-terminated event per filesystem change. We act on
# "Created" and "Renamed" events for .html files, wait ~2s for the write to
# settle, then invoke the ingester. The ingester deletes the staging file
# on success, so the only files remaining in the folder are failed
# captures awaiting manual cleanup.

set -euo pipefail

STAGING_DIR="$HOME/Downloads/SingleFile"
INGESTER="$HOME/.local/bin/ingest-singlefile-html.py"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"

mkdir -p "$STAGING_DIR"

log() {
    "$PIPELINE_LOG" singlefile-watcher INFO "$*"
}

warn() {
    "$PIPELINE_LOG" singlefile-watcher WARN "$*"
}

# Record startup time, never alert (interval 0): this only runs on
# (re)start, so the recorded gap is the previous instance's healthy uptime
# and any threshold would false-alert after every multi-day run. A watcher
# that dies and stays dead is caught by dt-watchdog's liveness check.
"$HOME/.local/bin/pipeline-record-run" singlefile-watcher 0 || true

# Runtime role gate, for a follower that still has this agent loaded from an
# older bootstrap. Exiting would churn launchd's KeepAlive throttle loop, so
# wait for promotion instead.
if ! "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; then
    log "follower role: ingest disabled until this Mac becomes the driver"
    until "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; do
        sleep 300
    done
    log "driver role detected: enabling watcher"
fi

# Poll a file's size until it has stayed identical for 5 consecutive samples
# (~2.5s of quiescence), capped at 30s total. Echoes "stable", "gone" if the
# file vanished mid-wait, or "unstable:<last-size>" if the cap is reached
# without the file going quiet. Always returns 0: under set -e a non-zero
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

# Ingest one .html file: wait for quiescence, then hand off to the ingester.
# Skips truncated captures so the next event (or backlog sweep) can retry.
ingest_html() {
    local path=$1 origin=$2 stability
    if ! "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; then
        log "skipping (follower role): $path ($origin)"
        return 0
    fi
    stability=$(wait_for_stable_size "$path")
    if [[ "$stability" == "gone" ]]; then
        log "file disappeared before ingest, skipping: $path ($origin)"
        return 0
    fi
    if [[ "$stability" != "stable" ]]; then
        local size=${stability#unstable:}
        warn "file size never stabilized after 30s, skipping: $path (last size=$size, origin=$origin)"
        return 0
    fi
    if [[ -f "$path" ]]; then
        log "ingesting ($origin) $path"
        "$INGESTER" "$path" || log "ingester exited non-zero for $path ($origin)"
    fi
}

log "starting, watching $STAGING_DIR"

# One-time backlog sweep: any .html files already present when the watcher
# starts (after a crash, re-bootstrap, or manual drop) would otherwise be
# ignored until they're rewritten. Pick them up before subscribing to events.
shopt -s nullglob 2>/dev/null || true
for backlog_path in "$STAGING_DIR"/*.html; do
    [[ -f "$backlog_path" ]] || continue
    ingest_html "$backlog_path" backlog
done
shopt -u nullglob 2>/dev/null || true

# --event Created --event Renamed: catch files written directly into the folder
#   *and* files that arrive via rename. Browser downloads (SingleFile's save
#   path) finalize by renaming a temp file to the final .html — FSEvents reports
#   that as Renamed, not Created, so without Renamed here a live desktop capture
#   is missed until the next watcher restart's backlog sweep.
# -0: NUL-separated output so filenames with newlines don't break us
/opt/homebrew/bin/fswatch -0 --event Created --event Renamed "$STAGING_DIR" | while IFS= read -r -d '' path; do
    case "$path" in
        *.html)
            ingest_html "$path" fswatch
            ;;
    esac
done
