#!/usr/bin/env fish

# If Peppermint theme is already set, don't bother setting it again, because
# doing so will create duplicate entries in Terminal.app's preferences.
set current (defaults read com.apple.terminal "Startup Window Settings")

if test $current != "Peppermint"
  open -a Terminal.app "$DOTFILES/terminal/Peppermint.terminal"
  defaults write com.apple.terminal "Default Window Settings" -string "Peppermint"
  defaults write com.apple.terminal "Startup Window Settings" -string "Peppermint"
end
