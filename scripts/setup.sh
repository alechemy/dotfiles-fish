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

# Some macOS tools invoked during bootstrap can leave this script's process
# group out of the tty foreground slot. The next interactive `read` then gets
# SIGTTIN and the parent shell reports `suspended (tty input)`. Reclaim the
# tty just before prompting so later questions still work.
ensure_tty_foreground() {
    [[ -t 1 ]] || return 0
    [[ -r /dev/tty && -w /dev/tty ]] || return 0
    [[ -x /usr/bin/python3 ]] || return 0

    /usr/bin/python3 - <<'PY' 2>/dev/null || true
import os
import signal

try:
    fd = os.open('/dev/tty', os.O_RDWR)
except OSError:
    raise SystemExit(0)

old_handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
try:
    my_pgid = os.getpgrp()
    if os.tcgetpgrp(fd) != my_pgid:
        os.tcsetpgrp(fd, my_pgid)
finally:
    signal.signal(signal.SIGTTOU, old_handler)
    os.close(fd)
PY
}

prompt_read() {
    ensure_tty_foreground
    read "$@"
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

# Note: there used to be a backgrounded `sudo` keep-alive here (a loop running
# `sudo -nv` every 60s to keep the timestamp fresh through long brew installs).
# That loop is removed because on macOS 26 later prompts have intermittently hit
# SIGTTIN (`zsh: suspended (tty input)`) after earlier setup steps, and the
# keep-alive is a plausible contributor even though it has not been proven to be
# the sole cause. With Touch ID for sudo enabled below (step 0b), any later sudo
# prompt is just a fingerprint tap, so the keep-alive is not worth the risk.

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

# 0b. Touch ID for sudo. macOS ships a template at /etc/pam.d/sudo_local.template
#     with `pam_tid.so` commented out; the active file (/etc/pam.d/sudo_local)
#     survives OS updates, unlike edits to /etc/pam.d/sudo. This cuts down on
#     password prompts during the rest of the bootstrap (and forever after).
#     pam_tid.so is harmless on Macs without enrolled Touch ID — it just falls
#     through to the password prompt.
if [ ! -f /etc/pam.d/sudo_local ] && [ -f /etc/pam.d/sudo_local.template ]; then
    info "Enabling Touch ID for sudo..."
    sudo sed 's/^#auth.*pam_tid\.so/auth       sufficient     pam_tid.so/' \
        /etc/pam.d/sudo_local.template | sudo tee /etc/pam.d/sudo_local >/dev/null
    success "Touch ID for sudo enabled (/etc/pam.d/sudo_local)"
fi

# 0c. ~/Library/LaunchAgents must exist before brew bundle runs any cask that ships an agent.
mkdir -p "$HOME/Library/LaunchAgents"

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
    if brew bundle --file="$DOTFILES/Brewfile"; then
        success "Dependencies installed"
    else
        info "WARNING: brew bundle reported failures. Missing entries:"
        brew bundle check --file="$DOTFILES/Brewfile" --verbose || true
        info "Continuing setup; resolve the above and re-run ./scripts/setup.sh when fixed."
    fi
fi

# 2b. Schedule `brew autoupdate` (24h interval, upgrade + cleanup, run at load,
#     prompt for sudo via pinentry-mac when a cask needs it, only when on AC
#     power so battery isn't drained by background upgrades). Idempotent: skip
#     if the launchd job is already loaded so re-running setup.sh doesn't
#     restart the timer.
if brew tap | grep -q '^domt4/autoupdate$'; then
    if brew autoupdate status 2>&1 | grep -q 'installed and running'; then
        info "brew autoupdate already running, leaving schedule untouched"
    else
        info "Starting brew autoupdate (daily, --upgrade --cleanup --immediate --sudo --ac-only)..."
        brew autoupdate start 86400 --upgrade --cleanup --immediate --sudo --ac-only
        success "brew autoupdate scheduled"
    fi
fi

# 3. Build generated configs (before stowing so the files exist)
info "Building generated configs..."

# 3a. 1Password CLI gate — prompt once up front so op-dependent build scripts don't each ask.
OP_READY=0
if command -v op >/dev/null 2>&1; then
    # `op vault list`, not `op whoami`: with 1Password app integration enabled,
    # `op whoami` always reports "not signed in" even though data commands work.
    if op vault list >/dev/null 2>&1; then
        OP_READY=1
    else
        info "1Password CLI can't read your vaults (needed for Zed + streamrip configs)."
        info "  Enable 1Password > Settings > Developer > 'Integrate with 1Password CLI', then unlock the app."
        info "  Or, for a temporary session: eval \$(op signin)"
        prompt_read -r -p "  ? Press Enter once 1Password is authorized, or 's' to skip op-dependent steps: " REPLY
        if [[ ! $REPLY =~ ^[Ss]$ ]] && op vault list >/dev/null 2>&1; then
            OP_READY=1
        else
            info "Skipping op-dependent build steps; re-run ./scripts/setup.sh after signing in."
        fi
    fi
fi

if [ "$OP_READY" -eq 1 ]; then
    chmod +x "$DOTFILES/scripts/build-zed-config.sh"
    "$DOTFILES/scripts/build-zed-config.sh"
else
    info "Skipping Zed config build (needs 1Password CLI signed in)."
fi
chmod +x "$DOTFILES/scripts/build-vscode-config.sh"
"$DOTFILES/scripts/build-vscode-config.sh"
chmod +x "$DOTFILES/scripts/build-launchd-plists.sh"
"$DOTFILES/scripts/build-launchd-plists.sh"

# streamrip is opt-in (single-machine; Qobuz creds aren't useful elsewhere).
# The build script pulls the Qobuz token from 1Password and would fail loudly
# on a machine without that vault item, so we prompt before running it. The
# stow loop below uses INSTALL_STREAMRIP to keep build + stow in sync.
INSTALL_STREAMRIP=0
prompt_read -r -p "  ? Install streamrip (Qobuz music ripping)? [y/N] " REPLY
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ "$OP_READY" -eq 1 ]; then
        INSTALL_STREAMRIP=1
        chmod +x "$DOTFILES/scripts/build-streamrip-config.sh"
        "$DOTFILES/scripts/build-streamrip-config.sh"
    else
        info "Skipping streamrip: 1Password CLI not signed in."
    fi
else
    info "Skipping streamrip."
fi

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
            # streamrip is opt-in (already gated by INSTALL_STREAMRIP above)
            [[ "$package" == "streamrip" && "$INSTALL_STREAMRIP" -ne 1 ]] && continue
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

    # Git SSH commit-signing key. The tracked gitconfig sets commit.gpgsign=true
    # with gpg.format=ssh, so a signing key must exist or every `git commit`
    # fails. Per-machine ed25519 key, no passphrase so signing stays headless.
    if [ ! -f "$HOME/.ssh/id_signing" ]; then
        info "Generating SSH commit-signing key..."
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        ssh-keygen -t ed25519 -C "git signing key" -f "$HOME/.ssh/id_signing" -N "" -q
        success "Created ~/.ssh/id_signing — add the .pub to GitHub as a Signing Key"
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
    KARABINER_JSON="$HOME/.config/karabiner/karabiner.json"
    if command -v goku &>/dev/null && [ -f "$HOME/.config/karabiner.edn" ]; then
        if [ ! -f "$KARABINER_JSON" ]; then
            info "WARNING: $KARABINER_JSON not found."
            info "  Open Karabiner-Elements once to generate it, grant input-monitoring"
            info "  permission, then re-run ./scripts/setup.sh (or run 'goku' manually)."
        else
            # Karabiner-Elements names its starter profile "Default profile" on
            # first launch; goku needs exact match "Default". Quit the app before
            # editing so its in-memory state can't race-write the file back.
            if command -v jq &>/dev/null \
                && ! jq -e '.profiles[] | select(.name == "Default")' "$KARABINER_JSON" >/dev/null 2>&1; then
                info "Renaming Karabiner profile to 'Default' for goku compatibility..."
                osascript -e 'tell application "Karabiner-Elements" to quit' >/dev/null 2>&1 || true
                tmp=$(mktemp) \
                    && jq '(.profiles[0].name) = "Default"' "$KARABINER_JSON" >"$tmp" \
                    && mv "$tmp" "$KARABINER_JSON" \
                    && success "Karabiner profile renamed to 'Default'"
                open -g -a "Karabiner-Elements" 2>/dev/null || true
            fi

            info "Regenerating Karabiner JSON via goku..."
            goku || info "WARNING: goku failed; run it manually after Karabiner is permission-granted"
        fi

        # Start the goku watcher service now that karabiner.edn is in place.
        # We intentionally don't use `restart_service: true` in the Brewfile
        # because bundle runs before stow; gokuw would launch with nothing
        # to watch, exit, and launchd would throttle the respawns into a
        # Bootstrap failed: 5 (EIO). Starting it here avoids that race.
        if ! brew services list | grep -q '^goku.*started'; then
            info "Starting goku watcher service..."
            brew services start yqrashawn/goku/goku || info "WARNING: failed to start goku service; run 'brew services start yqrashawn/goku/goku' manually"
        fi
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
        prompt_read -r -p "  ? Install DEVONthink pipeline (smart rules + launchd agents)? [y/N] " REPLY
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
            prompt_read -r -p "  ? Initialize LLM Wiki directory at ~/Wiki? [y/N] " REPLY
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
        prompt_read -r -p "  ? Apply macOS defaults? [y/N] " REPLY
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

    # $SHELL is inherited from the parent and doesn't reflect a mid-session chsh; ask dscl.
    CURRENT_LOGIN_SHELL=$(dscl . -read "/Users/$USER" UserShell 2>/dev/null | awk '{print $2}')
    if [ "$CURRENT_LOGIN_SHELL" != "$FISH_PATH" ]; then
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
    espanso service register >/dev/null 2>&1 || true
    if espanso_err=$(espanso start 2>&1); then
        success "Espanso registered and started"
    else
        info "WARNING: espanso start failed:"
        printf '%s\n' "$espanso_err" | sed 's/^/    /'
        info "  Try: espanso restart  (or kill stale espanso processes and re-run)"
    fi
fi

echo ""
echo "All done! Please restart your terminal."
