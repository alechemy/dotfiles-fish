# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal dotfiles managed with GNU Stow on macOS. All packages under `stow/` mirror the `$HOME` directory structure and are auto-linked by `setup.sh`. `stow-work/` holds work-specific config: gitignored apart from `.gitkeep`, so a fresh `git clone` leaves it empty and `setup.sh` skips it. After a file-copy from another machine the package has content and `setup.sh` auto-stows it (see step 4a in `scripts/setup.sh`).

**Key tools:** Fish shell, Homebrew, Mise (runtime versions), Starship (prompt), Ghostty (terminal), Zed (editor).

## Hardware setup

This dotfiles setup runs on a MacBook (with notch) used in two mutually exclusive modes. In portable mode the lid is open and the laptop is standalone. In docked mode the lid is closed (clamshell) and an ultrawide external monitor is the only active display. The user never runs with both displays active at once.

Two implications when designing or evaluating features in this repo:

1. **Single active display.** Even though two physical displays exist, only one is in use at any given moment. Workflows that depend on switching focus between monitors, mirroring across displays, or coordinating UI state across multiple active screens do not apply here and should not be proposed.

2. **Battery awareness on portable.** Docked mode is reliably on AC; portable mode is usually on battery. Features that poll on a timer, hit the network repeatedly, or otherwise wake the CPU should either degrade gracefully when on battery (longer intervals, deferred work) or skip entirely until the machine is plugged in. Apply this thinking both when adding new functionality and when reviewing existing code that may not have considered it.

   The canonical gate is `~/.local/bin/should-run-background-job` (source: `stow/bin/.local/bin/should-run-background-job`). It exits 0 on AC, non-zero on battery or UPS power, and accepts `--urgent` for user-invoked or deadline-bound work. The expected call patterns:
   - Bash entry script (launchd-driven): `"$HOME/.local/bin/should-run-background-job" || exit 0` — exit 0 from the caller so launchd doesn't treat the skip as a failure.
   - Python entry script: run as a subprocess, return early on non-zero. Always honor explicit user-invocation flags (`--force`, `--backfill`, `--dry-run`) as urgency overrides so the gate never blocks a manual run.
   - SketchyBar plugin or similar always-on consumer: branch to a cheap last-known-state path on skip rather than exiting with no UI update.

   `pipeline-record-run` (the missed-run tracker) should fire _before_ the gate so routine battery skips don't register as missed launchd ticks. Apple-signed `pmset` is the underlying detection mechanism; no TCC implications.

## Common Commands

Bootstrap a fresh machine:

```bash
./scripts/setup.sh
```

Restow a single package after adding/removing files:

```bash
cd ~/.dotfiles/stow
stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" <package>
```

Unstow (remove symlinks for) a package:

```bash
cd ~/.dotfiles/stow
stow --delete --target="$HOME" <package>
```

Opt into work config:

```bash
cd ~/.dotfiles/stow-work
stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" work
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
3. A `.stow-local-ignore` in the package root excludes the template from stowing. Stow anchors each ignore regex to the whole path segment, so the pattern must match the entire filename: use `.*\.template` (or `.*\.template\.json`), **not** `\.template$` — the latter only matches a file named exactly `.template` and silently lets `foo.plist.template` through.
4. The build script is called from `setup.sh` before stowing.

Current consumers:

- `stow/zed/` — op inject + `${HOME}` (`scripts/build-zed-config.sh`)
- `stow/streamrip/` — op inject + `${HOME}` (`scripts/build-streamrip-config.sh`)
- `stow/vscode/` — `${HOME}` only (`scripts/build-vscode-config.sh`)

A separate `__HOME__` expansion pattern exists for launch-agent plist templates under `stow/*/Library/LaunchAgents/*.plist.template`, handled by `scripts/build-launchd-plists.sh`.

### Seeded config (copy-if-absent, not stowed)

Some app config is portable and worth versioning but is a binary plist the app **rewrites at runtime** — stowing it via symlink is fragile, because an atomic-rename save replaces the symlink with a real file and silently de-stows it. For these, the repo keeps a tracked seed copy and a script copies it into place **only when the target is absent** (so a live, app-mutated file is never clobbered).

The pattern:

1. Seed files live under `stow/<pkg>/_seed/` mirroring their `$HOME`-relative path (e.g. `stow/devonthink/_seed/Library/Application Support/DEVONthink/SmartRules.plist`).
2. The package's `.stow-local-ignore` lists `_seed` so the directory is never symlinked.
3. A `scripts/seed-<pkg>-config.sh` walks `_seed/` and `cp`s each file to `$HOME` if the destination does not already exist. It is idempotent and safe to run with the app open.
4. `setup.sh` calls the seed script after stowing the package.

Only genuinely portable, user-authored config belongs in a seed. Do **not** seed app-shipped defaults (DEVONthink repopulates its built-in AI templates and Smart Rules example `.scpt`s from the app bundle on launch) or machine-specific state (window geometry, the preferences plist, licenses) — verify against the app bundle before adding a file.

Current consumer: `stow/devonthink/_seed/` — DEVONthink smart rules, smart groups, custom metadata, and batch-processing presets (`scripts/seed-devonthink-config.sh`). DEVONthink AI keys live in the macOS Keychain, not these plists, so they are never captured here.

### Adding a New Package

1. Create `stow/<toolname>/` mirroring the `$HOME` path (e.g. `stow/lazygit/.config/lazygit/`)
2. Place the config file inside
3. Restow: `cd stow && stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" <toolname>`
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

### Launch agents and TCC-protected folders

The same Apple-signed-vs-rotating-identity split that governs AppleEvents also governs the per-folder TCC protections on `~/Downloads`, `~/Desktop`, and `~/Documents`. When a launch agent reads or writes a file in one of those folders, macOS checks the accessing binary's signature. Apple-signed binaries (`/usr/bin/python3`, `/bin/mv`, `/bin/mkdir`, `osascript`) are not blocked in this context; non-Apple-signed helpers (Homebrew/`mise`/`uv`-managed tools — `node`/`defuddle`, `magick`, `markdownlint`, etc.) trigger a one-time "X wants to access files in your Downloads folder" prompt. Because launch agents run headless, a fumbled or dismissed keystroke on that prompt writes a persistent *deny* rule, after which the helper's `open()` returns `EPERM` on every run — silently, since the surrounding Apple-signed script keeps working. (`fswatch` is exempt: FSEvents *monitoring* is a different code path than file `open()` and does not trip the per-folder check.)

The rule: **a non-Apple-signed helper invoked under a launch agent must never open a file directly inside a TCC-protected folder.** Stage the file into the per-user temp dir first (`tempfile.TemporaryDirectory()` / `mktemp`, which lives under `$TMPDIR` → `/var/folders/…`, not protected) and point the helper at the copy. Do the copy itself with an Apple-signed binary. `ingest-singlefile-html.py` is the reference: it copies the staging HTML out of `~/Downloads/SingleFile/` into `tmpdir` before handing it to `defuddle`. This also makes the pipeline robust to the helper's path/signature rotating on upgrade — there is no folder grant to lose.

This is not enforced by a linter; it is a design rule to apply whenever a new pipeline reads from or writes to Downloads/Desktop/Documents under launchd.

### Python script shebangs

Python interpreter management is split between mise and uv on purpose. mise (`stow/mise/.config/mise/config.toml`) provides the day-to-day `python3` on `$PATH`. uv (Brewfile) is reserved for scripts that declare third-party deps via PEP 723. There is no repo-wide `pyproject.toml` / `uv.lock` — each script stands alone.

Pick a script's shebang from this three-tier rule:

1. **TCC-sensitive** (script sends AppleEvents AND is invoked by a launch agent, either directly via the plist or transitively through a launchd-driven shell script that calls `"$SCRIPT" args`) → `#!/usr/bin/python3`. Apple-signed, stable TCC identity, stdlib only. If the work needs third-party deps, use the split-architecture pattern from the section above (sender stays `/usr/bin/python3`, parser is a `uv run --script` subprocess).
2. **Has third-party deps, not TCC-sensitive** → `#!/usr/bin/env -S uv run --script` with a PEP 723 inline `# /// script` block declaring `requires-python` and `dependencies`. Reference: `stow/devonthink/.local/bin/import-granola-parse.py`, `stow/bin/.local/bin/tagger.py`.
3. **Pure stdlib, not TCC-sensitive** → `#!/usr/bin/env python3`. Resolves through PATH to mise's Python.

For tier 1 scripts, even when the launchd plist provides the interpreter explicitly (`/usr/bin/python3 /path/to/script.py`), still write the shebang as `#!/usr/bin/python3` so direct invocation during testing uses the same interpreter as production rather than mise's.

### Audio tagging with mutagen

When writing MP4/m4a boolean atoms (`cpil`, `pgap`) with mutagen, assign a **bare bool** — `audio["cpil"] = True` — never a list. mutagen renders a list by truthiness, so `audio["cpil"] = [False]` silently writes `True`. `tagger.py` sets the compilation flag this way.

## External design notes (gitignored, outside the repo)

Some pipelines have design docs kept outside the public repo because they document sensitive recipes (e.g. local-store decryption). **Read the relevant file before modifying its pipeline** — the docs cover schema, breakage modes, and debug recipes that aren't reconstructable from the code alone.

- `~/.local/share/granola-import/NOTES.md` — Granola → DEVONthink import: encryption key chain, SQLCipher schema, ProseMirror conversion, debug recipes, brittle points. Read before touching `stow/devonthink/.local/bin/import-granola*.py` or `stow/devonthink/Library/LaunchAgents/com.user.granola-import.plist*`.

When adding a new pipeline that needs gitignored design notes, append an entry here so future sessions discover it without having to be pointed.
