#!/usr/bin/env bash
#
# Install the LaunchDaemon that raises the Metal wired-memory ceiling
# (iogpu.wired_limit_mb) at every boot, so local MLX/llama.cpp models can map
# more than the ~75% of RAM macOS allows by default.
#
# Why a LaunchDaemon and not a stow package:
#   - `sysctl` writes the live kernel only; the value is back to 0 (= use the
#     built-in heuristic) after a reboot.
#   - /etc/sysctl.conf has not been read by macOS for years, so the classic
#     answer does not work.
#   - Setting the sysctl needs root, so it must be a daemon in
#     /Library/LaunchDaemons (root:wheel, outside $HOME) — stow cannot place
#     it and scripts/build-launchd-plists.sh only handles user LaunchAgents.
#
# The limit is a ceiling on wired GPU allocations, not a reservation: nothing
# is taken from the system until something actually asks for that much, so a
# high value costs nothing at boot and cannot wedge startup. The real risk is
# a model large enough to squeeze the OS at runtime, which is why the value is
# derived from this machine's RAM rather than hardcoded — a limit at or above
# physical memory is the genuine footgun and is refused below.
#
# Usage:
#   install-iogpu-limit.sh              install/refresh, apply to the live kernel
#   install-iogpu-limit.sh --print      show the computed value and current state
#   install-iogpu-limit.sh --uninstall  bootout, remove the plist, restore default
#
set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$DOTFILES/launchd/com.user.iogpu-wired-limit.plist.template"
LABEL="com.user.iogpu-wired-limit"
PLIST="/Library/LaunchDaemons/$LABEL.plist"

# Headroom left to macOS. Capped at 6 GiB so a big-RAM machine gets the memory
# back, floored at a quarter of RAM so a small one keeps a proportional share
# (where this is no more generous than the default, the install is skipped).
MAX_RESERVE_MB=6144
MIN_RAM_MB=32768

RAM_MB=$(( $(sysctl -n hw.memsize) / 1048576 ))
RESERVE_MB=$(( RAM_MB / 4 ))
[ "$RESERVE_MB" -gt "$MAX_RESERVE_MB" ] && RESERVE_MB=$MAX_RESERVE_MB
LIMIT_MB=$(( RAM_MB - RESERVE_MB ))

if [ "$LIMIT_MB" -ge "$RAM_MB" ] || [ "$RESERVE_MB" -lt 4096 ]; then
    echo "Error: computed limit ${LIMIT_MB}MB leaves ${RESERVE_MB}MB of ${RAM_MB}MB for the system; refusing." >&2
    exit 1
fi

CURRENT=$(sysctl -n iogpu.wired_limit_mb 2>/dev/null || echo "unavailable")

if [ "${1:-}" = "--print" ]; then
    echo "RAM:            ${RAM_MB}MB"
    echo "Computed limit: ${LIMIT_MB}MB (${RESERVE_MB}MB reserved)"
    echo "Live sysctl:    ${CURRENT}"
    if [ -f "$PLIST" ]; then
        echo "Daemon:         installed at $PLIST"
    else
        echo "Daemon:         not installed"
    fi
    exit 0
fi

if [ "${1:-}" = "--uninstall" ]; then
    if [ -f "$PLIST" ]; then
        sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
        sudo rm -f "$PLIST"
        echo "Removed $PLIST"
    else
        echo "Not installed."
    fi
    sudo sysctl iogpu.wired_limit_mb=0 >/dev/null
    echo "Live limit reset to 0 (macOS default heuristic)."
    exit 0
fi

if [ "$RAM_MB" -lt "$MIN_RAM_MB" ]; then
    echo "Skipping: ${RAM_MB}MB of RAM — the default ceiling is already about as generous as this would be."
    exit 0
fi

[ -f "$TEMPLATE" ] || { echo "Error: missing $TEMPLATE" >&2; exit 1; }

TMP=$(mktemp -t iogpu-wired-limit)
trap 'rm -f "$TMP"' EXIT
sed "s|__WIRED_LIMIT_MB__|${LIMIT_MB}|g" "$TEMPLATE" > "$TMP"
plutil -lint -s "$TMP" || { echo "Error: rendered plist is invalid" >&2; exit 1; }

if ! cmp -s "$TMP" "$PLIST" 2>/dev/null; then
    sudo install -o root -g wheel -m 644 "$TMP" "$PLIST"
    # launchd keeps running a loaded daemon's old definition, so replace it.
    sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
    sudo launchctl bootstrap system "$PLIST"
    echo "Installed $PLIST (limit ${LIMIT_MB}MB)"
elif ! sudo launchctl print "system/$LABEL" >/dev/null 2>&1; then
    sudo launchctl bootstrap system "$PLIST"
    echo "Bootstrapped $LABEL (limit ${LIMIT_MB}MB)"
else
    echo "Already installed and loaded (limit ${LIMIT_MB}MB)"
fi

if [ "$CURRENT" != "$LIMIT_MB" ]; then
    sudo sysctl iogpu.wired_limit_mb="$LIMIT_MB" >/dev/null
    echo "Live limit set to ${LIMIT_MB}MB (was ${CURRENT})"
fi
