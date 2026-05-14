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

### Generated configs (template → build → stow)

Some package configs are generated at install time from a tracked template. The pattern:
1. `config.template.{json,toml}` (tracked) — full config with placeholders
2. A build script in `scripts/` produces the real config (gitignored). Two flavors:
   - **`op inject`**: resolves `op://Vault/Item/Field` references via 1Password CLI. Requires an authenticated `op` session; build scripts fail loudly if the output still contains `op://`.
   - **`${HOME}` expansion**: pure sed substitution. Used where the target tool needs absolute paths and doesn't honor its own variable substitution.
3. A `.stow-local-ignore` in the package root excludes `*.template.*` from stowing.
4. The build script is called from `setup.sh` before stowing.

Current consumers:
- `stow/zed/` — op inject + `${HOME}` (`scripts/build-zed-config.sh`)
- `stow/streamrip/` — op inject + `${HOME}` (`scripts/build-streamrip-config.sh`)
- `stow/vscode/` — `${HOME}` only (`scripts/build-vscode-config.sh`)

A separate `__HOME__` expansion pattern exists for launch-agent plist templates under `stow/*/Library/LaunchAgents/*.plist.template`, handled by `scripts/build-launchd-plists.sh`.

### Adding a New Package

1. Create `stow/<toolname>/` mirroring the `$HOME` path (e.g. `stow/lazygit/.config/lazygit/`)
2. Place the config file inside
3. Restow: `cd stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" <toolname>`
4. If installed via Homebrew, add to `Brewfile`
5. If the tool writes new files to its config dir at runtime, you need `--no-folding` (already the default in setup.sh)

Restow is only needed when **adding or removing files** within a package — editing existing stowed files requires no action since symlinks already point here.

### Launch Agents and AppleEvents

When a launch agent invokes a script that sends AppleEvents to a TCC-protected app like DEVONthink, macOS attributes the event to the calling binary's code signature. Adhoc-signed binaries at versioned paths (mise's Python, Homebrew's Python) get a fresh TCC identity on every upgrade, which invalidates the prior Automation grant. The same applies to interpreters launched by `uv run`. The system then re-prompts "X wants to control data in other apps," and because launch agents run headless, the prompt blocks the pipeline silently when the user is AFK.

Two rules keep this stable:

1. The plist's `ProgramArguments[0]` must be an Apple-signed binary at a path that never rotates: `/usr/bin/python3`, `/bin/bash`, `/bin/sh`, or `/usr/bin/osascript`. `/usr/bin/env` is also Apple-signed but is excluded because it resolves through launchd's PATH and would let mise's shimmed Python win.
2. Sub-scripts that the entry script invokes via shebang resolution (e.g. `"$VAR" arg` in a bash script, where `$VAR` holds a script path) must themselves use an explicit interpreter shebang from the same allowlist. Avoid `#!/usr/bin/env python3` for these, since `env` resolves through PATH again and reintroduces the same failure mode.

When the work needs Python ≥ 3.10 or third-party packages, use the split-architecture pattern. The entry script runs under `/usr/bin/python3` (stdlib only) and owns every `osascript` invocation; a separate parser script with `#!/usr/bin/env -S uv run --script` is invoked via `subprocess.run([parser_path], ...)` and exchanges JSON over stdin/stdout for the heavy work. The parser never sends AppleEvents. Reference: `stow/devonthink/.local/bin/import-granola.py` (sender) plus `import-granola-parse.py` (parser).

`scripts/lint-launchd-plists.sh` enforces both rules across every plist template in the repo and runs as part of `setup.sh`. It will halt the bootstrap on any violation.

## External design notes (gitignored, outside the repo)

Some pipelines have design docs kept outside the public repo because they document sensitive recipes (e.g. local-store decryption). **Read the relevant file before modifying its pipeline** — the docs cover schema, breakage modes, and debug recipes that aren't reconstructable from the code alone.

- `~/.local/share/granola-import/NOTES.md` — Granola → DEVONthink import: encryption key chain, SQLCipher schema, ProseMirror conversion, debug recipes, brittle points. Read before touching `stow/devonthink/.local/bin/import-granola*.py` or `stow/devonthink/Library/LaunchAgents/com.user.granola-import.plist*`.

When adding a new pipeline that needs gitignored design notes, append an entry here so future sessions discover it without having to be pointed.
