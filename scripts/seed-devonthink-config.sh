#!/usr/bin/env bash
#
# Seeds DEVONthink's portable configuration onto a machine: smart rules, smart
# groups, custom metadata definitions, and batch-processing presets. These live
# in ~/Library/Application Support/DEVONthink/ as plists that DEVONthink
# rewrites at runtime, so they are COPIED rather than stowed/symlinked (an
# atomic-rename save would replace a symlink with a real file and silently
# de-stow it).
#
# Copy-if-absent: an existing target means DEVONthink already owns that file, so
# we never clobber it. This makes the script idempotent and safe to run whether
# or not DEVONthink is running. Source of truth is stow/devonthink/_seed/, which
# is excluded from stowing by stow/devonthink/.stow-local-ignore.
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_ROOT="$DOTFILES/stow/devonthink/_seed"

if [ ! -d "$SEED_ROOT" ]; then
  echo "No DEVONthink seed directory at $SEED_ROOT; nothing to seed."
  exit 0
fi

copied=0
skipped=0
while IFS= read -r src; do
  rel="${src#"$SEED_ROOT"/}"
  dest="$HOME/$rel"
  if [ -e "$dest" ]; then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$(dirname "$dest")"
  cp -p "$src" "$dest"
  echo "  seeded $rel"
  copied=$((copied + 1))
done < <(find "$SEED_ROOT" -type f ! -name '.DS_Store')

echo "DEVONthink config seed: $copied copied, $skipped already present"
