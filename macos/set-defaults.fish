#!/usr/bin/env fish

# Disable press-and-hold for keys in favor of key repeat
defaults write -g ApplePressAndHoldEnabled -bool false
# Show the /Volumes folder
sudo chflags nohidden /Volumes
# Disable the 'Are you sure you want to open this application?' dialog
defaults write com.apple.LaunchServices LSQuarantine -bool false
# Always open everything in Finder's list view
defaults write com.apple.Finder FXPreferredViewStyle Nlsv
# Disable the warning when changing a file extension
defaults write com.apple.Finder FXEnableExtensionChangeWarning -bool false
# Always show all files
defaults write com.apple.Finder AppleShowAllFiles -bool true
# Maccy paste on select:
defaults write org.p0deje.Maccy pasteByDefault true
# Disable font smoothing:
defaults -currentHost write -g AppleFontSmoothing -int 0
# Open new blank file in TextEdit:
defaults write com.apple.TextEdit NSShowAppCentricOpenPanelInsteadOfUntitledFile -bool false
