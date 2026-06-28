#!/usr/bin/env bash
#
# Restow stow packages whose tracked files changed between two git refs.
#
# `git pull` updates the working tree under stow/<pkg>/ but never invokes stow,
# so a file synced from another machine lands unlinked (and a file deleted
# upstream leaves a dangling symlink) until the next `setup.sh`. The post-merge
# and post-rewrite hooks call this script so the sync self-heals: each package
# touched by the pull is restowed, which recomputes its symlinks from the
# current contents (creating new links and pruning removed ones).
#
# Usage: restow-changed.sh <old-ref> <new-ref>
#   The hooks pass ORIG_HEAD HEAD (git sets ORIG_HEAD to the pre-merge/
#   pre-rebase tip). Run by hand with any two commit-ish refs.
#
# Opt-in packages (devonthink, streamrip, stow-work/work, stow-local/local) are
# restowed only if they are already active on this machine, so a pull never
# activates config the machine opted out of. Every other stow/ package is
# restowed unconditionally, matching setup.sh, so a brand-new package syncs in.

set -uo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OLD="${1:-}"
NEW="${2:-HEAD}"

if [ -z "$OLD" ]; then
    echo "restow-changed: usage: restow-changed.sh <old-ref> <new-ref>" >&2
    exit 0
fi

command -v stow >/dev/null 2>&1 || {
    echo "restow-changed: stow not installed; skipping" >&2
    exit 0
}

# A no-op merge (already up to date) leaves ORIG_HEAD == HEAD; missing refs mean
# the hook fired in a context we can't diff. Either way there's nothing to do.
git -C "$DOTFILES" rev-parse --verify --quiet "${OLD}^{commit}" >/dev/null || exit 0
git -C "$DOTFILES" rev-parse --verify --quiet "${NEW}^{commit}" >/dev/null || exit 0

# True when at least one of the package's tracked files is currently a symlink
# pointing back into this package — i.e. the package is stowed on this machine.
# Walks all tracked files so packages whose first entries are unstowed (e.g.
# devonthink's _seed/ copies) are still detected by their real stowed files.
is_active() {
    local root="$1" pkg="$2" f rel target dest
    while IFS= read -r f; do
        rel="${f#"$root/$pkg/"}"
        target="$HOME/$rel"
        [ -L "$target" ] || continue
        dest="$(readlink "$target")"
        case "$dest" in
            *"/$root/$pkg/"*) return 0 ;;
        esac
    done < <(git -C "$DOTFILES" ls-files "$root/$pkg")
    return 1
}

restow_pkg() {
    local root="$1" pkg="$2"
    ( cd "$DOTFILES/$root" && \
      stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' \
           --target="$HOME" "$pkg" )
}

changed="$(git -C "$DOTFILES" diff --name-only "$OLD" "$NEW" -- stow stow-work stow-local 2>/dev/null \
    | awk -F/ 'NF >= 2 { print $1, $2 }' | sort -u)"

[ -n "$changed" ] || exit 0

while read -r root pkg; do
    [ -n "$root" ] || continue

    # Whole package removed upstream: stow can't recompute what to delete
    # without the package dir, so flag it for manual cleanup rather than error.
    if [ ! -d "$DOTFILES/$root/$pkg" ]; then
        echo "restow-changed: $root/$pkg removed upstream; run 'stow --delete' by hand if stale symlinks remain" >&2
        continue
    fi

    case "$root/$pkg" in
        stow/devonthink|stow/streamrip|stow-work/work|stow-local/local)
            if is_active "$root" "$pkg"; then
                if restow_pkg "$root" "$pkg"; then
                    echo "restow-changed: restowed $root/$pkg (active opt-in)"
                fi
            else
                echo "restow-changed: skipped $root/$pkg (opt-in, not active here)"
            fi
            ;;
        *)
            if restow_pkg "$root" "$pkg"; then
                echo "restow-changed: restowed $root/$pkg"
            fi
            ;;
    esac
done <<EOF
$changed
EOF

exit 0
