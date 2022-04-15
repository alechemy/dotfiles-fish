#!/usr/bin/env fish
set KARABINER_HOME "$HOME/.config/karabiner"
mkdir -p "$KARABINER_HOME"

ln -sf "$DOTFILES/karabiner/karabiner.json" "$KARABINER_HOME/karabiner.json"

