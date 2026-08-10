#!/bin/bash
# update-npm-tools.sh
# Keep mise's latest-pinned global npm CLIs current.
#
# mise resolves a `latest` pin only at install time and never re-checks, so
# every `"npm:<tool>" = "latest"` entry in ~/.config/mise/config.toml silently
# goes stale — the npm-side gap that brew autoupdate covers for Homebrew.
# Each latest-pinned npm tool gets a `mise upgrade`; tools pinned to a real
# version (and non-npm backends) are left alone, so pinning a line in the
# config is how a tool opts out (e.g. after a release regresses).
#
# Driven by launchd DAILY (com.user.npm-tools-update.plist), battery-gated. A
# skipped tick self-heals the next day.
#
# Usage:
#   update-npm-tools.sh            # launchd-driven (battery-gated)
#   update-npm-tools.sh --force    # upgrade now, bypassing the power gate

set -euo pipefail

PIPELINE_LOG="$HOME/.local/bin/pipeline-log"
# pipeline-log and pipeline-record-run ship with the opt-in devonthink
# package; this agent runs on every machine, so degrade to plain echo.
log() { if [[ -x "$PIPELINE_LOG" ]]; then "$PIPELINE_LOG" npm-tools-update INFO "$*"; else echo "INFO $*"; fi; }
err() { if [[ -x "$PIPELINE_LOG" ]]; then "$PIPELINE_LOG" npm-tools-update ERROR "$*"; else echo "ERROR $*" >&2; fi; }

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

RECORD_RUN="$HOME/.local/bin/pipeline-record-run"
[[ -x "$RECORD_RUN" ]] && { "$RECORD_RUN" npm-tools-update 0 || true; }

if [[ "$FORCE" -ne 1 ]]; then
    "$HOME/.local/bin/should-run-background-job" || exit 0
fi

# launchd's PATH doesn't carry Homebrew, so resolve mise explicitly.
MISE="$(command -v mise || true)"
[[ -z "$MISE" ]] && for c in /opt/homebrew/bin/mise /usr/local/bin/mise; do
    [[ -x "$c" ]] && { MISE="$c"; break; }
done
if [[ -z "$MISE" ]]; then
    err "mise not found on PATH or in Homebrew prefixes; cannot update npm tools"
    exit 1
fi

# From $HOME only the global config is in scope; a project-local mise.toml
# under the invoking cwd must not leak into the enumeration.
cd "$HOME"

version_of() {
    "$MISE" ls --json "$1" 2>/dev/null | /usr/bin/python3 -c '
import json, sys
for i in json.load(sys.stdin):
    if i.get("active"):
        print(i.get("version", ""))
        break' 2>/dev/null
}

TOOLS="$("$MISE" ls --json | /usr/bin/python3 -c '
import json, sys
for name, installs in json.load(sys.stdin).items():
    if name.startswith("npm:") and any(
        i.get("requested_version") == "latest" for i in installs
    ):
        print(name)')"

FAILED=0
while IFS= read -r tool; do
    [[ -z "$tool" ]] && continue
    BEFORE="$(version_of "$tool" || true)"
    if ! OUTPUT=$("$MISE" upgrade "$tool" 2>&1); then
        err "mise upgrade $tool failed: $OUTPUT"
        FAILED=1
        continue
    fi
    AFTER="$(version_of "$tool" || true)"
    if [[ -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
        log "$tool ${BEFORE:-unknown} -> ${AFTER}"
    else
        log "$tool up to date (${AFTER:-unknown})"
    fi
done <<< "$TOOLS"

exit "$FAILED"
