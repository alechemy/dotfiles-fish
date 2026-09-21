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

git_dir="$(git -C "$DOTFILES" rev-parse --git-dir 2>/dev/null)" || exit 0
common_dir="$(git -C "$DOTFILES" rev-parse --git-common-dir 2>/dev/null)" || exit 0
if [ "$git_dir" != "$common_dir" ]; then
    echo "restow-changed: linked worktree; live dotfiles remain owned by the primary checkout" >&2
    exit 0
fi

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

# GNU Stow cannot discover a stale destination when the source's whole parent
# directory disappeared. Remove links for tracked deletions explicitly before
# restowing the surviving package contents.
while IFS= read -r deleted; do
    [ -n "$deleted" ] || continue
    root="${deleted%%/*}"
    rest="${deleted#*/}"
    pkg="${rest%%/*}"
    rel="${rest#*/}"
    [ "$rel" != "$rest" ] || continue
    dest="$HOME/$rel"
    [ -L "$dest" ] || continue
    target="$(readlink "$dest")"
    case "$target" in
        *"/$root/$pkg/"*)
            rm "$dest"
            echo "restow-changed: pruned deleted link $dest"
            ;;
    esac
done < <(git -C "$DOTFILES" -c core.quotePath=off diff --diff-filter=D --name-only "$OLD" "$NEW" -- stow stow-work stow-local 2>/dev/null)

# Generated configs: outputs are gitignored, so a pull that changes a template
# leaves the built file stale (restow is a no-op for it). Rebuild them BEFORE the
# restow below so a newly generated output (e.g. a brand-new launch agent's
# plist) is on disk when stow runs and gets linked; rebuilding after the restow
# would leave it unlinked. Failures warn but never abort the git operation.
changed_files="$(git -C "$DOTFILES" -c core.quotePath=off diff --name-only "$OLD" "$NEW" 2>/dev/null)"

rebuild() {
    local script="$1"
    shift
    if "$DOTFILES/scripts/$script" "$@"; then
        echo "restow-changed: rebuilt via scripts/$script $*"
    else
        echo "restow-changed: scripts/$script $* failed; re-run it by hand" >&2
        return 1
    fi
}

# A script-only update can create an output that setup previously skipped.
# Restow its package only when the output was added or removed; edits to an
# already-linked output need no Stow operation. Opt-in checks still run below.
rebuild_stowed() {
    local script="$1" output present root rest pkg i=0
    local before=()
    shift
    for output in "$@"; do
        present=0
        if [ -e "$DOTFILES/$output" ] || [ -L "$DOTFILES/$output" ]; then
            present=1
        fi
        before+=("$present")
    done
    rebuild "$script" || return 1
    for output in "$@"; do
        present=0
        if [ -e "$DOTFILES/$output" ] || [ -L "$DOTFILES/$output" ]; then
            present=1
        fi
        if [ "$present" != "${before[$i]}" ]; then
            root="${output%%/*}"
            rest="${output#*/}"
            pkg="${rest%%/*}"
            changed="$(printf '%s\n%s %s\n' "$changed" "$root" "$pkg" | sort -u)"
        fi
        i=$((i + 1))
    done
}

op_ok() { command -v op >/dev/null 2>&1 && op vault list >/dev/null 2>&1; }

plist_changed=
if grep -Eq '^(stow|stow-work|stow-local)/[^/]+/Library/LaunchAgents/[^/]+\.plist\.template$|^scripts/build-launchd-plists\.sh$' <<<"$changed_files"; then
    plist_changed=1
    plist_outputs=()
    while IFS= read -r template; do
        plist_outputs+=("${template%.template}")
    done < <(cd "$DOTFILES" && find stow stow-work stow-local \
        -path '*/Library/LaunchAgents/*.plist.template' 2>/dev/null)
    rebuild_stowed build-launchd-plists.sh ${plist_outputs[@]+"${plist_outputs[@]}"}
fi
if grep -Eq '^stow/vscode/.*settings\.template\.json$|^scripts/build-vscode-config\.sh$' <<<"$changed_files"; then
    rebuild_stowed build-vscode-config.sh stow/vscode/Library/Application\ Support/VSCodium/User/settings.json
fi
if grep -Eq '^stow/git/(\.gitconfig|\.config/git/allowed_signers)$|^scripts/build-git-allowed-signers\.sh$' <<<"$changed_files"; then
    rebuild build-git-allowed-signers.sh
fi
if grep -Eq '^stow/pi/\.pi/agent/settings\.fragment\.json$|^scripts/merge-pi-settings\.sh$' <<<"$changed_files"; then
    rebuild merge-pi-settings.sh
fi
if grep -Eq '^stow/pi/\.pi/agent/models\.fragment\.json$|^scripts/merge-pi-settings\.sh$' <<<"$changed_files"; then
    rebuild merge-pi-settings.sh --models
fi
if grep -Eq '^scripts/(setup-cmux\.sh|cmux/worktrunk-feedback\.ts)$' <<<"$changed_files"; then
    rebuild setup-cmux.sh --install-pi-hook
fi
if grep -Eq '^stow/worktrunk/|^scripts/setup-worktrunk\.sh$' <<<"$changed_files"; then
    rebuild setup-worktrunk.sh
fi
if grep -Eq '^stow/zed/.*settings\.template\.jsonc$|^scripts/build-zed-config\.sh$' <<<"$changed_files"; then
    if op_ok; then
        rebuild_stowed build-zed-config.sh stow/zed/.config/zed/settings.json
    else
        echo "restow-changed: zed build inputs changed but 1Password CLI is unavailable; run scripts/build-zed-config.sh by hand" >&2
    fi
fi
if grep -Eq '^stow/streamrip/.*config\.template\.toml$|^scripts/build-streamrip-config\.sh$' <<<"$changed_files"; then
    if ! is_active stow streamrip; then
        echo "restow-changed: skipped streamrip rebuild (opt-in, not active here)"
    elif op_ok; then
        rebuild_stowed build-streamrip-config.sh stow/streamrip/.config/streamrip/config.toml
    else
        echo "restow-changed: streamrip build inputs changed but 1Password CLI is unavailable; run scripts/build-streamrip-config.sh by hand" >&2
    fi
fi
for script in build-context7-config.sh build-things-config.sh; do
    if grep -Fxq "scripts/$script" <<<"$changed_files"; then
        if op_ok; then
            if [ "$script" = build-context7-config.sh ]; then
                rebuild_stowed "$script" stow/fish/.config/fish/conf.d/context7.fish
            else
                rebuild "$script"
            fi
        else
            echo "restow-changed: $script changed but 1Password CLI is unavailable; run scripts/$script by hand" >&2
        fi
    fi
done
if grep -Eq '^launchd/com\.user\.iogpu-wired-limit\.plist\.template$|^scripts/install-iogpu-limit\.sh$' <<<"$changed_files"; then
    echo "restow-changed: root LaunchDaemon inputs changed; run scripts/install-iogpu-limit.sh by hand (requires sudo); no daemon was reloaded" >&2
fi
if grep -Eq '^devonthink/utils/dtnote-handler\.applescript$|^scripts/build-dtnote-handler\.sh$' <<<"$changed_files"; then
    if [ -d "$HOME/Applications/DTNote.app" ]; then
        rebuild build-dtnote-handler.sh
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
