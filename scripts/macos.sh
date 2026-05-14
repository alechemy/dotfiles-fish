#!/usr/bin/env bash

set -euo pipefail

# Close any open System Settings panes, to prevent them from overriding
# settings we’re about to change
osascript -e 'tell application "System Settings" to quit' || true

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

# Disable window opening animations
defaults write -g NSAutomaticWindowAnimationsEnabled -bool false

# Auto-hide the menu bar (SketchyBar replaces it)
# 0 = Always, 1 = On Desktop Only, 2 = In Full Screen Only
defaults write com.apple.controlcenter AutoHideMenuBarOption -int 0

# Disable "Displays have separate Spaces"
# https://nikitabobko.github.io/AeroSpace/guide#a-note-on-displays-have-separate-spaces
defaults write com.apple.spaces spans-displays -bool true

# Move windows by dragging any part of the window (while holding ctr+cmd)
defaults write -g NSWindowShouldDragOnGesture -bool true

# Dark mode
defaults write -g AppleInterfaceStyle Dark

# Fastest key repeat. InitialKeyRepeat is the delay before repeat begins
# (lower = faster start). KeyRepeat is the interval between repeats (2 is the
# fastest the slider in System Settings exposes).
defaults write -g InitialKeyRepeat -int 15
defaults write -g KeyRepeat -int 2

# Traditional (non-natural) scrolling — scroll content moves the same direction
# as the fingers, opposite of the macOS default.
defaults write -g com.apple.swipescrolldirection -bool false

# Disable text "autocorrect" features that misbehave in code and shell prompts.
defaults write -g NSAutomaticCapitalizationEnabled -bool false
defaults write -g NSAutomaticPeriodSubstitutionEnabled -bool false

# Show the path bar and status bar in Finder windows.
defaults write com.apple.finder ShowPathbar -bool true
defaults write com.apple.finder ShowStatusBar -bool true

# New Finder windows open the home folder (PfHm = Home).
defaults write com.apple.finder NewWindowTarget -string "PfHm"
defaults write com.apple.finder NewWindowTargetPath -string "file:///"

# Finder search defaults to the current folder, not "This Mac".
defaults write com.apple.finder FXDefaultSearchScope -string "SCev"

# Dock on the right edge and auto-hide.
defaults write com.apple.dock orientation -string "right"
defaults write com.apple.dock autohide -bool true

# Save screenshots to ~/Screenshots instead of the Desktop.
mkdir -p "$HOME/Screenshots"
defaults write com.apple.screencapture location "$HOME/Screenshots"

###############################################################################
# Third Party Apps                                                            #
###############################################################################

echo "Applying Third Party App settings..."

# Quit running apps when auto-updating via MacUpdater
if [ -d "/Applications/MacUpdater.app" ]; then
    defaults write com.corecode.MacUpdater HiddenOptionQuitAppsForAutoUpdate -bool YES
    defaults write com.corecode.MacUpdater HiddenOptionAutoUpdateAfterManualScan -bool YES
fi

# Disable dark-mode PDF rendering in DEVONthink
if [ -d "/Applications/DEVONthink.app" ]; then
    defaults write com.devon-technologies.think DisablePDFDarkMode true
fi

# Opt App Tamer out of macOS automatic termination. As a UIElement (menubar)
# app it's otherwise a candidate for being silently killed under memory
# pressure, which defeats the purpose of running it.
if [ -d "/Applications/App Tamer.app" ]; then
    defaults write com.stclairsoft.apptamer NSSupportsAutomaticTermination -bool NO
fi

###############################################################################
# File Operations                                                             #
###############################################################################

# Create symlink from Chromium bookmarks to Chrome so Alfred can see them.
# Bookmarks is a regular file (Chromium's JSON bookmark store), not a directory.
APP_SUPPORT="$HOME/Library/Application Support"
CHROME_HOME="$APP_SUPPORT/Google/Chrome/Default"
CHROMIUM_HOME="$APP_SUPPORT/Chromium/Default"
if [ -f "$CHROMIUM_HOME/Bookmarks" ] && [ -d "$CHROME_HOME" ]; then
    echo "Linking Chromium bookmarks to Chrome..."
    if [ -e "$CHROME_HOME/Bookmarks" ] && [ ! -L "$CHROME_HOME/Bookmarks" ]; then
        mv "$CHROME_HOME/Bookmarks" "$CHROME_HOME/Bookmarks.bak.$(date +%s)"
    fi
    ln -sf "$CHROMIUM_HOME/Bookmarks" "$CHROME_HOME/Bookmarks"
fi

###############################################################################
# Kill affected applications                                                  #
###############################################################################

echo "Restarting apps..."
for app in "Dock" "Finder" "SystemUIServer"; do
	killall "${app}" > /dev/null 2>&1 || true
done

echo "macOS setup complete!"
