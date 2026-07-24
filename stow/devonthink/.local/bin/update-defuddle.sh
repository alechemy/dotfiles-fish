#!/bin/bash
# update-defuddle.sh
# Keep the mise-managed `defuddle` CLI current.
#
# defuddle is pinned to `latest` in mise's config, but mise resolves `latest`
# only at install time and never re-checks — so the tool silently goes stale.
# `mise upgrade npm:defuddle` is the only thing that pulls a newer npm release.
# defuddle's readability extraction is what `ingest-singlefile-html.py` runs on
# every SingleFile capture, so a stale version quietly degrades bookmark quality.
#
# Driven by launchd DAILY (com.user.defuddle-update.plist), battery-gated. A
# skipped tick self-heals the next day. If a release ever regresses capture
# quality, pin the config line (e.g. "npm:defuddle" = "0.19.1") — mise upgrade
# respects the pin and stops chasing latest.
#
# Usage:
#   update-defuddle.sh            # launchd-driven (battery-gated)
#   update-defuddle.sh --force    # upgrade now, bypassing the power gate

set -euo pipefail

PIPELINE_LOG="$HOME/.local/bin/pipeline-log"
log()  { "$PIPELINE_LOG" defuddle-update INFO "$*"; }
warn() { "$PIPELINE_LOG" defuddle-update WARN "$*"; }
err()  { "$PIPELINE_LOG" defuddle-update ERROR "$*"; }

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

"$HOME/.local/bin/pipeline-record-run" defuddle-update 0 || true

if [[ "$FORCE" -ne 1 ]]; then
    "$HOME/.local/bin/should-run-background-job" || exit 0
fi

# launchd's PATH doesn't carry Homebrew, so resolve mise explicitly.
MISE="$(command -v mise || true)"
[[ -z "$MISE" ]] && for c in /opt/homebrew/bin/mise /usr/local/bin/mise; do
    [[ -x "$c" ]] && { MISE="$c"; break; }
done
if [[ -z "$MISE" ]]; then
    err "mise not found on PATH or in Homebrew prefixes; cannot update defuddle"
    exit 1
fi

version() { "$MISE" exec npm:defuddle -- defuddle --version 2>/dev/null | tr -d '[:space:]'; }

BEFORE="$(version || true)"
if ! OUTPUT=$("$MISE" upgrade npm:defuddle 2>&1); then
    err "mise upgrade npm:defuddle failed: $OUTPUT"
    exit 1
fi
AFTER="$(version || true)"

if [[ -n "$AFTER" && "$AFTER" != "$BEFORE" ]]; then
    log "defuddle ${BEFORE:-unknown} -> ${AFTER}"
else
    log "defuddle up to date (${AFTER:-unknown})"
fi
