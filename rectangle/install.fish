#!/usr/bin/env fish
# Increase stash size to 10px, from default of 1px
defaults write com.knollsoft.Hookshot stashCursorBoxWidth -float 10
defaults write com.knollsoft.Hookshot stashVisibleWidth -float 10
# Prevent a window that is quickly dragged above the menu bar from going into Mission Control
defaults write com.knollsoft.Hookshot missionControlDragging -int 2
