#!/usr/bin/env bash
# Quietly import the song to the beets library
/opt/homebrew/bin/beet import --singletons --quiet "$1";
# Run bpmanalyser
/opt/homebrew/bin/beet bpmanalyser;
# trash the file if it's still hanging around
test -f "$1" && /opt/homebrew/bin/trash "$1";
