set -gx DOTFILES ~/.dotfiles
set -gx PROJECTS ~/Developer
fish_add_path ~/.local/bin

# Homebrew — must be early so brew-installed tools (starship, mise, etc.) are in PATH
if test -x /opt/homebrew/bin/brew
    eval (/opt/homebrew/bin/brew shellenv)
else if test -x /usr/local/bin/brew
    eval (/usr/local/bin/brew shellenv)
end

# pnpm — declaratively replaces what `pnpm setup` would write into this file.
# In v11, globally-installed binaries live under $PNPM_HOME/bin (not $PNPM_HOME
# directly), so the path on PATH is the bin subdir.
set -gx PNPM_HOME "$HOME/Library/pnpm"
fish_add_path "$PNPM_HOME/bin"
