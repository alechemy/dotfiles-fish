#!/usr/bin/env fish

set -Ux PYENV_ROOT $HOME/.pyenv
fish_add_path $PYENV_ROOT/bin

# configure autoenv fisher plugin
set --erase --global autovenv_dir
set -Ux autovenv_dir $HOME/.pyenv/shims
