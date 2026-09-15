#!/usr/bin/env bash
# Build local SSH verification trust without writing a machine key into Stow.
# Historical trust stays tracked; setup generates id_signing before this runs.
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUBLIC_KEY="$HOME/.ssh/id_signing.pub"
OUT="$HOME/.config/git/allowed_signers.local"
HISTORICAL="$DOTFILES/stow/git/.config/git/allowed_signers"

if [ ! -f "$PUBLIC_KEY" ]; then
    echo "build-git-allowed-signers: missing ~/.ssh/id_signing.pub; run setup from the primary checkout" >&2
    exit 1
fi
ssh-keygen -lf "$PUBLIC_KEY" >/dev/null
read -r key_type key_data _ < "$PUBLIC_KEY"
case "$key_type" in
    ssh-*|ecdsa-*) ;;
    *) echo "build-git-allowed-signers: expected an SSH public key" >&2; exit 1 ;;
esac
principal="$(git config --file "$DOTFILES/stow/git/.gitconfig" user.email)"
[ -n "$principal" ]

# Never follow a pre-existing output symlink into tracked or unrelated files.
if [ -L "$OUT" ] || { [ -e "$OUT" ] && [ ! -f "$OUT" ]; }; then
    echo "build-git-allowed-signers: refusing non-regular output $OUT" >&2
    exit 1
fi
mkdir -p "$(dirname "$OUT")"
tmp="$(mktemp "${OUT}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
{
    cat "$HISTORICAL"
    printf '\n%s namespaces="git" %s %s\n' "$principal" "$key_type" "$key_data"
} > "$tmp"
chmod 600 "$tmp"
if [ -f "$OUT" ] && cmp -s "$tmp" "$OUT"; then
    exit 0
fi
mv "$tmp" "$OUT"
echo "Built machine-local Git allowed signers"
