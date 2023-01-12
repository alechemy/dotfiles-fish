#!/usr/bin/env fish

set HAMMERSPOON_HOME "$HOME/.hammerspoon"
mkdir -p "$HAMMERSPOON_HOME/apps"

ln -sf "$DOTFILES/hammerspoon/init.lua" "$HAMMERSPOON_HOME/init.lua"
ln -sf "$DOTFILES/hammerspoon/apps/init.lua" "$HAMMERSPOON_HOME/apps/init.lua"
