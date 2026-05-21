#!/usr/bin/env bash
# Shared helper: detect which display the bar is currently rendering on.
#
# Echoes one of:
#   compact   — running on the MacBook built-in display (lid open, no external)
#   spacious  — running on an external display (lid closed → clamshell)
#
# Relies on the user's stated invariant: lid is always closed when an external
# display is connected, and standalone use is always laptop-only. That lets us
# read AppleClamshellState as a proxy for "which display is the bar on" without
# a slow system_profiler call.

display_mode() {
  if /usr/sbin/ioreg -r -k AppleClamshellState 2>/dev/null \
    | grep -q '"AppleClamshellState" = Yes'; then
    echo spacious
  else
    echo compact
  fi
}
