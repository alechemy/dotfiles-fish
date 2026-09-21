# Dotfiles architecture reference

Detailed architecture, invariants, and operational notes for this repository. `AGENTS.md` is the concise task-routing index.

## Overview

Personal dotfiles managed with GNU Stow on macOS. All packages under `stow/` mirror the `$HOME` directory structure and are auto-linked by `setup.sh`. `stow-work/` holds work-specific config: gitignored apart from `.gitkeep`, so a fresh `git clone` leaves it empty and `setup.sh` skips it. After a file-copy from another machine the package has content and `setup.sh` auto-stows it (see step 4a in `scripts/setup.sh`).

**Key tools:** Fish shell, Homebrew, Mise (runtime versions), Pi (coding-agent harness), Starship (prompt), cmux (terminal workspace), and Zed (editor).

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

Each directory under `stow/` must mirror the path relative to `$HOME`. For example, the libghostty settings that cmux reads from `~/.config/ghostty/config` live at `stow/cmux/.config/ghostty/config`. Stow creates symlinks from `$HOME` back into this repo.

`setup.sh` runs `stow --restow --no-folding` for every directory in `stow/` automatically, except the opt-in packages (`devonthink`, `streamrip`), which are prompted for separately. The `--no-folding` flag prevents Stow from symlinking entire directories (it creates individual file symlinks instead), which avoids conflicts with tools that write new files into their config directories.

### Auto-restow on pull (git hooks)

`git pull` updates the working tree under `stow/<pkg>/` but never invokes stow, so a file added to an already-stowed package on another machine lands **unlinked** after you pull it here (and a file deleted upstream leaves a **dangling** symlink) until the next `setup.sh`. This is the most common way symlinks silently go missing — a new file shows up in the repo but not in `$HOME`.

A git `post-merge` hook (and `post-rewrite`, for `pull.rebase=true` / rebases) closes the gap. The hooks live in the tracked `scripts/git-hooks/` directory and call `scripts/restow-changed.sh ORIG_HEAD HEAD`, which diffs the two refs, removes destination links for tracked deletions, maps changed paths to their `stow/`, `stow-work/`, or `stow-local/` package, and restows each one's surviving contents. The explicit deletion pass matters when a source directory disappears entirely: Stow cannot discover links beneath a source path that no longer exists. git skips `post-merge` when a merge stops on conflicts, so a `post-commit` hook covers that path: it fires only for commits with a second parent (`HEAD^2`), i.e. the commit that completes a conflicted merge, and calls the worker with `HEAD^1 HEAD`.

Rules baked into the worker:

- **Opt-in packages** (`devonthink`, `streamrip`, `stow-work/work`, `stow-local/local`) are restowed only if already **active** on this machine — detected by finding at least one of the package's files currently symlinked back into the package. The probe enumerates package files from disk (so generated/gitignored files like streamrip's built `config.toml` count) plus the pre-merge tree's paths (so a package whose stowed files were all renamed upstream is still recognized by its now-dangling old symlinks). A pull therefore never *activates* config a machine opted out of. Every other `stow/` package is restowed unconditionally, matching `setup.sh`, so a brand-new package syncs in automatically.
- A package whose directory was deleted entirely upstream has its tracked destination links pruned from the old-tree paths. The worker still logs the missing package because generated or untracked package files cannot be reconstructed from git and may require manual cleanup.
- **Generated configs rebuild too:** when the pulled diff touches a user LaunchAgent template, the vscode/zed settings template, streamrip's `config.template.toml`, or their builder scripts, the worker reruns the matching build script. Context7 and Things builder changes also trigger their builds. If a rebuild adds or removes a generated Stow output, its package is queued for restowing; rewriting an existing output needs no extra Stow operation. Secret-backed builds require an authenticated `op` session and warn otherwise; streamrip also requires an already-active package. Root LaunchDaemon template or installer changes only print a manual `scripts/install-iogpu-limit.sh` instruction; hooks never run sudo. The Pi settings and models fragments trigger their respective merges through `scripts/merge-pi-settings.sh`. A merger script change triggers both, even with no Stow package changes. The DTNote source and builder also trigger their existing rebuild handler when `~/Applications/DTNote.app` exists. Rebuilt launchd plists are files on disk only. Launchd keeps running the old definition until the label is booted out and re-bootstrapped or the next login; the worker prints a reminder.
- The hooks never abort the git operation: ref/`stow`-missing checks short-circuit to exit 0, and restow/rebuild failures are non-fatal.

`core.hooksPath` is **local** git config (lives in `.git/config`, not tracked), so it can't ship in the repo — `setup.sh` step 0d points it at `scripts/git-hooks` on every machine and re-marks the hooks executable. To wire it up by hand on a clone that hasn't re-run setup: `git -C ~/.dotfiles config --local core.hooksPath "$(git -C ~/.dotfiles rev-parse --show-toplevel)/scripts/git-hooks"`. To restow after a sync without waiting for a hook, run `scripts/restow-changed.sh <old-ref> <new-ref>` directly (e.g. `HEAD@{1} HEAD`).

Setup refuses linked dotfiles checkouts. The restow worker and its `post-merge`, `post-rewrite`, and `post-commit` entrypoints skip them so experimental worktrees cannot replace live HOME links. The hook-level checks also cover branches carrying an older worker script. Integrate task changes into the primary checkout before applying them.

### Fish command authority and port listeners

Bare `copilot` no longer expands to `copilot --allow-all`. Pass `--allow-all`
explicitly only when that authority is intended. This does not change Pi's
Copilot provider. `unpop` is removed: `git reset --merge` is not a general
inverse of `git stash pop`. Inspect the index and working tree, then choose the
actual Git command for the recovery you need.

`ports` is the only port helper. `killport` remains an abbreviation for
`ports kill`.

```fish
ports show 8081
ports pid 8081
ports kill 8081
ports kill 8081 --force
```

`kill` sends SIGTERM to one verified TCP listener owner. It checks for exit
30 times at 0.1-second intervals. If the process survives, it returns failure
without escalating. `--force` permits SIGKILL only after this TERM wait and a
fresh check of the original listener and process identity. It waits again after
KILL. Process queries add to the roughly three-second wait per signal.

The helper validates argument counts and ports from 1 through 65535 before
queries. It deduplicates IPv4/IPv6 PIDs, refuses multiple owners, and requires
a listener owned by the current user. It prints at most 160 characters of
owner UID, start time and executable, never command arguments. Query warnings,
malformed output and changed identities stop signaling. A new process that
acquires the port is not selected as a replacement target. It does not use
`sudo`, act on UDP sockets, or stop services by name.

Identity checks use `ps` owner, start time and executable immediately before
each signal. macOS shell tools do not provide an atomic check-and-signal handle;
PID reuse between that check and `kill` remains a narrow OS-level race. Start
times have one-second resolution. Use the application's own shutdown command
when stronger lifecycle guarantees are needed.

After these changes are integrated into the primary dotfiles checkout, a new
interactive Fish shell loads the new abbreviations and helper. Existing shells
keep loaded functions and abbreviations until refreshed. Either open a new
shell or run the following in each existing shell after integration:

```fish
source ~/.config/fish/conf.d/abbrs.fish
source ~/.config/fish/functions/ports.fish
complete -c ports -e
source ~/.config/fish/completions/ports.fish
```

Sourcing the abbreviation file removes old `copilot` and `unpop` definitions.
Text already expanded on a command line does not change; clear that line before
running it. No Stow operation is needed for edits to these existing linked files.
Do not source from a task worktree to activate unintegrated changes.

Run the isolated regressions with
`/usr/bin/python3 -m unittest discover -s scripts/tests -p test_shell_safety.py`.
The suite uses a disposable HOME, no Fish startup files, synthetic identities,
and listeners it creates itself. It never launches Copilot or targets a live
development server.

### Secrets gate (betterleaks)

Two hooks in `scripts/git-hooks/` scan for leaked secrets with [betterleaks](https://betterleaks.com) (Brewfile) and — unlike the restow hooks — block on a finding: `pre-commit` scans staged changes, and `pre-push` scans every outgoing commit per pushed ref (`remote..local`, or all history reachable from a new branch's tip). New-branch scans deliberately include commits known to other remotes: a private remote is not evidence that a commit is safe for this destination. pre-push is the authoritative gate: it catches commits made with `--no-verify` or by tooling that skipped the pre-commit hook. Both skip with a warning when betterleaks isn't installed, so bare git still works mid-bootstrap.

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
- `stow/fish/.config/fish/conf.d/context7.fish` — op read (`scripts/build-context7-config.sh`); exports the optional `CONTEXT7_API_KEY` so the official `@upstash/context7-pi` extension gets authenticated higher rate limits in terminal Pi sessions. The extension remains usable anonymously when the key is absent. Version `0.1.2` is source-reviewed and pinned in Pi's settings fragment; it registers two native tools, its `context7-docs` skill, and the `/c7-docs` command without an MCP server.
- `~/.zshenv` — op read (`scripts/build-things-config.sh`); exports `THINGS_AUTH_TOKEN` for the Things URL-scheme automation. Output lives outside the stow tree, so there is no package; the script chmods it 600.

For Context7 key rotation, update the `credential` field of `mcp-server-context7` in the Private 1Password vault, then run `~/.dotfiles/scripts/build-context7-config.sh` from the primary checkout. The builder references the item by ID, so update the existing item rather than replacing it. Open a new terminal and restart Pi to inherit the new export; rebuilding the file does not update existing processes. Keep the resolved key out of documentation and tool output.

A separate `__HOME__` expansion pattern exists for launch-agent plist templates under `stow/*/Library/LaunchAgents/*.plist.template`, handled by `scripts/build-launchd-plists.sh`.

### Git signing trust

Setup creates `~/.ssh/id_signing` only when absent. `scripts/build-git-allowed-signers.sh` combines its public key with the deliberately trusted historical keys in `stow/git/.config/git/allowed_signers`. The result, `~/.config/git/allowed_signers.local`, is machine-local and is the configured SSH verification source. No machine key is written into Stow or Git. The new key is restricted to the `git` signing namespace.

Setup builds this file after key generation. The restow hook rebuilds it when the trust source, Git config, or builder changes; a missing local public key only warns in the hook. After deliberately rotating the local signing key, run the builder by hand. Rebuilding replaces the generated file, so maintain deliberate historical trust in the tracked source, not the generated output. Registering a new signing key with GitHub remains a separate manual step.

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

### Retired Claude Code integration

Pi is the active coding client. Claude Code's Homebrew declaration, Stow package, project compatibility links, restart shortcut, editor integrations, ACP bootstrap, and work-MCP merger are removed. Setup no longer installs or configures Claude. Historical transcripts and app-owned state under `~/.claude/` and `~/.claude.json` remain private and unstowed so recall can still read old work. Uninstall without `--zap` to preserve them.

The private work MCP fragment under `stow-work/` is retained as inactive data, not merged into any client. Context7 remains a native Pi package; DEVONthink and Jira use their existing shared skills. Pi's Anthropic provider and Claude model identifiers are independent of the retired Claude Code application.

### Shared agent instructions and skills

`stow/agents/.agents/AGENTS.md` is the only global instruction source. Pi and the retained Copilot CLI adapter expose it through symlinks in their own config directories. Project instructions use `AGENTS.md`; this repository no longer ships `CLAUDE.md` compatibility links.

Harness-neutral Agent Skills live under `stow/agents/.agents/skills/`. Pi discovers them directly under `~/.agents/skills`. Keep detailed skill instructions and bundled helpers there so Pi loads only each description until use. The `devonthink` skill follows this pattern: its bundled read-only client starts DEVONthink's official stdio server on demand and exposes only field discovery, record search, and custom-metadata reads. It validates vendor response shapes, projects allowed fields, enforces returned record limits and batch UUID membership, bounds transport and stdout, and suppresses backend error text. Singleton metadata has no echoed UUID; its identity remains bound by the request. See the shared skill for exact limits and version evidence. `setup.sh` disables and boots out DEVONthink's separate HTTP MCP login item because no retained client needs its always-on full tool surface; it warns if a loaded service cannot be stopped, while direct stdio requests remain available. To restore the HTTP service deliberately, run `launchctl enable "gui/$(id -u)/com.devon-technologies.think.mcp-server"`, then turn **Launch at login** back on under DEVONthink's Settings → AI → MCP.

The `agent-browser` skill is the default interactive browser workflow. Homebrew owns the CLI and the `google-chrome` cask; the skill explicitly selects `~/.config/agent-browser/config.json` for headless, fresh-profile task sessions. The Claude Code integration and proposed Pi Chrome DevTools adapter are retired. Browser profiles, credentials, runtime state, and output remain unstowed. See [Agent browser](agent-browser.md) for configuration ownership and usage limits.

The `jira` skill uses Atlassian's official `acli` through Bash for scoped story reads, JQL searches, and comments. The Brewfile and setup's exact-formula trust list own `atlassian/acli/acli`; no MCP adapter is involved. Authentication remains CLI-owned and user-initiated through browser OAuth. The shared skill contains no site, account, or real custom-field mapping. Keep those details in private project instructions or `stow-work/`. Its read-first and explicit-write-approval rules are instructions, not CLI permission enforcement. See `stow/agents/.agents/skills/jira/references/setup.md` for authentication, the one-story verification procedure, and command-version evidence. Homebrew can reject an older selected Xcode even for this prebuilt CLI. Do not change the global Xcode selection without approval; Homebrew filters a per-command `DEVELOPER_DIR` override.

The `code-review` skill adapts Matt Pocock's pinned upstream skill into read-only Correctness, Spec, and Standards reviews. Its advertised description and one routing rule in the shared `AGENTS.md` cover ordinary review requests without a slash command. It owns comparison selection and evidence requirements; the installed `pi-subagents` skill owns Pi execution mechanics. `simplify-review` remains a focused command-only workflow and shares `code-review/references/maintainability.md` rather than duplicating criteria. Source revision and MIT attribution live with the new skill. `scripts/tests/test_code_review_recipes.py` exercises its documented Git comparisons in disposable repositories.

The `wizard` skill adapts Matt Pocock's manual-procedure generator, pinned to revision `3cca18b368ae95cdbdebbff572ccafa662551015`. It bundles the unchanged upstream Bash template and MIT license under the shared skills directory. Use `/skill:wizard <procedure>` in Pi to generate a scoped, human-run setup or migration script. Its local instructions preserve credential privacy and remote-write approval, and require checking template compatibility before use. Restow `agents` after adding or removing its files.

The `recall` skill delegates session-format parsing to the separately versioned `agent-reader` CLI. Its reviewed Pi discovery and normalized JSON changes are not published upstream, so `scripts/install-agent-reader.sh` builds an exact-base, checksummed source overlay from `patches/agent-reader/` without pushing or retaining a Git remote. The installer uses only help output for capability checks, runs synthetic fixtures under the upstream test suite, and installs from a stable private wheel cache so setup never emits transcript paths, names, prompts, or content. Replace the overlay with an exact public commit once equivalent upstream code is reachable.

Recall's bundled `~/.agents/skills/recall/recall-filter.py` is a stdlib-only stdin filter over agent-reader's normalized JSON, not another transcript parser. `session-index` projects only client, ID, activity date, and counts after exact-workspace selection. `transcript` requires an exact workspace/client/session and releases only turn indices and timestamps by default, including topic-match results. Content recall requires explicit opt-in and a starting turn. Excerpts use sanitized prose lines, retaining only literal topic matches when specified, with hard limits of five turns and 2,000 characters per turn. Known credential formats, sensitive credential lines, and recognizable dumps are removed before clipping. Unknown fields, names, labels, source paths, and model/provider strings are never projected. This heuristic cannot certify arbitrary secrets or private prose; audits remain metadata-only, and uncertain content requires a user-sanitized excerpt or handoff.

Both filter operations require a timezone-bearing date window or explicit all-history, count unknown dates, and expose pagination rather than silently capping history. Unknown dates are excluded from windows and included as null in all-history. Session activity can be a file modification time, so content recall discovers all workspace session metadata and applies the requested window to normalized turns. The skill documents Bash pipelines with `pipefail`, tracing disabled, and suppressed upstream stderr so raw agent-reader output cannot reach tool context. The helper reads the normalized JSON into memory; its output bounds are not streaming input limits. Tests use synthetic normalized JSON under `scripts/tests/test_recall_filter.py`, with documented shell recipes tested in `scripts/tests/test_recall_recipes.py`.

Recall and handoff share repo-root `.context/`, falling back to `~/.context/` outside repositories. Handoffs carry exact `Workspace:`, `Topic:`, and UTC `Updated:` headers. The shell lookup compares only the leading workspace and topic lines before opening bodies, and nonrepo filenames include a distinguishing workspace slug to avoid collisions. These local sanitized artifacts are never staged. Adding the bundled filter requires restowing the `agents` package; retained clients use the same installed shared-skill path.

### Markdown reading PDFs

The shared `md-to-pdf` skill owns a stdlib Python converter, a Typst template, and the `boox-note-max` reading profile. `stow/bin/.local/bin/md-to-pdf` exposes the same helper as a shell command. Pandoc and Typst plus the three Source font families are declared in the Brewfile. The default `tiempos-berkeley` pairing uses locally installed commercial fonts; `--fonts source` selects the open-source alternative. Mermaid CLI is pinned in the Mise configuration; its Node helper renders fenced diagrams through a temporary Chromium profile with page networking blocked. Conversion stays local; fingerprinted editions and PDF-hash checks protect annotated copies. See the [workflow reference](../stow/agents/.agents/skills/md-to-pdf/references/workflow.md) for dependencies, supported input, output records, and the sample document. Restow `agents` and `bin` when adding or removing skill files. Focused tests live in `scripts/tests/test_md_to_pdf.py`; actual rendering requires the installed compiler and fonts.

### Merged Pi settings (fragment → merge into app-owned JSON, not stowed)

Pi updates files under `~/.pi/agent/`. Stowing the live `settings.json` would let runtime writes churn the repo or atomically replace the symlink. The tracked `stow/pi/.pi/agent/settings.fragment.json` contains only portable preferences and is excluded by `stow/pi/.stow-local-ignore`. It owns every setting it declares, including the complete ordered `packages` and `enabledModels` arrays. `scripts/merge-pi-settings.sh` recursively merges fragment objects over live settings, preserves unrelated fields such as `lastChangelogVersion`, and replaces arrays rather than unioning them. Removing a package from the fragment therefore removes it from the merged settings; an empty array clears the list. Runtime edits through `/settings`, model defaults, or `pi install` and `pi remove` do not become repository policy automatically and can be overwritten by the next merge.

Both merge inputs must be readable regular files containing exactly one JSON object. The merger rejects symlinks, including dangling ones, directories, FIFOs, malformed JSON, empty files, multiple documents, and non-object values. Only an absent live settings file starts from `{}`; an absent fragment is an error. It writes a mode-644 temporary file in the destination directory and atomically replaces the target only after a successful merge and chmod. Failures leave existing live bytes intact. A byte-identical result leaves the target's inode and modification time unchanged. `setup.sh` runs the merge before stowing, and `restow-changed.sh` reruns it when the fragment or merge script changes.

To capture an intentional preference change, inspect only an explicit non-secret field allowlist, then edit and review the fragment diff. The approved capture fields are `theme`, `defaultProvider`, `defaultModel`, `defaultThinkingLevel`, `enabledModels`, `packages`, `defaultProjectTrust`, `enableInstallTelemetry`, `hideThinkingBlock`, `tuiMode`, `fullscreenExitOutput`, `subagents.modelScope`, `subagents.agentOverridesByProvider`, and `subagents.worktreeProvider`. For the Subagents routing fields, capture only reviewed provider/model IDs, scope flags, role descriptions, context preferences, and explicit tool/extension/fallback lists. Do not capture prompts, private paths, or arbitrary provider configuration. Preserve array order. Check values as well as field names: private package sources or model identifiers do not belong in git. Do not copy or print the whole live settings file, other runtime fields, or generated credential files. Expand this allowlist only after deciding that the new setting is portable and safe. Validate with the disposable-HOME tests before applying a focused merge. Recheck live portable values immediately before application so a concurrent preference change is not replaced by a stale capture.

Keep `auth.json`, `models-store.json`, sessions, backups, and the live `models.json` out of Stow and git. `auth.json` contains OAuth credentials. oMLX generates `models.json` and timestamped backups with its local server credential, so the local integration owns those files. Portable model overrides live in `stow/pi/.pi/agent/models.fragment.json`, excluded from Stow alongside the settings fragment. `scripts/merge-pi-settings.sh --models` applies that fragment to the live `models.json` using the same validation and recursive merge, with mode 600 because existing providers can contain credentials. Setup and auto-restow apply it without replacing unrelated provider fields. The fragment sets `github-copilot/gpt-5.6-sol` to a 272,000-token context window and enables request-controlled thinking for the configured Qwen model. In models mode, the merger also applies tracked `modelOverrides` to matching entries in a provider's generated `models` array, preserving other entries and fields. Pi's native overrides do not apply to those custom entries. Overrides for absent IDs remain declarations; the merger does not create model entries. If oMLX regenerates the file, rerun the models merge. Pi reloads it when `/model` opens. The models merge also restores mode 600 when the content is already identical, without replacing the file.

Fresh machines authenticate interactively with Pi's `/login`; setup only prints a reminder when the three subscription providers are missing.

The fragment keeps OpenAI Codex `gpt-6-astra` as the startup model with high thinking. Its six-entry cycle includes two Codex models, three Copilot models, and the exact oMLX `Qwen3.8-27B-oQ8e-mtp` model, without per-entry thinking suffixes. Direct Anthropic stays out of the cycle because Pi's third-party Anthropic OAuth uses paid extra usage rather than the included Claude Max allowance; it remains reachable through `/model`. Dark theme, hidden thinking blocks, fullscreen TUI, and transcript output on exit are deliberate preferences. Pi's keybindings preserve the agent-CLI convention of Enter for a newline and Cmd+Enter or Ctrl+S to submit.

`defaultProjectTrust: "ask"` keeps project resources opt-in. Without a saved trust decision or explicit override, non-interactive Pi ignores those resources rather than prompting. Trusted project settings can override global settings. `enableInstallTelemetry: false` disables the install/update ping, not update checks or extension network requests.

The declared package versions are `@upstash/context7-pi@0.1.2`, a local guarded build of `pi-subagents@0.65.0`, and `pi-web-access@0.27.0`. The Subagents package reference is `./local/copilot-delegation/node_modules/pi-subagents`, relative to the live settings directory. It deliberately avoids the unpatched npm package. Pi skips exact npm versions during package updates; the local build has a separate owner. [Agent tooling maintenance](agent-tooling-maintenance.md) records update ownership, bounded source-review findings, convergence limits and the repeatable offline description projection. Homebrew owns the Pi CLI; package bumps require explicit source review, fragment changes and separately approved installation/verification. The settings merger installs nothing. Subagents' project discovery is not demonstrably gated by Pi trust, its cleanup intervals do not promise erasure, and Web Access 0.27.0 authenticated PDF fetching must not be used pending a reviewed fix.

[Copilot local delegation](agent-tooling-maintenance.md#copilot-local-delegation) owns the guarded Subagents build and activation procedure. A Copilot root routes `scout` and `local-editor` to `omlx/Qwen3.8-27B-oQ8e-mtp`; other roles and providers retain same-provider routing. Both local roles use fresh context with no cloud fallback, ambient extensions, nested delegation, or resume. Scout has `read`, `grep`, `find`, and `ls`, and is limited to short, mechanical retrieval over one narrow source area or question. Merge-conflict analysis, cross-cutting synthesis, architecture decisions, and broad repository reconstruction remain on Copilot. Local editor also has `edit` and `write`, while the Copilot parent retains shell access, diff review, and validation. Only the root may launch local roles. Run one local role at a time and cap foreground local runs at three minutes.

Pi Web Access reads the separately stowed `~/.pi/web-search.json`. The tracked file never contains the Gemini API key: `geminiApiKey` is a command-backed credential source that calls the absolute 1Password CLI path with the existing item's stable ID. Pi Web Access resolves it only when a Gemini request needs it. Keep future provider credentials command-backed or environment-backed rather than pasting keys into this tracked JSON.

### Worktrunk task worktrees

[Worktrunk integration](worktrunk.md) documents the complete Pi, cmux, and Hunk workflow. Homebrew owns `worktrunk`; Fish loads its official wrapper. `setup-worktrunk.sh` seeds the app-owned user config copy-if-absent. The `worktrunk` Stow package links the native Pi activity extension, while `bin` exposes `wt pi` through `wt-pi` and `agents` owns its shared skill and Python helper. Approvals, task bindings, activity records, and Worktrunk runtime state stay outside Stow and Git.

Pi's fragment selects the guarded Subagents build's official `worktreeProvider: "worktrunk"`. Worktrunk only allocates those worktrees; Subagents retains setup, evidence, resume, and cleanup. The human launcher reserves that namespace and never bypasses the delegation guard. Capture only the reviewed allocator name through `subagents.worktreeProvider`, not private worktree paths or project hook commands. Conservative user defaults disable automatic commit, rebase, and removal during merge. The task cleanup helper retains branches and refuses active, dirty, or unreviewed ignored state.

### Software updates

[Software updates](software-updates.md) documents the range-limited daily Mise job, weekly read-only audit, local reports and rollout procedure. `scripts/setup-software-updates.py` wraps the existing Homebrew job to record attempts, successful completions and deferrals, and moves notifications to failure-only accounting. It preserves the generated updater and schedule.

### Homebrew automatic updates

`stow/homebrew/.homebrew/brew.env` sets `HOMEBREW_UPGRADE_GREEDY_CASKS=microsoft-teams`. Homebrew reads it on each invocation, including the existing daily 06:00 `brew autoupdate` job. The Teams-only exception includes it in upgrades despite the cask's `auto_updates true` flag. Other self-updating casks retain their default behavior.

Homebrew's Teams installer excludes Microsoft AutoUpdate, which [Teams on Mac needs for automatic updates](https://learn.microsoft.com/en-us/microsoftteams/teams-client-update#updating-teams-on-mac-devices). The exception makes Homebrew responsible for Teams updates. The scheduled job still requires AC power and may prompt for an administrator password through its existing GUI helper.

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

Keyboard Maestro (11.0.4) has a native `Unlock` system trigger but no *lock* counterpart. `stow/lock-watcher/` fills exactly that gap: `com.user.lock-watcher` (KeepAlive) runs `lock-watcher.applescript` — AppleScriptObjC under `/usr/bin/osascript` — which observes the `com.apple.screenIsLocked` distributed notification and fires the KM macro named in its `lockMacroName` property (`On Lock, Disable Proxy + Quit Feishin and Qobuz`) via `do script` — rename the macro and that property together. Fully event-driven: the process blocks in its run loop between locks (no polling, battery-clean). The macro's contents live in KM's own library, not this repo — KM's macro plist is app-owned runtime state, so it must remain outside Stow.

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

### System LaunchDaemons (root-owned, outside the stow tree)

Everything under `stow/*/Library/LaunchAgents/` is a *user* agent: it runs as the logged-in user, is symlinked into `$HOME`, is generated by `scripts/build-launchd-plists.sh`, and is checked by `scripts/lint-launchd-plists.sh`. Work that needs root can't use any of that — a **LaunchDaemon** must live at `/Library/LaunchDaemons/<label>.plist`, owned `root:wheel` and mode 644, which is outside `$HOME` and therefore unstowable. Neither the plist builder nor the linter looks there, so a daemon carries its own render-and-install script.

The pattern: the plist template is tracked at `launchd/<label>.plist.template`, a `scripts/install-<thing>.sh` renders it, `sudo install`s it, replaces the running definition (`launchctl bootout system/<label>` then `bootstrap system <path>` — launchd otherwise keeps the old one), and `setup.sh` calls the installer. The Apple-signed-`ProgramArguments[0]` rule from the AppleEvents section still applies by reasoning even though the linter can't enforce it here.

Current consumer: `com.user.iogpu-wired-limit` (`launchd/com.user.iogpu-wired-limit.plist.template`, `scripts/install-iogpu-limit.sh`) raises `iogpu.wired_limit_mb`, the ceiling on wired Metal allocations, so local MLX models can map more than the ~75% of RAM macOS allows by default. `sysctl` writes only the live kernel and macOS stopped reading `/etc/sysctl.conf` years ago, so without the daemon the value resets to `0` on every boot.

Two properties keep it from being a footgun, and both must survive any edit:

- **The value is derived from `hw.memsize`, never hardcoded.** A limit at or above physical RAM is the actual danger, so the script computes `RAM − reserve` (reserve = a quarter of RAM, capped at 6 GiB) and refuses to install anything that leaves less than 4 GiB. Hardcoding this machine's 59392 would be wrong on the next one.
- **It is a ceiling, not a reservation.** Nothing is taken from the system until a process actually asks for that much, so a high limit cannot wedge boot and there is no boot-loop risk to recover from. The residual risk is entirely at runtime: a model big enough to squeeze the OS into swap. `--uninstall` boots the daemon out and resets the live value to `0`; `--print` reports the computed value against what the kernel currently holds.

### AppleScript: `do shell script` returns CR, not LF

AppleScript coerces a shell helper's LF output to CR (classic Mac line endings). Several smart rules pipe a record's body through a Python helper and write the result back — `set newText to do shell script "…helper < tmp"` then `set plain text of theRecord to newText` — and without a modifier that round-trip silently rewrites the **entire body** as one CR-delimited line.

Nothing looks wrong in DEVONthink (it renders CR fine), but every downstream consumer that splits on `\n` then sees a note with **no lines and no headers**. That is how a duplicated daily note happens: `entity-dt-bridge.js`'s `merge_timeline` sees no machine event bullets to match against, treats every desired event block as new, and re-inserts each one as a duplicate. The same input wipes a body in `sync-markdown-h1.py`, which then emits only its H1.

Three rules:

1. **Every `do shell script` whose output is written back into a record must end with `without altering line endings`.** This applies to `set plain text of`, `set comment of`, and `set rich text of` sinks alike. The modifier also repairs the usual `if newText is not originalText` idempotency guard, which is otherwise always true (CR vs LF) and rewrites the record on every pass.
2. **Build note bodies with `linefeed`, never `return`.** AppleScript's `return` constant *is* CR, so a skeleton like `"# " & headingDate & return & return & "- "` births a CR-delimited note before any helper touches it. The daily-note skeleton is duplicated in four places (`create-daily-note.sh`, two smart rules, and the AppleScript embedded in `ingest-singlefile-html.py`) — change them together.
3. **Consumers must be tolerant, not trusting.** `splitlines()` splits on CR; `split("\n")` does not, and this pipeline's Python uses both. Anything reading a record body — or an AppleScript-built `--content` argument — must use `splitlines()` or normalize CRs first, and JXA must split on `/\r\n|\r|\n/` (`bodyLines()` in `entity-dt-bridge.js`). Every writer emits `\n`, so a body that picks up CRs some other way self-heals on its next edit.

`devonthink/tests/test_applescript_line_endings.py` enforces rules 1 and 2 across every AppleScript in the repo, including the ones embedded in Python and shell scripts, and asserts the underlying coercion still happens so the guard can't rot into a no-op.

### Pipeline log: one call, one record

`dt-watchdog.sh` scans the shared pipeline log and raises one macOS notification per new failure signature. It reads **records, not lines**: a line beginning with a TAB is folded into the record above instead of being matched on its own.

Both writers enforce that shape — `pipeline-log` (shell) and `pipeline_log.py` (Python). A message spanning several lines (a captured subprocess stderr, an exception traceback) keeps its first line as the record head, TAB-indents the rest, drops blank continuations, and caps the whole record at 3000 characters. The cap is not cosmetic: the append is atomic only up to PIPE_BUF (~4 KB), so an uncapped record lets concurrent smart rules interleave fragments.

The failure this prevents: one failed `mise upgrade` raised **four** notifications — one for each line carrying a level token — and not one of them contained the `caused by:` line that explained it, because that line has no level token for `FAILURE_PATTERN` to match. The scanner now lifts the first `caused by:` / `help:` / `hint:` continuation onto its parent's notification, so the alert names the reason and not just the failure.

Two rules for new code:

- Anything appending to this log **without** going through a writer must emit one physical line per record, or TAB-indent its continuations. A raw multi-line append is read as one failure per line.
- When a tool's output is captured into a log message, suppress its progress rendering (`mise` needs `--quiet`). A redraw burst is CR-separated **on a single physical line**, so resolve it to the text after the last CR — `sanitize_output` in `update-npm-tools.sh`. Folding CRs to newlines instead spends the entire line budget on progress frames and pushes the real error out of the record. The global updater now logs only projected outcomes and failure categories; its shell entrypoint retains this filter to bound console diagnostics.

`devonthink/tests/test_pipeline_log_records.py` covers both writers, the scanner's grouping, and the redraw collapse.

### Python script shebangs

Python interpreter management is split between mise and uv on purpose. mise (`stow/mise/.config/mise/config.toml`) provides the day-to-day `python3` on `$PATH`. uv (Brewfile) is reserved for scripts that declare third-party deps via PEP 723. There is no repo-wide `pyproject.toml` / `uv.lock` — each script stands alone.

Pick a script's shebang from this three-tier rule:

1. **TCC-sensitive** (script sends AppleEvents AND is invoked by a launch agent, either directly via the plist or transitively through a launchd-driven shell script that calls `"$SCRIPT" args`) → `#!/usr/bin/python3`. Apple-signed, stable TCC identity, stdlib only. If the work needs third-party deps, use the split-architecture pattern from the section above (sender stays `/usr/bin/python3`, parser is a `uv run --script` subprocess).
2. **Has third-party deps, not TCC-sensitive** → `#!/usr/bin/env -S uv run --script` with a PEP 723 inline `# /// script` block declaring `requires-python` and `dependencies`. Reference: `stow/bin/.local/bin/tagger.py`.
3. **Pure stdlib, not TCC-sensitive** → `#!/usr/bin/env python3`. Resolves through PATH to mise's Python.

For tier 1 scripts, even when the launchd plist provides the interpreter explicitly (`/usr/bin/python3 /path/to/script.py`), still write the shebang as `#!/usr/bin/python3` so direct invocation during testing uses the same interpreter as production rather than mise's.

### Git: verify HEAD before amending

Multiple Pi sessions can run against this repo at once, so HEAD may not be the commit you made earlier in your own session. Before any `git commit --amend`, run `git log -1` and confirm HEAD is the exact commit you intend to rewrite; if it isn't, make a new commit instead. To repair a wrong amend: `git reset --soft HEAD@{1}` restores the clobbered commit and re-stages only your changes.

### cmux: persistent Pi task workspaces

cmux owns local task workspaces and terminal surfaces. Its Stow package owns the `~/.config/ghostty/config` file that cmux reads for libghostty terminal settings; the standalone Ghostty app is not installed. [The cmux runbook](cmux.md) documents startup, exact task identity, Pi restoration, Hunk feedback, cleanup, and recovery.

Homebrew owns cmux and Hunk. `scripts/setup-cmux.sh` removes known transitional app-specific command overrides, installs the reviewed native Hunk feedback extension, and installs only cmux's official Pi hook when requested. Normal setup and the cmux restow handler pass that flag. The official hook owns cmux lifecycle state, notifications, and application-level conversation restoration. The repository's Worktrunk Pi extension separately owns task markers and verified feedback delivery. Runtime sessions, review state, task records, local backups, and cmux's app-owned preferences remain outside Stow and Git.

### tmux: test config on an isolated socket

Never run `tmux kill-server` (or `kill-session`) on the default socket for verification — a live server may be hosting a remote (Moshi) session, and killing it drops that client. Test config changes on a throwaway socket instead: `tmux -L test new -d && tmux -L test show -g <option> && tmux -L test kill-server`. Also note a running server never re-reads `tmux.conf`; if options look half-applied (e.g. `mouse on` but default `history-limit`), you attached to a pre-existing server rather than starting a fresh one.

### Audio tagging with mutagen

When writing MP4/m4a boolean atoms (`cpil`, `pgap`) with mutagen, assign a **bare bool** — `audio["cpil"] = True` — never a list. mutagen renders a list by truthiness, so `audio["cpil"] = [False]` silently writes `True`. `tagger.py` sets the compilation flag this way.

### Music library: artist identity comes from tags, not folders

Navidrome (and Music.app) build the artist and album from **tag values**; the folder tree is only where the bytes live. A library path is therefore never evidence of what a client displays — `Breakfast_ Unscrambled` on disk renders as `Breakfast: Unscrambled` because `sanitize()` mapped the `:` on the way out. When diagnosing a wrong artist or album name, read the tag.

Source metadata is inconsistent about artist case (Qobuz has shipped both `Charli xcx` and `Charli XCX`), and case is the one difference that forks an artist without looking like a change: on the NAS's case-sensitive ext4 a verbatim folder name creates a *second* artist folder, while Navidrome merges the two case-insensitively and then displays whichever spelling it scanned last — so the whole back catalog silently adopts a new import's casing. `music-organize.py` closes this at the single chokepoint every entry path shares (`riptag` in both modes and `import-album.py` all delegate filing to it): `artist_case_index()` resolves the incoming artist against existing artist folders case-insensitively, and `write_artist_case()` writes the library's spelling back into the `albumartist`/`artist` tags.

Two invariants there:

- **Recase only, never rename.** A field is rewritten only when it casefolds equal to the canonical spelling. That is what keeps `sanitize()`'s lossy path mapping out of the tags — `AC/DC` files into the existing `AC_DC` folder, but `"ac/dc" != "ac_dc"` so the tag keeps its slash. Any change that widens this comparison (normalizing punctuation, diacritics, `feat.` suffixes) starts writing folder names into tags.
- **The organizer files, it does not migrate.** When the library already holds several spellings it warns, picks the one with the most albums so repeated imports converge instead of alternating, and leaves the other folders alone. Consolidating pre-existing drift is `music-doctor`'s `artist_name_variant` finding, not a side effect of an import.

That `artist_name_variant` check groups artist *folders*, so it cannot see drift that exists only in tags. Nothing in the pipeline produces that state, but `tagger.py --album-artist` run by hand can.

### Private music NAS topology

The [private NAS configuration contract](music-nas.md) defines the version 1
`~/.config/music/nas.json` overlay and `MUSIC_NAS_CONFIG` override. The file is
private, not stowed, seeded, or generated by setup. `_music_nas.py` validates it
on demand with the standard library. Shell and Fish use a getter, never eval or
source. Remote workers receive an explicit mode-600 projection and the helper.
No credentials are copied. Library-root CLI overrides and topology-independent
commands do not require the private file. See the contract for migration,
validation, deployment, and launchd behavior.

### Music recovery and download ownership

`riptag-worker.sh` serializes downloads with a host-wide `/tmp/riptag-worker.lock`
directory. An existing lock always blocks a new worker. After an interrupted run,
confirm that no worker is running before removing the stale lock directory.
Each download uses a private `.riptag-run.*` directory inside its inbox and passes
that directory to streamrip with `-f`. The worker refuses zero or multiple album
outputs instead of selecting an unrelated recent folder. Failed run files and
logs remain in that directory.

Resume metadata stays on the download host under
`~/.local/state/riptag/sessions/<id>.meta`. `riptag --resume=<id>` uses local
metadata when present; otherwise it queries the NAS. `--local` never transfers a
NAS session. Legacy `/tmp/riptag-<id>.meta` sessions need manual recovery; the new
worker refuses to guess their download directory. NAS deployment and result
files use a fresh remote directory per invocation. The wrapper exposes only the
current session ID to `batch_rip`, which retains failed entries for retry.

Music-doctor deletes only truly empty directories. Covers, sidecars, archives,
and any other contents require review rather than recursive deletion.
Top-hits adoption checks the full unique chart-rank set and each expected tagged
artist, title, and duration under the year lock. Initial verified tagging stores
`TOP_HITS_IDENTITY` with the Qobuz recording ID, source artist credit, and expected
display tags, and persists the same `tag_identity` in the manifest before saving
audio. The manifest is authoritative; an audio atom alone cannot establish an
expected credit. Retagging and verification reload that identity so featured
credits survive repeated tagging. A changed recording ID requires redo. Legacy files
without provenance must match tags derived from known chart or recording credits;
unknown feature credits remain unverified and staging stays intact. These metadata
checks do not prove a recording's binary identity.

A verified fresh download also binds its progress record to the requested Qobuz
recording ID. Download reuse and assembly require that binding. Missing or changed
IDs do not become valid from title/duration similarity. Such staging is retained;
manually move the rank's recorded files and rank-directory audio aside before a
fresh download, or use redo for an already-filed album. Old progress without this
binding requires the same manual recovery, not an automatic migration.

Download skips and assembly both reverify current files, including another check
immediately before tagging. A previously tagged staged file can use exact
manifest-authoritative artist/title/duration only with the same bound recording
ID. This preserves interrupted-assembly retries for de-censored titles and other
supported display transformations. These checks do not lock out unrelated file
writers. Recorded completion is not current completion: missing files or unavailable storage block unattended work until
reconciled, without automatically redownloading a previously assembled album.

Runnability stores a versioned file identity with each analysis: device, inode,
size, nanosecond modification time, and a full-file SHA-256 digest. SMB can
report different change times and birth times for an unchanged file when it is
opened, so those timestamps must not participate in identity checks. Hashing
checks the open file and its path afterward to detect mutation or replacement.
The check runs before and after analysis and again before tag saving. It is not
a cross-process file lock; unrelated writers can still race a check and save.

Existing rows with a missing or metadata-only identity require one reanalysis
before tag writes. Identity checks read the full file, including when deciding
whether cached analysis is current, so nightly scans incur NAS read traffic even
for unchanged tracks. Per-track analysis failures remain stored for retry;
writing only considers successful analysis rows.

During tag writing, Ctrl-C requests a graceful stop. The command cancels queued
tracks, waits for active writes, and records their resulting identities before
exiting with status 130. Repeated Ctrl-C requests keep waiting rather than
interrupting database updates. Each successful write commits its identity
immediately. A forced process kill, crash, or database failure can still leave
stale rows; reanalyze the affected files before retrying a write.

### AeroSpace scripting: identify apps by PID, not name

When a script bridges AeroSpace and System Events (e.g. to hide or focus a specific app), key off the **PID**, not the app name. AeroSpace's `%{app-name}` and the System Events process name can disagree in case, so a name comparison silently mismatches: it fails to exclude the target when picking a sibling, and `set visible of (process whose name is …)` can no-op against the wrong identity. Use AeroSpace's `%{app-pid}` and hide/match via System Events `unix id` (`first application process whose unix id is <pid>`), which is namespace-safe. Reference: `scripts/aerospace-hide.sh`.

Context for that script: AeroSpace emulates workspaces by hiding/showing windows that all share one macOS Space, so a native Cmd-H on the *frontmost* app makes macOS activate the next global-MRU app — often on another workspace — and AeroSpace follows focus there, yanking you off your workspace. Hiding a *non-frontmost* app moves no focus, so the handler focuses a same-workspace sibling first, then hides the target by PID. AeroSpace exposes no hide/unhide callback, and `reload-config` (the only way to apply a gap change) re-syncs the visible workspace to the focused window's workspace — a no-op when they already agree, but the reason gap recomputes must not run while focus is mid-transition.

macOS hide/unhide is **not transparent to AeroSpace's layout tree**: an app's split arrangement is not restored when it is unhidden, even though its hidden windows keep appearing in `list-windows` output. Hiding is therefore viable only at `aerospace-hide.sh`'s deliberate single-app scale — do not bulk-hide background-workspace apps programmatically (e.g. to dodge the macOS 27 beta parked-window bounce, AeroSpace discussion #2155); it trades a flicker for lost layouts. When testing anything that touches hide/show or workspace transitions, verify against a workspace with a real multi-window split, not a single-window one — single-window round-trips hide this class of breakage.

### Agent comment controls

The comment and response policy has one generation source: `stow/agents/.agents/AGENTS.md`, exposed to Pi and the retained Copilot CLI adapter through their symlinks. Do not add output styles or lifecycle injections that restate it.

**Deterministic stripper (opt-in per repo).** `uncomment` (goldziher/uncomment, an AST/tree-sitter stripper, installed as the `github:Goldziher/uncomment` mise tool — never via npm, whose `uncomment-cli` package ships a stale committed 2.0.0 binary on every version) actually deletes the noise, but only in repos that opt in by carrying a root `.uncommentrc.toml` marker — so repos with deliberate, load-bearing comments (this one included) stay untouched until chosen.

- In-session: `stow/bin/.local/bin/agent-strip-comments` runs from the retained Copilot `agentStop` hook (`stow/copilot/.copilot/hooks/comment-gate.json`). Its Claude-format parsing remains compatibility code, but no Claude hook is installed. It gates on the repo-root marker, always exits 0 (never blocks a turn), and scopes the strip along two axes, because `uncomment` itself only knows how to strip a whole file. An incremental gate keeps no-edit turns cheap: the hook records the transcript byte offset it last ran at (`~/.local/state/agent-hooks/`) and exits before the full parse when the region appended since carries no Edit/Write tool_use — Claude Code transcripts only, since Copilot's edits never appear as those markers.
  - **Files.** Neither tool exposes a changed-file list, so it intersects git's changed files with the files the agent itself touched this session — exact edit targets parsed from a Claude Code transcript, or referenced paths from a Copilot one — so unrelated uncommitted work is never opened (with no transcript it falls back to all changed files).
  - **Lines.** Each file is handed to `uncomment-scoped` with a baseline, and only comments on lines the agent *added* over that baseline are removed. The baseline is the pre-edit content Claude Code records on every Edit/Write tool result (`originalFile`), so uncommitted work that predates the session is protected too; failing that, the file's HEAD blob; failing that (a new file), empty, which strips the lot. Without line scoping, touching one line of a file deletes every explanatory comment in it.

  The hook passes preserve patterns as flags: the CLI keeps TODO/FIXME/docstrings/lint pragmas by default, plus the hook's `IMPORTANT/NOTE/WARNING/SAFETY/SECURITY/keep`; a `remove_docs = true` line in the marker adds `--remove-doc`, which **genuinely strips docstrings** — opting a repo in with that line costs docstrings for real. The CLI parses `.uncommentrc.toml` natively but reads its own settings from a `[global]` table; the marker's bare top-level keys sit outside it, so the CLI ignores them (no error) and `remove_docs` takes effect only through the hook's flag. Keep the keys top-level.
- `stow/bin/.local/bin/uncomment-scoped` is the line-scoping wrapper: it strips a scratch copy via `uncomment-clean`, then merges back only the removals that land on added lines. `uncomment` deletes a comment-only line outright and rewrites inline-comment lines in place, never reordering or adding lines; equal line counts before and after therefore prove a 1:1 mapping judged per index, and otherwise the removals are located by diff, which blank lines make ambiguous — a deleted line is dropped only when agent-authored, an ambiguous hunk keeps the original text, and any merge that grows the file is discarded. A line whose exact trimmed text already appears in the baseline is never touched, so a comment that was merely moved or reindented survives. With an empty baseline the output is byte-identical to `uncomment-clean` with the target explicitly selected for trimming.
- `stow/bin/.local/bin/uncomment-clean` wraps `uncomment` and trims trailing whitespace only on explicit `--trim-target PATH` arguments, followed by `--` and the underlying flags/operands. Without explicit trim targets it only passes arguments through. The scoped caller names its scratch copy; preservation-pattern option values can never become trim targets. Markdown-family files retain significant hard breaks, and binaries are skipped with Perl's `-T` test. Cleaner failure propagates to the scoped caller, while the agent hook remains nonblocking and retains its old checkpoint for retry. The binary resolves via PATH → mise shim → install path.
- Commit/CI gate (the non-bypassable half): `comment-gate-init [repo]` drops the marker and prints portable pre-commit / lefthook / GitHub-Actions snippets that run `uncomment` plus the same Markdown- and binary-safe trailing-whitespace trim on staged files, then fail CI on residual noise. The snippets are self-contained (no dependency on this machine's `uncomment-clean`). Unlike the in-session hook these are **whole-file** — they have no session baseline to scope against, so they strip a staged file's pre-existing comments too. Only opt a repo in if that is what you want on every commit.
