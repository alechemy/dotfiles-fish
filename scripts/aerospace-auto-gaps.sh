#!/usr/bin/env bash
# Recompute outer gaps from the tiled window count on the focused workspace so
# every window keeps a constant width (one third of the monitor; see
# aerospace-gaps-lib.sh for the math).
# Triggered from AeroSpace callbacks (on-window-detected, on-focus-changed,
# exec-on-workspace-change), the SketchyBar front_app_switched hook, and
# aerospace-hide.sh. $1 is an optional trigger label for the log.
#
# Single-flight worker. Callback bursts (cmd-W fires focus-changed +
# front_app_switched + sometimes workspace-change within milliseconds) must
# not be dropped: the invocation that loses the lock is usually the one
# carrying the final state. So a losing invocation marks a pending flag and
# exits, and the lock holder loops until the flag stays clear.
#
# Each pass sleeps briefly before sampling. AeroSpace's callbacks fire while
# its window tree is still mutating — a closed window can outlive the focus
# shift that announces it, and on-window-detected fires before the
# move-node-to-workspace matchers have placed the new window — so an
# immediate sample reads pre-transition state and bakes in a wrong gap that
# nothing corrects until the next unrelated event.
#
# Source of truth: the dotfiles file. Runtime: a regenerated copy at
# ~/.aerospace.toml with the active gap baked in. The runtime file is rebuilt
# from source whenever source is newer or the gap target changes, so any edits
# to the dotfiles config propagate on the next event without manual resync.

set -e

# AeroSpace callbacks don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

# When true, manual hyper-g cycles suppress auto-mode for the active workspace
# until you leave it. Set to false to disable suppression entirely.
SUPPRESSION_ENABLED=true

SOURCE_FILE="$HOME/.dotfiles/stow/aerospace/.aerospace.toml"
RUNTIME_FILE="$HOME/.aerospace.toml"
# Per-user state dir, not /tmp: fixed names in world-writable /tmp are
# symlink-squat targets, and $TMPDIR isn't guaranteed in aerospace callbacks.
STATE_DIR="$HOME/.cache/aerospace-gaps"
mkdir -p "$STATE_DIR"
SUPPRESS_FILE="$STATE_DIR/suppressed-workspace"
PENDING_FILE="$STATE_DIR/pending"
LOG_FILE="$STATE_DIR/gaps.log"
TRIGGER="${1:-unlabeled}"

. "$HOME/.dotfiles/scripts/aerospace-gaps-lib.sh"

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE"; }

# App snapshot of workspace $1 from a tree_snapshot on stdin, e.g.
# "Ghostty:12010(h_tiles),Finder:88(floating)". Logged with every
# apply/mismatch so a flapping window can be identified from the log alone —
# and, via the id, whether an episode was an AX dropout (same id returns) or a
# recreated window (new id).
ws_apps() {
    awk -F'|' -v ws="$1" '$1 == ws {printf "%s%s:%s(%s)", s, $3, $2, $4; s=","} END {print ""}'
}

# CG window numbers of all on-screen windows. AeroSpace window ids are CG
# window numbers, and AeroSpace keeps parked (hidden-workspace) windows
# technically on-screen — so a tiled window that left the tree but is still
# here did not close; its app just stopped answering AX. Minimized and
# native-fullscreen windows report on-screen false, so they classify as real
# departures. Fails open to empty (= nothing phantom) on any bridge error.
cg_onscreen_ids() {
    osascript -l JavaScript -e '
        ObjC.import("CoreGraphics");
        ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(0, 0)))
            .filter(w => w.kCGWindowIsOnscreen)
            .map(w => w.kCGWindowNumber)
            .join("\n");
    ' 2>/dev/null || true
}

# Skip when more than one monitor is connected (e.g. clamshell + lid open) so
# the manual + automatic gap states don't fight during transient configs.
# The TOML's named-monitor gap rule already keeps the laptop's built-in panel
# on the 4 px fallback regardless of what's written here.
mons_json=$(aerospace list-monitors --json 2>/dev/null || echo '[]')
if [ "$(jq 'length' <<<"$mons_json" 2>/dev/null || echo 1)" -gt 1 ]; then
    exit 0
fi

# Auto-gaps only rewrites the DELL's outer gaps; the built-in display always
# uses the outer fallback, so its window widths never vary with the count. When
# the DELL isn't connected (laptop mode) there is no gap to adjust, but source
# edits must still propagate to the runtime copy — otherwise reload_wm reloads
# a stale runtime for the whole portable session. Sync (under the shared lock,
# so a concurrent cycle-gaps rebuild can't race the copy) and exit. The plain
# copy is safe: the DELL gap values are inert undocked, and the first docked
# event recomputes them.
if ! jq -e --arg m 'DELL U4025QW' 'any(.[]; ."monitor-name" | contains($m))' \
        <<<"$mons_json" >/dev/null 2>&1; then
    if [ ! -f "$RUNTIME_FILE" ] || [ -L "$RUNTIME_FILE" ] || [ "$SOURCE_FILE" -nt "$RUNTIME_FILE" ]; then
        exec 9>"$STATE_DIR/lock"
        flock 9
        TMP=$(mktemp "$RUNTIME_FILE.XXXXXX")
        trap 'rm -f "$TMP"' EXIT
        cp "$SOURCE_FILE" "$TMP"
        chmod 0644 "$TMP"
        mv "$TMP" "$RUNTIME_FILE"
        aerospace reload-config
        log "portable-sync source->runtime trigger=$TRIGGER"
    fi
    exit 0
fi

# Mark pending before trying the lock: marking after a failed flock leaves a
# window where the holder's final pending check completes before the mark
# lands, dropping the event. The winner clears its own self-set flag at the
# top of pass 1.
exec 9>"$STATE_DIR/lock"
: >"$PENDING_FILE"
if ! flock -n 9; then
    exit 0
fi

read_gap() {
    # Extracts the gap integer assigned to the named monitor on outer.left,
    # ignoring the digits embedded in the monitor name itself ("U4025QW").
    # Always exits 0 so set -e doesn't kill the script when the pattern is
    # absent (e.g. during a TOML format migration). Caller treats empty as
    # "fall back to source".
    sed -nE 's/.*outer\.left = \[\{ monitor\."DELL U4025QW" = ([0-9]+).*/\1/p' "$1" 2>/dev/null \
        | head -n1 || true
}

compute_gap_presets || exit 0

reload_pending=false
for pass in 1 2 3 4 5; do
    rm -f "$PENDING_FILE"
    sleep 0.2

    ws=$(aerospace list-workspaces --focused)

    if [ "$SUPPRESSION_ENABLED" = true ] && [ -f "$SUPPRESS_FILE" ]; then
        suppressed_ws=$(cat "$SUPPRESS_FILE")
        if [ "$suppressed_ws" = "$ws" ]; then
            break
        fi
        rm -f "$SUPPRESS_FILE"
    fi

    tree=$(tree_snapshot)
    count=$(printf '%s\n' "$tree" | tiled_in "$ws" | wc -l | tr -d ' ')

    listed=$(aerospace list-windows --workspace "$ws" --format "%{window-layout}" \
        | grep -cE '^(h|v)_(tiles|accordion)$' || true)
    # count and listed tally the same tiled windows two ways (list-windows --all
    # filtered by workspace vs --workspace); they diverge only on an unreliable
    # sample — a window mid-appear/close, straddling workspaces, or a phantom
    # slot from AX starvation — so skip it rather than bake a wrong gap and churn.
    if [ "$count" != "$listed" ]; then
        log "tree/listing mismatch ws=$ws tree=$count listed=$listed trigger=$TRIGGER pass=$pass apps=$(printf '%s\n' "$tree" | ws_apps "$ws")"
        continue
    fi

    # A tiled window that vanished from the whole tree but is still on-screen
    # in CG did not close — its app stopped answering AX (starved or stopped
    # process) and AeroSpace dropped it. The tiler thrash that follows is
    # AeroSpace's; adapting the gap to the phantom count would add two more
    # reloads (out and back). Freeze instead: skip the pass, keep the last
    # known ids so healing is detected, and let the heartbeat retry.
    IDS_FILE="$STATE_DIR/tree-ids-$ws"
    cur_ids=$(printf '%s\n' "$tree" | tiled_in "$ws" | awk -F'|' '{print $2}' | sort -n)
    if [ -f "$IDS_FILE" ]; then
        vanished=""
        while IFS= read -r id; do
            [ -z "$id" ] && continue
            printf '%s\n' "$tree" | awk -F'|' '{print $2}' | grep -qx "$id" \
                || vanished="$vanished$id "
        done <"$IDS_FILE"
        if [ -n "$vanished" ]; then
            cg_ids=$(cg_onscreen_ids)
            phantom=""
            for id in $vanished; do
                printf '%s\n' "$cg_ids" | grep -qx "$id" && phantom="$phantom$id "
            done
            if [ -n "$phantom" ]; then
                log "phantom-drop ws=$ws ids=${phantom% } trigger=$TRIGGER pass=$pass apps=$(printf '%s\n' "$tree" | ws_apps "$ws")"
                continue
            fi
        fi
    fi
    printf '%s\n' "$cur_ids" >"$IDS_FILE"

    # Map count to the outer-left/right value that keeps window width constant.
    case "$count" in
        0|1) target=$gap_centered ;;
        2)   target=$gap_split ;;
        *)   target=$gap_full ;;
    esac

    # Decide whether the runtime needs rebuilding from source.
    needs_rebuild=false
    if [ ! -f "$RUNTIME_FILE" ] || [ -L "$RUNTIME_FILE" ] || [ "$SOURCE_FILE" -nt "$RUNTIME_FILE" ]; then
        needs_rebuild=true
    fi

    current=$(read_gap "$RUNTIME_FILE")
    [ -z "$current" ] && current=$(read_gap "$SOURCE_FILE")

    if [ "$needs_rebuild" = true ] || [ "$current" != "$target" ] || [ "$reload_pending" = true ]; then
        apps=$(printf '%s\n' "$tree" | ws_apps "$ws")
        # Stage to a sibling temp file and atomically rename into place. mv on
        # the same filesystem uses rename(2), so $RUNTIME_FILE never appears
        # truncated even if a process is killed mid-write.
        TMP=$(mktemp "$RUNTIME_FILE.XXXXXX")
        trap 'rm -f "$TMP"' EXIT
        cp "$SOURCE_FILE" "$TMP"
        sed -i '' "s/outer\.left = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.left = [{ monitor.\"DELL U4025QW\" = $target }/" "$TMP"
        sed -i '' "s/outer\.right = \[{ monitor\.\"DELL U4025QW\" = [0-9]* }/outer.right = [{ monitor.\"DELL U4025QW\" = $target }/" "$TMP"
        chmod 0644 "$TMP"
        mv "$TMP" "$RUNTIME_FILE"

        # reload-config re-syncs the visible workspace to the focused window's
        # workspace; fired mid-transition it yanks the user back, whose
        # workspace-change event then applies the opposite gap — a visible
        # ping-pong between two workspaces with different counts. Reload only
        # while focus still matches the sampled workspace; otherwise leave the
        # runtime file staged and let the next pass reload with settled focus
        # (reload_pending forces that reload even if the staged gap already
        # matches the new workspace's target).
        if [ "$(aerospace list-workspaces --focused)" != "$ws" ]; then
            reload_pending=true
            log "defer-reload ws=$ws count=$count gap=$current->$target trigger=$TRIGGER pass=$pass"
            continue
        fi
        reload_pending=false

        aerospace reload-config
        log "apply ws=$ws count=$count gap=$current->$target trigger=$TRIGGER pass=$pass apps=$apps"
    fi

    # Re-sample: if focus moved while we worked, or an event queued behind the
    # lock, this pass's decision may already be stale.
    [ "$(aerospace list-workspaces --focused)" != "$ws" ] && continue
    [ -f "$PENDING_FILE" ] && continue
    break
done

if [ "$pass" = 5 ] && [ -f "$PENDING_FILE" ]; then
    log "pass budget exhausted with work pending trigger=$TRIGGER"
fi

# An event landing between the last pending check and lock release would set
# the flag with no worker left to see it; release and hand off to a fresh
# instance, which either takes the lock or re-marks pending for whoever holds it.
flock -u 9
if [ -f "$PENDING_FILE" ]; then
    exec "$0" retrigger
fi
