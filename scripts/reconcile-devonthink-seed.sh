#!/usr/bin/env bash
#
# Reconciles a machine's live DEVONthink config against the repo seed: the
# update path seed-devonthink-config.sh deliberately lacks. The seed script is
# copy-if-absent, so once a plist exists on a machine, repository changes to
# smart rules, smart groups, custom metadata, or batch presets never reach it.
# This script reports the drift and, on request, applies selected seed files
# over the live ones (backing up each live file first).
#
# Reconciliation:
#   ./reconcile-devonthink-seed.sh
#       Report only. Print a status table (missing | same | differs) comparing
#       each _seed/ file against its ~/ destination. Plists are compared after
#       `plutil -convert xml1` canonicalization so binary byte-order noise is
#       not mistaken for drift. Nothing is written.
#   ./reconcile-devonthink-seed.sh --apply [relative-path ...]
#       Copy seed files over the live copies. With no paths, applies every
#       file that currently differs; with paths, applies exactly those
#       (relative to _seed/, e.g. "Library/Application Support/DEVONthink/
#       SmartRules.plist"). Each live file is backed up first to
#       ~/.local/state/devonthink/seed-backups/<timestamp>/ preserving its
#       relative path.
#   Add --force to --apply to proceed while DEVONthink is running. Refused by
#   default: the app rewrites these plists at runtime and would clobber or be
#   clobbered by the copy.
#
# CustomMetaData.plist is the exception to both the compare and the apply: it
# is a schema seed-devonthink-config.sh merges by identifier rather than
# copying whole (see that script's header), so a byte/XML compare here would
# report the merge's own `index` reassignment as permanent drift, and a plain
# copy on --apply would drop any field a machine picked up locally. Both
# route through normalize-devonthink-plist.py's --custom-metadata-* modes
# instead, which implement the identical identifier-aware algorithm.
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_ROOT="$DOTFILES/stow/devonthink/_seed"
META_REL="Library/Application Support/DEVONthink/CustomMetaData.plist"

if [ ! -d "$SEED_ROOT" ]; then
  echo "No DEVONthink seed directory at $SEED_ROOT; nothing to reconcile." >&2
  exit 1
fi

APPLY=0
FORCE=0
TARGETS=()
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --force) FORCE=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 2 ;;
    *) TARGETS+=("$arg") ;;
  esac
done

if [ "$APPLY" -eq 0 ] && [ "${#TARGETS[@]}" -gt 0 ]; then
  echo "Relative paths are only valid with --apply." >&2
  exit 2
fi

# Echoes one of: missing | same | differs
status_of() {
  local seed_file="$1" live="$2"
  local rel="${seed_file#"$SEED_ROOT"/}"
  if [ ! -e "$live" ]; then
    echo "missing"
    return 0
  fi
  if [ "$rel" = "$META_REL" ]; then
    "$DOTFILES/scripts/normalize-devonthink-plist.py" \
      --custom-metadata-status "$seed_file" "$live"
    return 0
  fi
  case "$seed_file" in
    *.plist)
      if diff -q <("$DOTFILES/scripts/normalize-devonthink-plist.py" "$seed_file" 2>/dev/null) \
                 <("$DOTFILES/scripts/normalize-devonthink-plist.py" "$live" 2>/dev/null) \
                 >/dev/null 2>&1; then
        echo "same"
      else
        echo "differs"
      fi
      ;;
    *)
      if cmp -s "$seed_file" "$live"; then echo "same"; else echo "differs"; fi
      ;;
  esac
  return 0
}

if [ "$APPLY" -eq 0 ]; then
  echo "DEVONthink seed reconciliation (_seed vs \$HOME):"
  same=0
  differ=0
  missing=0
  while IFS= read -r src; do
    rel="${src#"$SEED_ROOT"/}"
    live="$HOME/$rel"
    st="$(status_of "$src" "$live")"
    printf '  %-8s %s\n' "$st" "$rel"
    case "$st" in
      same) same=$((same + 1)) ;;
      differs) differ=$((differ + 1)) ;;
      missing) missing=$((missing + 1)) ;;
    esac
  done < <(find "$SEED_ROOT" -type f ! -name '.DS_Store')
  echo "Reconciliation: $same same, $differ differ, $missing missing"
  if [ "$differ" -gt 0 ] || [ "$missing" -gt 0 ]; then
    echo "Apply with: $(basename "$0") --apply [relative-path ...]"
  fi
  exit 0
fi

if [ "$FORCE" -ne 1 ] && pgrep -qx DEVONthink; then
  echo "DEVONthink is running — it rewrites these plists at runtime and would" >&2
  echo "clobber or be clobbered by this copy. Quit it first, or re-run with --force." >&2
  exit 1
fi

to_apply=()
if [ "${#TARGETS[@]}" -gt 0 ]; then
  for rel in "${TARGETS[@]}"; do
    if [ ! -f "$SEED_ROOT/$rel" ]; then
      echo "Not a seed file: $rel" >&2
      exit 2
    fi
    to_apply+=("$rel")
  done
else
  while IFS= read -r src; do
    rel="${src#"$SEED_ROOT"/}"
    if [ "$(status_of "$src" "$HOME/$rel")" = "differs" ]; then
      to_apply+=("$rel")
    fi
  done < <(find "$SEED_ROOT" -type f ! -name '.DS_Store')
fi

if [ "${#to_apply[@]}" -eq 0 ]; then
  echo "Nothing to apply: no differing seed files."
  exit 0
fi

BACKUP_DIR="$HOME/.local/state/devonthink/seed-backups/$(date +%Y%m%dT%H%M%S)"

applied=0
skipped=0
backed_up=0
for rel in "${to_apply[@]}"; do
  src="$SEED_ROOT/$rel"
  live="$HOME/$rel"
  st="$(status_of "$src" "$live")"
  if [ "$st" = "same" ]; then
    echo "  same, skipping $rel"
    skipped=$((skipped + 1))
    continue
  fi
  if [ -e "$live" ]; then
    bdest="$BACKUP_DIR/$rel"
    mkdir -p "$(dirname "$bdest")"
    cp -p "$live" "$bdest"
    backed_up=$((backed_up + 1))
  fi
  mkdir -p "$(dirname "$live")"
  if [ "$rel" = "$META_REL" ]; then
    "$DOTFILES/scripts/normalize-devonthink-plist.py" \
      --custom-metadata-merge "$src" "$live" >/dev/null
  else
    cp -p "$src" "$live"
  fi
  echo "  applied $rel"
  applied=$((applied + 1))
done

echo "DEVONthink seed reconcile: $applied applied, $skipped unchanged"
if [ "$backed_up" -gt 0 ]; then
  echo "$backed_up live file(s) backed up under $BACKUP_DIR"
fi
