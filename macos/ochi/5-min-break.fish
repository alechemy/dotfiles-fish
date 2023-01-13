#!/usr/bin/env fish
# Kill Ochi, wait for 5min, then reopen it and close its active window.
fish -c 'killall Ochi; sleep 300; open -a Ochi; osascript \
  -e "activate application \"Ochi\"" \
  -e "delay 0.5" \
  -e "tell application \"System Events\"" \
  -e "tell process \"Ochi\"" \
  -e "keystroke \"w\" using {command down}" \
  -e "end tell" \
  -e "end tell" >/dev/null' &
