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

# True when at least one of the package's files is currently a symlink pointing
# back into this package — i.e. the package is stowed on this machine.
# Enumerates from disk, not git ls-files: a package whose only stowable file is
# generated/gitignored (streamrip's config.toml) is invisible to ls-files.
# Also probes the pre-merge tree's paths: after an upstream rename/replace of
# every stowed file, only the old paths' (now dangling) symlinks prove activity,
# and [ -L ] + readlink work fine on dangling links.
is_active() {
    local root="$1" pkg="$2" f rel target dest
    while IFS= read -r f; do
        rel="${f#"$DOTFILES/"}"
        rel="${rel#"$root/$pkg/"}"
        target="$HOME/$rel"
        [ -L "$target" ] || continue
        dest="$(readlink "$target")"
        case "$dest" in
            *"/$root/$pkg/"*) return 0 ;;
        esac
    done < <(
        find "$DOTFILES/$root/$pkg" -type f \
            -not -path '*/_seed/*' -not -name .stow-local-ignore -not -name .DS_Store
        git -C "$DOTFILES" -c core.quotePath=off ls-tree -r --name-only "$OLD" -- "$root/$pkg" 2>/dev/null
    )
    return 1
}

restow_pkg() {
    local root="$1" pkg="$2"
    ( cd "$DOTFILES/$root" && \
      stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' \
           --target="$HOME" "$pkg" )
}

# Package content always lives at root/pkg/file (depth >= 3); depth-2 entries
# like stow-work/.gitkeep are not packages.
changed="$(git -C "$DOTFILES" -c core.quotePath=off diff --name-only "$OLD" "$NEW" -- stow stow-work stow-local 2>/dev/null \
    | awk -F/ 'NF >= 3 { print $1, $2 }' | sort -u)"

[ -n "$changed" ] || exit 0

# Generated configs: outputs are gitignored, so a pull that changes a template
# leaves the built file stale (restow is a no-op for it). Rebuild them BEFORE the
# restow below so a newly generated output (e.g. a brand-new launch agent's
# plist) is on disk when stow runs and gets linked; rebuilding after the restow
# would leave it unlinked. Failures warn but never abort the git operation.
changed_files="$(git -C "$DOTFILES" -c core.quotePath=off diff --name-only "$OLD" "$NEW" 2>/dev/null)"

rebuild() {
    if "$DOTFILES/scripts/$1"; then
        echo "restow-changed: rebuilt via scripts/$1"
    else
        echo "restow-changed: scripts/$1 failed; re-run it by hand" >&2
    fi
}

op_ok() { command -v op >/dev/null 2>&1 && op vault list >/dev/null 2>&1; }

plist_changed=
if grep -q '\.plist\.template$' <<<"$changed_files"; then
    plist_changed=1
    rebuild build-launchd-plists.sh
fi
if grep -q '^stow/vscode/.*settings\.template\.json$' <<<"$changed_files"; then
    rebuild build-vscode-config.sh
fi
if grep -q '^stow/zed/.*settings\.template\.jsonc$' <<<"$changed_files"; then
    if op_ok; then
        rebuild build-zed-config.sh
    else
        echo "restow-changed: zed template changed but 1Password CLI is unavailable; run scripts/build-zed-config.sh by hand" >&2
    fi
fi
if grep -q '^stow/streamrip/.*config\.template\.toml$' <<<"$changed_files"; then
    if op_ok; then
        rebuild build-streamrip-config.sh
    else
        echo "restow-changed: streamrip template changed but 1Password CLI is unavailable; run scripts/build-streamrip-config.sh by hand" >&2
    fi
fi
if grep -q '^stow/navidrome/\.config/navidrome/env\.template$' <<<"$changed_files"; then
    echo "restow-changed: navidrome env.template changed; update ~/.config/navidrome/env by hand" >&2
fi

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

# The rebuilt plists are linked now, but launchd keeps running the old in-memory
# definitions until each changed label is reloaded.
if [ -n "$plist_changed" ]; then
    echo "restow-changed: launchd still runs the old agent definition(s); bootout + bootstrap the affected label(s) or log out/in" >&2
fi

exit 0
