#!/usr/bin/env bash
# Merge portable Pi preferences into Pi's app-owned settings file.

set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kind=settings
mode=644
case "${1:-}" in
    '') ;;
    --models) kind=models; mode=600 ;;
    *) echo "Usage: $0 [--models]" >&2; exit 1 ;;
esac
FRAGMENT="$DOTFILES/stow/pi/.pi/agent/$kind.fragment.json"
TARGET="$HOME/.pi/agent/$kind.json"
TARGET_DIR="$(dirname "$TARGET")"

if ! command -v jq >/dev/null 2>&1; then
    echo "merge-pi-settings: jq is required; skipping" >&2
    exit 1
fi

if [ -L "$FRAGMENT" ] || [ ! -f "$FRAGMENT" ] || [ ! -r "$FRAGMENT" ]; then
    echo "merge-pi-settings: fragment must be a readable regular file, not a symlink" >&2
    exit 1
fi

current_input=/dev/null
missing_target=true
if [ -L "$TARGET" ]; then
    echo "merge-pi-settings: refusing to replace symlink: $TARGET" >&2
    exit 1
elif [ -e "$TARGET" ]; then
    if [ ! -f "$TARGET" ] || [ ! -r "$TARGET" ]; then
        echo "merge-pi-settings: live $kind must be a readable regular file" >&2
        exit 1
    fi
    current_input="$TARGET"
    missing_target=false
fi

mkdir -p "$TARGET_DIR"
tmp=$(mktemp "$TARGET_DIR/.$kind.json.XXXXXX")
trap 'rm -f "$tmp"' EXIT

if ! jq -S -n --slurpfile current "$current_input" --slurpfile fragment "$FRAGMENT" \
    --argjson missing "$missing_target" --arg kind "$kind" '
    def one_object:
        if length == 1 and (.[0] | type) == "object" then .[0]
        else error("expected exactly one JSON object") end;
    ($fragment | one_object) as $managed |
    (if $missing then {} else ($current | one_object) end) * $managed |
    if $kind == "models" and (.providers | type) == "object" then
        .providers |= with_entries(
            .key as $provider |
            ($managed.providers[$provider].modelOverrides // {}) as $overrides |
            if (.value.models | type) == "array" then
                .value.models |= map(. * ($overrides[.id] // {}))
            else . end
        )
    else . end
    ' >"$tmp" 2>/dev/null; then
    echo "merge-pi-settings: inputs must each contain exactly one JSON object; left live $kind untouched" >&2
    exit 1
fi
if ! chmod "$mode" "$tmp"; then
    echo "merge-pi-settings: chmod failed; left live $kind untouched" >&2
    exit 1
fi

if [ -e "$TARGET" ] && cmp -s "$tmp" "$TARGET"; then
    if [ "$kind" = models ] && ! chmod 600 "$TARGET"; then
        echo "merge-pi-settings: could not secure live models permissions" >&2
        exit 1
    fi
    echo "Pi $kind already up to date: $TARGET"
    exit 0
fi

if ! mv "$tmp" "$TARGET"; then
    echo "merge-pi-settings: replacement failed; left live $kind untouched" >&2
    exit 1
fi
trap - EXIT
echo "Merged Pi $kind: $TARGET"
