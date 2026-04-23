#!/usr/bin/env bash
#
# Injects secrets into config.template.toml (tracked) using 1Password CLI
# and saves to config.toml (gitignored, symlinked by stow).
#
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STREAMRIP_DIR="$DOTFILES/stow/streamrip/Library/Application Support/streamrip"
TEMPLATE="$STREAMRIP_DIR/config.template.toml"
OUT="$STREAMRIP_DIR/config.toml"

if [ ! -f "$TEMPLATE" ]; then
  echo "Error: $TEMPLATE not found"
  exit 1
fi

if command -v op >/dev/null 2>&1; then
  echo "Injecting secrets using 1Password CLI..."
  op inject -f -i "$TEMPLATE" -o "$OUT"
else
  echo "Warning: 1Password CLI (op) is not installed."
  echo "  Copying template config directly. op:// references will not be resolved."
  cp "$TEMPLATE" "$OUT"
fi

# Expand ${HOME} to actual home directory path
sed -i '' "s|\${HOME}|${HOME}|g" "$OUT"

echo "Successfully built streamrip config: $OUT"
