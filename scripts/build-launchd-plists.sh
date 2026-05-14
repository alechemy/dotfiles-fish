#!/usr/bin/env bash
#
# Generates LaunchAgent .plist files from .plist.template files by substituting
# __HOME__ with the current user's home directory. This avoids hardcoding a
# username in the dotfiles repo.
#
# Scans every stow package for templates under
# stow/<pkg>/Library/LaunchAgents/*.plist.template.
#
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

count=0
while IFS= read -r template; do
  out="${template%.template}"
  sed "s|__HOME__|${HOME}|g" "$template" > "$out"
  count=$((count + 1))
done < <(find "$DOTFILES/stow" -path '*/Library/LaunchAgents/*.plist.template')

echo "Successfully built $count LaunchAgent plist(s)"
