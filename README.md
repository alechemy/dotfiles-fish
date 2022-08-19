# .dotfiles

My dotfiles for fish shell.

## Pre-requisites

- [Homebrew](https://brew.sh)
- Fish
- Starship
- fzf
- fd
- bat
- espanso
- Karabiner Elements
- VSCodium

```fish
brew install fish starship fzf fd bat espanso
```

```fish
brew install --cask karabiner-elements vscodium
```

## Installation

If possible, transfer `.gitignore`d files from prior machine.

```fish
git clone https://github.com/alechemy/dotfiles-fish.git ~/.dotfiles
cd ~/.dotfiles
./script/bootstrap.fish
```
