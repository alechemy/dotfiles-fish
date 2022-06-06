#!/usr/bin/env bash
# Quietly import the music to the beets library
/opt/homebrew/bin/beet -c /Users/alec/.dotfiles/beets/hazel-config.yaml import --quiet "$1";
# Run bpmanalyser
/opt/homebrew/bin/beet bpmanalyser;
# trash the dir if it's still hanging around
test -d "$1" && /opt/homebrew/bin/trash "$1";
