#!/usr/bin/env bash
#
# Merge the repo's tracked MCP-server fragments into ~/.claude.json, leaving the
# runtime state Claude Code owns in that file (projects, caches, machineID,
# oauthAccount, numStartups, …) untouched. Only the personal fragment ships in
# the public tree; the optional work fragment lives under the gitignored
# stow-work/work so a work-only server URL never reaches GitHub.
#
# Additive, fragment-wins: fragment definitions overwrite any stale live copy of
# the same server and add new ones, but a server added ad-hoc on a machine (not
# named in a fragment) is preserved. Removing a server is therefore manual.
#
# Not stowed — ~/.claude.json is app-owned and rewritten via atomic rename, so a
# symlink would de-stow on first save. This merge runs at setup time instead.
set -uo pipefail

DOTFILES="${DOTFILES:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET="$HOME/.claude.json"
PERSONAL="$DOTFILES/stow/claude/mcp-servers.json"
WORK="$DOTFILES/stow-work/work/mcp-servers.json"

if ! command -v jq >/dev/null 2>&1; then
    echo "merge-claude-mcp: jq not found; skipping MCP server merge" >&2
    exit 0
fi

if [ ! -f "$PERSONAL" ]; then
    echo "merge-claude-mcp: missing personal fragment $PERSONAL" >&2
    exit 1
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/claude-json.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

if jq -n \
    --slurpfile cur <(cat "$TARGET" 2>/dev/null || echo '{}') \
    --slurpfile personal "$PERSONAL" \
    --slurpfile work <(cat "$WORK" 2>/dev/null || echo '{}') \
    '($cur[0] // {})
     | .mcpServers = ((.mcpServers // {}) + ($personal[0] // {}) + ($work[0] // {}))' \
    >"$tmp" && jq -e . "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$TARGET"
    chmod 600 "$TARGET"
    echo "merge-claude-mcp: merged MCP servers into $TARGET"
else
    echo "merge-claude-mcp: merge failed; left $TARGET untouched" >&2
    exit 1
fi
