#!/usr/bin/env bash
# Install the reviewed agent-reader Pi/JSON overlay without modifying its remote.

set -euo pipefail
umask 077

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OVERLAY_DIR="$DOTFILES/patches/agent-reader"
PATCH="$OVERLAY_DIR/pi-json.patch"
RUNTIME_CONSTRAINTS="$OVERLAY_DIR/runtime-constraints.txt"
BUILD_CONSTRAINTS="$OVERLAY_DIR/build-constraints.txt"
UPSTREAM="https://github.com/alechemy/agent-reader.git"
BASE="09080db090f0741707652535fd8be8b8df429e4c"
PATCH_SHA="8313edf83407011ddeda34259db1be6ba31f05d8146a3e11bb7715c1eea31415"
RUNTIME_SHA="20a22fe7b6e17d86643ebaa0a2a42dc4fe221adf5e762e6f313874678187a46a"
BUILD_SHA="aaad1694a1b6f67b382f62cb7440fc2f501b10e75e0e263bee946ba3d939631c"
PATCHED_TREE="15cab576ed4cbbf1700ff0fbe21ecc3c2671ace5"
PYTHON_VERSION="3.12.13"
WHEEL_NAME="agent_reader-0.1.0-py3-none-any.whl"
CACHE_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/dotfiles-tools"
CACHE_DIR="$CACHE_ROOT/agent-reader-${BASE:0:12}-$PATCH_SHA"

force=0
case "${1:-}" in
    "") ;;
    --force) force=1 ;;
    *)
        echo "usage: install-agent-reader.sh [--force]" >&2
        exit 2
        ;;
esac
[ "$#" -le 1 ] || { echo "usage: install-agent-reader.sh [--force]" >&2; exit 2; }

sha256() {
    /usr/bin/shasum -a 256 "$1" | /usr/bin/awk '{print $1}'
}

require_digest() {
    local path=$1 expected=$2 label=$3 actual
    if [ ! -f "$path" ]; then
        echo "install-agent-reader: missing $label: $path" >&2
        return 1
    fi
    actual=$(sha256 "$path")
    if [ "$actual" != "$expected" ]; then
        echo "install-agent-reader: $label checksum mismatch" >&2
        return 1
    fi
}

agent_reader_compatible() {
    local list_help transcript_help
    command -v agent-read >/dev/null 2>&1 || return 1
    list_help=$(agent-read list --help 2>&1) || return 1
    transcript_help=$(agent-read transcript --help 2>&1) || return 1
    [[ "$list_help" == *"--json"* ]] || return 1
    [[ "$list_help" == *"{claude,copilot,pi}"* ]] || return 1
    [[ "$list_help" == *"0 = all"* ]] || return 1
    [[ "$list_help" == *"default: 20 for table"* && "$list_help" == *"for JSON"* ]] || return 1
    [[ "$transcript_help" == *"--session"* ]] || return 1
}

manifest_value() {
    local key=$1 manifest=$2
    /usr/bin/awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$manifest"
}

cache_dir_valid() {
    local candidate=$1
    local manifest="$candidate/manifest" wheel="$candidate/$WHEEL_NAME" recorded
    [ -d "$candidate" ] && [ ! -L "$candidate" ] || return 1
    [ -f "$manifest" ] && [ ! -L "$manifest" ] || return 1
    [ -f "$wheel" ] && [ ! -L "$wheel" ] || return 1
    [ "$(manifest_value base "$manifest")" = "$BASE" ] || return 1
    [ "$(manifest_value patch_sha256 "$manifest")" = "$PATCH_SHA" ] || return 1
    [ "$(manifest_value patched_tree "$manifest")" = "$PATCHED_TREE" ] || return 1
    [ "$(manifest_value runtime_constraints_sha256 "$manifest")" = "$RUNTIME_SHA" ] || return 1
    [ "$(manifest_value build_constraints_sha256 "$manifest")" = "$BUILD_SHA" ] || return 1
    [ "$(manifest_value python "$manifest")" = "$PYTHON_VERSION" ] || return 1
    [ "$(manifest_value wheel_file "$manifest")" = "$WHEEL_NAME" ] || return 1
    recorded=$(manifest_value wheel_sha256 "$manifest")
    [ -n "$recorded" ] && [ "$(sha256 "$wheel")" = "$recorded" ]
}

cache_valid() {
    cache_dir_valid "$CACHE_DIR"
}

require_digest "$PATCH" "$PATCH_SHA" "overlay"
require_digest "$RUNTIME_CONSTRAINTS" "$RUNTIME_SHA" "runtime constraints"
require_digest "$BUILD_CONSTRAINTS" "$BUILD_SHA" "build constraints"

if [ "$force" -eq 0 ] && agent_reader_compatible; then
    echo "install-agent-reader: compatible agent-reader already installed"
    exit 0
fi

for command in git uv; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "install-agent-reader: required command not found: $command" >&2
        exit 1
    fi
done

tmp=""
cache_stage=""
backup=""
cleanup() {
    [ -z "$tmp" ] || rm -rf -- "$tmp"
    [ -z "$cache_stage" ] || rm -rf -- "$cache_stage"
    if [ -n "$backup" ] && [ ! -e "$CACHE_DIR" ] && [ ! -L "$CACHE_DIR" ]; then
        mv "$backup" "$CACHE_DIR" || true
    fi
}
trap cleanup EXIT

if ! cache_valid; then
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/agent-reader-overlay.XXXXXX")
    source="$tmp/source"
    git -c core.hooksPath=/dev/null clone --quiet --filter=blob:none --no-tags \
        "$UPSTREAM" "$source"
    git -C "$source" -c advice.detachedHead=false checkout --quiet --detach "$BASE"
    [ "$(git -C "$source" rev-parse HEAD)" = "$BASE" ] || {
        echo "install-agent-reader: upstream base mismatch" >&2
        exit 1
    }

    git -C "$source" apply --check "$PATCH"
    git -C "$source" apply --index "$PATCH"
    git -C "$source" diff --cached --check

    expected_paths=$(cat <<'EOF'
README.md
fixtures/pi-session.jsonl
pyproject.toml
src/agent_reader/cli.py
src/agent_reader/extractors/pi.py
src/agent_reader/models.py
src/agent_reader/render.py
tests/conftest.py
tests/test_cli.py
tests/test_discovery.py
tests/test_pi_extractor.py
EOF
)
    actual_paths=$(git -C "$source" diff --cached --name-only)
    if [ "$actual_paths" != "$expected_paths" ]; then
        echo "install-agent-reader: overlay changed an unexpected path" >&2
        exit 1
    fi
    if [ "$(git -C "$source" write-tree)" != "$PATCHED_TREE" ]; then
        echo "install-agent-reader: patched tree mismatch" >&2
        exit 1
    fi

    test_home="$tmp/test-home"
    mkdir "$test_home"
    uv python install "$PYTHON_VERSION" >/dev/null
    managed_python=$(uv python find --managed-python "$PYTHON_VERSION")
    uv_cache=$(uv cache dir)
    (
        cd "$source"
        HOME="$test_home" UV_CACHE_DIR="$uv_cache" \
            UV_PROJECT_ENVIRONMENT="$tmp/test-venv" PYTHONDONTWRITEBYTECODE=1 \
            uv run --frozen --group dev --python "$managed_python" \
            --managed-python pytest -q -p no:cacheprovider
    )
    if [ -n "$(git -C "$source" ls-files --others --exclude-standard)" ]; then
        echo "install-agent-reader: tests left unexpected source files" >&2
        exit 1
    fi

    mkdir -p "$CACHE_ROOT"
    cache_stage=$(mktemp -d "$CACHE_ROOT/.agent-reader-overlay.XXXXXX")
    uv build --project "$source" --wheel --out-dir "$cache_stage" \
        --python "$PYTHON_VERSION" --managed-python \
        --build-constraints "$BUILD_CONSTRAINTS" >/dev/null
    [ -f "$cache_stage/$WHEEL_NAME" ] || {
        echo "install-agent-reader: expected wheel was not built" >&2
        exit 1
    }
    wheel_sha=$(sha256 "$cache_stage/$WHEEL_NAME")
    cat >"$cache_stage/manifest" <<EOF
base=$BASE
patch_sha256=$PATCH_SHA
patched_tree=$PATCHED_TREE
runtime_constraints_sha256=$RUNTIME_SHA
build_constraints_sha256=$BUILD_SHA
python=$PYTHON_VERSION
wheel_file=$WHEEL_NAME
wheel_sha256=$wheel_sha
EOF

    if ! cache_dir_valid "$cache_stage"; then
        echo "install-agent-reader: staged wheel cache failed validation" >&2
        exit 1
    fi
    if [ -e "$CACHE_DIR" ] || [ -L "$CACHE_DIR" ]; then
        backup="$CACHE_DIR.backup.$$"
        if [ -e "$backup" ] || [ -L "$backup" ]; then
            echo "install-agent-reader: cache backup path already exists" >&2
            exit 1
        fi
        mv "$CACHE_DIR" "$backup"
    fi
    if ! mv "$cache_stage" "$CACHE_DIR"; then
        [ -z "$backup" ] || mv "$backup" "$CACHE_DIR"
        backup=""
        echo "install-agent-reader: could not publish wheel cache" >&2
        exit 1
    fi
    cache_stage=""
    if ! cache_valid; then
        rm -rf -- "$CACHE_DIR"
        [ -z "$backup" ] || mv "$backup" "$CACHE_DIR"
        backup=""
        echo "install-agent-reader: published wheel cache failed validation" >&2
        exit 1
    fi
    [ -z "$backup" ] || rm -rf -- "$backup"
    backup=""
fi

uv tool install --force --python "$PYTHON_VERSION" --managed-python \
    --constraints "$RUNTIME_CONSTRAINTS" \
    --build-constraints "$BUILD_CONSTRAINTS" \
    "$CACHE_DIR/$WHEEL_NAME" >/dev/null

tool_bin=$(uv tool dir --bin)
PATH="$tool_bin:$PATH"
export PATH
hash -r
if ! agent_reader_compatible; then
    echo "install-agent-reader: installed tool failed capability validation" >&2
    exit 1
fi

echo "install-agent-reader: installed reviewed Pi/JSON overlay"
