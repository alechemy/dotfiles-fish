# .dotfiles

My personal dotfiles, managed with [GNU Stow](https://www.gnu.org/software/stow/), Fish shell, and [Mise](https://mise.jdx.dev/).

## Quick Start

```bash
git clone https://github.com/alechemy/dotfiles-fish.git ~/.dotfiles
cd ~/.dotfiles
./scripts/setup.sh
```

`setup.sh` will:

1. Install Homebrew (if needed)
2. Install all dependencies from `Brewfile`
3. Build generated configs (e.g. merge secret overlays into Zed settings)
4. Symlink all stow packages in `stow/` to `$HOME`
5. Optionally apply macOS defaults (`scripts/macos.sh`)
6. Set Fish as the default shell

## Structure

```
.dotfiles/
├── Brewfile              # Homebrew dependencies (CLI tools + casks)
├── scripts/
│   ├── setup.sh          # Main bootstrap script
│   ├── build-zed-config.sh # Merge Zed base config + secrets
│   ├── setup-vscode.sh   # VSCodium settings + extensions
│   └── macos.sh          # macOS system defaults
├── stow/                 # Stow packages (auto-linked by setup.sh)
│   ├── bin/              # ~/.local/bin scripts
│   ├── editorconfig/     # ~/.editorconfig
│   ├── fish/             # Fish shell config, functions, plugins
│   ├── git/              # Git config (.gitconfig, .gitignore)
│   ├── hammerspoon/      # Window management + hotkeys
│   ├── karabiner/        # Keyboard remapping
│   ├── keychain/         # Keychain helper scripts
│   ├── ghostty/          # Terminal emulator config
│   ├── mise/             # Runtime version manager (node, python, java)
│   ├── starship/         # Shell prompt theme
│   ├── swiftbar/         # Menu bar plugins
│   └── zed/              # Zed editor (uses base+secrets merge)
├── stow-work/            # Opt-in stow packages (not auto-linked)
│   └── work/             # Work-specific fish abbreviations
└── vscode/               # VSCodium settings (linked via setup-vscode.sh)
```

## Stow Packages

Each directory under `stow/` mirrors the target structure relative to `$HOME`. Running `stow --target="$HOME" fish` from `stow/` symlinks everything inside `stow/fish/` into your home directory.

All packages in `stow/` are linked automatically by `setup.sh`. To restow a single package after making changes:

```bash
cd ~/.dotfiles/stow
stow --restow --target="$HOME" fish
```

## Work Config

Work-specific config lives in `stow-work/` and is **not** auto-linked. On a work machine, opt in with:

```bash
cd ~/.dotfiles/stow-work
stow --restow --ignore='.DS_Store' --target="$HOME" work
```

## Manual Steps

### VSCodium

Link settings and install extensions:

```bash
./scripts/setup-vscode.sh
```

### macOS Defaults

Applied automatically during setup (with a prompt), or run manually:

```bash
./scripts/macos.sh
```

### Navidrome Scripts

The `play-random-album` and SwiftBar Navidrome plugin pull credentials from macOS Keychain. Add your password once:

```bash
security add-generic-password -s 'Navidrome' -a 'alec' -w
```

## Adding a New Package

When you find a new tool whose config you want to track:

**1. Figure out where the tool stores its config.**

Most CLI tools use `~/.config/<tool>/` (XDG) or a dotfile in `$HOME` directly (e.g. `~/.editorconfig`). Check the tool's docs or look for an existing config file.

**2. Create a stow package that mirrors the home directory path.**

The directory structure inside your package must match the path relative to `$HOME`:

```bash
# Example: tool uses ~/.config/lazygit/config.yml
mkdir -p stow/lazygit/.config/lazygit

# Example: tool uses ~/.somethingrc
mkdir -p stow/something
```

**3. Move (or create) the config file into the package.**

```bash
# Move existing config into the package
mv ~/.config/lazygit/config.yml stow/lazygit/.config/lazygit/config.yml

# Or create it fresh
vim stow/lazygit/.config/lazygit/config.yml
```

**4. Stow the package to create the symlink.**

```bash
cd ~/.dotfiles/stow
stow --restow --target="$HOME" lazygit
```

Verify it worked:

```bash
ls -la ~/.config/lazygit/config.yml
# Should show: ... -> ../../.dotfiles/stow/lazygit/.config/lazygit/config.yml
```

**5. If the tool was installed via Homebrew, add it to the `Brewfile`.**

```bash
echo 'brew "lazygit"' >> ~/.dotfiles/Brewfile
```

That's it. The next time `setup.sh` runs on a fresh machine, it will install the tool from the Brewfile and symlink the config automatically (since `setup.sh` stows every directory in `stow/`).

### Tips

- **Naming**: name the package directory after the tool (e.g. `stow/lazygit/`, `stow/ghostty/`).
- **Work-only config**: put it in `stow-work/` instead of `stow/` so it won't be auto-linked on personal machines.
- **Restow after changes**: if you edit files that are already stowed, the symlinks still point to the right place — no restow needed. Only restow if you add or remove files within a package.
- **Conflicts**: if stow complains about an existing file, back it up and remove it first, then restow.

## Removing a Package

When you stop using a tool and want to clean up:

**1. Unstow the package to remove the symlinks.**

```bash
cd ~/.dotfiles/stow
stow --delete --target="$HOME" lazygit
```

Verify the symlinks are gone:

```bash
ls -la ~/.config/lazygit/config.yml
# Should show "No such file or directory"
```

**2. Delete the package directory.**

```bash
rm -rf ~/.dotfiles/stow/lazygit
```

**3. If the tool is in the `Brewfile`, remove it.**

Open `~/.dotfiles/Brewfile` and delete the relevant line (e.g. `brew "lazygit"`).

**4. If the package had secrets handling, clean up the extras.**

- Remove the build script (e.g. `scripts/build-lazygit-config.sh`)
- Remove the build script call from `setup.sh`
- Remove the gitignore entries for the generated config and secrets file

## Updating Dependencies

To capture new Homebrew packages you've installed since the last update:

```bash
brew bundle dump --file=~/.dotfiles/Brewfile --force
```

This overwrites the Brewfile with your current installed formulae and casks. Review the diff before committing — `brew bundle dump` includes everything Homebrew knows about, so you may want to remove one-off tools you don't need on every machine.

To uninstall packages that are no longer in the Brewfile:

```bash
brew bundle cleanup --file=~/.dotfiles/Brewfile
```

This does a dry run by default — it lists what would be removed. Add `--force` to actually uninstall them.

## Handling Secrets in Config Files

Some tools store API keys or credentials in their main config file (e.g. Zed's `settings.json`). Since these files are stowed and tracked in git, we use a **base + secrets merge** pattern to keep secrets out of version control.

### How it works

Instead of tracking the config file directly:

1. **`config.base.json`** (tracked) — the full config with secret values left empty
2. **`config.secrets.json`** (gitignored) — a small overlay containing only the secret keys
3. **A build script** merges them with `jq` deep merge into the final config file (also gitignored)
4. **Stow** symlinks only the merged output, not the base or secrets files

The build scripts run automatically during `setup.sh` (before stowing), so on a fresh machine you just need to create the secrets file and run setup.

### Existing: Zed (`stow/zed/`)

```
stow/zed/.config/zed/
├── settings.base.json      # Tracked — full config, empty MCP settings
├── settings.secrets.json   # Gitignored — API key overlay
├── settings.json           # Gitignored — generated by build script
└── keymap.json             # Tracked — no secrets, stowed normally
```

On a fresh machine, create the secrets file:

```bash
cat > stow/zed/.config/zed/settings.secrets.json << 'EOF'
{
  "context_servers": {
    "mcp-server-context7": {
      "settings": {
        "context7_api_key": "your-key-here"
      }
    }
  }
}
EOF
```

Then run `./scripts/setup.sh` (or just `./scripts/build-zed-config.sh` + restow).

### Adding secrets handling to another app

**1. Rename the tracked config to a `.base` variant.**

```bash
mv stow/myapp/.config/myapp/config.json stow/myapp/.config/myapp/config.base.json
```

Remove the secret values from the base file (leave the keys with empty/placeholder values so the structure is valid).

**2. Create a secrets overlay file** with just the secret keys:

```bash
cat > stow/myapp/.config/myapp/config.secrets.json << 'EOF'
{
  "some_nested": {
    "api_key": "your-secret"
  }
}
EOF
```

**3. Gitignore both the generated config and the secrets file.**

```gitignore
stow/myapp/.config/myapp/config.json
stow/myapp/.config/myapp/config.secrets.json
```

**4. Create a `.stow-local-ignore`** in the package root (`stow/myapp/.stow-local-ignore`) so stow skips the source files:

```
.*\.base\.json
.*\.secrets\.json
```

**5. Create a build script** (see `scripts/build-zed-config.sh` as a template). The core logic is:

```bash
# Strip JSONC comments, then deep merge base + secrets
sed 's|//.*$||' "$BASE" | jq -s '.[0] * .[1]' - "$SECRETS" > "$OUT"
```

If your config is plain JSON (no comments), you can skip the `sed` and use `jq -s '.[0] * .[1]' "$BASE" "$SECRETS"` directly.

**6. Add the build script call to `setup.sh`** (in the "Build generated configs" section, before stowing).

## Restoring on a Fresh Machine

**1. Clone and run setup.**

```bash
git clone https://github.com/alechemy/dotfiles-fish.git ~/.dotfiles
cd ~/.dotfiles
./scripts/setup.sh
```

This handles Homebrew, stow packages, Fish shell, mise runtimes, and VSCodium automatically.

**2. Create secrets files before or after setup.**

Any package that uses the base + secrets merge pattern needs its secrets file created manually (these are gitignored and won't be in the clone). Currently this is just Zed:

```bash
cat > ~/.dotfiles/stow/zed/.config/zed/settings.secrets.json << 'EOF'
{
  "context_servers": {
    "mcp-server-context7": {
      "settings": {
        "context7_api_key": "your-key-here"
      }
    }
  }
}
EOF
```

If you create the secrets file after running setup, rebuild and restow:

```bash
./scripts/build-zed-config.sh
cd ~/.dotfiles/stow && stow --restow --target="$HOME" zed
```

**3. Opt into work config (work machines only).**

```bash
cd ~/.dotfiles/stow-work
stow --restow --ignore='.DS_Store' --target="$HOME" work
```

**4. Add Navidrome credentials to Keychain** (if using Navidrome scripts).

```bash
security add-generic-password -s 'Navidrome' -a 'alec' -w
```

## Overview

| Component | Tool |
|-----------|------|
| OS | macOS |
| Shell | Fish |
| Package manager | Homebrew |
| Dotfile manager | GNU Stow |
| Runtime manager | Mise |
| Terminal | Ghostty |
| Prompt | Starship |
| Editor | VSCodium |
