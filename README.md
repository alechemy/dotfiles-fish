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
| Window management | AeroSpace |
| Keyboard | Karabiner-Elements |

## Quick Start

For a fresh Mac, follow [`MIGRATION.md`](MIGRATION.md) instead of the snippet below. The build scripts require an authenticated 1Password CLI session, DEVONthink installed, and a few other prerequisites that `setup.sh` does not bootstrap on its own.

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
│   ├── setup.sh                  # Main bootstrap script
│   ├── build-zed-config.sh       # Inject 1Password secrets into Zed config
│   ├── build-streamrip-config.sh # Inject 1Password secrets into streamrip config
│   ├── build-vscode-config.sh    # Expand ${HOME} in VSCodium settings.json
│   ├── build-launchd-plists.sh   # Expand __HOME__ in launch-agent plist templates
│   ├── lint-launchd-plists.sh    # Enforce TCC-stable interpreters in plist templates
│   ├── setup-vscode.sh           # VSCodium extension install
│   └── macos.sh                  # macOS system defaults
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
│   ├── karabiner/           # Keyboard remapping
│   ├── mise/                # Runtime version manager
│   ├── nas-mount/           # Auto-mount NAS SMB shares (launch agent)
│   ├── navidrome/           # Music server scripts
│   ├── sketchybar/          # Menu bar
│   ├── starship/            # Shell prompt theme
│   ├── streamrip/           # Music downloader config (1Password op inject)
│   ├── vscode/              # VSCodium settings (${HOME} expansion)
│   └── zed/                 # Zed editor (1Password op inject)
└── stow-work/               # Opt-in work config (not auto-linked)
    └── work/                # Work-specific fish abbreviations
```

Each directory under `stow/` mirrors the path relative to `$HOME`. Stow creates symlinks from `$HOME` back into this repo. Editing stowed files requires no action since symlinks already point here -- only restow when adding or removing files.

## Runtimes (mise)

Language runtimes and globally-installed CLIs are declared in `stow/mise/.config/mise/config.toml`. Running `mise install` on a fresh machine reproduces everything listed there.

```bash
mise use -g node@lts             # add/pin a runtime globally
mise use -g npm:defuddle         # add a global npm CLI (survives node upgrades)
mise up                          # update all tools to latest matching versions
mise up npm:defuddle             # update a single tool
mise ls                          # list everything mise manages
mise uninstall <tool>            # remove from disk
mise unuse -g <tool>             # remove the declaration from config.toml
```

`mise use`/`mise unuse` edit `config.toml` in place, so the dotfiles repo stays in sync. Prefer the `npm:<pkg>` backend over raw `npm install -g` so globals get reinstalled against whichever node version is active.

## Generated configs (template → build → stow)

Some configs are generated from a tracked template at install time. Two flavors:

- **1Password secret injection** (`stow/zed/`, `stow/streamrip/`): the template has `op://Vault/Item/Field` references which `op inject` resolves into the real config (gitignored). Both also do `${HOME}` expansion.
- **Path expansion only** (`stow/vscode/`): the template has `${HOME}` placeholders that get expanded into absolute paths. Used where the target tool requires absolute paths and doesn't honor its own variable substitution (e.g., `be5invis.vscode-custom-css` reads `file://` URIs literally).

Each follows the same pattern: tracked `*.template.{json,toml}`, generated output gitignored, build script run before stow, and a `.stow-local-ignore` entry that excludes the template from being symlinked. See `scripts/build-zed-config.sh` as the canonical example.

## Work Config

Work-specific config in `stow-work/` is not auto-linked. Opt in on a work machine:

```bash
cd ~/.dotfiles/stow-work
stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" work
```
