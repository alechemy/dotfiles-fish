#!/usr/bin/env bash
#
# Expands ${HOME} in settings.template.json and saves to settings.json
# (gitignored, symlinked by stow).
#
# Unlike build-zed-config.sh and build-streamrip-config.sh, this script
# performs no secret injection — VSCodium settings.json has no op:// references.
# Path expansion is needed because be5invis.vscode-custom-css requires
# absolute file:// URIs and does not honor VS Code variable substitution.
#
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VSCODE_DIR="$DOTFILES/stow/vscode/Library/Application Support/VSCodium/User"
TEMPLATE="$VSCODE_DIR/settings.template.json"
OUT="$VSCODE_DIR/settings.json"

if [ ! -f "$TEMPLATE" ]; then
  echo "Error: $TEMPLATE not found" >&2
  exit 1
fi

sed "s|\${HOME}|${HOME}|g" "$TEMPLATE" > "$OUT"

echo "Successfully built VSCodium config: $OUT"
