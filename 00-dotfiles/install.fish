#!/usr/bin/env fish
set -Ux EDITOR nano
set -Ux VISUAL $EDITOR
set -Ux WEDITOR code

set -Ux DOTFILES ~/.dotfiles
set -Ux PROJECTS ~/Developer

fish_add_path $DOTFILES/bin $HOME/.bin

for f in $DOTFILES/*/functions
  set -Up fish_function_path $f
end

# Deduplicate fish_function_path
set -U fish_function_path (printf '%s\n' $fish_function_path | sort -u)

for f in $DOTFILES/*/conf.d/*.fish
  ln -sf $f ~/.config/fish/conf.d/(basename $f)
end

if test -f ~/.localrc.fish
  ln -sf ~/.localrc.fish ~/.config/fish/conf.d/localrc.fish
end
