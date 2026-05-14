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

if ! command -v op >/dev/null 2>&1; then
  echo "Error: 1Password CLI (op) is not installed." >&2
  echo "  Install with: brew install --cask 1password-cli" >&2
  exit 1
fi
if ! op whoami >/dev/null 2>&1; then
  echo "Error: 1Password CLI is not signed in." >&2
  echo "  Sign in with: eval \$(op signin)" >&2
  echo "  Or enable Touch ID/SSH agent in 1Password's Developer settings." >&2
  exit 1
fi

echo "Injecting secrets using 1Password CLI..."
op inject -f -i "$TEMPLATE" -o "$OUT"

# Fail loudly if any op:// reference remains unresolved.
if grep -q 'op://' "$OUT"; then
  echo "Error: unresolved op:// references remain in $OUT" >&2
  grep -n 'op://' "$OUT" >&2
  rm -f "$OUT"
  exit 1
fi

# Expand ${HOME} to actual home directory path
sed -i '' "s|\${HOME}|${HOME}|g" "$OUT"

echo "Successfully built streamrip config: $OUT"
