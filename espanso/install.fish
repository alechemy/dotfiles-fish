#!/usr/bin/env fish
set ESPANSO_HOME "$HOME/Library/Application Support/espanso"

mkdir -p "$ESPANSO_HOME/config"
mkdir -p "$ESPANSO_HOME/match"

ln -sf $DOTFILES/espanso/config/* $ESPANSO_HOME/config/
ln -sf $DOTFILES/espanso/match/* $ESPANSO_HOME/match/
