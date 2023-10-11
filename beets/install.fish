#!/usr/bin/env fish
pip3 install -r requirements.txt
brew install chromaprint imagemagick

set BEETS_HOME "$HOME/.config/beets"

mkdir -p "$BEETS_HOME"

ln -sf "$DOTFILES/beets/config.yaml" "$BEETS_HOME/config.yaml"
ln -sf "$DOTFILES/beets/genres.txt" "$BEETS_HOME/genres.txt"
ln -sf "$DOTFILES/beets/genres-tree.yaml" "$BEETS_HOME/genres-tree.yaml"
ln -sf "$DOTFILES/beets/secrets.yaml" "$BEETS_HOME/secrets.yaml"
