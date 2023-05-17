#!/usr/bin/env fish

git config --global credential.helper osxkeychain

# better diffs
if command -qs delta
    git config --global core.pager delta
    git config --global interactive.diffFilter 'delta --color-only'
    git config --global diff.colorMoved default
end

# use vscode as mergetool
if command -qs code
    git config --global merge.tool vscode
    and git config --global mergetool.vscode.cmd "code --wait $MERGED"
end

# clean log
abbr -a glog 'git log -n10 --oneline'

# "git stash unpop"
abbr -a unpop 'git reset --merge'
