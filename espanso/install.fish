#!/usr/bin/env fish
set ESPANSO_HOME "$HOME/Library/Preferences/espanso"
ln -sf "$DOTFILES/espanso/default.yml" "$ESPANSO_HOME/default.yml"

mkdir -p "$ESPANSO_HOME/user"
ln -sf $DOTFILES/espanso/user/* $ESPANSO_HOME/user/

espanso register
