#!/bin/bash
# launchd entry for com.user.aerospace-gaps-heartbeat; see the plist template
# for why the heartbeat exists. Exit 0 on the battery skip so launchd doesn't
# treat it as a failure. launchd's StandardOutPath appends raw lines with no
# time information, so output is timestamped here at the sink; and because a
# battery skip would repeat every 30s tick, gate chatter is logged only when
# the gate's outcome changes, with the previous outcome kept in a state file.
STATE="$HOME/.cache/aerospace-gaps/heartbeat.gate-state"

{
    outcome=pass
    gate_msg=$("$HOME/.local/bin/should-run-background-job" 2>&1) || outcome=skip
    cur="$outcome $gate_msg"
    prev=$(cat "$STATE" 2>/dev/null)
    if [ "$cur" != "$prev" ]; then
        mkdir -p "${STATE%/*}"
        printf '%s\n' "$cur" > "$STATE"
        [ -z "$gate_msg" ] || echo "$gate_msg (repeats suppressed)"
        if [ "$outcome" = pass ] && [ "${prev%% *}" = skip ]; then
            echo "gate passes again, heartbeat resuming"
        fi
    fi
    [ "$outcome" = pass ] || exit 0
    exec "$HOME/.dotfiles/scripts/aerospace-auto-gaps.sh" heartbeat
} 2>&1 | while IFS= read -r line || [ -n "$line" ]; do
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"
done
