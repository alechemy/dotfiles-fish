#!/usr/bin/env bash
#
# Injects secrets into settings.template.json (JSONC, tracked) using 1Password CLI
# and saves to settings.json (gitignored, symlinked by stow).
#
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZED_DIR="$DOTFILES/stow/zed/.config/zed"
TEMPLATE="$ZED_DIR/settings.template.json"
OUT="$ZED_DIR/settings.json"

if [ ! -f "$TEMPLATE" ]; then
    echo "Error: $TEMPLATE not found"
    exit 1
fi

if command -v op >/dev/null 2>&1; then
    echo "Injecting secrets using 1Password CLI..."
    # op inject replaces op:// references and outputs to settings.json
    op inject -f -i "$TEMPLATE" -o "$OUT"
else
    echo "Warning: 1Password CLI (op) is not installed."
    echo "  Copying template config directly. op:// references will not be resolved."
    cp "$TEMPLATE" "$OUT"
fi

# Replace ${HOME} placeholder with the actual home directory path
sed "s|\${HOME}|${HOME}|g" "$OUT" > "${OUT}.tmp" && mv "${OUT}.tmp" "$OUT"

echo "Successfully built Zed config: $OUT"
