#!/usr/bin/env fish

# Disable press-and-hold for keys in favor of key repeat
defaults write -g ApplePressAndHoldEnabled -bool false
# Disable the 'Are you sure you want to open this application?' dialog
defaults write com.apple.LaunchServices LSQuarantine -bool false
# Always open everything in Finder's list view
defaults write com.apple.Finder FXPreferredViewStyle Nlsv
# Clear all preference files, to ensure that the above command takes effect
sudo find $HOME -name ".DS_Store" -exec rm {} \;
# Disable the warning when changing a file extension
defaults write com.apple.Finder FXEnableExtensionChangeWarning -bool false
# Expand save panel by default
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
# Always show all files elsewhere
defaults write -g AppleShowAllFiles -bool false
# Always show all files in Finder
defaults write com.apple.Finder AppleShowAllFiles -bool true
# Maccy preferences
defaults write org.p0deje.Maccy pasteByDefault -bool true
defaults write org.p0deje.Maccy fuzzySearch -bool true
defaults write org.p0deje.Maccy maxMenuItems -int 10
# Disable font smoothing
defaults -currentHost write -g AppleFontSmoothing -int 0
# Open new blank file in TextEdit
defaults write com.apple.TextEdit NSShowAppCentricOpenPanelInsteadOfUntitledFile -bool false
# Speed up the dock hide/show animation
defaults write com.apple.dock autohide-delay -float 0.1
defaults write com.apple.dock autohide-time-modifier -float 0.5
# Quit running apps when auto-updating via MacUpdater
defaults write com.corecode.MacUpdater HiddenOptionQuitAppsForAutoUpdate -bool YES
# Prefer strongest signal
sudo /System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport prefs joinMode=Strongest
# Place system UI in dark mode by default
defaults write -g NSRequiresAquaSystemAppearance -bool Yes
# Force dark mode in DEVONthink
defaults delete com.devon-technologies.think3 NSRequiresAquaSystemAppearance
