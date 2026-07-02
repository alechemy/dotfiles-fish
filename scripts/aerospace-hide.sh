#!/usr/bin/env bash
# Cmd-H handler: hide the focused app without leaving the current workspace.
#
# AeroSpace emulates workspaces by hiding/showing windows that all physically
# live on one macOS Space. A native Cmd-H hides the frontmost app, which makes
# macOS activate the next app in global most-recently-used order — often on
# another workspace — so AeroSpace follows focus there and yanks you off the
# workspace you were on. Hiding a non-frontmost app moves no focus, so we focus a
# sibling window on the current workspace first, then hide the target. When the
# target is the only window here there's nothing to hold focus and macOS jumps us
# anyway, so we snap back to the (now empty) workspace afterward.
#
# Everything keys off the app PID, not its name: AeroSpace's app-name and the
# System Events process name disagree on case for some apps (e.g. Ghostty vs
# ghostty), which would otherwise break sibling exclusion and the hide.

set -e

# AeroSpace bindings don't inherit shell PATH on macOS.
export PATH="/opt/homebrew/bin:$PATH"

ws=$(aerospace list-workspaces --focused)

# The focused window's app is the one Cmd-H would hide.
target_pid=$(aerospace list-windows --focused --format "%{app-pid}")
[ -z "$target_pid" ] && exit 0

# Pick a visible sibling on this workspace to hold focus: any window belonging to
# a different app that isn't already a hidden-app placeholder.
sibling=$(aerospace list-windows --workspace "$ws" \
    --format "%{app-pid}|%{window-id}|%{window-layout}" \
  | awk -F'|' -v p="$target_pid" '$1 != p && $3 != "macos_native_window_of_hidden_app" { print $2; exit }')

[ -n "$sibling" ] && aerospace focus --window-id "$sibling"

# Hide the target app by PID; it may no longer be frontmost after the refocus.
# (First run may surface a one-time "control System Events" permission prompt;
# grant it and press again.)
osascript -e "tell application \"System Events\" to set visible of (first application process whose unix id is $target_pid) to false"

# Target was the only window here → macOS jumped us elsewhere; snap back.
if [ "$(aerospace list-workspaces --focused)" != "$ws" ]; then
    aerospace workspace "$ws"
fi

# Recompute outer gaps for the now-correct workspace.
"$HOME/.dotfiles/scripts/aerospace-auto-gaps.sh" hide
