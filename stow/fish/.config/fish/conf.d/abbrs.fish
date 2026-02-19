#!/usr/bin/env fish

abbr -a -- glog 'git log -n10 --oneline'
abbr -a -- rm 'rm -I'
abbr -a -- unpop 'git reset --merge'

# Kill a process running on a given port
# Usage: > kill_port 8081
function kill_port
    kill -9 (lsof -ti tcp:$argv)
end
abbr -a killport --function kill_port
