#!/usr/bin/env fish

mkdir -p "$HOME/Library/LaunchAgents"
set AUTOUPDATE_PLIST "$HOME/Library/LaunchAgents/com.github.domt4.homebrew-autoupdate.plist"
test -f "$AUTOUPDATE_PLIST" || touch "$AUTOUPDATE_PLIST"
# TO-DO: configure this to run only if `brew autoupdate status` reports
# that the autoupdate service is not currently running.
brew autoupdate start --upgrade --cleanup
