#!/bin/bash
#
# mount-nas.sh — mount the local NAS's SMB shares.
#
# Driven by the com.user.mount-nas launch agent: once at login (RunAtLoad) and
# again on every network change (WatchPaths on /etc/resolv.conf). Both triggers
# matter — at login the script can run before Wi-Fi has associated, and the
# network-change trigger re-mounts when the laptop returns to the home network
# after roaming or wakes from sleep.
#
# No credentials live here. macOS NetFS resolves the SMB password from the
# login Keychain — the entry Finder saves when you tick "Remember this password
# in my keychain" on first connect. If that entry is missing, `mount volume`
# falls back to a GUI prompt (see MIGRATION.md).
#
# Idempotent: shares already mounted are left alone, and when the NAS is
# unreachable (portable mode, away from home) the script exits 0 quietly.

set -u

NAS_HOST="192.168.50.54"
NAS_USER="alec"
SHARES=(Media Archive)
HOME_GATEWAY="192.168.50.1"  # only attempt mounts when this is the default gateway

log() {
    printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

# Network-identity gate. Reading the default route is a kernel routing-table
# lookup — it doesn't poke the Wi-Fi radio the way an SMB probe to a remote
# IP would when associated with a foreign network. Works on both Wi-Fi and
# ethernet (docked mode), and avoids Location Services / SSID-read issues.
GATEWAY=$(/sbin/route -n get default 2>/dev/null | awk '/^[[:space:]]*gateway:/ {print $2}')
if [ "$GATEWAY" != "$HOME_GATEWAY" ]; then
    log "default gateway is '${GATEWAY:-none}', not $HOME_GATEWAY — skipping."
    exit 0
fi

# Even on the home LAN the NAS may be powered off, or login may have fired
# before Wi-Fi associated (WatchPaths will retry on the next resolver change).
if ! /usr/bin/nc -z -G 5 -w 5 "$NAS_HOST" 445 >/dev/null 2>&1; then
    log "NAS $NAS_HOST not reachable on :445 — skipping."
    exit 0
fi

for share in "${SHARES[@]}"; do
    if mount | grep -q "@${NAS_HOST}/${share} on /Volumes/"; then
        log "${share}: already mounted."
        continue
    fi
    if /usr/bin/osascript -e "mount volume \"smb://${NAS_USER}@${NAS_HOST}/${share}\"" >/dev/null 2>&1; then
        log "${share}: mounted."
    else
        log "${share}: mount failed — check the Keychain credential for ${NAS_USER}@${NAS_HOST}."
    fi
done
