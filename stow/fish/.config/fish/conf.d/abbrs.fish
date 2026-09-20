if status is-interactive
    abbr -a -- glog 'git log -n10 --oneline'
    # Remove retired abbreviations when this file is sourced in an existing shell.
    abbr -e copilot unpop

    abbr -a dotfiles "$HOME/.dotfiles"

    # TERM first; killport 8081 --force explicitly permits KILL
    abbr -a killport 'ports kill'
    abbr -a -- rwm reload_wm

    abbr -a -- pn 'wt pi new'
    abbr -a -- po 'wt pi open'
    abbr -a -- pl 'wt pi list'
    abbr -a -- ws 'wt switch'
    abbr -a -- wb 'wt switch -'
    abbr -a -- hd 'hunk diff'
    abbr -a -- hs 'hunk diff --staged'
    abbr -a -- hc 'hunk show HEAD'
    abbr -a -- hwatch 'hunk diff --watch'
    abbr -a -- prm 'wt pi remove'
    abbr -a -- wmerge 'wt merge --no-commit --no-rebase --no-remove'
end
