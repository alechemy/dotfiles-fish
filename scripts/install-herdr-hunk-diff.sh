#!/usr/bin/env bash
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REVISION=b063856e85436668a165e511ed16a503ea729752
PATCH="$DOTFILES/scripts/patches/herdr-hunk-diff.patch"
PATCH_HASH="$(shasum -a 256 "$PATCH" | cut -d ' ' -f 1)"
CACHE="$HOME/.local/share/herdr-hunk-diff"
PLUGIN="$CACHE/$REVISION-${PATCH_HASH:0:12}"

if [ ! -f "$PLUGIN/.dotfiles-built" ]; then
    if [ -e "$PLUGIN" ]; then
        echo "Incomplete Hunk plugin build at $PLUGIN; move it aside before retrying." >&2
        exit 1
    fi
    mkdir -p "$CACHE"
    BUILD="$(mktemp -d "$CACHE/.build.XXXXXX")"
    trap 'rm -rf "$BUILD"' EXIT
    git -C "$BUILD" init -q
    git -C "$BUILD" fetch -q --depth 1 https://github.com/jhochenbaum/herdr-hunk-diff.git "$REVISION"
    git -C "$BUILD" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    test "$(git -C "$BUILD" rev-parse HEAD)" = "$REVISION"
    git -C "$BUILD" apply --check "$PATCH"
    git -C "$BUILD" apply "$PATCH"
    (
        cd "$BUILD"
        npm ci --ignore-scripts --no-audit --no-fund
        npm run build
        npm test
    )
    touch "$BUILD/.dotfiles-built"
    mv "$BUILD" "$PLUGIN"
    trap - EXIT
fi

CONFIG_DIR="$(herdr plugin config-dir jhochenbaum.hunkdiff)"
node "$DOTFILES/scripts/configure-herdr-hunk.mjs" "$PLUGIN" "$CONFIG_DIR"
herdr config check
herdr plugin link "$PLUGIN" >/dev/null
echo "Herdr Hunk Diff linked at $PLUGIN"
if ! herdr server reload-config; then
    echo "Herdr will load the Hunk bindings on its next startup."
fi
