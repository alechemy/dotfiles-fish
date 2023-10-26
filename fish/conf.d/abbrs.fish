#!/usr/bin/env fish

# Add new abbrs to this file.
# Must run `./00-dotfiles/install.fish` to install new abbrs.
abbr -a -- beetlog 'tail -f ~/.config/beets/beets.log'
abbr -a -- glog 'git log -n10 --oneline'
abbr -a -- rm 'rm -I'
abbr -a -- unpop 'git reset --merge'

# Kill a process running on a given port
# Usage: > kill_port 8081
function kill_port
    bash -c "kill -9 $(lsof -ti tcp:$argv)"
end
abbr -a killport --function kill_port
