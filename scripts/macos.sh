#!/usr/bin/env bash

# Close any open System Settings panes, to prevent them from overriding
# settings we’re about to change
osascript -e 'tell application "System Settings" to quit'

# Ask for the administrator password upfront
sudo -v

# Keep-alive: update existing `sudo` time stamp until script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

###############################################################################
# General UI/UX                                                               #
###############################################################################

echo "Applying General UI/UX settings..."

# Disable press-and-hold for keys in favor of key repeat
defaults write -g ApplePressAndHoldEnabled -bool false

# Disable the 'Are you sure you want to open this application?' dialog
defaults write com.apple.LaunchServices LSQuarantine -bool false

# Always open everything in Finder's list view
defaults write com.apple.Finder FXPreferredViewStyle Nlsv

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

# Open new blank file in TextEdit
defaults write com.apple.TextEdit NSShowAppCentricOpenPanelInsteadOfUntitledFile -bool false

# Speed up the dock hide/show animation
defaults write com.apple.dock autohide-delay -float 0.1
defaults write com.apple.dock autohide-time-modifier -float 0.5

###############################################################################
# Third Party Apps                                                            #
###############################################################################

echo "Applying Third Party App settings..."

# Quit running apps when auto-updating via MacUpdater
defaults write com.corecode.MacUpdater HiddenOptionQuitAppsForAutoUpdate -bool YES
defaults write com.corecode.MacUpdater HiddenOptionAutoUpdateAfterManualScan -bool YES

###############################################################################
# File Operations                                                             #
###############################################################################

# Clear all preference files, to ensure that the above command takes effect
# echo "Clearing .DS_Store files..."
# sudo find "$HOME" -name ".DS_Store" -exec rm {} \;

# Create symlink from Chromium bookmarks to Chrome, primarily so that Alfred can see it
if [ -d "$HOME/Library/Application Support/Chromium" ] && [ -d "$HOME/Library/Application Support/Google/Chrome" ]; then
    echo "Linking Chromium bookmarks to Chrome..."
    mv "$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks" "$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks.bak" 2>/dev/null
    ln -sf "$HOME/Library/Application Support/Chromium/Default/Bookmarks" "$HOME/Library/Application Support/Google/Chrome/Default/Bookmarks"
fi



###############################################################################
# Kill affected applications                                                  #
###############################################################################

echo "Restarting apps..."
for app in "Dock" "Finder" "SystemUIServer"; do
	killall "${app}" > /dev/null 2>&1
done

echo "macOS setup complete!"
