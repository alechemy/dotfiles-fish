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

# Some macOS tools invoked during bootstrap can leave this script's process
# group out of the tty foreground slot; a bare `read` then gets SIGTTIN and
# the parent shell reports `suspended (tty input)`. Copied from setup.sh,
# whose step 8 runs this script after the most SIGTTIN-prone steps.
ensure_tty_foreground() {
    [[ -t 1 ]] || return 0
    [[ -r /dev/tty && -w /dev/tty ]] || return 0
    [[ -x /usr/bin/python3 ]] || return 0

    /usr/bin/python3 - <<'PY' 2>/dev/null || true
import os
import signal

try:
    fd = os.open('/dev/tty', os.O_RDWR)
except OSError:
    raise SystemExit(0)

old_handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
try:
    my_pgid = os.getpgrp()
    if os.tcgetpgrp(fd) != my_pgid:
        os.tcsetpgrp(fd, my_pgid)
finally:
    signal.signal(signal.SIGTTOU, old_handler)
    os.close(fd)
PY
}

prompt_read() {
    ensure_tty_foreground
    read "$@"
}

# Install extensions
if command -v codium &> /dev/null; then
    if [ -f "$VSCODE_STOW/extensions.txt" ]; then
        prompt_read -p "  ? Install VSCodium extensions? [y/N] " -n 1 -r
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
