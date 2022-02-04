#!/usr/bin/env fish
open -a Terminal.app "$DOTFILES/terminal/Peppermint.terminal"
defaults write com.apple.terminal "Default Window Settings" -string "Peppermint"
defaults write com.apple.terminal "Startup Window Settings" -string "Peppermint"
