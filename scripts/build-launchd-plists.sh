#!/usr/bin/env bash
#
# Generates LaunchAgent .plist files from .plist.template files by substituting
# __HOME__ with the current user's home directory. This avoids hardcoding a
# username in the dotfiles repo.
#
# Scans stow/, stow-work/, and stow-local/ for templates under
# <pkg>/Library/LaunchAgents/*.plist.template. Each output is validated with
# plutil and replaced atomically, and only when its content actually changed —
# launchd keeps running a loaded agent's old definition regardless, so callers
# need to know which labels to bootout/bootstrap.
#
# Usage: build-launchd-plists.sh [--changed-file <path>]
#   With --changed-file, appends the path of each rewritten .plist to <path>,
#   one per line (setup.sh uses this to reload only the affected agents).
#
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CHANGED_FILE=""
if [ "${1:-}" = "--changed-file" ] && [ -n "${2:-}" ]; then
  CHANGED_FILE=$2
fi

count=0
changed=0
while IFS= read -r template; do
  out="${template%.template}"
  tmp="${out}.tmp"
  sed "s|__HOME__|${HOME}|g" "$template" > "$tmp"
  if ! plutil -lint -s "$tmp"; then
    rm -f "$tmp"
    echo "Error: $template renders to an invalid plist (see plutil output above)" >&2
    exit 1
  fi
  if [ -f "$out" ] && cmp -s "$tmp" "$out"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$out"
    changed=$((changed + 1))
    if [ -n "$CHANGED_FILE" ]; then
      printf '%s\n' "$out" >> "$CHANGED_FILE"
    fi
  fi
  count=$((count + 1))
done < <(find "$DOTFILES/stow" "$DOTFILES/stow-work" "$DOTFILES/stow-local" \
              -path '*/Library/LaunchAgents/*.plist.template' 2>/dev/null)

if [ "$count" -eq 0 ]; then
  echo "Error: no LaunchAgent plist templates found — path layout changed?" >&2
  exit 1
fi

echo "Built $count LaunchAgent plist(s); $changed changed"
