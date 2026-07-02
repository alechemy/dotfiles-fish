# TIP: To maintain these organized sections, manually add new packages or generate a
# dump to a temporary file (`brew bundle dump --file=/tmp/Brewfile`) and copy over what you need.
# To see what you currently have installed, use:
#   - `brew tap` (taps)
#   - `brew leaves` (binaries/formulae)
#   - `brew list --cask` (apps)
#   - `mas list` (Mac App Store apps)

# Third-party taps. When adding an entry installed from one of these (or a new
# tap), also trust it in scripts/setup.sh (step 1b) — Homebrew 5.2/6.0 ignores
# untrusted taps' formulae/casks/commands by default.
tap "domt4/autoupdate"
tap "felixkratz/formulae"
tap "modem-dev/tap"
tap "nikitabobko/tap"
tap "wontaeyang/hrm"
tap "yqrashawn/goku"

# Binaries
brew "ast-grep"
brew "bat"
brew "felixkratz/formulae/borders"
brew "coreutils"
brew "eza"
brew "fd"
brew "fish"
brew "flock"
brew "fswatch"
brew "fzf"
brew "gh"
brew "ghostscript"
brew "git"
brew "git-delta"
brew "gnu-sed"
brew "gnu-tar"
brew "grep"
brew "modem-dev/tap/hunk"
brew "imagemagick"
brew "jq"
brew "jscpd"
brew "lazygit"
# Pillow (built from source in ~/Developer/streamrip/.venv) dynamically links these
# at runtime; pinned here so `brew autoremove` can't drop them and break `rip`.
brew "libimagequant"
brew "libraqm"
brew "m1ddc"
brew "markdownlint-cli"
brew "mas"
brew "maven"
brew "media-control"
brew "mise"
brew "monolith"
brew "mosh"
brew "mpv"
brew "nowplaying-cli"
brew "pandoc"
brew "pinentry-mac"
brew "ripgrep"
brew "rsync"
brew "felixkratz/formulae/sketchybar"
brew "skopeo"
brew "sqlcipher"
brew "starship"
brew "stow"
brew "switchaudio-osx"
brew "tmux"
brew "uv"
brew "wget"
brew "yqrashawn/goku/goku"
brew "yt-dlp"
brew "zoxide"

# Apps
cask "1password"
cask "1password-cli@beta"
cask "nikitabobko/tap/aerospace"
cask "alfred"
cask "app-tamer"
cask "appcleaner"
cask "claude-code@latest"
cask "cleanshot"
cask "copilot-cli"
# dropzone disabled until the cask is updated to v5
# cask "dropzone"
cask "espanso"
# Feishin has no upstream Homebrew cask, so this repo carries one in a local-only
# tap: homebrew/Casks/feishin.rb, linked in as `alec/local` by setup.sh (step 1c)
# and trusted in step 1b. The cask's postflight strips com.apple.quarantine so the
# unsigned app launches without a Gatekeeper prompt. Bump version + sha256 in the
# cask to update; `brew livecheck feishin` reports new releases.
# (Navidrome frontend; the SketchyBar 'feishin' plugin depends on it.)
cask "alec/local/feishin"
cask "font-hack-nerd-font"
cask "font-jetbrains-mono"
cask "ghostty"
cask "granola"
cask "wontaeyang/hrm/hrm"
cask "karabiner-elements"
cask "keyboard-maestro"
cask "launchcontrol"
cask "localsend"
cask "maestral"
cask "marked-app"
cask "orbstack"
cask "qobuz"
cask "soundsource"
cask "tailscale-app"
cask "ungoogled-chromium"
cask "vscodium"
cask "xld"
cask "zed@preview"

# Mac App Store
mas "1Password for Safari", id: 1569813296
mas "CotEditor", id: 1024640650
mas "Drafts", id: 1435957248
mas "Dropover", id: 1355679052
mas "Fantastical", id: 975937182
mas "Find Any File", id: 402569179
mas "Flighty", id: 1358823008
mas "Folder Peek", id: 1615988943
mas "GrandPerspective", id: 1111570163
mas "Hand Mirror", id: 1502839586
mas "Infuse", id: 1136220934
mas "Keka", id: 470158793
mas "Name Mangler 3", id: 603637384
mas "Parcel", id: 375589283
mas "Refined GitHub", id: 1519867270
mas "StopTheMadness Pro", id: 6471380298
mas "Things", id: 904280696
mas "Tot", id: 1491071483
mas "Transmit", id: 1436522307
mas "uBlock Origin Lite", id: 6745342698
mas "Wipr", id: 1662217862
# Xcode is ~15 GB; uncomment to install eagerly on a fresh machine, otherwise
# install on demand via `mas install 497799835` (requires the Apple ID to have
# previously "obtained" Xcode in the App Store at least once).
# mas "Xcode", id: 497799835
