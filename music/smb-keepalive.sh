#!/bin/zsh
MOUNTPOINT="/Volumes/Media/Music/Music/Media.localized/Automatically Add to Music.localized"

if [ -d "$MOUNTPOINT" ]; then
  ls "$MOUNTPOINT" >/dev/null 2>&1
fi
