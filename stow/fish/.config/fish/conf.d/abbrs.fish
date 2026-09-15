if status is-interactive
    abbr -a -- glog 'git log -n10 --oneline'
    # Remove retired abbreviations when this file is sourced in an existing shell.
    abbr -e copilot unpop

    abbr -a dotfiles "$HOME/.dotfiles"

    # TERM first; killport 8081 --force explicitly permits KILL
    abbr -a killport 'ports kill'
    abbr -a -- rwm reload_wm
end
