#!/usr/bin/env fish
brew install --cask selfcontrol

# https://github.com/AlexanderDickie/auto-selfcontrol-rs
set AUTO_SELFCONTROL_HOME "$HOME/.config/auto-selfcontrol-rs"

mkdir -p "$AUTO_SELFCONTROL_HOME"

ln -sf $DOTFILES/selfcontrol/config.yaml $AUTO_SELFCONTROL_HOME/config.yaml
