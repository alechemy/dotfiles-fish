#!/bin/bash
# Watch ~/Downloads/SingleFile/ for new .html files and hand each one to
# ingest-singlefile-html.py. Launched by com.user.singlefile-watcher.plist.
#
# fswatch emits one NUL-terminated event per filesystem change. We only
# act on "Created" events for .html files, wait ~2s for the write to
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

log "starting, watching $STAGING_DIR"

# --event Created: only fire on new-file events (not writes, renames, deletes)
# --format "%p": just the path (default includes flags we don't want parsed)
# -0: NUL-separated output so filenames with newlines don't break us
/opt/homebrew/bin/fswatch -0 --event Created "$STAGING_DIR" | while IFS= read -r -d '' path; do
    case "$path" in
        *.html)
            # Give SingleFile a moment to finish flushing; large captures
            # can take a second or two after the initial file creation
            # event fires.
            sleep 2
            if [[ -f "$path" ]]; then
                log "ingesting $path"
                "$INGESTER" "$path" || log "ingester exited non-zero for $path"
            fi
            ;;
    esac
done
