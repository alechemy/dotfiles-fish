#!/usr/bin/env bash
#
# Merge the gitignored work MCP-server fragment into ~/.claude.json, leaving the
# runtime state Claude Code owns in that file (projects, caches, machineID,
# oauthAccount, numStartups, …) untouched. The fragment stays under stow-work/
# so a work-only server URL never reaches GitHub.
#
# Fragment definitions overwrite stale live copies and add new ones. Ad-hoc
# servers are preserved unless explicitly retired below.
#
# Not stowed — ~/.claude.json is app-owned and rewritten via atomic rename, so a
# symlink would de-stow on first save. This merge runs at setup time instead.
set -uo pipefail

DOTFILES="${DOTFILES:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET="$HOME/.claude.json"
WORK="$DOTFILES/stow-work/work/mcp-servers.json"

if ! command -v jq >/dev/null 2>&1; then
    echo "merge-claude-mcp: jq not found; skipping MCP server merge" >&2
    exit 0
fi

current_input=/dev/null
if [ -e "$TARGET" ]; then
    if [ ! -f "$TARGET" ] || [ ! -r "$TARGET" ]; then
        echo "merge-claude-mcp: live state is not a readable file; left it untouched" >&2
        exit 1
    fi
    current_input="$TARGET"
fi

work_input=/dev/null
if [ -e "$WORK" ]; then
    if [ ! -f "$WORK" ] || [ ! -r "$WORK" ]; then
        echo "merge-claude-mcp: work fragment is not a readable file; left live state untouched" >&2
        exit 1
    fi
    work_input="$WORK"
fi

if ! tmp="$(mktemp "${TMPDIR:-/tmp}/claude-json.XXXXXX")"; then
    echo "merge-claude-mcp: could not create a temporary file; left live state untouched" >&2
    exit 1
fi
trap 'rm -f "$tmp"' EXIT

if ! jq -n \
    --slurpfile cur "$current_input" \
    --slurpfile work "$work_input" \
    '($cur[0] // {})
     | del(.mcpServers.filesystem, .mcpServers.ankimcp, .mcpServers.devonthink)
     | .mcpServers = ((.mcpServers // {}) + ($work[0] // {}))' \
    >"$tmp" || ! jq -e . "$tmp" >/dev/null 2>&1; then
    echo "merge-claude-mcp: merge failed; left live state untouched" >&2
    exit 1
fi

if ! chmod 600 "$tmp"; then
    echo "merge-claude-mcp: could not secure merged state; left live state untouched" >&2
    exit 1
fi
if ! mv "$tmp" "$TARGET"; then
    echo "merge-claude-mcp: could not replace live state; left it untouched" >&2
    exit 1
fi

echo "merge-claude-mcp: merged MCP servers into $TARGET"
