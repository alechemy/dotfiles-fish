#!/usr/bin/env bash

set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VSCODE_STOW="$DOTFILES/stow/vscode"

info() {
    printf "\r  [ \033[00;34m..\033[0m ] $1\n"
}

success() {
    printf "\r\033[2K  [ \033[00;32mOK\033[0m ] $1\n"
}

fail() {
    printf "\r\033[2K  [\033[0;31mFAIL\033[0m] $1\n"
    echo ''
    exit 1
}

# Install extensions
if command -v codium &> /dev/null; then
    if [ -f "$VSCODE_STOW/extensions.txt" ]; then
        read -p "  ? Install VSCodium extensions? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            info "Installing VSCodium extensions..."
            args=()
            while IFS= read -r module || [[ -n "$module" ]]; do
                [[ "$module" =~ ^#.*$ ]] && continue
                [[ -z "$module" ]] && continue
                args+=(--install-extension "$module")
            done < "$VSCODE_STOW/extensions.txt"
            if [ ${#args[@]} -gt 0 ]; then
                codium "${args[@]}" --force || info "WARNING: one or more extensions failed to install (see codium output above); re-run setup.sh to retry"
            fi
            success "Extensions installed"
        else
            info "Skipping VSCodium extensions."
        fi
    fi
else
    info "codium command not found, skipping extension installation."
fi
