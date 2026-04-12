# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal dotfiles managed with GNU Stow on macOS. All packages under `stow/` mirror the `$HOME` directory structure and are auto-linked by `setup.sh`. `stow-work/` holds opt-in work-specific config.

**Key tools:** Fish shell, Homebrew, Mise (runtime versions), Starship (prompt), Ghostty (terminal), Zed (editor).

## Common Commands

Bootstrap a fresh machine:
```bash
./scripts/setup.sh
```

Restow a single package after adding/removing files:
```bash
cd ~/.dotfiles/stow
stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" <package>
```

Unstow (remove symlinks for) a package:
```bash
cd ~/.dotfiles/stow
stow --delete --target="$HOME" <package>
```

Opt into work config:
```bash
cd ~/.dotfiles/stow-work
stow --restow --ignore='.DS_Store' --target="$HOME" work
```

Rebuild Zed config (injects 1Password secrets):
```bash
./scripts/build-zed-config.sh
```

Capture currently installed Homebrew packages (to a temp file to preserve Brewfile sections):
```bash
brew bundle dump --file=/tmp/Brewfile --force
# Then manually copy needed lines into ~/.dotfiles/Brewfile
```

## Architecture

### Stow Package Layout

Each directory under `stow/` must mirror the path relative to `$HOME`. For example, a file that should live at `~/.config/ghostty/config` goes at `stow/ghostty/.config/ghostty/config`. Stow creates symlinks from `$HOME` back into this repo.

`setup.sh` runs `stow --restow --no-folding` for every directory in `stow/` automatically. The `--no-folding` flag prevents Stow from symlinking entire directories (it creates individual file symlinks instead), which avoids conflicts with tools that write new files into their config directories.

### Secrets Handling (1Password CLI)

For configs containing secrets, the pattern is:
1. `config.template.json` (tracked) — full config with `op://Vault/Item/Field` references
2. A build script runs `op inject -f -i config.template.json -o config.json` to produce the real config (gitignored)
3. A `.stow-local-ignore` in the package root excludes `*.template.*` files from stowing
4. The build script is called from `setup.sh` before stowing

Currently only `stow/zed/` uses this pattern. See `scripts/build-zed-config.sh` as the reference implementation.

### Adding a New Package

1. Create `stow/<toolname>/` mirroring the `$HOME` path (e.g. `stow/lazygit/.config/lazygit/`)
2. Place the config file inside
3. Restow: `cd stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" <toolname>`
4. If installed via Homebrew, add to `Brewfile`
5. If the tool writes new files to its config dir at runtime, you need `--no-folding` (already the default in setup.sh)

Restow is only needed when **adding or removing files** within a package — editing existing stowed files requires no action since symlinks already point here.
