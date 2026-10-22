# Software updates

## Ownership

Homebrew owns formulae and casks without native updaters. Its existing daily 06:00 job also owns Microsoft Teams through `HOMEBREW_UPGRADE_GREEDY_CASKS=microsoft-teams`. Microsoft AutoUpdate remains excluded. Do not enable blanket greedy upgrades.

Native app and CLI updaters retain their channels. App Store automatic updates own Store apps; `scripts/macos.sh` enables the system preference and the audit checks it. MacUpdater is no longer configured or used as an update authority.

Mise owns declared runtimes and portable global CLIs. Node stays on 24, Python on 3.14, Java on 26 and pnpm on 11. Changing these release lines requires review and an update to the updater's runtime policy. Mermaid and uncomment retain exact pins. PostHog is declared in Mise. npm remains bundled with Node. A separately upgraded Corepack requires a demonstrated project need and an explicit owner.

The existing daily job also owns unversioned npm Pi packages and the ordinary uv tools `osxphotos` and `qmk`. The locally guarded Subagents build, patched agent-reader, exact pins and shared skill adaptations retain their [reviewed-tool procedures](agent-tooling-maintenance.md#update-procedures-and-convergence).

## Daily Mise job

The existing `com.user.npm-tools-update` label and `~/.local/bin/update-npm-tools.sh` entrypoint run the `mise`, `pi` and `uv` jobs in sequence through `software_updates.py`. Each job has its own lock and last-success receipt. A failure in one does not skip the others. There is no additional scheduler. The wrapper runs at 03:15 and at login; each job is due when its last successful run is at least 20 hours old.

The updater runs commands from `$HOME`, clears inherited Mise overrides, and checks both `mise config ls --json` and each tool's source. It refuses to install anything if a project, system or alternate configuration is selected. A `mise.toml` in `$HOME` therefore blocks the job rather than silently widening its scope.

Each tool gets a separate range-aware `mise upgrade --quiet --yes --no-prune` call. The updater never uses `--bump`. Exact versions skip installation; unrecognized selectors require review. Node runs first, then the other runtimes and managed CLIs. An installation failure does not stop unrelated tools. A failed Node check defers pnpm and npm tools.

Unmanaged npm globals, except Node's bundled npm and Corepack, block a subsequent Node version change until they are migrated. This prevents a runtime upgrade from removing their commands from the active path. The audit also inventories retained Node installations, where an older global package can otherwise go unnoticed.

After each installation, the updater checks the active installation and probes known runtime and npm executables. Pinned npm tools get the same executable checks without an upgrade. Adding another npm tool requires an executable probe in `PROBES`; an unknown probe fails rather than claiming verification. Previous versions are always retained. Do not prune a runtime while an unmanaged CLI or another external path still depends on it.

The shared power gate defers work on battery. Deferral does not update the last-success timestamp. A process lock prevents overlapping runs and releases automatically when a process exits. Installations have per-command timeouts, and a timeout terminates the command's process group.

Run a catch-up pass with the normal power gate:

```sh
~/.local/bin/update-npm-tools.sh
```

`--force` bypasses both the power and recency gates. It does not bypass the lock, configuration checks, pins or unmanaged-global guard.

## Pi packages

Ordinary npm declarations in `stow/pi/.pi/agent/settings.fragment.json` are unversioned. The updater follows each package's public npm `latest` tag, rejects prereleases and skips downgrades. An exact version declaration opts a package out. Local package sources, including the guarded Subagents build, are never upgraded. Package order, resource filters, credentials and other settings remain unchanged.

`software_package_updates.py` installs updates while Pi sessions remain open. After a successful activation, it sends a macOS notification asking you to run `/reload` in each open Pi session. It never stops or reloads sessions automatically. Already-loaded code can remain old while later imports or asset reads use updated files; reloading picks up the new extension versions. Atomic directory exchange prevents partially installed package trees but does not prevent this temporary version mixing. `pi-agent-browser-native` requires a full Pi restart after updates because `/reload` can retain its previous compiled code.

Updates are installed into a private copy of the npm directory with lifecycle scripts, peer installation, audit and funding requests disabled. Public npm configuration is isolated from user registry configuration. The updater validates installed package identities and versions, rechecks declarations and npm project metadata, then uses macOS's atomic directory exchange. Failed staging leaves the active installation intact. The previous directory is retained at `~/.local/state/software-updates/pi-previous`, replaced after the next successful activation. To restore it, close all Pi clients, move `~/.pi/agent/npm` aside and copy that backup to `~/.pi/agent/npm` before restarting.

Pi's tracked `npmCommand: ["npm", "--ignore-scripts"]` also disables lifecycle scripts for native package installation, including fresh-machine startup. Scheduled updates do not run per-release source reviews or model calls. Manifest validation is not an extension behavior or privacy guarantee. The initial candidate releases passed disposable-HOME loader checks and the existing VCC, Anthropic transport, cmux and Autoresearch regressions. Authenticated Web Access PDF fetching remains prohibited pending a separately reviewed fix.

Run only the Pi job with `/usr/bin/python3 ~/.local/bin/software_updates.py pi --force`. This bypasses power and recency checks, not the update lock or staging validation.

## uv tools

The `uv` job upgrades installed `osxphotos` and `qmk` environments individually with prereleases disabled, retaining their original install constraints. It then checks the installed version and runs each tool's version probe. It does not install missing tools or call `uv tool upgrade --all`. Other uv tools remain review-owned until added to `UV_TOOLS` with an executable probe. In particular, agent-reader's local wheel and parser patches are preserved.

uv owns environment replacement; this job records before/after versions but does not back up uv environments. Failures are reported independently and do not stop another allowlisted tool's update. Run only this job with `/usr/bin/python3 ~/.local/bin/software_updates.py uv --force`.

## Weekly audit

`com.user.software-update-audit` runs on Sunday at 10:15. At login, it catches up if no audit has succeeded since the most recent scheduled Sunday run. Its command is:

```sh
/usr/bin/python3 ~/.local/bin/software_updates.py audit
```

Add `--force` for an immediate audit. The audit installs nothing. Homebrew automatic metadata updates, Copilot automatic downloads and `mas` automatic Spotlight indexing are disabled in its command environment. It uses `mas outdated --inaccurate`, never the mode that starts App Store downloads.

The local reports are:

- `~/.local/state/software-updates/report.md` provides the summary.
- `~/.local/state/software-updates/report.json` includes source coverage, actual versions, receipts, owners and retained-runtime details.

The audit covers Homebrew, global Mise declarations, unmanaged npm packages under installed Node versions, uv tools, every configured Pi package and App Store apps. Pi package paths come from the live declarations, so the guarded Subagents build follows its current local source. Extension reports include published stable and prerelease tags without changing installations. Unknown local package names are not sent to the public registry. It scans `/Applications`, `~/Applications` and their Utilities folders for independent apps. It reads public HTTPS Sparkle feeds where supported, filters release channels and system requirements, and leaves other checks unknown. Feeds with credentials, query parameters or private destinations are not fetched. A native updater still decides license eligibility and staged rollouts. Apps outside those directories are outside inventory coverage.

`brew outdated --greedy` supplies candidates, not installation instructions. The audit compares app bundle versions or a supported executable probe with the catalog before classifying an app. A current bundle with an old receipt is `stale-receipt`. Beta versions remain `alternate-channel`; numerically newer versions are `ahead-of-catalog`. Unclear comparisons remain `unknown`. Formula pins, disabled packages and reviewed tools have separate statuses. Missing metadata never establishes that an app is current.

App Store results mean only that its read-only catalog check reported a candidate or reported no update. Unindexed apps and private-account eligibility can require the Store screen. The report does not claim that the machine is fully current.

## Job state and notifications

`mise.json`, `pi.json`, `uv.json`, `homebrew.json` and `audit.json` in the same state directory record the last attempt, successful completion, failure or deferral. A failed or deferred run preserves the preceding success time. The reports show unknown historical dates until a job has run under this accounting.

Logs contain projected tool names and outcomes, not backend output, credentials or configuration dumps. Each job keeps a 128 KiB log and one rotated copy. Failure and overdue notifications are deduplicated. The existing pipeline logger and watchdog handle notifications when installed; otherwise the job uses a macOS notification. Successful runs are silent except when Pi packages actually change. That activation sends one native macOS `/reload` reminder, even when pipeline logging is installed. An already-current package check sends no reminder. A weekly audit cannot report its own failure to launch, so login catch-up and launchd status remain necessary checks if reports stop appearing.

`scripts/setup-software-updates.py` wraps the existing Homebrew LaunchAgent entrypoint with accounting. It preserves the calendar schedule, environment and generated updater, including AC gating and GUI administrator prompts. It sets the generated updater's recognized notifier mode to `never`; the wrapper owns failure notifications. Fresh setup passes `--no-notify` to Homebrew. The helper refuses unfamiliar entrypoints or notifier formats and refuses to reload a running update. Re-run it after deliberately regenerating Homebrew's job.

## Deployment and catch-up

Run setup and Stow only from the primary checkout. For a focused deployment after adding these files:

```sh
scripts/build-launchd-plists.sh
scripts/lint-launchd-plists.sh
(cd stow && stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" mise)
/usr/bin/python3 scripts/setup-software-updates.py
```

Reload the two Mise-package LaunchAgents after changing their definitions and checking that neither is running. Changing only the existing wrapper or its linked Python modules needs no launchd reload. `scripts/setup.sh` loads them through its existing changed-plist mechanism. Replacing a plist on disk does not change a definition already loaded by launchd.

Before a one-off update, record versions and review release notes. PostHog 0.18 changes sourcemap and Hermes uploads to event-based release attribution. Use `--release-mode symbol-set` when a project needs the preceding behavior, and verify its release coordinates before changing upload automation. Keep VPN and running-app updates in an agreed restart window. Record confirmed targets and explicit deferrals in the local `catch-up.json`, not in Git.

The bounded manual checks are administrator approval when needed, the App Store screen for account-specific updates, and one agreed Teams meeting-join test. Updating Teams does not establish that its meeting crash is fixed.

## Verification

```sh
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_software_updates.py
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_software_package_updates.py
/usr/bin/python3 -m unittest discover -s devonthink/tests -p test_pipeline_log_records.py
scripts/lint-launchd-plists.sh
```

Fixtures cover global-only selection, ranges, exact pins, missing tools, environment overrides, battery deferral, login recency, overlaps, partial failures, runtime-dependent command resolution, unmanaged-global guards, stale receipts, alternate channels, malformed bundles, public-feed restrictions, report failures and Homebrew job migration.
