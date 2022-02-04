#!/usr/bin/env fish
true > "$DOTFILES/vscode/extensions.txt"
code-insiders --list-extensions > "$DOTFILES/vscode/extensions.txt"
