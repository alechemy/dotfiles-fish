#!/bin/bash
# launchd entry for com.user.aerospace-gaps-heartbeat; see the plist template
# for why the heartbeat exists. Exit 0 on the battery skip so launchd doesn't
# treat it as a failure. launchd's StandardOutPath appends raw lines with no
# time information, so output is timestamped here at the sink.
{
    "$HOME/.local/bin/should-run-background-job" || exit 0
    exec "$HOME/.dotfiles/scripts/aerospace-auto-gaps.sh" heartbeat
} 2>&1 | while IFS= read -r line || [ -n "$line" ]; do
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"
done
