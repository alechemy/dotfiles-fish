set -gx DOTFILES ~/.dotfiles
set -gx PROJECTS ~/Developer
fish_add_path ~/.local/bin

# Homebrew — must be early so brew-installed tools (starship, mise, etc.) are in PATH
if test -x /opt/homebrew/bin/brew
    eval (/opt/homebrew/bin/brew shellenv)
else if test -x /usr/local/bin/brew
    eval (/usr/local/bin/brew shellenv)
end
