# .dotfiles

Personal dotfiles for macOS, managed with [GNU Stow](https://www.gnu.org/software/stow/).

| Component | Tool |
|-----------|------|
| Shell | Fish |
| Package manager | Homebrew |
| Dotfile manager | GNU Stow |
| Runtime manager | Mise |
| Terminal | Ghostty |
| Prompt | Starship |
| Editor | Zed |
| Window management | AeroSpace + Hammerspoon |
| Keyboard | Karabiner-Elements |

## Quick Start

```bash
git clone https://github.com/alechemy/dotfiles-fish.git ~/.dotfiles
cd ~/.dotfiles
./scripts/setup.sh
```

This installs Homebrew + all dependencies from `Brewfile`, builds generated configs (injecting secrets via 1Password CLI), symlinks all stow packages to `$HOME`, and sets Fish as the default shell.

## Structure

```
.dotfiles/
├── Brewfile                 # Homebrew dependencies
├── scripts/
│   ├── setup.sh             # Main bootstrap script
│   ├── build-zed-config.sh  # Inject 1Password secrets into Zed config
│   ├── setup-vscode.sh      # VSCodium settings + extensions
│   └── macos.sh             # macOS system defaults
├── stow/                    # Stow packages (auto-linked by setup.sh)
│   ├── aerospace/           # Tiling window manager
│   ├── bin/                 # ~/.local/bin scripts
│   ├── borders/             # JankyBorders window borders
│   ├── devonthink/          # DEVONthink automation
│   ├── editorconfig/        # ~/.editorconfig
│   ├── espanso/             # Text expansion
│   ├── fish/                # Fish shell config, functions, plugins
│   ├── ghostty/             # Terminal emulator
│   ├── git/                 # Git config + global gitignore
│   ├── hammerspoon/         # Hotkeys + automation
│   ├── karabiner/           # Keyboard remapping
│   ├── mise/                # Runtime version manager
│   ├── navidrome/           # Music server scripts
│   ├── sketchybar/          # Menu bar
│   ├── starship/            # Shell prompt theme
│   ├── streamrip/           # Music downloader config
│   ├── vscode/              # VSCodium settings
│   └── zed/                 # Zed editor (uses 1Password op inject)
└── stow-work/               # Opt-in work config (not auto-linked)
    └── work/                # Work-specific fish abbreviations
```

Each directory under `stow/` mirrors the path relative to `$HOME`. Stow creates symlinks from `$HOME` back into this repo. Editing stowed files requires no action since symlinks already point here -- only restow when adding or removing files.

## Secrets

Configs with secrets use the 1Password CLI (`op inject`) pattern: a tracked `.template.json` with `op://` references is injected into the real config (gitignored) at setup time. Currently only `stow/zed/` uses this. See `scripts/build-zed-config.sh`.

## Work Config

Work-specific config in `stow-work/` is not auto-linked. Opt in on a work machine:

```bash
cd ~/.dotfiles/stow-work
stow --restow --ignore='.DS_Store' --target="$HOME" work
```
