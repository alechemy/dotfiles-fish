#!/bin/bash
set -euo pipefail

MAX_ERR_LINES=6
sanitize_output() {
    awk -v max="$MAX_ERR_LINES" '
        { sub(/^.*\r/, ""); sub(/[[:space:]]+$/, "") }
        NF == 0 { next }
        ++n <= max { print }
        n == max + 1 { print "[output truncated]" }
    '
}

/usr/bin/python3 "$HOME/.local/bin/software_updates.py" mise "$@" 2>&1 | sanitize_output
