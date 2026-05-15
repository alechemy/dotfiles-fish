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

# 0. Xcode Command Line Tools (Homebrew + git rely on these). If absent,
#    the user gets a modal install dialog. Block here rather than letting
#    later steps fail mid-flight or, worse, block silently in a launchd
#    context after the user walks away.
if ! xcode-select -p &>/dev/null; then
    info "Xcode Command Line Tools not installed. Launching the installer..."
    xcode-select --install || true
    fail "Wait for the Xcode CLT install to finish, then re-run ./scripts/setup.sh"
fi

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

    # Regenerate Karabiner JSON from the EDN source. The repo tracks
    # karabiner.edn (Goku's source format); the runtime karabiner.json is
    # generated and not stowed. Without this step a fresh machine has an
    # empty (or default) JSON and Hyper bindings don't work.
    #
    # Best-effort: relies on `goku` being on PATH, which it will be once
    # Brewfile (`yqrashawn/goku/goku`, installed at step 2) has run. If goku
    # is missing for any reason, this is a silent no-op rather than a fatal
    # error — the user can run `goku` manually afterwards.
    if command -v goku &>/dev/null && [ -f "$HOME/.config/karabiner.edn" ]; then
        info "Regenerating Karabiner JSON via goku..."
        goku || info "WARNING: goku failed; run it manually after Karabiner is permission-granted"
    fi

    # 4b. DEVONthink Pipeline (opt-in, single-machine only)
    #
    # Skip the prompt if DEVONthink isn't installed yet. Loading the launchd
    # agents before DT exists means the daily-note + watchdog scripts fire
    # against a missing app and spam errors. Re-running setup.sh after the
    # DT install picks the pipeline back up.
    if [ ! -d "/Applications/DEVONthink.app" ]; then
        info "DEVONthink.app not found. Skipping pipeline install."
        info "Install DEVONthink, open Lorebook, then re-run setup.sh to enable the pipeline."
    else
        read -r -p "  ? Install DEVONthink pipeline (smart rules + launchd agents)? [y/N] " REPLY
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
            # com.user.granola-import.plist is built from a gitignored
            # template; load_plist logs and skips if it's absent.
            for plist in com.user.dt-daily-note \
                         com.user.dt-watchdog \
                         com.user.singlefile-watcher \
                         com.user.granola-import \
                         com.user.github-stars-import; do
                load_plist "$HOME/Library/LaunchAgents/${plist}.plist"
            done
            success "DEVONthink pipeline installed"

            # Wiki integration (optional)
            read -r -p "  ? Initialize LLM Wiki directory at ~/Wiki? [y/N] " REPLY
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                chmod +x "$DOTFILES/scripts/init-wiki.sh"
                "$DOTFILES/scripts/init-wiki.sh"
            fi
        else
            info "Skipping DEVONthink pipeline."
        fi
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
        read -r -p "  ? Apply macOS defaults? [y/N] " REPLY
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

    # Persist the Catppuccin Macchiato theme as fish universal variables on
    # first run. config.fish deliberately omits `fish_config theme choose`
    # (~120ms/shell) on the assumption these universals are set; without this
    # one-shot, a fresh machine would have no colors until the user ran the
    # theme command manually.
    if ! "$FISH_PATH" -c 'set -q fish_color_command' 2>/dev/null; then
        info "Persisting Catppuccin Macchiato as universal fish theme..."
        if echo y | "$FISH_PATH" -c 'fish_config theme save catppuccin-macchiato --color-theme=dark' >/dev/null 2>&1; then
            success "Fish theme saved"
        else
            info "WARNING: failed to save fish theme; run manually:"
            info "  echo y | fish -c 'fish_config theme save catppuccin-macchiato --color-theme=dark'"
        fi
    fi
fi

# 7. Install mise tool versions
if command -v mise &> /dev/null; then
    info "Installing mise tool versions..."
    mise install --yes
    success "Mise tool versions installed"
fi

# 7b. claude-agent-acp — the Zed ACP bridge referenced from
#     stow/zed/.config/zed/settings.template.jsonc. Upstream gitignores dist/,
#     so a clone alone is not enough; we also build it. node/npm come from
#     mise, hence the `mise exec` invocations.
#
#     URL provenance: verified against the existing local clone at
#     ~/Developer/claude-agent-acp via `git config --get remote.origin.url`.
#     If upstream moves, re-confirm there before editing.
ACP_DIR="$HOME/Developer/claude-agent-acp"
ACP_REPO="https://github.com/rohan-patra/claude-agent-acp"
if [ ! -d "$ACP_DIR/.git" ]; then
    info "Cloning claude-agent-acp..."
    mkdir -p "$HOME/Developer"
    if git clone "$ACP_REPO" "$ACP_DIR"; then
        success "claude-agent-acp cloned"
    else
        info "WARNING: failed to clone claude-agent-acp; Zed agent will be broken until cloned manually"
    fi
fi

if [ -d "$ACP_DIR" ] && [ ! -f "$ACP_DIR/dist/index.js" ]; then
    info "Building claude-agent-acp (npm install + npm run build)..."
    if command -v mise &>/dev/null; then
        if (cd "$ACP_DIR" && mise exec -- npm install --no-fund --no-audit && mise exec -- npm run build); then
            success "claude-agent-acp built"
        else
            info "WARNING: claude-agent-acp build failed; Zed agent will be unavailable until built manually"
        fi
    else
        info "WARNING: mise not on PATH; skipping claude-agent-acp build"
    fi
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
