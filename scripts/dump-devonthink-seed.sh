#!/usr/bin/env bash
#
# Dumps DEVONthink's portable configuration back into the repo seed: the
# reverse of seed-devonthink-config.sh. Run after editing smart rules, smart
# groups, custom metadata definitions, or batch-processing presets so a fresh
# machine seeds the current definitions instead of stale ones — the seed is
# the ONLY carrier of rule criteria/actions (their bodies are opaque blobs the
# repo's scripts can't reconstruct).
#
# Only files already present in _seed/ are refreshed; a plist DEVONthink adds
# in a future version won't silently start shipping in the repo. DEVONthink
# rewrites these plists on settings changes, so quit it first for a
# guaranteed-consistent copy; --force skips that check.
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_ROOT="$DOTFILES/stow/devonthink/_seed"

if [ ! -d "$SEED_ROOT" ]; then
  echo "No DEVONthink seed directory at $SEED_ROOT; nothing to dump." >&2
  exit 1
fi

if [ "${1:-}" != "--force" ] && pgrep -qx DEVONthink; then
  echo "DEVONthink is running — quit it first so the plists are quiescent," >&2
  echo "or re-run with --force to dump anyway." >&2
  exit 1
fi

updated=0
unchanged=0
while IFS= read -r seed_file; do
  rel="${seed_file#"$SEED_ROOT"/}"
  live="$HOME/$rel"
  if [ ! -f "$live" ]; then
    echo "  MISSING live counterpart, skipping: ~/$rel" >&2
    continue
  fi
  if cmp -s "$live" "$seed_file"; then
    unchanged=$((unchanged + 1))
    continue
  fi
  plutil -lint "$live" >/dev/null
  cp -p "$live" "$seed_file"
  echo "  dumped $rel"
  updated=$((updated + 1))
done < <(find "$SEED_ROOT" -type f ! -name '.DS_Store')

echo "DEVONthink seed dump: $updated updated, $unchanged unchanged"
if [ "$updated" -gt 0 ]; then
  echo "Review and commit: git -C \"$DOTFILES\" add stow/devonthink/_seed && git -C \"$DOTFILES\" commit"
fi
