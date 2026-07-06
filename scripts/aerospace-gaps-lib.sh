# Shared gap math and window counting for the aerospace gap scripts.
# Sourced, not executed. Caller must set $SOURCE_FILE first.
#
# Constant-width scheme: every tiled window keeps the same width
# w = (screen - 2*inner - 2*base)/3 regardless of window count, with the
# leftover space absorbed by the outer gaps. Adding/removing a window or
# switching workspaces therefore never changes a terminal's column count
# (Claude Code mangles its output when reflowed to a new width).
#
# The screen width is queried live from NSScreen so the same script works
# across machines and scaled-resolution choices (3360 pt on the M1 Max at
# 110 Hz, wider on machines that drive the panel harder). The query is
# in-process AppKit via JXA: no AppleEvents, no TCC prompt, ~140 ms.
#
# Tree view (--all) of every window: "workspace|window-id|app-name|layout" per
# line. The tree is the source of truth for counting: when an app stops
# answering AX requests without the window closing (a SIGSTOPped or starved
# process), AeroSpace keeps the node in the layout tree — still rendering its
# slot — while omitting it from --workspace listings. The tiler follows the
# tree, so anything sizing gaps must too.
tree_snapshot() {
    aerospace list-windows --all --format "%{workspace}|%{window-id}|%{app-name}|%{window-layout}"
}

# Filter a tree_snapshot on stdin to the tiled windows of workspace $1.
tiled_in() {
    awk -F'|' -v ws="$1" '$1 == ws && $4 ~ /^(h|v)_(tiles|accordion)$/'
}

count_tiled_windows() {
    tree_snapshot | tiled_in "$1" | wc -l | tr -d ' '
}

# Sets gap_full (>=3 windows), gap_split (2), gap_centered (0-1).
# Returns non-zero if the screen width cannot be determined.
compute_gap_presets() {
    local width inner base span w
    width=$(osascript -l JavaScript -e \
        'ObjC.import("AppKit"); $.NSScreen.mainScreen.frame.size.width' 2>/dev/null)
    width=${width%%.*}
    case "$width" in ''|*[!0-9]*) return 1 ;; esac

    inner=$(sed -nE 's/^[[:space:]]*inner\.horizontal = ([0-9]+).*/\1/p' "$SOURCE_FILE" | head -n1)
    [ -z "$inner" ] && inner=8

    # Scan base 7-9: consecutive values cover all residues mod 3, so one of
    # them makes w an integer. The 1-up/2-up divisions floor, costing <=1 pt.
    for base in 7 8 9; do
        span=$(( width - 2*inner - 2*base ))
        if [ $(( span % 3 )) -eq 0 ]; then
            break
        fi
    done
    w=$(( span / 3 ))

    gap_full=$base
    gap_split=$(( (width - 2*w - inner) / 2 ))
    gap_centered=$(( (width - w) / 2 ))
}
