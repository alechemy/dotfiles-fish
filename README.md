# .dotfiles

My dotfiles for fish shell.

## Pre-requisites

- [Homebrew](https://brew.sh)
- Fish
- Starship
- fzf
- fd
- bat
- Karabiner Elements
- Kitty
- Hammerspoon
- VSCodium

```fish
brew install fish starship fzf fd bat
```

```fish
brew install --cask hammerspoon karabiner-elements kitty vscodium
```

## Installation

If possible, transfer `.gitignore`d files from prior machine.

```fish
git clone https://github.com/alechemy/dotfiles-fish.git ~/.dotfiles
cd ~/.dotfiles
./script/bootstrap.fish
```
