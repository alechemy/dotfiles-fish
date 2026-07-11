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

# True when the plist's content was rewritten by this run's build step
# (CHANGED_PLISTS is filled after build-launchd-plists.sh runs). launchd's
# "already loaded" keeps the OLD definition running, so changed agents must
# be booted out and re-bootstrapped to pick up the new file.
plist_changed() {
    local base
    base=$(basename "$1")
    printf '%s\n' "${CHANGED_PLISTS:-}" | grep -q "/${base}\$"
}

reload_agent() {
    local plist=$1
    launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist" 2>&1
}

# Bootstrap an always-on user LaunchAgent, treating "already loaded" as a
# no-op and logging a warning (not a hard failure) on other errors. The
# DEVONthink opt-in block has its own stricter loader that fails hard.
load_launch_agent() {
    local plist=$1 display=${2:-} err
    display=${display:-$(basename "$plist" .plist)}
    if [ ! -f "$plist" ]; then
        info "$display agent plist not found at $plist, skipping"
        return 0
    fi
    info "Loading $display agent..."
    if err=$(launchctl bootstrap "gui/$(id -u)" "$plist" 2>&1); then
        success "$display agent loaded"
        return 0
    fi
    case "$err" in
        *"Bootstrap failed: 17"*|*"already loaded"*)
            if plist_changed "$plist"; then
                if err=$(reload_agent "$plist"); then
                    success "$display agent reloaded (definition changed)"
                else
                    info "WARNING: failed to reload $display agent: $err"
                fi
            else
                info "$display agent already loaded"
            fi
            ;;
        *)
            info "WARNING: failed to load $display agent: $err" ;;
    esac
}

# Dry-run stow for a package and back up any non-symlink files that would
# conflict, preserving them as <target>.backup.<epoch>. Called immediately
# before the actual `stow --restow` so first-run machines with pre-existing
# ~/.gitconfig (or similar) don't break the install.
backup_stow_conflicts() {
    local package=$1 conflicts
    conflicts=$(stow --no --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" "$package" 2>&1 || true)
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

# 0a. Keep xcode-select pointed at Xcode.app when it's installed (CLT alone
#     omits xctrace, full SDKs, Instruments). Self-heal a stale pointer to a
#     deleted Xcode with --reset.
XCODE_APP_DEV="/Applications/Xcode.app/Contents/Developer"
current_xcode_dev=$(xcode-select -p 2>/dev/null || true)
if [ -d "$XCODE_APP_DEV" ] && [ "$current_xcode_dev" != "$XCODE_APP_DEV" ]; then
    info "Repointing xcode-select at Xcode.app (was: ${current_xcode_dev:-unset})..."
    sudo xcode-select -s "$XCODE_APP_DEV"
    success "xcode-select now points at $XCODE_APP_DEV"
elif [ -n "$current_xcode_dev" ] && [ ! -d "$current_xcode_dev" ]; then
    info "xcode-select points at missing path $current_xcode_dev; resetting..."
    sudo xcode-select --reset
    success "xcode-select reset to system default ($(xcode-select -p 2>/dev/null || echo 'unset'))"
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

# 0d. Point this repo's git hooks at the tracked scripts/git-hooks/ directory so
#     `git pull` self-heals stow symlinks. A pull updates the working tree under
#     stow/<pkg>/ but never runs stow, so files synced from another machine land
#     unlinked (and upstream-deleted files leave dangling symlinks) until the
#     next setup.sh. The post-merge / post-rewrite hooks restow the affected
#     packages via scripts/restow-changed.sh. core.hooksPath is local config
#     (not tracked), so it must be (re)set here on every machine.
HOOKS_DIR="$DOTFILES/scripts/git-hooks"
if [ -d "$HOOKS_DIR" ]; then
    chmod +x "$HOOKS_DIR"/* "$DOTFILES/scripts/restow-changed.sh" 2>/dev/null || true
    if [ "$(git -C "$DOTFILES" config --local --get core.hooksPath 2>/dev/null)" != "$HOOKS_DIR" ]; then
        info "Pointing git hooks at scripts/git-hooks (auto-restow on pull)..."
        git -C "$DOTFILES" config --local core.hooksPath "$HOOKS_DIR"
        success "git hooks configured (core.hooksPath -> scripts/git-hooks)"
    fi
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
    # A failed installer download yields an empty command substitution, which
    # /bin/bash -c "" treats as success — verify brew actually landed.
    command -v brew &> /dev/null || fail "Homebrew install failed (network?); re-run ./scripts/setup.sh"
    success "Homebrew installed"
else
    success "Homebrew already installed"
fi

# 1b. Trust the specific third-party tap entries we install. Homebrew 5.2/6.0
#     makes $HOMEBREW_REQUIRE_TAP_TRUST the default, after which untrusted taps'
#     formulae/casks/commands are silently ignored — `brew bundle` would skip
#     sketchybar, borders, goku, aerospace, hrm, and feishin, and `brew autoupdate`
#     (step 2b) would stop resolving. Trust the exact entries we depend on (not
#     whole taps, per Homebrew's guidance) before the bundle runs. `brew trust`
#     is idempotent, doesn't validate that the tap exists yet, and writes
#     ~/.homebrew/trust.json (or $XDG_CONFIG_HOME/homebrew/trust.json).
#
#     alec/local/feishin is our own local tap (materialized in step 1c); it's
#     trusted here for the same reason — without it the Brewfile's feishin cask
#     would be silently skipped under strict tap-trust.
#
#     When adding a Brewfile entry from a new third-party tap, add it here too,
#     or it will be ignored once strict tap-trust becomes the default.
if brew trust --help >/dev/null 2>&1; then
    info "Trusting third-party tap entries..."
    if brew trust --command domt4/autoupdate/autoupdate \
        && brew trust --formula \
            felixkratz/formulae/borders \
            felixkratz/formulae/sketchybar \
            modem-dev/tap/hunk \
            yqrashawn/goku/goku \
        && brew trust --cask nikitabobko/tap/aerospace wontaeyang/hrm/hrm alec/local/feishin; then
        success "Third-party tap entries trusted"
    else
        info "WARNING: 'brew trust' reported a failure; some tap entries may be ignored under HOMEBREW_REQUIRE_TAP_TRUST."
    fi
fi

# 1c. Materialize the local Homebrew tap (alec/local) that carries the Feishin
#     cask. Feishin ships no upstream cask, so the repo keeps one at
#     homebrew/Casks/feishin.rb and exposes it through a local-only tap whose
#     Casks directory is a symlink back into this repo — editing the cask here
#     is live, with no copy to keep in sync. Must run before `brew bundle`
#     (step 2) so the Brewfile's `cask "alec/local/feishin"` resolves. The
#     cask's postflight strips com.apple.quarantine so the unsigned app launches
#     without a Gatekeeper prompt.
LOCAL_TAP_DIR="$(brew --repository)/Library/Taps/alec/homebrew-local"
if [ "$(readlink "$LOCAL_TAP_DIR/Casks" 2>/dev/null)" != "$DOTFILES/homebrew/Casks" ]; then
    info "Materializing local Homebrew tap (alec/local)..."
    mkdir -p "$LOCAL_TAP_DIR"
    ln -sfn "$DOTFILES/homebrew/Casks" "$LOCAL_TAP_DIR/Casks"
    success "Local tap alec/local linked to homebrew/Casks"
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

# 2b. Schedule `brew autoupdate` (daily at 6:00, upgrade + cleanup, run at load,
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
    # The tap only writes StartInterval (24h from agent load); pin the run to
    # 6:00 by swapping in a StartCalendarInterval. Re-running `brew autoupdate
    # start` regenerates the plist, so converge on every setup run.
    AUTOUPDATE_PLIST="$HOME/Library/LaunchAgents/com.github.domt4.homebrew-autoupdate.plist"
    if [[ -f "$AUTOUPDATE_PLIST" ]] &&
        /usr/libexec/PlistBuddy -c 'Print :StartInterval' "$AUTOUPDATE_PLIST" >/dev/null 2>&1; then
        info "Pinning brew autoupdate to 6:00 daily..."
        /usr/libexec/PlistBuddy \
            -c 'Delete :StartInterval' \
            -c 'Add :StartCalendarInterval dict' \
            -c 'Add :StartCalendarInterval:Hour integer 6' \
            -c 'Add :StartCalendarInterval:Minute integer 0' \
            "$AUTOUPDATE_PLIST"
        launchctl bootout "gui/$(id -u)/com.github.domt4.homebrew-autoupdate" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$AUTOUPDATE_PLIST"
        success "brew autoupdate pinned to 6:00 daily"
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
    chmod +x "$DOTFILES/scripts/build-context7-config.sh"
    "$DOTFILES/scripts/build-context7-config.sh" \
        || info "WARNING: Context7 key build failed; re-run ./scripts/setup.sh after fixing 1Password."
    chmod +x "$DOTFILES/scripts/build-things-config.sh"
    "$DOTFILES/scripts/build-things-config.sh" \
        || info "WARNING: Things token build failed; re-run ./scripts/setup.sh after fixing 1Password."
else
    info "Skipping Zed + Context7 + Things config build (needs 1Password CLI signed in)."
    # A `git clone` never produces stow/zed/.config/zed/settings.json (gitignored).
    # If the file is present here without a rebuild, this tree was copied from
    # another machine and the file holds personal API keys resolved by op inject.
    # Stowing it would symlink those secrets into ~/.config/zed/.
    if [ -f "$DOTFILES/stow/zed/.config/zed/settings.json" ]; then
        info "WARNING: stow/zed/.config/zed/settings.json already exists and will be stowed as-is."
        info "  If this tree was copied from another machine it contains personal API keys."
        info "  Sign into 1Password CLI and re-run setup.sh, or delete the file before continuing."
    fi
fi
chmod +x "$DOTFILES/scripts/build-vscode-config.sh"
"$DOTFILES/scripts/build-vscode-config.sh"
chmod +x "$DOTFILES/scripts/build-launchd-plists.sh"
CHANGED_PLISTS_FILE=$(mktemp)
"$DOTFILES/scripts/build-launchd-plists.sh" --changed-file "$CHANGED_PLISTS_FILE"
CHANGED_PLISTS=$(cat "$CHANGED_PLISTS_FILE" 2>/dev/null || true)
rm -f "$CHANGED_PLISTS_FILE"

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
            stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" "$package"
        fi
    done
    cd "$DOTFILES"
    success "Dotfiles stowed"

    # LinearMouse's config is app-rewritten (an atomic-rename save de-stows a
    # symlink), so seed it copy-if-absent rather than stow it.
    "$DOTFILES/scripts/seed-linearmouse-config.sh"

    # hunk ships its Claude Code skill inside the formula; symlink it (via the
    # version-stable opt prefix) instead of vendoring a copy that would drift on
    # `brew upgrade hunk`.
    if HUNK_PREFIX="$(brew --prefix hunk 2>/dev/null)" &&
        [ -d "$HUNK_PREFIX/libexec/skills/hunk-review" ]; then
        mkdir -p "$HOME/.claude/skills"
        ln -sfn "$HUNK_PREFIX/libexec/skills/hunk-review" "$HOME/.claude/skills/hunk-review"
        success "Linked hunk-review Claude skill"
    fi

    # 4a. Opt-in work config (stow-work/work/).
    #
    # stow-work/ is gitignored apart from .gitkeep, so a fresh `git clone` has
    # an empty work package and this block is a no-op. After a file-copy from
    # another machine the package has content and we stow it automatically.
    #
    # The work package mixes $HOME-mirroring subtrees (.config, .ssh, .m2)
    # with top-level items that are installed by hand and must NOT be stowed:
    # standalone Markdown docs, scripts/ (root-owned wrappers installed to
    # /usr/local/bin), and sudoers.d/ (root-owned fragments in /etc/sudoers.d).
    # The .stow-local-ignore that excludes them is itself gitignored, so we
    # seed it here when missing.
    if [ -d "$DOTFILES/stow-work/work" ] && [ -n "$(ls -A "$DOTFILES/stow-work/work" 2>/dev/null)" ]; then
        WORK_IGNORE="$DOTFILES/stow-work/work/.stow-local-ignore"
        if [ ! -f "$WORK_IGNORE" ]; then
            info "Seeding stow-work/work/.stow-local-ignore..."
            cat >"$WORK_IGNORE" <<'EOF'
.*\.md$
^scripts$
^sudoers\.d$
EOF
        fi
        info "Stowing stow-work/work..."
        cd "$DOTFILES/stow-work"
        backup_stow_conflicts work
        stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" work
        cd "$DOTFILES"
        success "stow-work/work stowed"
    fi

    # 4a-bis. Opt-in machine-local stow package (stow-local/local/).
    #
    # Same gitignore treatment as stow-work: a fresh `git clone` ships only
    # .gitkeep, so this is a no-op. After a file-copy from another machine the
    # package has content and we stow it. This is the home for personal,
    # non-shareable tooling that shouldn't live in the public repo. A companion
    # stow-local/install.sh (also gitignored) finishes any out-of-tree setup and
    # is run by hand, not from here.
    if [ -d "$DOTFILES/stow-local/local" ] && [ -n "$(ls -A "$DOTFILES/stow-local/local" 2>/dev/null)" ]; then
        info "Stowing stow-local/local..."
        cd "$DOTFILES/stow-local"
        backup_stow_conflicts local
        stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" local
        cd "$DOTFILES"
        success "stow-local/local stowed"
        [ -x "$DOTFILES/stow-local/install.sh" ] && info "stow-local/install.sh present — run it to finish machine-local setup"
    fi

    # Aerospace runtime config is not stowed (scripts/aerospace-*-gaps.sh
    # rewrites it). Seed it from source on fresh installs so aerospace doesn't
    # start with empty defaults until the first window event fires.
    if [ ! -e "$HOME/.aerospace.toml" ]; then
        cp "$STOW_DIR/aerospace/.aerospace.toml" "$HOME/.aerospace.toml"
        success "Seeded ~/.aerospace.toml from source"
    fi

    # Always-on user LaunchAgents. Stowed above by the main loop; bootstrap now
    # so they're live without waiting for the next login. RunAtLoad means each
    # also fires once immediately. The DEVONthink agents are loaded separately
    # in the opt-in block below.
    load_launch_agent "$HOME/Library/LaunchAgents/com.user.mount-nas.plist" "NAS auto-mount"
    load_launch_agent "$HOME/Library/LaunchAgents/com.user.check-stale-dev-servers.plist" "stale-dev-servers"
    load_launch_agent "$HOME/Library/LaunchAgents/com.user.aerospace-gaps-heartbeat.plist" "aerospace-gaps heartbeat"
    load_launch_agent "$HOME/Library/LaunchAgents/com.user.caddy.plist" "Caddy (oMLX CSP proxy)"

    # Chromium -> Safari bookmark bridge for Alfred. Gate on the Bookmarks file
    # (not the profile dir — a fresh profile has no Bookmarks until the first
    # bookmark is made); the plist is stowed on every machine and launchd loads
    # it at login regardless, but the watcher exits 0 when Bookmarks is absent
    # and KeepAlive.SuccessfulExit=false leaves it dormant. Writing Safari's
    # bookmarks is Full Disk Access-gated, so /usr/bin/python3 must be granted
    # FDA (see CLAUDE.md); until then the agent logs a permission error.
    if [ -f "$HOME/Library/Application Support/Chromium/Default/Bookmarks" ]; then
        load_launch_agent "$HOME/Library/LaunchAgents/com.user.chromium-bookmarks-sync.plist" "chromium-bookmarks sync"
        info "  Grant Full Disk Access to /usr/bin/python3 so the bookmark sync can write Safari's bookmarks"
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
        # Karabiner-Elements writes the runtime JSON (with its default profile)
        # on first launch. On a fresh machine that launch hasn't happened yet, so
        # the file is absent and goku has nothing to compile into. Launch the app
        # headless and wait for the file to appear so the rename + goku steps
        # below run in this same pass, instead of needing a second setup.sh run.
        # The JSON is created regardless of input-monitoring permission; that
        # grant only affects whether remapping actually fires, not file creation.
        if [ ! -f "$KARABINER_JSON" ] && [ -d "/Applications/Karabiner-Elements.app" ]; then
            info "Karabiner JSON not found; launching Karabiner-Elements to generate it..."
            open -g -a "Karabiner-Elements" 2>/dev/null || true
            for _ in $(seq 1 20); do
                if [ -f "$KARABINER_JSON" ]; then break; fi
                sleep 0.5
            done
        fi

        if [ ! -f "$KARABINER_JSON" ]; then
            info "WARNING: $KARABINER_JSON not found."
            info "  Install Karabiner-Elements and open it once to generate it, grant"
            info "  input-monitoring permission, then re-run ./scripts/setup.sh (or run 'goku')."
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

            # Force-enable "Modify events" for the Glove80 (vendor 5824 / product
            # 10203). ZMK mouse-key emulation (HID_POINTING=y) makes the board
            # advertise a mouse HID collection, so Karabiner sees a composite
            # keyboard+pointing device and leaves it ungrabbed by default — its
            # F-key -> m1ddc media-key rules then never fire (raw F11 falls through
            # to Show Desktop). Reflashing the board re-defaults the toggle to off,
            # so re-assert it on every setup run. goku preserves the devices array
            # across regeneration, so this is not clobbered.
            if command -v jq &>/dev/null \
                && ! jq -e '.profiles[] | select(.selected==true) | .devices[]? | select(.identifiers.vendor_id==5824 and .identifiers.product_id==10203 and .ignore==false)' "$KARABINER_JSON" >/dev/null 2>&1; then
                info "Enabling Karabiner 'Modify events' for the Glove80..."
                tmp=$(mktemp) \
                    && jq '(.profiles[] | select(.selected==true) | .devices) |=
                             (( . // [] | map(select(.identifiers.vendor_id != 5824 or .identifiers.product_id != 10203)) )
                              + [{"identifiers":{"is_keyboard":true,"is_pointing_device":true,"vendor_id":5824,"product_id":10203},
                                  "ignore":false,"manipulate_caps_lock_led":false,"treat_as_built_in_keyboard":false,
                                  "disable_built_in_keyboard_if_exists":false}])' "$KARABINER_JSON" >"$tmp" \
                    && mv "$tmp" "$KARABINER_JSON" \
                    && success "Glove80 enabled in Karabiner (Modify events on)"
            fi
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
            stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" devonthink
            cd "$DOTFILES"
            success "DEVONthink pipeline stowed"

            # Seed smart rules / smart groups / custom metadata / batch presets
            # into ~/Library/Application Support/DEVONthink/. Copy-if-absent, so
            # this never clobbers config DEVONthink already owns on this machine.
            info "Seeding DEVONthink config (smart rules, custom metadata)..."
            "$DOTFILES/scripts/seed-devonthink-config.sh"

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
                        if plist_changed "$path"; then
                            if err=$(reload_agent "$path"); then
                                info "  $name reloaded (definition changed)"
                            else
                                fail "Failed to reload $name: $err"
                            fi
                        else
                            info "  $name already loaded"
                        fi
                        ;;
                    *)
                        fail "Failed to load $name: $err"
                        ;;
                esac
            }
            # Pipeline role: exactly one Mac (the driver) runs the document-
            # mutating ingest agents; followers only sync + serve read/UI, with
            # mutating smart rules self-skipping via should-run-dt-driver. Default
            # to follower so a second Mac never becomes an accidental co-driver.
            ROLE_FILE="$HOME/.config/dt-pipeline/role"
            mkdir -p "$HOME/.config/dt-pipeline"
            if [ ! -f "$ROLE_FILE" ]; then
                prompt_read -r -p "  ? Is this Mac the DEVONthink pipeline DRIVER (ingests + runs smart rules)? [y/N] " REPLY
                if [[ $REPLY =~ ^[Yy]$ ]]; then echo driver > "$ROLE_FILE"; else echo follower > "$ROLE_FILE"; fi
            fi
            DT_ROLE=$(tr -d '[:space:]' < "$ROLE_FILE" | tr '[:upper:]' '[:lower:]')
            info "DEVONthink pipeline role: $DT_ROLE"

            # dt-watchdog runs on every machine (keeps DT + sync alive; mutating
            # rules self-skip on a follower). The ingest + entity agents run only
            # on the driver. com.user.granola-import needs the gitignored parser
            # restored from backup; load_plist logs and skips an absent plist.
            dt_driver_agents=(com.user.dt-daily-note \
                              com.user.singlefile-watcher \
                              com.user.boox-import-watcher \
                              com.user.granola-import \
                              com.user.github-stars-import \
                              com.user.dt-morning-brief \
                              com.user.entity-filing \
                              com.user.dt-database-archive)
            dt_agents=(com.user.dt-watchdog)
            if [ "$DT_ROLE" = driver ]; then
                dt_agents+=("${dt_driver_agents[@]}")
            else
                info "  Follower: loading dt-watchdog only; the ingest and entity agents stay disabled."
                # A Mac demoted to follower can still have driver agents loaded
                # from an earlier bootstrap; boot them out so they stop mutating
                # the synced database.
                for label in "${dt_driver_agents[@]}"; do
                    if launchctl bootout "gui/$(id -u)/$label" 2>/dev/null; then
                        info "  booted out stale driver agent: $label"
                    fi
                done
            fi
            for plist in "${dt_agents[@]}"; do
                load_plist "$HOME/Library/LaunchAgents/${plist}.plist"
            done
            success "DEVONthink pipeline installed"

            # The morning brief reads the calendar through EventKit and the
            # address book through the Contacts framework, both under
            # /usr/bin/osascript; those TCC grants can only be created
            # interactively. One manual run of each answers the prompt for good.
            if [ "$DT_ROLE" = driver ]; then
                info "If this machine hasn't granted osascript Calendar/Contacts access yet, run once each:"
                info "  osascript -l JavaScript ~/.local/bin/calendar-events-json.js"
                info "  osascript -l JavaScript ~/.local/bin/contacts-json.js"
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

    # Warn if the Navidrome Keychain entry is missing. The sketchybar music
    # plugin (plugins/feishin.sh) reads the password via
    #   security find-generic-password -s 'Navidrome' -a <user>
    # and shows "No keychain" when it's absent. The password is a personal
    # secret that can't be provisioned from the repo, so this is a non-fatal
    # nudge with the exact command. Only relevant once the env file exists —
    # without it the plugin shows "No config" instead and the entry is moot.
    # Existence is checked without `-w` so no Keychain-access prompt is raised.
    NAVIDROME_ENV="$HOME/.config/navidrome/env"
    if [ -f "$NAVIDROME_ENV" ]; then
        ND_USER=$(awk -F= '/^[[:space:]]*NAVIDROME_USERNAME=/{gsub(/["[:space:]]/,"",$2); print $2; exit}' "$NAVIDROME_ENV")
        ND_USER="${ND_USER:-alec}"
        if ! security find-generic-password -s 'Navidrome' -a "$ND_USER" >/dev/null 2>&1; then
            echo ""
            info "WARNING: no 'Navidrome' Keychain entry for user '$ND_USER'."
            info "  The sketchybar music item will show \"No keychain\" until you add it:"
            info "    security add-generic-password -s 'Navidrome' -a '$ND_USER' -w '<password>' -U"
        fi
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
FISH_PATH="$(command -v fish || true)"
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
        "$FISH_PATH" -c 'curl -sL https://raw.githubusercontent.com/jorgebucaran/fisher/main/functions/fisher.fish | source && fisher update'
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
