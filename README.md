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

This installs Homebrew + all dependencies from `Brewfile`, builds generated configs (injecting secrets via 1Password CLI), symlinks the stow packages to `$HOME` (DEVONthink and streamrip are opt-in prompts), and sets Fish as the default shell.

## Structure

```
.dotfiles/
├── Brewfile                 # Homebrew dependencies
├── homebrew/                # Local-only tap (casks with no upstream cask)
├── devonthink/              # DEVONthink pipeline docs
├── drafts/                  # Drafts action scripts (repo is canonical)
├── keyboard-maestro/        # Scripts referenced by KM macros
├── hrm/                     # Glove80 keyboard layout + README
├── firmware/                # Keyboard/device firmware + layouts
│   ├── ploopy-knob/         # Ploopy Knob QMK keymap + build recipe
│   └── tailorkey/           # Glove80/Go60 TailorKey layouts
├── scripts/
│   ├── setup.sh                  # Main bootstrap script
│   ├── restow-changed.sh         # Auto-restow worker (git post-merge/-rewrite/-commit hooks)
│   ├── git-hooks/                # Tracked hooks wired via core.hooksPath
│   ├── build-zed-config.sh       # Inject 1Password secrets into Zed config
│   ├── build-streamrip-config.sh # Inject 1Password secrets into streamrip config
│   ├── build-context7-config.sh  # op read → fish conf.d Context7 key export
│   ├── build-things-config.sh    # op read → ~/.zshenv Things auth token
│   ├── build-vscode-config.sh    # Expand ${HOME} in VSCodium settings.json
│   ├── build-launchd-plists.sh   # Expand __HOME__ in launch-agent plist templates
│   ├── lint-launchd-plists.sh    # Enforce TCC-stable interpreters in launch agents
│   ├── seed-devonthink-config.sh # Copy-if-absent DEVONthink seed plists
│   ├── setup-vscode.sh           # VSCodium extension install
│   ├── aerospace-*.sh|py         # AeroSpace gap/window helpers
│   └── macos.sh                  # macOS system defaults
├── stow/                    # Stow packages (auto-linked by setup.sh; devonthink + streamrip opt-in)
│   ├── aerospace/           # Tiling window manager
│   ├── bin/                 # ~/.local/bin scripts
│   ├── borders/             # JankyBorders window borders
│   ├── chromium-bookmarks/  # Chromium → Safari bookmark bridge for Alfred
│   ├── claude/              # Claude Code global config, skills, hooks
│   ├── copilot/             # Copilot CLI config
│   ├── devonthink/          # DEVONthink automation (opt-in)
│   ├── dropzone/            # Dropzone action bundles
│   ├── editorconfig/        # ~/.editorconfig
│   ├── espanso/             # Text expansion
│   ├── fish/                # Fish shell config, functions, plugins
│   ├── ghostty/             # Terminal emulator
│   ├── git/                 # Git config + global excludes
│   ├── karabiner/           # Keyboard remapping (goku EDN source)
│   ├── linearmouse/         # Ploopy Knob scroll config (seeded, not stowed)
│   ├── mise/                # Runtime version manager
│   ├── nas-mount/           # Auto-mount NAS SMB shares (launch agent)
│   ├── navidrome/           # Navidrome client env
│   ├── sketchybar/          # Menu bar
│   ├── ssh/                 # SSH client config
│   ├── starship/            # Shell prompt theme
│   ├── streamrip/           # Music downloader config (opt-in, op inject)
│   ├── tmux/                # Phone-friendly tmux config
│   ├── vscode/              # VSCodium settings (${HOME} expansion)
│   └── zed/                 # Zed editor (1Password op inject)
├── stow-work/               # Work config (gitignored; auto-stowed when populated by file-copy)
└── stow-local/              # Machine-local config (gitignored)
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

Some configs are generated from a tracked template at install time. Three flavors:

- **1Password secret injection** (`stow/zed/`, `stow/streamrip/`): the template has `op://Vault/Item/Field` references which `op inject` resolves into the real config (gitignored). Both also do `${HOME}` expansion.
- **1Password single-value fetch** (`build-context7-config.sh` → fish conf.d Context7 key; `build-things-config.sh` → `~/.zshenv` Things token): `op read` fetches one secret and the script writes the output directly — no template file, used where a template sibling would be harmful (fish auto-sources everything in conf.d) or the output lives outside the stow tree.
- **Path expansion only** (`stow/vscode/`): the template has `${HOME}` placeholders that get expanded into absolute paths. Used where the target tool requires absolute paths and doesn't honor its own variable substitution (e.g., `be5invis.vscode-custom-css` reads `file://` URIs literally).

Each follows the same pattern: tracked `*.template.{json,toml}`, generated output gitignored, build script run before stow, and a `.stow-local-ignore` entry that excludes the template from being symlinked. See `scripts/build-zed-config.sh` as the canonical example.

## Work Config

`stow-work/` holds work-specific config (fish functions, sketchybar overlays, Copilot/Atlassian MCP setup, docs). It is gitignored apart from `.gitkeep`, so a fresh clone leaves it empty and nothing links. On a work machine, populate it by file-copy from another machine — `setup.sh` auto-stows it once it has content. To link without a full setup re-run:

```bash
cd ~/.dotfiles/stow-work
stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" work
```
