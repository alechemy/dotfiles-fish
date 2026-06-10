#!/usr/bin/env bash
# Recompute AeroSpace outer gaps when the frontmost app changes.
#
# This bridges the one count change AeroSpace's own callbacks miss: hiding an app
# (Cmd-H) reclassifies its window to macos_native_window_of_hidden_app and drops
# the tiled count, but fires no on-focus-changed / on-window-detected / workspace
# callback. macOS does post NSWorkspaceDidActivateApplication when the next app
# comes forward, which SketchyBar surfaces as front_app_switched, so the gap
# script is hooked here.
#
# Backgrounded with a short settle: front_app_switched can arrive before AeroSpace
# has reclassified the hidden window, so wait briefly before counting. The gap
# script's own flock and idempotent early-exit make the extra invocations on
# ordinary app switches (and the redundant one on unhide, which on-focus-changed
# already handles) safe and cheap.

if [ "$SENDER" = "front_app_switched" ]; then
  ( sleep 0.5; "$HOME/.dotfiles/scripts/aerospace-auto-gaps.sh" ) >/dev/null 2>&1 &
fi
