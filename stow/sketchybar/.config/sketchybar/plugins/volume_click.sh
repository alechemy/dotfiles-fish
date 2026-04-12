#!/usr/bin/env bash

osascript -e '
tell application "System Events"
    tell process "SoundSource"
        click menu bar item 1 of menu bar 2
    end tell
end tell
' 2>/dev/null
