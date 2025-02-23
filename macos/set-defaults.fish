#!/usr/bin/env fish

# Disable press-and-hold for keys in favor of key repeat
defaults write -g ApplePressAndHoldEnabled -bool false
# Disable the 'Are you sure you want to open this application?' dialog
defaults write com.apple.LaunchServices LSQuarantine -bool false
# Always open everything in Finder's list view
defaults write com.apple.Finder FXPreferredViewStyle Nlsv
# Clear all preference files, to ensure that the above command takes effect
sudo find $HOME -name ".DS_Store" -exec rm {} \;
# Don't create .DS_Store files on network shares
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
# Disable the warning when changing a file extension
defaults write com.apple.Finder FXEnableExtensionChangeWarning -bool false
# Expand save panel by default
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
# Always show all files in Finder
defaults write com.apple.Finder AppleShowAllFiles -bool true
# Always show all files elsewhere
defaults write -g AppleShowAllFiles -bool true
# Disable font smoothing
defaults -currentHost write -g AppleFontSmoothing -int 0
# Open new blank file in TextEdit
defaults write com.apple.TextEdit NSShowAppCentricOpenPanelInsteadOfUntitledFile -bool false
# Speed up the dock hide/show animation
defaults write com.apple.dock autohide-delay -float 0.1
defaults write com.apple.dock autohide-time-modifier -float 0.5
# Quit running apps when auto-updating via MacUpdater
defaults write com.corecode.MacUpdater HiddenOptionQuitAppsForAutoUpdate -bool YES
defaults write com.corecode.MacUpdater HiddenOptionAutoUpdateAfterManualScan -bool YES
# Prefer strongest signal
sudo /System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport prefs joinMode=Strongest
