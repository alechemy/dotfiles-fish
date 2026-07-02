#!/usr/bin/env fish

abbr -a -- glog 'git log -n10 --oneline'
abbr -a -- unpop 'git reset --merge'

abbr -a dotfiles "$HOME/.dotfiles"

abbr -a -- copilot 'copilot --allow-all'

# Kill the process listening on a given port
# Usage: > killport 8081
abbr -a killport 'ports kill'
abbr -a -- rwm reload_wm
