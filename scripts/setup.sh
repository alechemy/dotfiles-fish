#!/usr/bin/env bash

set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STOW_DIR="$DOTFILES/stow"

info() {
    printf "\r  [ \033[00;34m..\033[0m ] $1\n"
}

success() {
    printf "\r\033[2K  [ \033[00;32mOK\033[0m ] $1\n"
}

fail() {
    printf "\r\033[2K  [\033[0;31mFAIL\033[0m] $1\n"
    echo ''
    exit 1
}

# Dry-run stow for a package and back up any non-symlink files that would
# conflict, preserving them as <target>.backup.<epoch>. Called immediately
# before the actual `stow --restow` so first-run machines with pre-existing
# ~/.gitconfig (or similar) don't break the install.
backup_stow_conflicts() {
    local package=$1 conflicts
    conflicts=$(stow --no --no-folding --ignore='.DS_Store' --target="$HOME" "$package" 2>&1 || true)
    if echo "$conflicts" | grep -q 'cannot stow'; then
        echo "$conflicts" | grep 'existing target' | while read -r line; do
            local target target_path
            target=$(echo "$line" | sed 's/.*existing target //' | sed 's/ since.*//')
            target_path="$HOME/$target"
            if [ -e "$target_path" ] && [ ! -L "$target_path" ]; then
                info "Backing up conflicting $target..."
                mv "$target_path" "$target_path.backup.$(date +%s)"
            fi
        done
    fi
}

sudo -v

# Keep-alive: update existing `sudo` time stamp until script has finished
while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &

echo "Setting up dotfiles..."

# 1. Install Homebrew
if ! command -v brew &> /dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add brew to path for this session
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    success "Homebrew installed"
else
    success "Homebrew already installed"
fi

# 2. Install dependencies via Brewfile
if [ -f "$DOTFILES/Brewfile" ]; then
    info "Installing dependencies from Brewfile..."
    brew bundle --file="$DOTFILES/Brewfile"
    success "Dependencies installed"
fi

# 3. Build generated configs (before stowing so the files exist)
info "Building generated configs..."
chmod +x "$DOTFILES/scripts/build-zed-config.sh"
"$DOTFILES/scripts/build-zed-config.sh"
chmod +x "$DOTFILES/scripts/build-streamrip-config.sh"
"$DOTFILES/scripts/build-streamrip-config.sh"
chmod +x "$DOTFILES/scripts/build-vscode-config.sh"
"$DOTFILES/scripts/build-vscode-config.sh"
chmod +x "$DOTFILES/scripts/build-launchd-plists.sh"
"$DOTFILES/scripts/build-launchd-plists.sh"
success "Generated configs built"

# 3b. Lint launchd plist templates: catch ProgramArguments[0] regressions and
#     sub-scripts whose interpreter would resolve through the launchd PATH
#     (which puts mise's shim dir first and re-prompts for Automation
#     permission on every Python rotation). See CLAUDE.md for the rule.
info "Linting launchd plist templates..."
chmod +x "$DOTFILES/scripts/lint-launchd-plists.sh"
"$DOTFILES/scripts/lint-launchd-plists.sh"
success "Launchd plist templates pass TCC stability checks"

# 4. Stow dotfiles
if command -v stow &> /dev/null; then
    info "Stowing dotfiles..."

    mkdir -p "$HOME/.config" "$HOME/.local/bin" "$HOME/.local/share"

    # For each package, back up any non-symlink conflicts (e.g. ~/.gitconfig
    # created by git on first use), then stow.
    cd "$STOW_DIR"
    for package in *; do
        if [ -d "$package" ]; then
            # DEVONthink is opt-in (handled separately below)
            [[ "$package" == "devonthink" ]] && continue
            backup_stow_conflicts "$package"
            stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" "$package"
        fi
    done
    cd "$DOTFILES"
    success "Dotfiles stowed"

    # Aerospace runtime config is not stowed (scripts/aerospace-*-gaps.sh
    # rewrites it). Seed it from source on fresh installs so aerospace doesn't
    # start with empty defaults until the first window event fires.
    if [ ! -e "$HOME/.aerospace.toml" ]; then
        cp "$STOW_DIR/aerospace/.aerospace.toml" "$HOME/.aerospace.toml"
        success "Seeded ~/.aerospace.toml from source"
    fi

    # 4b. DEVONthink Pipeline (opt-in, single-machine only)
    read -p "  ? Install DEVONthink pipeline (smart rules + launchd agents)? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Stowing DEVONthink pipeline..."
        cd "$STOW_DIR"
        backup_stow_conflicts devonthink
        stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink
        cd "$DOTFILES"
        success "DEVONthink pipeline stowed"

        info "Loading launchd agents..."
        # Bootstrap each plist, surfacing real failures while tolerating the
        # "already loaded" case (launchctl exits 37 / Bootstrap failed: 17).
        load_plist() {
            local path=$1 name err
            name=$(basename "$path" .plist)
            if [ ! -f "$path" ]; then
                info "  $name plist not found at $path, skipping"
                return 0
            fi
            err=$(launchctl bootstrap "gui/$(id -u)" "$path" 2>&1) && return 0
            case "$err" in
                *"Bootstrap failed: 17"*|*"already loaded"*)
                    info "  $name already loaded"
                    ;;
                *)
                    fail "Failed to load $name: $err"
                    ;;
            esac
        }
        for plist in com.user.dt-daily-note com.user.dt-watchdog com.user.apptamer-watchdog; do
            load_plist "$HOME/Library/LaunchAgents/${plist}.plist"
        done
        success "DEVONthink pipeline installed"

        # Wiki integration (optional)
        read -p "  ? Initialize LLM Wiki directory at ~/Wiki? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            chmod +x "$DOTFILES/scripts/init-wiki.sh"
            "$DOTFILES/scripts/init-wiki.sh"
        fi
    else
        info "Skipping DEVONthink pipeline."
    fi

    # Warn if Ghostty has a config in Application Support that would shadow the stowed one
    GHOSTTY_APPSUPPORT="$HOME/Library/Application Support/com.mitchellh.ghostty/config"
    if [ -f "$GHOSTTY_APPSUPPORT" ] && [ ! -L "$GHOSTTY_APPSUPPORT" ]; then
        echo ""
        info "WARNING: $GHOSTTY_APPSUPPORT exists and will shadow ~/.config/ghostty/config"
        info "Remove it so Ghostty uses your stowed config: rm \"$GHOSTTY_APPSUPPORT\""
    fi
else
    fail "Stow is not installed. Please check Brewfile installation."
fi

# 5. macOS Defaults
if [[ "$(uname)" == "Darwin" ]]; then
    info "Applying macOS defaults..."
    if [ -f "$DOTFILES/scripts/macos.sh" ]; then
        read -p "  ? Apply macOS defaults? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            # Ensure it's executable
            chmod +x "$DOTFILES/scripts/macos.sh"
            "$DOTFILES/scripts/macos.sh"
            success "macOS defaults applied"
        else
            info "Skipping macOS defaults."
        fi
    else
        echo "  - scripts/macos.sh not found, skipping."
    fi
fi

# 6. Fish Shell Setup (Add to /etc/shells and chsh)
FISH_PATH="$(command -v fish)"
if [ -n "$FISH_PATH" ]; then
    if ! grep -q "$FISH_PATH" /etc/shells; then
        info "Adding fish to /etc/shells..."
        echo "$FISH_PATH" | sudo tee -a /etc/shells
        success "Fish added to /etc/shells"
    fi

    if [ "$SHELL" != "$FISH_PATH" ]; then
        info "Changing default shell to fish..."
        chsh -s "$FISH_PATH"
        success "Default shell changed to fish"
    fi

    # Install fisher and plugins from fish_plugins
    if ! "$FISH_PATH" -c 'type -q fisher'; then
        info "Installing fisher..."
        "$FISH_PATH" -c 'curl -sL https://git.io/fisher | source && fisher update'
        success "Fisher and plugins installed"
    else
        info "Updating fisher plugins..."
        "$FISH_PATH" -c 'fisher update'
        success "Fisher plugins updated"
    fi
fi

# 7. Install mise tool versions
if command -v mise &> /dev/null; then
    info "Installing mise tool versions..."
    mise install --yes
    success "Mise tool versions installed"
fi

# 8. VSCodium Setup
if [ -f "$DOTFILES/scripts/setup-vscode.sh" ]; then
    info "Setting up VSCodium..."
    chmod +x "$DOTFILES/scripts/setup-vscode.sh"
    "$DOTFILES/scripts/setup-vscode.sh"
    success "VSCodium setup complete"
fi

# 9. Espanso Setup
if command -v espanso &> /dev/null; then
    info "Setting up Espanso..."
    espanso service register || true
    espanso start || true
    success "Espanso registered and started"
fi

echo ""
echo "All done! Please restart your terminal."
