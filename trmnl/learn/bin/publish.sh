#!/usr/bin/env bash
# Builds and publishes dist/corpus.json to the public repo TRMNL polls.
# Pages caches for 10 minutes, so a new fact can take that long to appear.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PUBLIC_REPO="${TRMNL_LEARN_REPO:-$HOME/Work/trmnl-learn}"

[ -d "$PUBLIC_REPO/.git" ] || {
  echo "publish: $PUBLIC_REPO is not a git checkout — clone alechemy/trmnl-learn or set TRMNL_LEARN_REPO" >&2
  exit 1
}

"$PLUGIN_DIR/bin/build.js"
"$PLUGIN_DIR/bin/overflow-check.rb"

rm -f "$PUBLIC_REPO"/corpus-*.json
cp "$PLUGIN_DIR"/dist/corpus.json "$PLUGIN_DIR"/dist/corpus-*.json "$PUBLIC_REPO/"
cd "$PUBLIC_REPO"

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files -o --exclude-standard)" ]; then
  echo "publish: corpus unchanged"
  exit 0
fi

count=$(node -e 'console.log(JSON.parse(require("fs").readFileSync("corpus.json","utf8")).facts.length)')
git add -A
git commit -q -m "Update corpus: ${count} facts"
git push -q origin main
echo "publish: pushed ${count} facts"
