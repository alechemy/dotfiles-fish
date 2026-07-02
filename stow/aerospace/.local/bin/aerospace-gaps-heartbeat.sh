#!/bin/bash
# launchd entry for com.user.aerospace-gaps-heartbeat; see the plist template
# for why the heartbeat exists. Exit 0 on the battery skip so launchd doesn't
# treat it as a failure.
"$HOME/.local/bin/should-run-background-job" || exit 0
exec "$HOME/.dotfiles/scripts/aerospace-auto-gaps.sh" heartbeat
