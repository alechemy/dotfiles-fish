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
- [ ] Open a terminal and verify: `op vault list` should list your vaults. (`op whoami` false-negatives under the app integration — it reports "not signed in" even when data commands work.) If vaults don't list, the `build-{zed,streamrip}-config.sh` scripts will fail.

## 2. Bootstrap

- [ ] Clone the dotfiles (or copy the folder from the old machine):

  ```bash
  git clone <repo-url> ~/.dotfiles
  cd ~/.dotfiles
  ./scripts/setup.sh
  ```

- [ ] On a fresh Mac, setup skips the pipeline prompt if DEVONthink is absent. If it is already installed, decline pipeline activation until the database, private configuration, local model, and permissions below are ready. Then re-run `./scripts/setup.sh` and choose the intended driver or follower role. Only the driver should run the ingest and entity jobs.
- [ ] When prompted, accept **macOS defaults** to apply `scripts/macos.sh`.

If `setup.sh` halts early, fix the reported issue and re-run — it's idempotent.

## 3. Manual app installs (not in Brewfile)

- [ ] **DEVONthink 4** — paid download from devontechnologies.com. Open the app once and let it create its database before the launchd agents fire.
- [ ] **Marked 2** — direct download from marked2app.com (deliberately not in the Brewfile: the `marked-app` cask now ships Marked 3, a separate unlicensed major version).
- [ ] **Operator Mono SSm Lig** — paid font from typography.com. Used by Ghostty and Zed. Drop the `.otf` files into `~/Library/Fonts/`. Without this, both apps fall back to a system monospace.
- [ ] **SingleFile browser extension** — install in Chromium (`ungoogled-chromium`, installed via Brewfile):
  - From the Chrome Web Store (or load unpacked from the SingleFile repo).
  - Import the canonical settings: **SingleFile → Options → JSON settings editor**, paste the contents of `stow/devonthink/.config/devonthink-pipeline/singlefile-extension-settings.json`, save. This sets the filename template, the `url:` comment the ingester requires, and every other load-bearing option in one step (see docs/dotfiles-reference.md → "SingleFile extension settings").
  - In Chromium settings, leave the download location at the default `~/Downloads` and turn **off** "Ask where to save each file" — otherwise the `SingleFile/` prefix won't resolve to the watched folder.
  - Bind SingleFile's shortcut to `Cmd+D` in `chrome://extensions/shortcuts` (used by `capture-with-singlefile` and for one-click desktop capture).
  - Full rationale: `devonthink/README.md` → "SingleFile extension setup".
  - The tracked settings file is the filename-template authority, including its timestamp and `No title` fallback. Its `SingleFile/` prefix sends captures to `~/Downloads/SingleFile/`, the watcher's case-sensitive input folder. Do not replace it with an older template copied from a checklist.
- [ ] Any paid Setapp / direct-download apps not listed in `Brewfile` that you actively use.

## 4. Local repos to clone

- [ ] **`~/Developer/claude-agent-acp`** — `setup.sh` clones and builds this automatically (step 7b), so normally you don't need to do anything. If the build failed (look for a `WARNING: claude-agent-acp build failed` line), run it manually: `cd ~/Developer/claude-agent-acp && mise exec -- npm install && mise exec -- npm run build`. Without `dist/index.js`, the "Claude Code by Rohan Patra" agent entry in Zed won't function.
- [ ] Any other `~/Developer/*` repos you actively work in.

## 5. Bring over from the old machine

These live outside the dotfiles repo. Copy via Time Machine, AirDrop, or `scp`.

- [ ] **Importer state.** Copy `~/.local/state/devonthink/` from the old machine. Keep `github-stars-imported.json` and any `.bak` siblings as recovery aids. If that JSON is missing, the GitHub importer rebuilds IDs from existing DEVONthink bookmarks and checks exact URLs before creating records. This requires the intended database to be open and readable; it cannot recover IDs from an unavailable database. Lost state does not by itself require reimporting every star. The `*.last-run` heartbeats prevent temporary stale-job warnings during recovery, but do not prove current health. `entity-seed.yaml` was a one-time input; the DEVONthink People roster is authoritative, so the seed need not migrate.
- [ ] **Dropzone grid layout (`Actions5.dzdb`).** Dropzone 5 itself is a manual install (the Homebrew cask still ships v4 — it's commented out in the Brewfile). The action bundles (`Send to DEVONthink.dzbundle`, `Send to DEVONthink Inbox.dzbundle`) come along automatically via `stow/dropzone/` — they land at `~/Library/Application Support/Dropzone/Actions/`. What does *not* come along is the grid layout itself, which Dropzone 5 stores in `~/Library/Application Support/Dropzone/Actions5.dzdb` (a SQLite DB that mutates at runtime, so it's not stowed). To restore the grid (custom display names like "Send to 99_ARCHIVE", positions, "Automatically Add to Music" → Move Files target path, etc.), quit Dropzone 5, then `cp` the `Actions5.dzdb` from the old Mac's `~/Library/Application Support/Dropzone/` into the same path on the new Mac, then relaunch. Without this swap, Dropzone 5 will discover the bundles but show them as default-named entries you have to drag into the grid yourself. Note: on Dropzone 4 the path was `~/Library/Application Support/Dropzone 4/Actions/`; Dropzone 5 uses the unversioned `Dropzone/` dir.
- [ ] `~/.gnupg/` — only if you sign commits with GPG. You don't: commit signing here is SSH-based via the local `~/.ssh/id_signing` key (`gpg.format=ssh`), so skip this.
- [ ] `~/.config/op/` — 1Password CLI local state. Optional; 1Password rebuilds on first auth.
- [ ] **Keyboard Maestro macros**, **Alfred workflows** — neither stores state in `~/.config`. Export from the old machine and import on the new one; KM macros reference `~/.dotfiles/keyboard-maestro/` scripts by path, so the repo clone must exist before the macros run.
- [ ] **Drafts actions** — import from Drafts sync/backup, then re-paste the four scripts from `~/.dotfiles/drafts/` over the imported action bodies (the repo files are canonical; imported bodies may be stale). See `drafts/README.md`. (Espanso is *not* in this list: its config and matches live in `stow/espanso/.config/espanso/`, and `setup.sh` registers + starts the service at step 9.)
- [ ] **Karabiner-Elements** — `~/.config/karabiner/` *is* in the dotfiles (`stow/karabiner/`), so it comes along automatically. Just open the app once on the new machine and grant Input Monitoring.

### Enabled local-model and pipeline workflows

Complete these before enabling the driver's jobs. A follower does not need the local extraction model.

- [ ] Restore and open the intended DEVONthink database. Check its AI exclusions and MCP privacy settings against [the live-only checklist](devonthink/README.md#live-only-gui-state-fresh-machine-checklist). CloudKit sync is not a historical backup.
- [ ] Restore machine-local `~/.config/dt-pipeline/` configuration through a private transfer. Review `role`, `entities.conf`, and, when used, `journal.conf` for the new machine. Calendar/account identifiers, database/group identifiers, service URLs, and credentials must stay outside Git. Keep credential-bearing files mode `600`. Use [the entity design](devonthink/docs/entities.md) and [Boox setup](devonthink/docs/boox-local.md) as the schema references rather than copying private values into this checklist.
- [ ] For entity filing or Boox transcription, install the reviewed oMLX app and complete its first-run setup. Restore or download the model named by the pipeline configuration into `~/.omlx/models/`. The current documented model and installation procedure are in [the pipeline checklist](devonthink/README.md#live-only-gui-state-fresh-machine-checklist). Do not assume the app installer includes model weights.
- [ ] Set the local oMLX endpoint and key in `entities.conf` without displaying the key. Check `journal.conf` separately: its endpoint/key can inherit from `entities.conf`, but its model does not. Set the model's idle TTL in the oMLX admin UI in seconds. Confirm local model readiness before enabling extraction; an unavailable model leaves pending work queued.
- [ ] Finish the Calendar, Contacts, Automation, and optional Messages grants in step 7. Re-run setup only after these dependencies are ready, then inspect pipeline logs and completion stamps. Restored heartbeat files alone are not validation.

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

- [ ] **Navidrome Keychain entry.** In Keychain Access, create or update a password item with service `Navidrome`. Its account must exactly match `NAVIDROME_USERNAME` in the private env file above. For example, a fictional `music-listener` username needs account `music-listener`, not the old machine's account. Enter the password in Keychain Access rather than putting it in shell history. The `feishin` plugin uses that same username for its lookup.
- [ ] **Commit signing.** The tracked gitconfig already sets `gpg.format=ssh`, `commit.gpgsign=true`, and `signingkey=~/.ssh/id_signing.pub`, and `setup.sh` generates `~/.ssh/id_signing` if it's missing. Just add `~/.ssh/id_signing.pub` to GitHub as a **Signing Key** (Settings → SSH and GPG keys → New SSH key → type: Signing Key). No 1Password needed.

## 7. macOS permission grants (TCC)

macOS will prompt the first time each app tries to do something privileged. Pre-empting these saves friction:

**Accessibility** (System Settings → Privacy & Security → Accessibility):

- [ ] AeroSpace
- [ ] Keyboard Maestro
- [ ] Alfred
- [ ] Espanso

**Input Monitoring**:

- [ ] Karabiner-Elements (also needs its kernel extension approved at first launch)

**Automation** (System Settings → Privacy & Security → Automation) — needed for the DEVONthink launchd agents. macOS will prompt on first fire, but the agents run headless and the prompts block silently:

- [ ] `/usr/bin/python3` → DEVONthink 4 (entry scripts run under Apple-signed stdlib python for TCC stability)
- [ ] `/bin/bash` → DEVONthink 4
- [ ] `/usr/bin/osascript` → DEVONthink 4

Easiest way to surface the prompts: open DEVONthink, then manually run each script once from Terminal (`/usr/bin/python3 ~/.local/bin/import-github-stars.py`, etc.) so the system prompts while you're at the keyboard.

**Calendars and Contacts** are separate from Automation:

- [ ] For briefing, add the intended accounts to macOS Calendar. Run `/usr/bin/osascript -l JavaScript ~/.dotfiles/stow/devonthink/.local/bin/calendar-events-json.js` interactively and approve Calendars access.
- [ ] For contact matching and birthdays, run `/usr/bin/osascript -l JavaScript ~/.dotfiles/stow/devonthink/.local/bin/contacts-json.js` interactively and approve Contacts access. These probes return personal data; run them in your own terminal, not an agent transcript or shared log.
- [ ] For Messages-based LastContact only, grant Full Disk Access to `/usr/bin/python3` in System Settings. Calendar or Contacts permission does not grant access to Messages. Keep the Apple-signed interpreter paths; a grant to a versioned Homebrew/Mise Python is not equivalent.

## 8. macOS system settings

`scripts/macos.sh` covers a lot but doesn't touch user-preference territory. You probably want to revisit:

- [ ] **Trackpad** — tracking speed, three-finger drag (tap-to-click is set by `macos.sh` for both the built-in and Bluetooth trackpads).
- [ ] **Keyboard** — key repeat (`System Settings → Keyboard → Key Repeat Rate`), modifier keys (Caps Lock → Control if you use that).
- [ ] **Sound** — output device, alert volume.
- [ ] **Display arrangement** — if running in clamshell with the external monitor, set the external as the primary display in System Settings → Displays.
- [ ] **Energy** (laptop-specific) — "prevent automatic sleeping on power adapter when display is off" if you want DEVONthink agents to fire while clamshelled.
- [ ] **Login Items** — anything you want auto-launched that isn't in a Homebrew cask or Brewfile.

## 9. Verification

- [ ] `setup.sh` exited 0 and `git status` is clean.
- [ ] `fish -c 'echo $PROJECTS'` prints the path to your Developer dir (e.g. `$HOME/Developer`).
- [ ] `stow --no --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" *` from `~/.dotfiles/stow` reports no conflicts.
- [ ] AeroSpace responds to Hyper-key bindings; gap cycling works on the external.
- [ ] DEVONthink launches and shows your databases (after pointing it at the Lorebook).
- [ ] `op vault list`, `gh auth status`, and `tailscale status` all report signed in.
- [ ] VSCodium opens with custom CSS/JS applied (the `vscode-custom-css` extension requires running its "Enable Custom CSS and JS" command + a full quit; see `stow/vscode/Library/Application Support/VSCodium/User/settings.template.json` line ~344).
- [ ] Zed launches without complaining about the `claude-agent-acp` path.

## 10. Things that can break silently

- `~/.aerospace.toml` is a generated regular file. Edit `stow/aerospace/.aerospace.toml`, not the runtime copy. Manual gap overrides are session-local: leaving the workspace or restarting AeroSpace clears suppression at `~/.cache/aerospace-gaps/suppressed-workspace`. See `stow/aerospace/.stow-local-ignore`.
- DT launch agents run as **your user**, not root. If you change your username (you're not, but for the record), every `.plist` regenerates fine from its `.plist.template` via `scripts/build-launchd-plists.sh`.
- VSCodium's `vscode-custom-css` inlines `custom.{css,js}` into `workbench.html` on enable. Every edit to those files — and **every VSCodium update**, which replaces `workbench.html` — needs re-**Enable Custom CSS and JS** + full quit, or the machine silently runs unpatched. Check: `rg -c VSCODE-CUSTOM-CSS "/Applications/VSCodium.app/Contents/Resources/app/out/vs/code/electron-browser/workbench/workbench.html"` (any match means patched).
- The NAS auto-mount agent (`com.user.mount-nas`, package `stow/nas-mount/`) mounts the `Media` and `Archive` shares from `192.168.50.54` via macOS NetFS, which reads the SMB password from the **login Keychain**. A fresh machine has no such entry — connect to the NAS once in Finder and tick *Remember this password in my keychain*, or the first mount pops a GUI auth dialog instead of mounting silently. The agent exits 0 when the NAS is unreachable, so it's harmless off the home network.
