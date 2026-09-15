#!/usr/bin/env bash
# Build and publish only the managed corpus files to the reviewed public repo.
# Pages caches for 10 minutes, so a new fact can take that long to appear.
set -euo pipefail
shopt -s nullglob

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_REPO="${TRMNL_LEARN_REPO:-$HOME/Work/trmnl-learn}"

fail() {
  echo "publish: $*" >&2
  exit 1
}

[ -d "$PUBLIC_REPO" ] || fail "destination checkout does not exist"
PUBLIC_REPO="$(cd "$PUBLIC_REPO" && pwd -P)"
cd "$PUBLIC_REPO"

check_destination() {
  local top branch urls head upstream status
  top=$(git rev-parse --show-toplevel) || fail "destination is not a working checkout"
  [ "$top" = "$PUBLIC_REPO" ] || fail "destination must be the checkout root"
  branch=$(git symbolic-ref --quiet HEAD) || fail "detached HEAD; check out main first"
  [ "$branch" = refs/heads/main ] || fail "destination must be on main"
  urls=$(git remote get-url --push --all origin) || fail "origin has no push URL"
  case "$urls" in
    https://github.com/alechemy/trmnl-learn|https://github.com/alechemy/trmnl-learn.git|\
    git@github.com:alechemy/trmnl-learn|git@github.com:alechemy/trmnl-learn.git|\
    ssh://git@github.com/alechemy/trmnl-learn|ssh://git@github.com/alechemy/trmnl-learn.git) ;;
    *) fail "origin must have exactly one push URL for alechemy/trmnl-learn" ;;
  esac
  status=$(git status --porcelain --untracked-files=all) || fail "cannot inspect destination status"
  [ -z "$status" ] ||
    fail "destination has staged, unstaged, or untracked work; preserve it before publishing"
  head=$(git rev-parse HEAD)
  upstream=$(git rev-parse --verify refs/remotes/origin/main) ||
    fail "origin/main is missing; review and refresh the destination manually"
  [ "$head" = "$upstream" ] ||
    fail "main differs from origin/main; review pending commits or a failed push manually before retrying"
}

check_destination
"$PLUGIN_DIR/bin/build.js"
"$PLUGIN_DIR/bin/overflow-check.rb"
# Rendering can take minutes. Do not overwrite work created while it ran.
check_destination

[ -f "$PLUGIN_DIR/dist/corpus.json" ] || fail "build produced no corpus.json"
sources=("$PLUGIN_DIR/dist/corpus.json")
for path in "$PLUGIN_DIR"/dist/corpus-*.json; do
  [[ "${path##*/}" =~ ^corpus-[0-9]+\.json$ ]] && sources+=("$path")
done
[ "${#sources[@]}" -gt 1 ] || fail "build produced no numbered shards"
count=$(node -e 'const d=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")); if (!Array.isArray(d.facts)) process.exit(1); console.log(d.facts.length)' "${sources[0]}")

managed=(corpus.json)
for path in corpus-*.json; do
  [[ "$path" =~ ^corpus-[0-9]+\.json$ ]] && managed+=("$path")
done
for path in "${sources[@]}"; do
  managed+=("${path##*/}")
done
# Refuse symlinks, directories, and ignored local files before any deletion/copy.
for path in "${managed[@]}"; do
  [ ! -L "$path" ] && { [ ! -e "$path" ] || [ -f "$path" ]; } ||
    fail "managed path is not a regular file: $path"
  if git check-ignore --quiet -- "$path"; then
    fail "managed path is ignored: $path"
  fi
done
for path in "${managed[@]}"; do
  rm -f -- "$path"
done
cp "${sources[@]}" .
git add -A -- "${managed[@]}"
if git diff --cached --quiet -- "${managed[@]}"; then
  echo "publish: corpus unchanged"
  exit 0
fi

git commit -q --only -m "Update corpus: ${count} facts" -- "${managed[@]}"
commit=$(git rev-parse HEAD)
if ! git push -q origin "$commit:refs/heads/main"; then
  fail "push failed; corpus commit retained on main. Review it and retry the push manually"
fi
echo "publish: pushed ${count} facts"
