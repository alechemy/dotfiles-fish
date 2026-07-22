# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Personal dotfiles managed with GNU Stow on macOS. All packages under `stow/` mirror the `$HOME` directory structure and are auto-linked by `setup.sh`. `stow-work/` holds work-specific config: gitignored apart from `.gitkeep`, so a fresh `git clone` leaves it empty and `setup.sh` skips it. After a file-copy from another machine the package has content and `setup.sh` auto-stows it (see step 4a in `scripts/setup.sh`).

**Key tools:** Fish shell, Homebrew, Mise (runtime versions), Starship (prompt), Ghostty (terminal), Zed (editor).

## Hardware setup

This dotfiles setup runs on a MacBook (with notch) used in three modes. In portable mode the lid is open and the laptop is standalone. In docked mode the lid is closed (clamshell) and an ultrawide external monitor (`DELL U4025QW`) is the only active display. In travel mode the lid is open and a small secondary display — a Sidecar iPad, or a portable monitor — is active alongside the built-in panel.

Two implications when designing or evaluating features in this repo:

1. **The DELL is always alone; the built-in may not be.** Docked and portable are single-display, but travel mode runs two active screens, so display *count* and display *identity* are not interchangeable signals. Gate ultrawide-specific behavior on the DELL being present by name, never on "exactly one monitor" — a monitor-count check that means "am I docked?" silently fires in travel mode too. Travel mode is rare and deliberately unoptimized: it should degrade cleanly rather than get its own tuned code paths. Multi-display workflows (mirroring, cross-screen UI coordination) still do not apply and should not be proposed.

2. **Battery awareness.** Display mode and power source are independent signals: docked mode is reliably on AC, but portable mode can be on AC or battery — never infer the power source from the absence of the external monitor (or vice versa). Display-dependent behavior (e.g. gap calculations) keys off monitor presence; power-dependent behavior keys off actual power state via `pmset`. Features that poll on a timer, hit the network repeatedly, or otherwise wake the CPU should either degrade gracefully when on battery (longer intervals, deferred work) or skip entirely until the machine is plugged in. Apply this thinking both when adding new functionality and when reviewing existing code that may not have considered it.

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

Capture currently installed VSCodium extensions (extensions.txt drifts silently — nothing auto-captures installs):

```bash
codium --list-extensions | sort > ~/.dotfiles/stow/vscode/extensions.txt
```

## Architecture

### Stow Package Layout

Each directory under `stow/` must mirror the path relative to `$HOME`. For example, a file that should live at `~/.config/ghostty/config` goes at `stow/ghostty/.config/ghostty/config`. Stow creates symlinks from `$HOME` back into this repo.

`setup.sh` runs `stow --restow --no-folding` for every directory in `stow/` automatically, except the opt-in packages (`devonthink`, `streamrip`), which are prompted for separately. The `--no-folding` flag prevents Stow from symlinking entire directories (it creates individual file symlinks instead), which avoids conflicts with tools that write new files into their config directories.

### Auto-restow on pull (git hooks)

`git pull` updates the working tree under `stow/<pkg>/` but never invokes stow, so a file added to an already-stowed package on another machine lands **unlinked** after you pull it here (and a file deleted upstream leaves a **dangling** symlink) until the next `setup.sh`. This is the most common way symlinks silently go missing — a new file shows up in the repo but not in `$HOME`.

A git `post-merge` hook (and `post-rewrite`, for `pull.rebase=true` / rebases) closes the gap. The hooks live in the tracked `scripts/git-hooks/` directory and call `scripts/restow-changed.sh ORIG_HEAD HEAD`, which diffs the two refs, maps changed paths to their `stow/`, `stow-work/`, or `stow-local/` package, and restows each one (`stow --restow` recomputes a package's links from its current contents, so new files get linked and removed files get pruned in one pass). git skips `post-merge` when a merge stops on conflicts, so a `post-commit` hook covers that path: it fires only for commits with a second parent (`HEAD^2`), i.e. the commit that completes a conflicted merge, and calls the worker with `HEAD^1 HEAD`.

Rules baked into the worker:

- **Opt-in packages** (`devonthink`, `streamrip`, `stow-work/work`, `stow-local/local`) are restowed only if already **active** on this machine — detected by finding at least one of the package's files currently symlinked back into the package. The probe enumerates package files from disk (so generated/gitignored files like streamrip's built `config.toml` count) plus the pre-merge tree's paths (so a package whose stowed files were all renamed upstream is still recognized by its now-dangling old symlinks). A pull therefore never *activates* config a machine opted out of. Every other `stow/` package is restowed unconditionally, matching `setup.sh`, so a brand-new package syncs in automatically.
- A package whose directory was deleted entirely upstream can't be auto-pruned (stow needs the package contents to know what to unlink); the worker logs it for manual `stow --delete` instead of erroring.
- **Generated configs rebuild too:** when the pulled diff touches a `*.plist.template`, the vscode/zed settings template, or streamrip's `config.template.toml`, the worker reruns the matching build script (op-injected builds are gated on an authenticated `op` session and warn loudly otherwise). Rebuilt launchd plists are files on disk only — launchd keeps running the old definition until the label is booted out and re-bootstrapped (or next login); the worker prints a reminder.
- The hooks never abort the git operation: ref/`stow`-missing checks short-circuit to exit 0, and restow/rebuild failures are non-fatal.

`core.hooksPath` is **local** git config (lives in `.git/config`, not tracked), so it can't ship in the repo — `setup.sh` step 0d points it at `scripts/git-hooks` on every machine and re-marks the hooks executable. To wire it up by hand on a clone that hasn't re-run setup: `git -C ~/.dotfiles config --local core.hooksPath "$PWD/scripts/git-hooks"`. To restow after a sync without waiting for a hook, run `scripts/restow-changed.sh <old-ref> <new-ref>` directly (e.g. `HEAD@{1} HEAD`).

### Secrets gate (betterleaks)

Two hooks in `scripts/git-hooks/` scan for leaked secrets with [betterleaks](https://betterleaks.com) (Brewfile) and — unlike the restow hooks — block on a finding: `pre-commit` scans staged changes, and `pre-push` scans every outgoing commit per pushed ref (`remote..local`, or `--not --remotes` for a new branch). pre-push is the authoritative gate: it catches commits made with `--no-verify` or by tooling that skipped the pre-commit hook. Both skip with a warning when betterleaks isn't installed, so bare git still works mid-bootstrap.

Config is `.betterleaks.toml` at the repo root (auto-discovered; currently just extends the default ruleset). For a false positive: prefer a `betterleaks:allow` trailing comment on the flagged line, or an allowlist entry in the config; `git commit --no-verify` defers the decision to push time rather than bypassing it. Findings print redacted (`--redact=85`) — rerun `betterleaks git --staged .` without the flag to see the full match. Note betterleaks validates token *structure*, not just prefixes (e.g. a fabricated `AKIA…` string with non-base32 characters is correctly ignored), so test fixtures for the gate need realistically-shaped fakes.

### Generated configs (template → build → stow)

Some package configs are generated at install time from a tracked template. The pattern:

1. `config.template.{json,toml}` (tracked) — full config with placeholders
2. A build script in `scripts/` produces the real config (gitignored). Flavors:
   - **`op inject`**: resolves `op://Vault/Item/Field` references in a tracked template via 1Password CLI. Requires an authenticated `op` session; build scripts fail loudly if the output still contains `op://`.
   - **`op read`**: fetches a single secret and writes the output directly (no template file). Used when one value goes into a file format where a `*.template` sibling would be harmful — e.g. a fish `conf.d/*.template.fish` would itself be auto-sourced by fish.
   - **`${HOME}` expansion**: pure sed substitution. Used where the target tool needs absolute paths and doesn't honor its own variable substitution.
3. A `.stow-local-ignore` in the package root excludes the template from stowing. Stow anchors each ignore regex to the whole path segment, so the pattern must match the entire filename: use `.*\.template` (or `.*\.template\.json`), **not** `\.template$` — the latter only matches a file named exactly `.template` and silently lets `foo.plist.template` through.
4. The build script is called from `setup.sh` before stowing.

Current consumers:

- `stow/zed/` — op inject + `${HOME}` (`scripts/build-zed-config.sh`)
- `stow/streamrip/` — op inject + `${HOME}` (`scripts/build-streamrip-config.sh`)
- `stow/vscode/` — `${HOME}` only (`scripts/build-vscode-config.sh`)
- `stow/fish/.config/fish/conf.d/context7.fish` — op read (`scripts/build-context7-config.sh`); exports `CONTEXT7_API_KEY` so terminal-launched Context7 MCP authenticates instead of using the anonymous rate limit. Consumed by Claude Code (reads the env var natively) and Copilot CLI (the Copilot user MCP file references it as `${CONTEXT7_API_KEY}`; Copilot forwards only `PATH` + the `env` block to MCP servers, so the reference is required there). Copilot reads a single user MCP file (`~/.copilot/mcp-config.json`), and a work-only server shares it, so that file lives in `stow-work/work/.copilot/mcp-config.json` — context7 in Copilot is therefore present only on machines with the work package stowed
- `~/.zshenv` — op read (`scripts/build-things-config.sh`); exports `THINGS_AUTH_TOKEN` for the Things URL-scheme automation. Output lives outside the stow tree, so there is no package; the script chmods it 600.

A separate `__HOME__` expansion pattern exists for launch-agent plist templates under `stow/*/Library/LaunchAgents/*.plist.template`, handled by `scripts/build-launchd-plists.sh`.

### Seeded config (copy-if-absent, not stowed)

Some app config is portable and worth versioning but is a binary plist the app **rewrites at runtime** — stowing it via symlink is fragile, because an atomic-rename save replaces the symlink with a real file and silently de-stows it. For these, the repo keeps a tracked seed copy and a script copies it into place **only when the target is absent** (so a live, app-mutated file is never clobbered).

The pattern:

1. Seed files live under `stow/<pkg>/_seed/` mirroring their `$HOME`-relative path (e.g. `stow/devonthink/_seed/Library/Application Support/DEVONthink/SmartRules.plist`).
2. The package's `.stow-local-ignore` lists `_seed` so the directory is never symlinked.
3. A `scripts/seed-<pkg>-config.sh` walks `_seed/` and `cp`s each file to `$HOME` if the destination does not already exist. It is idempotent and safe to run with the app open.
4. `setup.sh` calls the seed script after stowing the package.
5. The seed is the **only carrier** of state the app keeps in these plists (e.g. smart-rule criteria/actions are opaque blobs no repo script can reconstruct), so it goes stale the moment the config is edited in the app's GUI. After any such edit, refresh it with `scripts/dump-devonthink-seed.sh` (the reverse copy; quit the app first, or `--force`) and commit the diff.
6. A seed carries **configuration, never machine state**. DEVONthink writes per-rule bookkeeping into the same plist — every smart rule that fires rewrites its own `LastExecution` — so a byte-diff is not a config diff. `scripts/normalize-devonthink-plist.py` strips those keys, and both the dump (which writes the seed) and `reconcile-devonthink-seed.sh` (which compares) route through it. Without that, the file reports drift forever, re-drifts the moment a rule next runs, and commits one machine's execution history. When a new runtime-owned key turns up, add it to `RUNTIME_KEYS` rather than dumping it.

Only genuinely portable, user-authored config belongs in a seed. Do **not** seed app-shipped defaults (DEVONthink repopulates its built-in AI templates and Smart Rules example `.scpt`s from the app bundle on launch) or machine-specific state (window geometry, the preferences plist, licenses) — verify against the app bundle before adding a file.

Current consumers:

- `stow/devonthink/_seed/` — DEVONthink smart rules, smart groups, custom metadata, and batch-processing presets (`scripts/seed-devonthink-config.sh`). DEVONthink AI keys live in the macOS Keychain, not these plists, so they are never captured here.
- `stow/linearmouse/_seed/` — LinearMouse scroll config (`~/.config/linearmouse/linearmouse.json`), scoped to the Ploopy Knob (VID `0x5043` / PID `0x63C3`) (`scripts/seed-linearmouse-config.sh`).

### Merged config (fragment → merge into app-owned JSON, not stowed)

`~/.claude.json` is Claude Code's state file: ~68 KB of runtime data the app rewrites via atomic rename on every launch (`projects`, `cachedGrowthBookFeatures`, `numStartups`, per-machine identity `machineID`/`userID`/`oauthAccount`). Stowing it whole is wrong on every axis — the atomic-rename save de-stows the symlink (as with a seeded plist), the churn produces constant cross-machine diffs, and committing machine identity leaks it and clobbers each machine's own. The only portable, user-authored slice is `.mcpServers`. So instead of stow-or-seed, a tracked **fragment** is merged into just that key, leaving everything else the app owns untouched.

The pattern:

1. A tracked `mcp-servers.json` fragment holds only the `mcpServers` object. It lives inside a stow package but is excluded from stowing (the package's `.stow-local-ignore` lists `mcp-servers\.json`), because nothing reads it from `$HOME` — the merge script reads it straight from the repo.
2. `scripts/merge-claude-mcp.sh` (jq) sets `~/.claude.json`'s `.mcpServers` to `(live ∪ personal ∪ work)`, writes atomically, and preserves every other key. It is **additive, fragment-wins**: a fragment entry overwrites a stale live copy of the same server and adds new ones, but a server added ad-hoc on a machine survives. Removing a server is therefore manual. Missing `~/.claude.json` (Claude Code never launched yet) starts from `{}`; missing `jq` skips with a warning.
3. `setup.sh` runs the merge in the generated-configs step (no `op` needed).
4. **Work/personal split.** MCP `env` values are `${VAR}` placeholders (Claude Code expands them at launch), so no fragment carries a literal secret — but a server URL can still be work-identifying (`<company>.atlassian.net`), and the repo is public. The personal fragment (`stow/claude/mcp-servers.json`: `devonthink`, `ankimcp`) is tracked publicly; the work fragment (`stow-work/work/mcp-servers.json`: `atlassian`) lives in the gitignored work package, and the merge folds it in only when present. See `stow-work/work/ATLASSIAN-MCP-SETUP.md`.

Current consumer: `~/.claude.json` `.mcpServers` (`scripts/merge-claude-mcp.sh`).

### Local Homebrew tap (apps with no upstream cask)

When an app has no Homebrew cask (or only a third-party one we don't want to depend on), the repo carries its own cask under `homebrew/Casks/<token>.rb` and exposes it through a **local-only tap** named `alec/local`, so it installs through the normal `brew bundle` path like any other app.

The mechanism:

1. The cask `.rb` lives in the repo at `homebrew/Casks/<token>.rb` — the single source of truth, version-controlled.
2. `setup.sh` step 1c creates `$(brew --repository)/Library/Taps/alec/homebrew-local/` and symlinks its `Casks` directory back to `homebrew/Casks/` in the repo. The tap is **not** a git repo and has no remote; a plain directory under `Library/Taps/<user>/homebrew-<repo>/Casks/` is enough for `brew` to resolve `alec/local/<token>`, and `brew update` skips non-git taps. Because `Casks` is a symlink, editing the cask in the repo is live — there is no copy to keep in sync.
3. The Brewfile references it by its full namespaced token: `cask "alec/local/<token>"`. **Do not** add a `tap "alec/local"` line — that would make `brew bundle` try to clone `github.com/alec/homebrew-local`, which doesn't exist. The tap is materialized by setup.sh instead, before `brew bundle` runs.
4. Under `HOMEBREW_REQUIRE_TAP_TRUST` (default in Homebrew 6.0), an untrusted tap's casks are silently skipped, so setup.sh step 1b also runs `brew trust --cask alec/local/<token>`.
5. The cask pins `version` + `sha256` and carries a `livecheck` block; updating means bumping both in the `.rb` (get the new sha256 from `shasum -a 256` of the downloaded asset). `brew livecheck <token>` and `brew autoupdate` flag when a new upstream release exists, but neither edits the cask file — the version bump is manual.

Unsigned/unnotarized apps (most GitHub-release Electron apps) need their quarantine attribute stripped or Gatekeeper blocks the first launch. The cask does this itself in a `postflight` block that runs `xattr -dr com.apple.quarantine "#{appdir}/<App>.app"`, so the strip happens however the cask is installed (`brew bundle`, a direct `brew install --cask`, or an autoupdate upgrade) — no separate `--no-quarantine` flag needed at the call site.

Current consumer: `homebrew/Casks/feishin.rb` — Feishin (Navidrome/Jellyfin/Subsonic desktop client; the SketchyBar `feishin` plugin depends on it).

### SingleFile extension settings (tracked, manually imported)

The SingleFile browser extension keeps its config in browser storage, not a file Stow can target, so the canonical settings live at `stow/devonthink/.config/devonthink-pipeline/singlefile-extension-settings.json` and are applied by hand (SingleFile options → JSON settings editor → paste/import). Stowing only provides a stable path to re-import from; nothing reads the file at runtime.

Two settings are load-bearing for the ingest pipeline and must not drift:

- `filenameTemplate` is prefixed with `SingleFile/` so captures land in `~/Downloads/SingleFile/`, the only folder `singlefile-watcher.sh` watches. It uses `{date-iso}`, not `{date-locale}` — a locale date renders with `/`, which SingleFile treats as a path separator (`/` is not in `filenameReplacedCharacters`), so a locale date would scatter captures into date-named subfolders the watcher never sees.
- `insertSingleFileComment: true` — `ingest-singlefile-html.py` recovers the source URL *only* from SingleFile's `url:` comment in the first 4 KB; without it every capture is rejected as "Not a SingleFile HTML."

`filenameReplacedCharacters` entries are regex character-class fragments, not literal characters — that is why the control-range entry is `\u0000-\u001f` (a regex range). Any backslash must be written pre-escaped as `\\` (JSON `"\\\\"`); a single backslash makes SingleFile build the class `[\]+`, where the `\` escapes the `]`, and every save fails with `Invalid regular expression: ... Unterminated character class`. Do not "simplify" the doubled backslash to a single one.

Capture is triggered by ⌘D bound to SingleFile in `chrome://extensions/shortcuts` (see `capture-with-singlefile`). The tracked JSON has every `saveTo*` destination disabled (plain browser download) and all token/secret fields empty — keep it so; a GitHub/S3/WebDAV/REST token here would be a plaintext secret in the repo.

### Chromium → Safari bookmark bridge (Alfred)

Alfred's bookmark search reads only Safari and Google Chrome, and it gates the Chrome source on the **app** being installed (it re-unticks "Google Chrome" in its Bookmarks prefs if `com.google.Chrome` isn't registered with LaunchServices — a fake `Bookmarks` file at Chrome's path is not enough). On a Chromium-default machine that leaves Safari as the only no-keyword path into Alfred's default results. `stow/chromium-bookmarks/` bridges the two so bookmarks made naturally in Chromium surface in Alfred without a keyword and without installing Chrome.

`com.user.chromium-bookmarks-sync.plist` runs `chromium-bookmarks-sync.py` (KeepAlive). It `fswatch`es the Chromium profile's `Bookmarks` file **directly** (not the profile dir, which Chromium writes constantly — watching the single file is event-driven and never wakes on unrelated profile churn; fswatch reliably catches the atomic rename-over Chromium uses to save). On each change it rebuilds one top-level Safari folder (`Chromium`) from the Chromium tree, leaving every other Safari bookmark untouched. Alfred indexes Safari bookmarks regardless of folder, so the mirrored entries become searchable.

Load-bearing design rules:

- **Full Disk Access.** `~/Library/Safari/` is FDA-gated. The plist's `ProgramArguments[0]` is `/usr/bin/python3` (Apple-signed, stable path) **invoked directly** — not via a bash wrapper — so TCC attributes the file access to python3 itself; granting FDA to `/usr/bin/python3` once is sufficient and survives interpreter upgrades. Until that grant exists the agent loads and watches but logs a permission error instead of writing. `setup.sh` loads the agent only when `~/Library/Application Support/Chromium/Default` exists and prints the FDA reminder.
- **Defer while Safari runs.** Safari caches bookmarks in memory and rewrites the file on its own edits, which would clobber a folder injected underneath it. The sync defers (logging it) whenever `pgrep -x Safari` matches; while a sync is pending the watcher polls every 30 s for Safari to quit and syncs as soon as it does — with nothing pending it never wakes on a timer, so the steady state stays event-driven. The script does not depend on Safari ever being open — Alfred reads the file, not Safari.
- **iCloud bookmark sync must stay off.** Verified off on this account (only `KEYCHAIN_SYNC` is enabled in `MobileMeAccounts.plist`; `BOOKMARKS` is absent). If Safari bookmark sync were on, the managed folder would propagate to other devices or be reverted by CloudKit.
- **Idempotent + non-destructive.** Managed-folder UUIDs derive deterministically (`uuid5`) from each Chromium node's `guid`, and idempotency is judged on a projection of only the keys the script owns (type/title/URL/UUID): Safari annotates managed nodes with its own bookkeeping keys (`Sync`, `ReadingListNonSync`) once it runs, so an unchanged Chromium tree no-ops even on an annotated file, and a real rewrite merges Safari's extra keys back in by `WebBookmarkUUID` instead of stripping them. It matches its own folder by a fixed `WebBookmarkUUID` (or `Title == "Chromium"`) and rebuilds only that. A one-time pre-write backup lands at `~/.local/state/chromium-bookmarks-sync/Safari-Bookmarks.firstrun-backup.plist`.
- Modes for manual use/testing: `--once` (single sync), `--dry-run` (report, no write), `--force` (sync even while Safari is running). The default (no args) is the watch loop the agent uses.

### Screen-lock → Keyboard Maestro bridge

Keyboard Maestro (11.0.4) has a native `Unlock` system trigger but no *lock* counterpart. `stow/lock-watcher/` fills exactly that gap: `com.user.lock-watcher` (KeepAlive) runs `lock-watcher.applescript` — AppleScriptObjC under `/usr/bin/osascript` — which observes the `com.apple.screenIsLocked` distributed notification and fires the KM macro named in its `lockMacroName` property (`On Lock, Disable Proxy + Quit Feishin and Qobuz`) via `do script` — rename the macro and that property together. Fully event-driven: the process blocks in its run loop between locks (no polling, battery-clean). The macro's contents live in KM's own library, not this repo — KM's macro plist is app-owned runtime state, the same reason `~/.claude.json` isn't stowed.

Load-bearing details:

- **Lock only.** Anything unlock-side belongs on KM's native `Unlock` trigger (as "Restart Feishin on Wake" already does), not on a `screenIsUnlocked` observer here — don't re-add one.
- The `tell application "Keyboard Maestro Engine"` is wrapped in `run script` so the file compiles on machines where KM was never launched (no scripting dictionary registered); at runtime the script stays dormant when KM is absent (exit 0 + `KeepAlive.SuccessfulExit=false`, same pattern as the bookmark sync). setup.sh additionally gates the bootstrap on the app's presence.
- At load it sends a harmless `getvariable` ping to KM Engine so the one-time osascript → KM Engine Automation prompt fires while the user is present, not behind a locked screen at the first real lock event. osascript is Apple-signed, so the grant never rotates.
- ASObjC under osascript: `run`, `name`, and `center` collide with AppleScript terminology — write `|run|()`, `|name|:`, and pick another variable name for the notification center.
- Scripting the KM **editor**: `make new action with properties {xml:…}` is reliable, but `make new trigger with properties {xml:…}` crashes KM 11.0.4 outright — set the whole macro's `xml` instead if a trigger must ever be scripted.
- On lid-close the handler races system sleep, so the macro may finish on the next wake; its actions (proxy off, quit apps) are idempotent, and the `Sleep`-triggered macro covers that path anyway. Expect both macros to fire on a lock-then-sleep.

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

When the work needs Python ≥ 3.10 or third-party packages, use the split-architecture pattern. The entry script runs under `/usr/bin/python3` (stdlib only) and owns every `osascript` invocation; a separate parser script with `#!/usr/bin/env -S uv run --script` is invoked via `subprocess.run([parser_path], ...)` and exchanges JSON over stdin/stdout for the heavy work. The parser never sends AppleEvents.

`scripts/lint-launchd-plists.sh` enforces both rules across every plist template in the repo and runs as part of `setup.sh`. It will halt the bootstrap on any violation.

### Launch agents and TCC-protected folders

The same Apple-signed-vs-rotating-identity split that governs AppleEvents also governs the per-folder TCC protections on `~/Downloads`, `~/Desktop`, and `~/Documents`. When a launch agent reads or writes a file in one of those folders, macOS checks the accessing binary's signature. Apple-signed binaries (`/usr/bin/python3`, `/bin/mv`, `/bin/mkdir`, `osascript`) are not blocked in this context; non-Apple-signed helpers (Homebrew/`mise`/`uv`-managed tools — `node`/`defuddle`, `magick`, `markdownlint`, etc.) trigger a one-time "X wants to access files in your Downloads folder" prompt. Because launch agents run headless, a fumbled or dismissed keystroke on that prompt writes a persistent *deny* rule, after which the helper's `open()` returns `EPERM` on every run — silently, since the surrounding Apple-signed script keeps working. (`fswatch` is exempt: FSEvents *monitoring* is a different code path than file `open()` and does not trip the per-folder check.)

The rule: **a non-Apple-signed helper invoked under a launch agent must never open a file directly inside a TCC-protected folder.** Stage the file into the per-user temp dir first (`tempfile.TemporaryDirectory()` / `mktemp`, which lives under `$TMPDIR` → `/var/folders/…`, not protected) and point the helper at the copy. Do the copy itself with an Apple-signed binary. `ingest-singlefile-html.py` is the reference: it copies the staging HTML out of `~/Downloads/SingleFile/` into `tmpdir` before handing it to `defuddle`. This also makes the pipeline robust to the helper's path/signature rotating on upgrade — there is no folder grant to lose.

This is not enforced by a linter; it is a design rule to apply whenever a new pipeline reads from or writes to Downloads/Desktop/Documents under launchd.

### AppleScript: `do shell script` returns CR, not LF

AppleScript coerces a shell helper's LF output to CR (classic Mac line endings). Several smart rules pipe a record's body through a Python helper and write the result back — `set newText to do shell script "…helper < tmp"` then `set plain text of theRecord to newText` — and without a modifier that round-trip silently rewrites the **entire body** as one CR-delimited line.

Nothing looks wrong in DEVONthink (it renders CR fine), but every downstream consumer that splits on `\n` then sees a note with **no lines and no headers**. That is how a duplicated daily note happens: `entity-dt-bridge.js`'s `upsert_section` fails to find `## Briefing`, takes its append path, and adds a second copy of every generated section instead of replacing the first. The same input wipes a body in `sync-markdown-h1.py`, which then emits only its H1.

Three rules:

1. **Every `do shell script` whose output is written back into a record must end with `without altering line endings`.** This applies to `set plain text of`, `set comment of`, and `set rich text of` sinks alike. The modifier also repairs the usual `if newText is not originalText` idempotency guard, which is otherwise always true (CR vs LF) and rewrites the record on every pass.
2. **Build note bodies with `linefeed`, never `return`.** AppleScript's `return` constant *is* CR, so a skeleton like `"# " & headingDate & return & return & "- "` births a CR-delimited note before any helper touches it. The daily-note skeleton is duplicated in four places (`create-daily-note.sh`, two smart rules, and the AppleScript embedded in `ingest-singlefile-html.py`) — change them together.
3. **Consumers must be tolerant, not trusting.** `splitlines()` splits on CR; `split("\n")` does not, and this pipeline's Python uses both. Anything reading a record body — or an AppleScript-built `--content` argument — must use `splitlines()` or normalize CRs first, and JXA must split on `/\r\n|\r|\n/` (`bodyLines()` in `entity-dt-bridge.js`). Every writer emits `\n`, so a body that picks up CRs some other way self-heals on its next edit.

`devonthink/tests/test_applescript_line_endings.py` enforces rules 1 and 2 across every AppleScript in the repo, including the ones embedded in Python and shell scripts, and asserts the underlying coercion still happens so the guard can't rot into a no-op.

### Python script shebangs

Python interpreter management is split between mise and uv on purpose. mise (`stow/mise/.config/mise/config.toml`) provides the day-to-day `python3` on `$PATH`. uv (Brewfile) is reserved for scripts that declare third-party deps via PEP 723. There is no repo-wide `pyproject.toml` / `uv.lock` — each script stands alone.

Pick a script's shebang from this three-tier rule:

1. **TCC-sensitive** (script sends AppleEvents AND is invoked by a launch agent, either directly via the plist or transitively through a launchd-driven shell script that calls `"$SCRIPT" args`) → `#!/usr/bin/python3`. Apple-signed, stable TCC identity, stdlib only. If the work needs third-party deps, use the split-architecture pattern from the section above (sender stays `/usr/bin/python3`, parser is a `uv run --script` subprocess).
2. **Has third-party deps, not TCC-sensitive** → `#!/usr/bin/env -S uv run --script` with a PEP 723 inline `# /// script` block declaring `requires-python` and `dependencies`. Reference: `stow/bin/.local/bin/tagger.py`.
3. **Pure stdlib, not TCC-sensitive** → `#!/usr/bin/env python3`. Resolves through PATH to mise's Python.

For tier 1 scripts, even when the launchd plist provides the interpreter explicitly (`/usr/bin/python3 /path/to/script.py`), still write the shebang as `#!/usr/bin/python3` so direct invocation during testing uses the same interpreter as production rather than mise's.

### Git: verify HEAD before amending

Multiple Claude Code sessions can run against this repo at once (desktop plus a Moshi phone session), so HEAD may not be the commit you made earlier in your own session. Before any `git commit --amend`, run `git log -1` and confirm HEAD is the exact commit you intend to rewrite; if it isn't, make a new commit instead. To repair a wrong amend: `git reset --soft HEAD@{1}` restores the clobbered commit and re-stages only your changes.

### tmux: test config on an isolated socket

Never run `tmux kill-server` (or `kill-session`) on the default socket for verification — a live server may be hosting a remote (Moshi) session, and killing it drops that client. Test config changes on a throwaway socket instead: `tmux -L test new -d && tmux -L test show -g <option> && tmux -L test kill-server`. Also note a running server never re-reads `tmux.conf`; if options look half-applied (e.g. `mouse on` but default `history-limit`), you attached to a pre-existing server rather than starting a fresh one.

### Audio tagging with mutagen

When writing MP4/m4a boolean atoms (`cpil`, `pgap`) with mutagen, assign a **bare bool** — `audio["cpil"] = True` — never a list. mutagen renders a list by truthiness, so `audio["cpil"] = [False]` silently writes `True`. `tagger.py` sets the compilation flag this way.

### AeroSpace scripting: identify apps by PID, not name

When a script bridges AeroSpace and System Events (e.g. to hide or focus a specific app), key off the **PID**, not the app name. AeroSpace's `%{app-name}` and the System Events process name disagree for some apps — notably case (`Ghostty` vs `ghostty`) — so a name comparison silently mismatches: it fails to exclude the target when picking a sibling, and `set visible of (process whose name is …)` can no-op against the wrong identity. Use AeroSpace's `%{app-pid}` and hide/match via System Events `unix id` (`first application process whose unix id is <pid>`), which is namespace-safe. Reference: `scripts/aerospace-hide.sh`.

Context for that script: AeroSpace emulates workspaces by hiding/showing windows that all share one macOS Space, so a native Cmd-H on the *frontmost* app makes macOS activate the next global-MRU app — often on another workspace — and AeroSpace follows focus there, yanking you off your workspace. Hiding a *non-frontmost* app moves no focus, so the handler focuses a same-workspace sibling first, then hides the target by PID. AeroSpace exposes no hide/unhide callback, and `reload-config` (the only way to apply a gap change) re-syncs the visible workspace to the focused window's workspace — a no-op when they already agree, but the reason gap recomputes must not run while focus is mid-transition.

macOS hide/unhide is **not transparent to AeroSpace's layout tree**: an app's split arrangement is not restored when it is unhidden, even though its hidden windows keep appearing in `list-windows` output. Hiding is therefore viable only at `aerospace-hide.sh`'s deliberate single-app scale — do not bulk-hide background-workspace apps programmatically (e.g. to dodge the macOS 27 beta parked-window bounce, AeroSpace discussion #2155); it trades a flicker for lost layouts. When testing anything that touches hide/show or workspace transitions, verify against a workspace with a real multi-window split, not a single-window one — single-window round-trips hide this class of breakage.

### Agentic comment-noise suppression (Claude Code + Copilot CLI)

Both agent CLIs are steered away from low-value code comments (change narration, restating the code, ticket/changelog refs) and verbose prose, machine-wide, by a two-layer design adapted from `~/Downloads/Suppressing-AI-Comments-Consolidated-Guide.md`. Determinism is the anchor: prompts only reduce how much noise is generated; the only enforceable removal is mechanical.

**Generation layer (always-on, every repo, non-destructive).** Reduces how often noise is written.

- Claude Code: the `Terse` output style (`stow/claude/.claude/output-styles/terse.md`, `keep-coding-instructions: true`) writes the comment policy into the *system prompt* — stronger placement than `CLAUDE.md`, which is only a user message that decays over a session — and is the default via `"outputStyle": "Terse"` in `settings.json`. Because an output style is fixed at session start, the policy is also re-injected where each gap actually opens: `comment-policy-inject.sh` emits `comment-policy.md` as `additionalContext` from a `UserPromptSubmit` hook (counters in-session decay), a `SessionStart`/`compact` hook (recovers it after compaction), and a `SubagentStart` hook (subagents don't inherit the output style).
- Copilot CLI: the policy lives in `copilot-instructions.md` (its global standing-instructions file, loaded every session). Copilot has *no* per-turn re-injection — a `userPromptSubmitted` command hook's stdout is not processed (github/copilot-cli#1157) and `prompt` hooks are `sessionStart`-only — so the instruction file is its whole generation layer.

The Response-style bullet pair in `CLAUDE.md` is the canonical prose; Copilot reads the same file (`stow/copilot/.copilot/copilot-instructions.md` symlinks to it). The output style and the re-injected `comment-policy.md` restate it for the system-prompt and per-turn slots — keep those three aligned. This replaced an earlier probabilistic `comment-noise-check.sh` PostToolUse hook (now removed) that only flagged JS/TS narration back to the model.

**Deterministic stripper (opt-in per repo).** `uncomment` (goldziher/uncomment, an AST/tree-sitter stripper, installed as the `npm:uncomment-cli` mise tool) actually deletes the noise, but only in repos that opt in by carrying a root `.uncommentrc.toml` marker — so repos with deliberate, load-bearing comments (this one included) stay untouched until chosen.

- In-session: `stow/bin/.local/bin/agent-strip-comments` runs from the Claude Code `Stop` hook and the Copilot `agentStop` hook (`stow/copilot/.copilot/hooks/comment-gate.json`). It gates on the repo-root marker, always exits 0 (never blocks a turn), and scopes the strip along two axes, because `uncomment` itself only knows how to strip a whole file:
  - **Files.** Neither tool exposes a changed-file list, so it intersects git's changed files with the files the agent itself touched this session — exact edit targets parsed from a Claude Code transcript, or referenced paths from a Copilot one — so unrelated uncommitted work is never opened (with no transcript it falls back to all changed files).
  - **Lines.** Each file is handed to `uncomment-scoped` with a baseline, and only comments on lines the agent *added* over that baseline are removed. The baseline is the pre-edit content Claude Code records on every Edit/Write tool result (`originalFile`), so uncommitted work that predates the session is protected too; failing that, the file's HEAD blob; failing that (a new file), empty, which strips the lot. Without line scoping, touching one line of a file deletes every explanatory comment in it.

  The installed CLI (2.0.0) has no config file, so preserve patterns are flags: it keeps TODO/FIXME/docstrings/lint pragmas by default, plus `IMPORTANT/NOTE/WARNING/SAFETY/SECURITY/keep`; a `remove_docs = true` line in the marker adds `--remove-doc`. **`--remove-doc` is inert on its own** — 2.0.0 re-preserves doc comments via the default ignore set, and only `--no-default-ignores --remove-doc` strips them, which would also drop lint pragmas. So `remove_docs = true` currently does nothing.
- `stow/bin/.local/bin/uncomment-scoped` is the line-scoping wrapper: it strips a scratch copy via `uncomment-clean`, then merges back only the removals that land on added lines. It leans on `uncomment` rewriting each line in place — a comment-only line is blanked, not deleted, nothing is reordered, and no line is ever added — so equal line counts before and after prove a 1:1 mapping and each line is judged on its own index. Only a multi-line block comment collapses; when the counts differ, the collapse has to be located by diff, which blank lines make ambiguous, so those hunks are taken only when every line under them is agent-authored and any merge that grows the file is discarded. A line whose exact trimmed text already appears in the baseline is never touched, so a comment that was merely moved or reindented survives. With an empty baseline the output is byte-identical to plain `uncomment-clean`.
- `stow/bin/.local/bin/uncomment-clean` wraps `uncomment` (same args/files) and fixes its one rough edge: `uncomment` doesn't reformat, so removing an inline comment leaves the code before it with trailing whitespace. The wrapper trims trailing whitespace on each processed file afterward — skipping Markdown-family files (where trailing spaces are significant hard breaks) and binaries (perl `-T` test) — so the tidy never depends on the repo having a formatter, corrupts a binary, or breaks a Markdown line break. It resolves the `uncomment` binary via PATH → mise shim → install path (hooks may run without mise activation).
- Commit/CI gate (the non-bypassable half): `comment-gate-init [repo]` drops the marker and prints portable pre-commit / lefthook / GitHub-Actions snippets that run `uncomment` plus the same Markdown- and binary-safe trailing-whitespace trim on staged files, then fail CI on residual noise. The snippets are self-contained (no dependency on this machine's `uncomment-clean`). Unlike the in-session hook these are **whole-file** — they have no session baseline to scope against, so they strip a staged file's pre-existing comments too. Only opt a repo in if that is what you want on every commit.

### Stub-scan completion gate (Claude Code Stop hook)

`stow/bin/.local/bin/agent-stub-scan` runs from the Claude Code `Stop` hook (every repo, no opt-in marker) and refuses to let a turn end while stub markers — `TODO`/`FIXME`/`HACK`/`XXX`, `not implemented`, `NotImplementedError`, `unimplemented!` — remain on lines the agent added this session: it exits 2 with the `file:line` list on stderr, which Claude Code feeds back to the model so it finishes the work or explicitly justifies the marker to the user. It is the complement of `agent-strip-comments`, not part of it: the stripper deliberately *preserves* TODO/FIXME and never blocks, and half the stub patterns are code, not comments.

Because this hook can block, its scoping is stricter than the stripper's: only exact edit targets parsed from the transcript count (no referenced-path or all-changed fallback — no transcript means no scan), and only their git-added lines vs `HEAD` (whole file when untracked), so pre-existing markers never trigger. `stop_hook_active` short-circuits to exit 0, capping enforcement at one round per stop chain — an intentional marker costs at most one extra round-trip. All infrastructure failures exit 0. Known gap: subagent edits live in separate transcripts, so stubs left by a `Task` agent aren't caught. Copilot CLI is not wired up — its `agentStop` hook has no blocking semantics.
