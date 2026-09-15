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
#
# CustomMetaData.plist is the exception: it is a *schema* (a list of field
# definitions), not user-authored content, so copy-if-absent would strand every
# machine that already owns the file on an old schema — a pipeline field added
# later would never arrive, and code keying on it would silently read empty. It
# is therefore MERGED: definitions the seed has and the live file lacks are
# appended by identifier, and an existing definition is never touched.
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_ROOT="$DOTFILES/stow/devonthink/_seed"

if [ ! -d "$SEED_ROOT" ]; then
  echo "No DEVONthink seed directory at $SEED_ROOT; nothing to seed."
  exit 0
fi

META_REL="Library/Application Support/DEVONthink/CustomMetaData.plist"
BACKUP_DIR="$HOME/.local/state/devonthink/seed-backups"

copied=0
skipped=0
while IFS= read -r src; do
  rel="${src#"$SEED_ROOT"/}"
  dest="$HOME/$rel"
  # The schema plist is created and merged below instead: both writes have to go
  # through the same DEVONthink-is-running guard and land atomically.
  if [ "$rel" = "$META_REL" ]; then
    continue
  fi
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

if [ -f "$SEED_ROOT/$META_REL" ]; then
  META_HELPER="$DOTFILES/scripts/normalize-devonthink-plist.py"
  if ! metadata_status=$(
    /usr/bin/python3 "$META_HELPER" --custom-metadata-status \
      "$SEED_ROOT/$META_REL" "$HOME/$META_REL"
  ); then
    echo "  custom metadata: seed or live plist is malformed" >&2
    exit 1
  fi
  if [ "$metadata_status" = "same" ]; then
    echo "  custom metadata schema: up to date"
  elif pgrep -qx DEVONthink; then
    echo "  custom metadata: schema needs updating, but DEVONthink is running." >&2
    echo "  Quit DEVONthink and re-run this script (or scripts/setup.sh)." >&2
  else
    mkdir -p "$(dirname "$HOME/$META_REL")"
    if [ -f "$HOME/$META_REL" ]; then
      mkdir -p "$BACKUP_DIR"
      cp -p "$HOME/$META_REL" \
        "$BACKUP_DIR/CustomMetaData.plist.$(date +%Y%m%d-%H%M%S)"
    fi
    /usr/bin/python3 "$META_HELPER" --custom-metadata-merge \
      "$SEED_ROOT/$META_REL" "$HOME/$META_REL"
    echo "  restart DEVONthink to pick up the new metadata field(s)"
  fi
fi
