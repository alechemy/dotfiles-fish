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
  needed=$(/usr/bin/python3 - "$SEED_ROOT/$META_REL" "$HOME/$META_REL" <<'PY'
import os, plistlib, sys

seed_path, live_path = sys.argv[1], sys.argv[2]
if not os.path.exists(live_path):
    print("create")
    raise SystemExit
try:
    seed = plistlib.load(open(seed_path, "rb"))
    live = plistlib.load(open(live_path, "rb"))
except Exception:
    print("none")
    raise SystemExit
if not isinstance(seed, list) or not isinstance(live, list):
    print("none")
    raise SystemExit
have = {f.get("identifier") for f in live}
print("merge" if [f for f in seed if f.get("identifier") not in have] else "none")
PY
)
  if [ "$needed" = "none" ]; then
    echo "  custom metadata schema: up to date"
  # DEVONthink rewrites this plist at runtime and would clobber, or be clobbered
  # by, a write underneath it — the same reason the reconciler refuses. Deferring
  # is safe: a flag's value lives on the record and syncs with it, so only the
  # GUI's ability to display the field waits for the next run.
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
    /usr/bin/python3 - "$SEED_ROOT/$META_REL" "$HOME/$META_REL" <<'PY'
import os, plistlib, sys, tempfile

seed_path, live_path = sys.argv[1], sys.argv[2]
seed = plistlib.load(open(seed_path, "rb"))
exists = os.path.exists(live_path)

if not exists:
    # Nothing to merge into, so the seed goes in verbatim — reassigning indices
    # would reorder the fields DEVONthink displays for no reason.
    live = seed
    print(f"  custom metadata: seeded schema ({len(seed)} fields)")
else:
    live = plistlib.load(open(live_path, "rb"))
    have = {f.get("identifier") for f in live}
    nxt = max((f.get("index", 0) for f in live), default=0) + 1
    for field in [f for f in seed if f.get("identifier") not in have]:
        field = dict(field)
        field["index"] = nxt
        nxt += 1
        live.append(field)
        print(f"  custom metadata: added {field.get('identifier')}")

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(live_path), suffix=".plist")
try:
    with os.fdopen(fd, "wb") as f:
        plistlib.dump(live, f)
    os.replace(tmp, live_path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
    echo "  restart DEVONthink to pick up the new metadata field(s)"
  fi
fi
