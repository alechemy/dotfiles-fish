#!/bin/bash
# Watch the Maestral-synced Boox "Notebooks" folder for new .pdf exports and
# hand each one to boox-stage.sh for local-only processing (boox-process.py
# does the OCR on-device; handwritten content never reaches the cloud-backed
# smart-rule stages). Launched by com.user.boox-import-watcher.plist.
#
# fswatch emits one NUL-terminated event per filesystem change; we act on
# "Created" and "Renamed" events for .pdf files, leave a two-minute grace
# period for device-side rename/deletion, wait for the write to settle, then
# invoke the stager. The stager deletes the source PDF on success.

set -euo pipefail

BOOX_PATHS="$HOME/.local/bin/boox-paths.sh"
STAGER="$HOME/.local/bin/boox-stage.sh"
PIPELINE_LOG="$HOME/.local/bin/pipeline-log"
BOOX_INGEST_DELAY_SECONDS=${BOOX_INGEST_DELAY_SECONDS:-120}

log() {
    "$PIPELINE_LOG" boox-import-watcher INFO "$*"
}

warn() {
    "$PIPELINE_LOG" boox-import-watcher WARN "$*"
}

file_added_epoch() {
    local path=$1 added
    added=$(stat -f%B -- "$path" 2>/dev/null || true)
    if [[ ! "$added" =~ ^[0-9]+$ ]] || [[ "$added" -le 0 ]]; then
        added=$(stat -f%m -- "$path" 2>/dev/null || echo 0)
    fi
    echo "$added"
}

now_epoch() {
    date +%s
}

ingest_delay_remaining() {
    local path=$1 delay=$BOOX_INGEST_DELAY_SECONDS added now age remaining
    if [[ ! "$delay" =~ ^[0-9]+$ ]]; then
        delay=120
    fi
    added=$(file_added_epoch "$path")
    now=$(now_epoch)
    age=$((now - added))
    if [[ $age -lt 0 ]]; then
        age=0
    fi
    remaining=$((delay - age))
    if [[ $remaining -lt 0 ]]; then
        remaining=0
    fi
    echo "$remaining"
}

wait_for_ingest_age() {
    local path=$1 remaining
    while [[ -e "$path" ]]; do
        remaining=$(ingest_delay_remaining "$path")
        if [[ $remaining -eq 0 ]]; then
            echo "ready"
            return 0
        fi
        sleep "$remaining"
    done
    echo "gone"
    return 0
}

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

# An unnamed notebook on the Boox is exported as "<Template>-<n>.pdf", where
# <Template> is the notebook's template type and <n> the device's incrementing
# counter. These are throwaway quick notes the user never titled, so the watcher
# drops them instead of importing — naming a note on the device is the deliberate
# signal that it should enter DEVONthink.
is_untitled_notebook() {
    [[ "$(basename "$1" .pdf)" =~ ^(Notebook|Infinite)-[0-9]+$ ]]
}

# Stage one .pdf after its local creation time is at least two minutes old,
# then wait for quiescence and hand it to boox-stage.sh. A device-side rename
# or deletion can sync during the grace period, making the original path
# disappear before it becomes eligible.
import_pdf() {
    local path=$1 origin=$2 eligibility stability
    if ! "$HOME/.local/bin/should-run-dt-driver" 2>/dev/null; then
        log "skipping (follower role): $path ($origin)"
        return 0
    fi
    eligibility=$(wait_for_ingest_age "$path")
    if [[ "$eligibility" == "gone" ]]; then
        log "file disappeared during ingest grace period, skipping: $path ($origin)"
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
        log "staging ($origin) $path"
        "$STAGER" "$path" || log "stager exited non-zero for $path ($origin)"
    fi
}

main() {
    # Unlike a missing watch dir (below), this is a broken install rather than a
    # transient sync race — there is no correct folder to fall back to, so fail
    # loudly instead of importing from a guessed path.
    if [[ ! -r "$BOOX_PATHS" ]]; then
        warn "cannot read $BOOX_PATHS (is the devonthink package stowed?)"
        exit 1
    fi
    # shellcheck source=boox-paths.sh
    source "$BOOX_PATHS"
    WATCH_DIR="$BOOX_NOTEBOOKS_DIR"

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

    # Subscribe before sweeping the backlog so changes that arrive during a
    # fresh file's grace period queue in the pipe instead of falling into a
    # startup blind spot. find recurses through Boox category subfolders.
    # --event Created --event Renamed catches both direct writes and sync-client
    # finalization by rename. -0 keeps filenames with newlines intact.
    /opt/homebrew/bin/fswatch -0 --event Created --event Renamed "$WATCH_DIR" | {
        while IFS= read -r -d '' backlog_path; do
            import_pdf "$backlog_path" backlog
        done < <(find "$WATCH_DIR" -type f -name '*.pdf' -print0)

        while IFS= read -r -d '' path; do
            case "$path" in
                *.pdf)
                    import_pdf "$path" fswatch
                    ;;
            esac
        done
    }
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
