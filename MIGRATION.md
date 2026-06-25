# Migration Checklist

Everything that `setup.sh` *can't* do for you on a new Mac. Work through it in order — some steps depend on earlier ones.

## 1. Before cloning the repo

- [ ] **Sign in to Apple ID** in System Settings (so `mas` can install App Store apps later). Then:
  - In **System Settings → [your name] → Media & Purchases → Free Downloads**, set **Require Password** to **Never Require**. Without this, `mas` will prompt for your Apple ID password on every install in the Brewfile's `mas` block (they're all redownloads of apps you already own, which Apple categorises as "free downloads").
  - In **System Settings → Touch ID & Password**, enable **App Store** under "Use Touch ID for" so any remaining prompts become a fingerprint tap.
- [ ] **Install 1Password** manually from 1password.com and sign in to your account. Wait until you see your vault before continuing.
- [ ] In **1Password → Settings → Developer**, enable:
  - **Connect with 1Password CLI** + **Biometric unlock for 1Password CLI** (the `op inject` build scripts depend on this)
  - (Optional) **Integrate with 1Password CLI** in your terminal
  - Leave **Use the SSH agent** OFF — this setup doesn't use it. SSH keys are on-disk files (`~/.ssh/id_*`), commit signing uses the local `~/.ssh/id_signing` key, and GitHub git ops go over HTTPS.
- [ ] Open a terminal and verify: `op whoami` should print your account. If it doesn't, the `build-{zed,streamrip}-config.sh` scripts will fail.

## 2. Bootstrap

- [ ] Clone the dotfiles (or copy the folder from the old machine):

  ```bash
  git clone <repo-url> ~/.dotfiles
  cd ~/.dotfiles
  ./scripts/setup.sh
  ```

- [ ] On a fresh Mac, `setup.sh` will detect that DEVONthink is not yet installed and **skip the pipeline prompt** with a "re-run after installing DEVONthink" message. Don't worry about it — install DEVONthink in step 3, then re-run `./scripts/setup.sh` to enable the launchd agents.
- [ ] When prompted, accept **macOS defaults** to apply `scripts/macos.sh`.

If `setup.sh` halts early, fix the reported issue and re-run — it's idempotent.

## 3. Manual app installs (not in Brewfile)

- [ ] **DEVONthink 4** — paid download from devontechnologies.com. Open the app once and let it create its database before the launchd agents fire.
- [ ] **Operator Mono SSm Lig** — paid font from typography.com. Used by Ghostty and Zed. Drop the `.otf` files into `~/Library/Fonts/`. Without this, both apps fall back to a system monospace.
- [ ] **SingleFile browser extension** — install in Chromium (`ungoogled-chromium`, installed via Brewfile):
  - From the Chrome Web Store (or load unpacked from the SingleFile repo).
  - In **SingleFile → Options → File name**, set the filename template to:

    ```
    SingleFile/%if-empty<{page-title}|No title>.{filename-extension}
    ```

    The `SingleFile/` prefix lands captures in `~/Downloads/SingleFile/` — the folder `stow/devonthink/.local/bin/singlefile-watcher.sh` watches (note the casing). Keep `%if-empty<{page-title}|No title>` verbatim; the ingester keys on the literal `No title` placeholder.
  - In Chromium settings, leave the download location at the default `~/Downloads` and turn **off** "Ask where to save each file" — otherwise the `SingleFile/` prefix won't resolve to the watched folder.
  - Bind SingleFile's shortcut to `Cmd+D` in `chrome://extensions/shortcuts` (used by `capture-with-singlefile` and for one-click desktop capture).
  - Full rationale: `devonthink/README.md` → "SingleFile extension setup".
- [ ] Any paid Setapp / direct-download apps not listed in `Brewfile` that you actively use.

## 4. Local repos to clone

- [ ] **`~/Developer/claude-agent-acp`** — `setup.sh` clones and builds this automatically (step 7b), so normally you don't need to do anything. If the build failed (look for a `WARNING: claude-agent-acp build failed` line), run it manually: `cd ~/Developer/claude-agent-acp && mise exec -- npm install && mise exec -- npm run build`. Without `dist/index.js`, the "Claude Code by Rohan Patra" agent entry in Zed won't function.
- [ ] Any other `~/Developer/*` repos you actively work in.

## 5. Bring over from the old machine

These live outside the dotfiles repo. Copy via Time Machine, AirDrop, or `scp`.

- [ ] **Granola pipeline files** — gitignored on purpose (the importers reverse-engineer Granola's local SQLCipher store, and we'd rather not advertise that publicly). Copy from the old machine:
  - `~/.dotfiles/stow/devonthink/.local/bin/import-granola.py` (the AppleEvents sender; stays under `/usr/bin/python3` for TCC stability)
  - `~/.dotfiles/stow/devonthink/.local/bin/import-granola-parse.py` (the `uv run --script` parser subprocess)
  - `~/.dotfiles/stow/devonthink/Library/LaunchAgents/com.user.granola-import.plist.template` (re-stow `devonthink` and re-run `scripts/build-launchd-plists.sh` after copying)
  - `~/.local/share/granola-import/` — design notes (`NOTES.md`) + any cached state.
- [ ] **Importer idempotency state.** Copy the whole `~/.local/state/devonthink/` directory. It holds two kinds of files: the JSON idempotency state that prevents importers from re-importing everything on first run (`github-stars-imported.json`, `granola-imported.json`, `granola-version.json`, optional `granola-failure.json`, plus any `.bak` siblings), and the `*.last-run` heartbeat files for every launchd-driven pipeline (`dt-daily-note`, `dt-watchdog`, `github-stars-import`, `granola-import`, `singlefile-watcher`). Without the JSONs, the next launchd fire re-imports your full GitHub star history and every Granola meeting and spams duplicate failure records into DT. Without the heartbeats, the watchdog flags pipelines as stale.
- [ ] **Dropzone grid layout (`Actions5.dzdb`).** The action bundles (`Send to DEVONthink.dzbundle`, `Send to DEVONthink Inbox.dzbundle`) come along automatically via `stow/dropzone/` — they land at `~/Library/Application Support/Dropzone/Actions/`. What does *not* come along is the grid layout itself, which Dropzone 5 stores in `~/Library/Application Support/Dropzone/Actions5.dzdb` (a SQLite DB that mutates at runtime, so it's not stowed). To restore the grid (custom display names like "Send to 99_ARCHIVE", positions, "Automatically Add to Music" → Move Files target path, etc.), quit Dropzone 5, then `cp` the `Actions5.dzdb` from the old Mac's `~/Library/Application Support/Dropzone/` into the same path on the new Mac, then relaunch. Without this swap, Dropzone 5 will discover the bundles but show them as default-named entries you have to drag into the grid yourself. Note: on Dropzone 4 the path was `~/Library/Application Support/Dropzone 4/Actions/`; Dropzone 5 uses the unversioned `Dropzone/` dir.
- [ ] `~/.gnupg/` — only if you sign commits with GPG. You don't: commit signing here is SSH-based via the local `~/.ssh/id_signing` key (`gpg.format=ssh`), so skip this.
- [ ] `~/.config/op/` — 1Password CLI local state. Optional; 1Password rebuilds on first auth.
- [ ] **Hazel rules**, **Keyboard Maestro macros**, **Alfred workflows**, **Drafts actions** — none of these tools store their state in `~/.config`. Export from the old machine and import on the new one. See each app's "backup/sync" feature. (Espanso is *not* in this list: its config and matches live in `stow/espanso/.config/espanso/`, and `setup.sh` registers + starts the service at step 9.)
- [ ] **Karabiner-Elements** — `~/.config/karabiner/` *is* in the dotfiles (`stow/karabiner/`), so it comes along automatically. Just open the app once on the new machine and grant Input Monitoring.

## 6. Post-install authentication

- [ ] `gh auth login` — GitHub CLI.
- [ ] `tailscale up` (or use the menu bar) — sign in to your tailnet.
- [ ] **Maestral** (Dropbox client) — first launch will prompt for OAuth.
- [ ] **Granola** — sign in to your account.
- [ ] **Marked 2**, **CleanShot X**, **Things**, etc. — first-launch logins where applicable.
- [ ] **Navidrome env file.** `stow/navidrome/.config/navidrome/env` is gitignored to keep the LAN URL out of the public repo's working tree. On the new Mac, copy the template into place and fill in real values:

  ```bash
  cp ~/.dotfiles/stow/navidrome/.config/navidrome/env.template \
     ~/.dotfiles/stow/navidrome/.config/navidrome/env
  $EDITOR ~/.dotfiles/stow/navidrome/.config/navidrome/env  # set NAVIDROME_URL + NAVIDROME_USERNAME
  cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" navidrome
  ```

- [ ] **Navidrome Keychain entry.** The `feishin` sketchybar plugin looks up the Navidrome password via macOS Keychain. Run: `security add-generic-password -s 'Navidrome' -a 'alec' -w '<password>' -U`. Without this, the sketchybar plugin shows "No keychain".
- [ ] **Commit signing.** The tracked gitconfig already sets `gpg.format=ssh`, `commit.gpgsign=true`, and `signingkey=~/.ssh/id_signing.pub`, and `setup.sh` generates `~/.ssh/id_signing` if it's missing. Just add `~/.ssh/id_signing.pub` to GitHub as a **Signing Key** (Settings → SSH and GPG keys → New SSH key → type: Signing Key). No 1Password needed.

## 7. macOS permission grants (TCC)

macOS will prompt the first time each app tries to do something privileged. Pre-empting these saves friction:

**Accessibility** (System Settings → Privacy & Security → Accessibility):

- [ ] AeroSpace
- [ ] Keyboard Maestro
- [ ] Alfred
- [ ] Hazel
- [ ] Espanso

**Input Monitoring**:

- [ ] Karabiner-Elements (also needs its kernel extension approved at first launch)

**Automation** (System Settings → Privacy & Security → Automation) — needed for the DEVONthink launchd agents. macOS will prompt on first fire, but the agents run headless and the prompts block silently:

- [ ] `/usr/bin/python3` → DEVONthink 4 (entry scripts run under Apple-signed stdlib python for TCC stability)
- [ ] `/bin/bash` → DEVONthink 4
- [ ] `/usr/bin/osascript` → DEVONthink 4

Easiest way to surface the prompts: open DEVONthink, then manually run each script once from Terminal (`/usr/bin/python3 ~/.local/bin/import-granola.py`, etc.) so the system prompts while you're at the keyboard.

The Granola importer is **not** an Automation sender into Granola — it reads Granola's local files directly via the parser subprocess. No Granola → DEVONthink Automation grant is needed.

**Files and Folders** / **Full Disk Access** — needed because the importer reads protected locations under `~/Library/Application Support/`:

- [ ] `/usr/bin/python3` (or the parser subprocess) — Full Disk Access, so `import-granola-parse.py` can read `~/Library/Application Support/Granola/`.

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
- [ ] `fish -c 'echo $PROJECTS'` prints the path to your Developer dir (e.g. `$HOME/Developer`).
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
- The NAS auto-mount agent (`com.user.mount-nas`, package `stow/nas-mount/`) mounts the `Media` and `Archive` shares from `192.168.50.54` via macOS NetFS, which reads the SMB password from the **login Keychain**. A fresh machine has no such entry — connect to the NAS once in Finder and tick *Remember this password in my keychain*, or the first mount pops a GUI auth dialog instead of mounting silently. The agent exits 0 when the NAS is unreachable, so it's harmless off the home network.
