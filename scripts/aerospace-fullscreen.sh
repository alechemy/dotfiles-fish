#!/usr/bin/env bash
# Toggle fullscreen on the focused window. On the ultrawide, plain fullscreen
# (bounded by the outer gaps) is the useful maximum — edge-to-edge across 40"
# rarely is. On any other display the gap-bounded version wastes the panel, so
# go edge-to-edge.

set -e

export PATH="/opt/homebrew/bin:$PATH"

if aerospace list-monitors --format '%{monitor-name}' | grep -qF 'DELL U4025QW'; then
    exec aerospace fullscreen
fi
exec aerospace fullscreen --no-outer-gaps
