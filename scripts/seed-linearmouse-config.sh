#!/usr/bin/env bash
#
# Seeds LinearMouse's configuration (~/.config/linearmouse/linearmouse.json)
# onto a machine. LinearMouse rewrites this file at runtime whenever settings
# change in its GUI, and an atomic-rename save would replace a symlink with a
# real file and silently de-stow it, so it is COPIED rather than stowed.
#
# Copy-if-absent: an existing target means LinearMouse already owns that file,
# so we never clobber it. Idempotent and safe whether or not LinearMouse is
# running. Source of truth is stow/linearmouse/_seed/, which is excluded from
# stowing by stow/linearmouse/.stow-local-ignore.
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_ROOT="$DOTFILES/stow/linearmouse/_seed"

if [ ! -d "$SEED_ROOT" ]; then
  echo "No LinearMouse seed directory at $SEED_ROOT; nothing to seed."
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

echo "LinearMouse config seed: $copied copied, $skipped already present"
