#!/usr/bin/env fish
sudo port install beets-full
# Unclear if this is needed still
# brew install aubio chromaprint imagemagick

set BEETS_HOME "$HOME/.config/beets"

mkdir -p "$BEETS_HOME"

ln -sf "$DOTFILES/beets/config.yaml" "$BEETS_HOME/config.yaml"
ln -sf "$DOTFILES/beets/soundtrack-config.yaml" "$BEETS_HOME/soundtrack-config.yaml"
ln -sf "$DOTFILES/beets/genres.txt" "$BEETS_HOME/genres.txt"
ln -sf "$DOTFILES/beets/genres-tree.yaml" "$BEETS_HOME/genres-tree.yaml"
ln -sf "$DOTFILES/beets/secrets.yaml" "$BEETS_HOME/secrets.yaml"
