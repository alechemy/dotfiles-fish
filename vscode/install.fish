#!/usr/bin/env fish
set VSCODE_HOME "$HOME/Library/Application Support/VSCodium"

mkdir -p "$VSCODE_HOME/User"

ln -sf "$DOTFILES/vscode/product.json" "$VSCODE_HOME"
ln -sf "$DOTFILES/vscode/settings.json" "$VSCODE_HOME/User/settings.json"
ln -sf "$DOTFILES/vscode/keybindings.json" "$VSCODE_HOME/User/keybindings.json"

while read module
    code --install-extension "$module" || true
end <"$DOTFILES/vscode/extensions.txt"

test -f "$VSCODE_HOME/User/globalStorage/alefragnani.project-manager/projects.json" || touch "$VSCODE_HOME/User/globalStorage/alefragnani.project-manager/projects.json"
ln -sf "$DOTFILES/vscode/projects.json" "$VSCODE_HOME/User/globalStorage/alefragnani.project-manager/projects.json"
