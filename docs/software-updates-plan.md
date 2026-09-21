# Software update plan

Status: implemented. [Software updates](software-updates.md) documents the jobs, audit, safety checks and deployment. Machine-specific catch-up results and explicit deferrals live in `~/.local/state/software-updates/catch-up.json`.

## Recommendation

Use one update owner per category, with a small read-only audit to catch failures. Extend the existing jobs rather than adding an updater for each app. Keep deliberate version pins and release channels, but remove accidental gaps in ownership.

Do not enable blanket Homebrew `--greedy` upgrades. Several cask receipts are older than the actual applications, and LinearMouse is deliberately ahead of the stable cask on a beta release. A blanket upgrade could reinstall current apps or replace a beta with stable.

## Findings

- The daily Homebrew job works. Homebrew formulae were current at the last check.
- Teams lacked Microsoft AutoUpdate, while Homebrew skipped it as self-updating. The newly installed Teams version is current, and the Teams-only Homebrew exception is active. Microsoft AutoUpdate will remain uninstalled by preference.
- `stow/mise/.local/bin/update-npm-tools.sh` deliberately selects only `npm:` tools configured as `latest`. It excludes Node, pnpm, Python and Java, even though Mise manages them.
- PostHog CLI and Corepack are installed globally under Node but absent from the tracked Mise tool declarations. They are outside that scheduled updater.
- CleanShot, CrossOver, DEVONthink, Keyboard Maestro and Karabiner have self-updated successfully. Their old Homebrew receipts are not evidence of stale applications.
- The repository still configures MacUpdater preferences. Its vendor has stopped maintaining the product and verifying updates; it should not be part of the update strategy.
- The earlier inventory did not cover every Mac App Store app or independently installed application. Complete that check before calling the machine current.

## 1. Catch up once

Refresh available versions at execution time. The last verified candidates were:

| Software | Installed | Available |
|---|---|---|
| PostHog CLI | 0.8.3 | 0.18.3 |
| pnpm | 11.9.0 | 11.27.0 |
| Node.js | 24.18.0 | 24.21.0 |
| Python | 3.14.6 | 3.14.7 |
| NordVPN | 10.9.0 | 10.11.0 |
| LaunchControl | 2.10.5 | 2.11 |
| Tailscale | 1.102.1 | 1.102.4 |
| Copilot CLI | 1.0.83 | 1.0.85 |
| Corepack | 0.35.0 | 0.36.0 |

Before updating, record actual executable and app versions, installation owners, channels and configured ranges. Review release notes for compatibility changes, particularly the pre-1.0 CLIs. Schedule VPN and running-app restarts together so updates do not interrupt a meeting or remote connection.

Update runtimes first, retaining previous versions with Mise's `--no-prune` option. Then install or update their managed CLIs and verify executable resolution. Move PostHog into Mise before removing its unmanaged copy. Determine whether Corepack is needed by projects; if so, give it a declared owner rather than leaving it as an incidental global installation.

Keep npm bundled with the selected Node release unless a project requires a separately managed version. Treat npm 12 and Java 27 as separate compatibility decisions, not routine catch-up requirements. Keep LinearMouse on its existing beta channel.

Check Mac App Store updates and independently installed apps as part of this pass. Confirm versions after installation, not merely successful installer exit codes.

## 2. Establish category-level ownership

| Category | Update owner and policy |
|---|---|
| Homebrew formulae and casks without self-updaters | Keep the existing daily Homebrew job, AC gating and administrator prompt support. |
| Self-updating apps and CLIs | Use their supported native updater and preserve their selected channel. Audit for missed updates. |
| Microsoft Teams | Keep the existing `HOMEBREW_UPGRADE_GREEDY_CASKS=microsoft-teams` exception so the daily Homebrew job owns updates. Do not install Microsoft AutoUpdate. |
| Mac App Store apps | Use App Store automatic updates, with read-only auditing for anything pending. |
| Development runtimes and portable global CLIs | Declare them in Mise and update within their configured ranges through one daily job. |
| Exact pins, reviewed extensions and patched tools | Retain their existing review-led update procedures. Report available upgrades without applying them. |

If an app's native updater remains broken, repair it first unless that updater is deliberately excluded. Teams is the explicit exception because Microsoft AutoUpdate is unwanted. Keep its existing Homebrew configuration rather than adding a separate scheduled command. Require a verified need before adding further exceptions.

## 3. Expand the existing Mise job

Change the current npm-only updater into a global Mise updater. Run from the home directory and verify that only user-global configuration is selected, never a project's tool declarations.

Before widening its scope, replace unrestricted runtime selectors with compatibility ranges: Node `24`, Python `3.14`, Java `26`, and the existing pnpm `11`. This prevents `latest` or `lts` from silently crossing runtime release lines. Review newer release lines periodically rather than freezing them indefinitely.

Use Mise's range-aware upgrade behavior without `--bump`. Preserve exact pins, including Mermaid and uncomment. Keep Pi extensions and the patched agent-reader installation outside generic package upgrades, as required by their existing maintenance procedures.

Retain AC gating, bounded logs and per-tool failure handling. Add overlap protection and catch-up at login for overdue runs. Report failed updates and distinguish a battery deferral from a successful update. Verify Node-dependent CLIs still resolve after a Node upgrade before cleaning up old installations.

Primary files will be `stow/mise/.config/mise/config.toml`, `stow/mise/.local/bin/update-npm-tools.sh`, its LaunchAgent template, and the associated setup and maintenance documentation. Update existing jobs in place rather than leaving duplicate schedules.

## 4. Add one weekly audit

Produce a local report covering Homebrew, Mise, unmanaged global npm packages, uv tools and Mac App Store apps. Include independently installed apps when their supported update metadata is available; label unsupported checks as unknown rather than current.

Use `brew outdated --greedy` only to find candidates. Reconcile app bundles or executable versions before reporting them as outdated. Distinguish actual lag, intentional pins, alternate channels, stale receipts, disabled packages and unknown status. Ambiguous version comparisons must not trigger installation or downgrade.

For each update job, show the last attempt, last successful completion, and any failure or deferral. Notify on actionable failures or overdue checks, not on every successful run. Keep the report credential-free and reuse the existing logging and notification facilities where practical.

## 5. Verify and roll out

Add focused tests for range selection, exact-pin preservation, global-only scope, battery deferral, overlapping runs, partial failures and stale-receipt handling. Check launchd templates with the repository linter and exercise the updater against disposable fixtures before applying it locally.

Verify the deployed job definitions and run a bounded catch-up pass. Acceptance requires all confirmed catch-up targets to be updated or explicitly deferred, Teams to remain covered by the daily Homebrew job without Microsoft AutoUpdate, and the audit to distinguish current apps from stale Homebrew receipts. Retain previous runtime versions until dependent CLI checks pass.

Reserve manual checks for administrator approval, private-account update screens and one agreed Teams meeting-join test. Installation success alone does not prove the meeting crash is fixed.

## References

- [Mise upgrade behavior](https://mise.jdx.dev/cli/upgrade.html).
- [Microsoft Teams updates on Mac](https://learn.microsoft.com/en-us/microsoftteams/teams-client-update#updating-teams-on-mac-devices).
- [MacUpdater discontinuation](https://www.corecode.io/macupdater/).
- [Existing reviewed-tool maintenance procedures](agent-tooling-maintenance.md#update-procedures-and-convergence).
