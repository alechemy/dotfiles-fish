# Migration Checklist

Everything that `setup.sh` *can't* do for you on a new Mac. Work through it in order — some steps depend on earlier ones.

## 1. Before cloning the repo

- [ ] **Sign in to Apple ID** in System Settings (so `mas` can install App Store apps later).
- [ ] **Install 1Password** manually from 1password.com and sign in to your account. Wait until you see your vault before continuing.
- [ ] In **1Password → Settings → Developer**, enable:
  - **Use the SSH agent** (replaces `~/.ssh/id_*` for git/ssh)
  - **Connect with 1Password CLI** + **Biometric unlock for 1Password CLI**
  - (Optional) **Integrate with 1Password CLI** in your terminal
- [ ] Open a terminal and verify: `op whoami` should print your account. If it doesn't, the `build-{zed,streamrip}-config.sh` scripts will fail.

## 2. Bootstrap

- [ ] Clone the dotfiles (or copy the folder from the old machine):
  ```bash
  git clone <repo-url> ~/.dotfiles
  cd ~/.dotfiles
  ./scripts/setup.sh
  ```
- [ ] When prompted, accept the **DEVONthink pipeline** install if this machine should run the launchd agents (daily note, watchdog, etc.). Skip on a laptop you don't want noisy background jobs on.
- [ ] When prompted, accept **macOS defaults** to apply `scripts/macos.sh`.

If `setup.sh` halts early, fix the reported issue and re-run — it's idempotent.

## 3. Manual app installs (not in Brewfile)

- [ ] **DEVONthink 4** — paid download from devontechnologies.com. Open the app once and let it create its database before the launchd agents fire.
- [ ] **Operator Mono SSm Lig** — paid font from typography.com. Used by Ghostty and Zed. Drop the `.otf` files into `~/Library/Fonts/`. Without this, both apps fall back to a system monospace.
- [ ] **SingleFile browser extension** — install in Chromium (`ungoogled-chromium`, installed via Brewfile):
  - From the Chrome Web Store (or load unpacked from the SingleFile repo).
  - In the extension's settings, set the auto-save directory to `~/Downloads/SingleFile/` (the DT watcher reads from there — note the casing, it's what `stow/devonthink/.local/bin/singlefile-watcher.sh` watches).
  - Pin the toolbar icon if you want one-click capture.
- [ ] Any paid Setapp / direct-download apps not listed in `Brewfile` that you actively use.

## 4. Local repos to clone

- [ ] **`~/Developer/claude-agent-acp`** — the Zed ACP bridge referenced from `stow/zed/.config/zed/settings.template.jsonc`. Clone it to the same path and run its build (`npm install && npm run build` or whatever the repo's README says) so `dist/index.js` exists. Without this, the "Claude Code by Rohan Patra" agent entry in Zed won't function.
- [ ] Any other `~/Developer/*` repos you actively work in.

## 5. Bring over from the old machine

These live outside the dotfiles repo. Copy via Time Machine, AirDrop, or `scp`.

- [ ] `~/.local/share/granola-import/` — design notes + any cached state for the Granola pipeline. Required if you enabled the DEVONthink pipeline in step 2 and want Granola import to work.
- [ ] `~/.gnupg/` — only if you sign commits with GPG. If you're using 1Password SSH agent + git's commit signing via SSH, skip this.
- [ ] `~/.config/op/` — 1Password CLI local state. Optional; 1Password rebuilds on first auth.
- [ ] **Hazel rules**, **Keyboard Maestro macros**, **Alfred workflows**, **Espanso matches**, **Drafts actions** — none of these tools store their state in `~/.config`. Export from the old machine and import on the new one. See each app's "backup/sync" feature.
- [ ] **Karabiner-Elements** — `~/.config/karabiner/` *is* in the dotfiles (`stow/karabiner/`), so it comes along automatically. Just open the app once on the new machine and grant Input Monitoring.

## 6. Post-install authentication

- [ ] `gh auth login` — GitHub CLI.
- [ ] `tailscale up` (or use the menu bar) — sign in to your tailnet.
- [ ] **Maestral** (Dropbox client) — first launch will prompt for OAuth.
- [ ] **Granola** — sign in to your account.
- [ ] **Marked 2**, **CleanShot X**, **Things**, etc. — first-launch logins where applicable.
- [ ] If you commit-sign via SSH (recommended given the 1Password agent): `git config --global user.signingkey "<your ssh pubkey>"` and `git config --global commit.gpgsign true` (the global gitconfig in `stow/git/` may already handle this — check).

## 7. macOS permission grants (TCC)

macOS will prompt the first time each app tries to do something privileged. Pre-empting these saves friction:

**Accessibility** (System Settings → Privacy & Security → Accessibility):
- [ ] AeroSpace
- [ ] Hammerspoon
- [ ] Keyboard Maestro
- [ ] Alfred
- [ ] Hazel
- [ ] Espanso

**Input Monitoring**:
- [ ] Karabiner-Elements (also needs its kernel extension approved at first launch)

**Automation** (System Settings → Privacy & Security → Automation) — needed for the DEVONthink launchd agents. macOS will prompt on first fire, but the agents run headless and the prompts block silently:
- [ ] `/usr/bin/python3` → DEVONthink 4 (the entry script for several pipelines runs under stdlib python)
- [ ] `/bin/bash` → DEVONthink 4
- [ ] `/usr/bin/osascript` → DEVONthink 4
- [ ] Granola → DEVONthink 4 (for the granola-import pipeline)

Easiest way to surface the prompts: open DEVONthink, then manually run each script once from Terminal (`/usr/bin/python3 ~/.local/bin/import-granola.py`, etc.) so the system prompts while you're at the keyboard.

**Files and Folders** / **Full Disk Access** — only if a script needs to read protected locations:
- [ ] DEVONthink (Full Disk Access — needed for reading the Granola SQLCipher store)

## 8. macOS system settings

`scripts/macos.sh` covers a lot but doesn't touch user-preference territory. You probably want to revisit:

- [ ] **Trackpad** — tap to click, tracking speed, three-finger drag.
- [ ] **Keyboard** — key repeat (`System Settings → Keyboard → Key Repeat Rate`), modifier keys (Caps Lock → Control if you use that).
- [ ] **Sound** — output device, alert volume.
- [ ] **Display arrangement** — if running in clamshell with the external monitor, set the external as the primary display in System Settings → Displays.
- [ ] **Energy** (laptop-specific) — "prevent automatic sleeping on power adapter when display is off" if you want DEVONthink agents to fire while clamshelled.
- [ ] **Login Items** — anything you want auto-launched that isn't in a Homebrew cask or Brewfile.

## 9. Verification

- [ ] `setup.sh` exited 0 and `git status` is clean.
- [ ] `fish -c 'echo $PROJECTS'` prints `/Users/alec/Developer`.
- [ ] `stow --no --no-folding --target="$HOME" *` from `~/.dotfiles/stow` reports no conflicts.
- [ ] AeroSpace responds to Hyper-key bindings; gap cycling works on the external.
- [ ] DEVONthink launches and shows your databases (after pointing it at the Lorebook).
- [ ] `op whoami`, `gh auth status`, and `tailscale status` all report signed in.
- [ ] VSCodium opens with custom CSS/JS applied (the `vscode-custom-css` extension requires running its "Enable Custom CSS and JS" command + a full quit; see `stow/vscode/Library/Application Support/VSCodium/User/settings.template.json` line ~344).
- [ ] Zed launches without complaining about the `claude-agent-acp` path.

## 10. Things that can break silently

- `~/.aerospace.toml` will be a regular file (not a symlink), regenerated from `stow/aerospace/.aerospace.toml` by the gap scripts. Don't edit it directly — edit the source in the dotfiles. See `stow/aerospace/.stow-local-ignore`.
- DT launch agents run as **your user**, not root. If you change your username (you're not, but for the record), every `.plist` regenerates fine from its `.plist.template` via `scripts/build-launchd-plists.sh`.
- VSCodium's `vscode-custom-css` inlines `custom.{css,js}` into `workbench.html` on enable. Every edit to those files needs **re-Enable Custom CSS + full quit** to take effect.
- The Granola pipeline decrypts a local SQLCipher store. If you upgrade Granola and the schema changes, the pipeline will break — see `~/.local/share/granola-import/NOTES.md`.
