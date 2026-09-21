# cmux migration plan

Status: Cutover implemented and approved. Focused synthetic coverage passed; the user authorized retirement without the optional two-task physical smoke. No remote writes were authorized.

## Goal

Use cmux for local terminal workspaces and the GitHub PR browser, with VSCodium for code navigation and manual editing. Remove Herdr from the normal cmux workflow while preserving guarded Pi delegation, task ownership, review feedback, and conservative cleanup.

The target is a practical two-app workflow. A one-window editor, unattended execution environment, general multiplexer framework, and replacement delegation package are outside scope.

## Execution and verification budget

The initial plan repeated failure testing across checkpoints and required broad verification of unchanged components. This revision limits verification to the migration's actual changes.

- Reuse the baseline, existing helpers, and existing tests. Refresh facts only when versions, configuration, or relevant source have changed. Do not repeat a full ecosystem survey at each checkpoint.
- Run only the suites affected by a change. Add focused regressions for new behavior and safety-sensitive failures. Existing tests cover unchanged behavior; do not rebuild their coverage through another integration harness.
- Use one combined two-task smoke test at C5. Earlier checkpoints get their focused checks, not separate full workflow rehearsals. Rerun the smoke only when a subsequent change affects its result.
- Use synthetic sessions and fixtures where practical. No paid model calls are required for routine wiring tests. No parallel reviewer council or delegated research by default. Add independent review only when changed ownership, deletion, or feedback-delivery code warrants it.
- Do not build a general GUI automation framework, crash-injection suite, or fresh-machine certification system for this migration. If a check needs new infrastructure or more than roughly 30 minutes of investigation to get running, report the gap and propose a narrower check before spending more time. This is an escalation point, not a reason to skip a safety requirement.
- Keep checkpoint updates to changed files, checks actually run, outcome, and blockers. Record versions and source identity once, updating them only when they change. No separate evidence dossier per checkbox.
- Preserve safety behavior even when verification is bounded. If duplicate-worker prevention, safe removal, or feedback targeting cannot be established, keep that operation unavailable or retain its old path until resolved. Do not make the whole cmux browser/terminal trial wait for optional UI parity.

### Minimum verification by risk

| Change | Sufficient verification |
| --- | --- |
| Startup or configuration | Validate the changed config, check preservation/idempotence if a setup helper changes, and open a native cmux terminal. |
| Task creation and reopening | Existing lifecycle tests plus focused checks that reopening reuses the task and a partial failure cannot create another writer. |
| Removal | Focused refusal checks for active/ambiguous ownership and dirty/ignored data, plus branch retention on successful removal. |
| Feedback delivery | Verify the selected idle recipient gets one delivery; wrong/busy/blocked targets are refused; failed or uncertain delivery retains comments without automatic resend. |
| Unchanged delegation package | Confirm package and policy settings remain unchanged and identify Herdr-only UI dependencies. Rerun provider/loader tests only if their implementation or inputs change. |
| Whole workflow | One two-task smoke and one short, agreed user trial. |

## Current baseline

Observed during planning on 2026-09-19:

- cmux 0.64.25, Pi 0.85.1, Worktrunk 0.78.0, and Hunk 0.22.0 are installed. These are observations, not version locks.
- Ghostty launches Herdr. The temporary cmux override at `~/Library/Application Support/com.cmuxterm.app/config.ghostty` also launches Herdr after clearing inherited Herdr pane/server context. Its isolated two-client attach test passed; native cmux integration remains unimplemented.
- The repository-owned `wt pi new/open/list/remove` helper uses Herdr tabs and panes. It already checks approvals, task ownership, duplicate writers, process identity, and removal safety.
- Hunk feedback uses the locally patched Herdr plugin. It explicitly selects a recipient, refuses unsafe delivery, submits to Pi with Ctrl+S, and retains comments after failure.
- The native Worktrunk activity extension aggregates Pi lifecycle state and records Herdr pane identity. Do not install a second writer for the same markers.
- The guarded local `pi-subagents` package supports ordinary headless delegation without Herdr. Its external inspector and project-pane actions are Herdr-specific.
- The installed Subagents managed-write-worktree cleanup restriction remains active. Its preservation candidate is not installed or activated. See [the maintenance record](agent-tooling-maintenance.md#copilot-local-delegation). Candidate deployment is a separate task, not a prerequisite for the native cmux trial.

## Ownership and constraints

| Concern | Owner |
| --- | --- |
| Windows, task workspaces, terminals, and PR browser | cmux owns them. |
| Agent execution, conversations, and delegation | Pi and the existing guarded Subagents build own them. |
| Human task worktrees | Worktrunk allocates them; the existing launcher retains ownership and validation. |
| Delegated worktrees | Subagents retains its namespace, evidence, and lifecycle restrictions. |
| Local review and feedback | Hunk holds comments; an explicit delivery integration targets the recorded Pi session. |
| Manual editing | VSCodium remains available. |
| Acceptance and publication | Repository checks, CI, and the user's current-turn authorization control them. |

Preserve these constraints throughout:

- Keep one writable worker per worktree, the existing provider/role/tool restrictions, and the distinction between human launch commands and guarded agent launch APIs.
- Keep Pi's Enter/Shift+Enter newline and Cmd+Enter/Ctrl+S submission behavior. Defer general keybinding redesign; fix only demonstrated workflow conflicts.
- Keep Ghostty/Herdr available during the trial. Never stop the default Herdr server or unrelated workers for testing.
- Preserve unrelated uncommitted changes. Apply setup and Stow only from the primary dotfiles checkout after integration.
- Prefer supported integrations and existing helpers. Review the exact executable integration before installing it; check stable and prerelease fixes before patching dependencies. Do not replace the guarded Subagents package as part of terminal migration.
- Use copy-if-absent or a narrowly owned merge for app-owned configuration. Keep profiles, credentials, sessions, task records, and backups outside Git. Inspect live settings through non-secret fields only.
- Retain explicit approval for remote writes, including writes performed by project hooks. Worktrees are not sandboxes, and local hooks are not unbypassable acceptance gates.

## Progress tracker

Checkpoints are work units, not separate certification projects. Check an item when its stated result is demonstrated; record unresolved limitations plainly.

| ID | Checkpoint | Dependency | Status |
| --- | --- | --- | --- |
| C0 | Confirm the changes needed. | Use the existing baseline. | Complete. |
| C1 | Start Fish and Pi directly in cmux. | C0 must be complete. | Complete; native hook and terminal configuration installed. |
| C2 | Adapt human task lifecycle. | C1 and the task identity format must be ready. | Complete with focused synthetic coverage. |
| C3 | Adapt Hunk feedback delivery. | C1 and C2's task identity format must be ready. | Complete with focused synthetic coverage. |
| C4 | Resolve Herdr-only Subagents UI actions. | C0 and C1 must be complete. | Complete; unused pane/inspector parity deferred by cutover approval. |
| C5 | Run the combined smoke and user trial. | C2 and C3 must work; C4 needs an accepted disposition. | Physical smoke waived by explicit cutover approval; gap retained. |
| C6 | Retire Herdr-specific setup. | C5 and retirement approval are required. | Complete. |

C2 and C3 may share implementation work where that reduces duplication. Optional C4 UI features may be explicitly deferred without blocking the core workflow. The browser/terminal trial can begin after C1; full migration waits for the required task and feedback paths.

## C0. Confirm the changes needed

- [x] Check for drift from the recorded baseline and inspect only the relevant Herdr dependencies, task schema, cmux Pi integration, and available Hunk integration. Reuse the implementation map below.
- [x] Choose configuration ownership and a backend-tagged task identity format. cmux's official Pi hook owns application restore and cmux lifecycle state; the Worktrunk extension owns task markers and verified feedback delivery.
- [x] Back up only live files about to change. No live file was changed during implementation; the setup helper creates a local backup when explicitly applied. Inventory active tasks and unsent comments when moving or retiring them.

Done when the first implementation slice and its rollback are clear. Do not turn C0 into another stack evaluation or package audit.

## C1. Native cmux startup and Pi integration

- [x] Start cmux terminals with Fish while clearing stale Herdr variables at that boundary. The standalone Ghostty app and transitional cmux override were retired; cmux owns the shared libghostty settings.
- [x] Review and install only the required Pi integration, not hooks for every detected agent. cmux owns session capture, application restore, and lifecycle display; Worktrunk owns task markers and feedback delivery.
- [ ] Check shell startup with and without inherited Herdr context, Pi's input bindings, status transitions, and socket access from a legitimate cmux descendant. Refuse unavailable socket access rather than weakening its policy.
- [ ] Document conversation recovery separately from process persistence. Review browser automation access before GitHub login; retain the existing agent-browser workflow and use fictional/local pages for automation checks.

Done when a native cmux terminal runs Pi without Herdr and the selected status/resume integration has a clear, verified owner. Resume safety is also covered by C2's task-reopen tests. No browser compatibility suite or app-restart harness is required here.

## C2. Human task lifecycle

Adapt `wt-pi`; do not build a second task manager or a general backend framework.

- [x] Add backend-tagged bindings with canonical worktree/repository identity, branch, returned cmux workspace/surface IDs, and the intended Pi session. Never locate a task solely by its title or current focus.
- [x] Inventory existing Herdr tasks before retirement. No task bindings required migration.
- [x] Preserve existing branch, approval, allocation, bootstrap, launch-lock, and human-only checks. Complete prerequisites before launching Pi. Use foreground helpers where cmux socket ancestry requires them.
- [x] Reconcile `new`, `open`, and cmux resume so repeated or failed launches cannot create a second writer. Preserve partial resources for recovery, process-start identity, and refusal on stale IDs or inaccessible inventory.
- [x] Preserve conservative removal: active sessions/terminals/services, unsent review feedback, dirty/untracked files, and unexplained ignored files block cleanup. Keep branches, protect `pi-subagents/`, and retain existing merge/signing/hook policy.

Done when task creation and reopening use cmux, repeated opening reuses the verified task, and cleanup retains work on uncertainty. Extend existing tests only where backend and identity changes affect those guarantees. Remove obsolete Herdr coverage after retirement; do not duplicate it wholesale for cmux.

## C3. Hunk review and feedback

Use Hunk's official APIs and any suitable existing cmux integration before writing a bridge. The Herdr plugin's safeguards remain the behavior to preserve.

- [x] Keep review scope explicit: working tree, staged changes, latest commit, and whole branch are different. Preserve reviewed/tested state and include dirty/untracked changes separately when a branch diff excludes them.
- [x] Bind feedback to the selected task's Pi session and recheck identity/readiness when sending. Refuse missing, changed, busy, blocked, or unknown recipients. Keep sending explicit and exclude agent annotations.
- [x] Use Pi's native message API instead of terminal keystrokes. Retain comments on failed or uncertain delivery; never blindly resend or clear them based only on a successful socket write.
- [x] Keep content-sensitive duplicate prevention and the existing handoff receipt for unresolved findings before clearing comments. Task closure must not silently discard unsent feedback. Local comments remain separate from GitHub replies.

Done when one synthetic comment reaches the intended idle session once and focused refusal/failure tests preserve it otherwise. Reuse scope tests and parameterize related failure cases; do not create a separate end-to-end scenario for every error condition.

## C4. Subagents compatibility

- [x] Confirm the guarded package and policy settings remain unchanged. Retain terminal-independent FleetView and normal headless delegation.
- [x] Defer unused project-pane and external-inspector parity under the user's cutover approval rather than implementing unused UI.
- [x] Keep protected launch paths and cleanup restrictions intact. No guarded package, provider policy, or loader change was made.

Done when ordinary delegation is preserved and Herdr-only actions have an accepted disposition. Unchanged provider routing does not require another model matrix or SDK-loader audit. If a proposed change touches the guarded package, provider policy, or loader, stop and scope that work separately rather than expanding this migration silently.

## C5. Combined smoke and cutover

Use existing tools and the smallest reusable fixture needed. Start ancestry-dependent checks from a genuine cmux terminal; do not relax socket access to accommodate the runner.

- [ ] Run one two-task smoke: create tasks, open Pi and Hunk, send a synthetic comment to the selected task, reopen without duplication, and remove only a stopped, clean task while retaining its branch. The second task must remain unaffected. Reuse focused C2/C3 results for failure cases instead of replaying their whole matrix here.
- [ ] Check that a saved session resumes the intended conversation without another writer. Use supported isolated facilities if available; otherwise agree on one safe reopen check when no unrelated cmux work will be interrupted. Do not build crash/sleep automation or quit an active app without approval.
- [ ] Conduct one agreed PR-response trial, capped at about ten minutes of user-assisted checks: GitHub login, discussion beside Pi, VSCodium editing, physical input/focus, and one notification. No test reply is posted without separate authorization.
- [x] Record outcome and remaining gaps. The user explicitly authorized cutover and retirement without the optional physical smoke; the unrun physical checks remain documented.

Done when the actual two-app workflow works and the high-risk checks pass. Fresh-machine certification, arbitrary crash recovery, sleep/wake matrices, and broad browser testing are deferred. Those limits do not permit unsafe cleanup or feedback delivery.

## C6. Retirement and maintenance

- [x] Account for remaining Herdr tasks and unsent reviews. The inventory found no task bindings; the only live Herdr agent was the session performing retirement.
- [x] Disable obsolete Herdr integrations and update setup/restow so they are not recreated. Historical application state, review receipts, and package caches remain outside Git.
- [x] Update the affected skills and operational docs. Herdr and standalone Ghostty are retired rather than retained as fallbacks.
- [x] Document the accepted startup, task commands, update ownership, recovery steps, and remaining limits. Run relevant setup/restow regressions only for the paths changed here.

Done when normal setup and everyday work no longer require Herdr, without abandoning old tasks or deleting their records.

## Implementation map

| Area | Existing files |
| --- | --- |
| Startup and configuration | `stow/cmux/.config/ghostty/config` owns libghostty terminal settings. `scripts/setup-cmux.sh` removes transitional overrides, installs the native Hunk extension, and installs the official Pi hook. |
| Task lifecycle and activity | `stow/bin/.local/bin/wt-pi`, `stow/agents/.agents/skills/worktrunk/wt_pi.py`, and `stow/worktrunk/.pi/agent/extensions/worktrunk.ts` own the current behavior. |
| Hunk feedback | `scripts/cmux/worktrunk-feedback.ts` is the native Hunk extension. The obsolete patched plugin and its installer were removed. |
| Delegation | `stow/pi/.pi/agent/settings.fragment.json` and the guarded local package remain unchanged by default. |
| Setup and docs | Change affected paths in `scripts/setup.sh`, `scripts/restow-changed.sh`, relevant setup helpers, shared skills, and the linked operational docs. |

Reuse `test_worktrunk.py`, `test_worktrunk_activity.mjs`, Hunk scope/delivery tests, and configuration/restow tests according to the diff. A small cmux smoke script is appropriate if needed, not a new test framework.

Before migration commits, inspect the intended diff, run focused checks, `git diff --check`, and the staged betterleaks scan. Include only migration changes. Publication remains separately authorized.

## Rollback

1. Pause new launches and preserve unsent comments and task receipts.
2. Stop only workers/services being moved, with approval, and confirm no duplicate writer remains.
3. Restore migration-owned settings without overwriting later user preferences.
4. Reinstalling the retired terminal stack requires an explicit rollback from repository history; preserved local application state is not an active configuration.
5. Retain worktrees and branches on uncertainty. Do not downgrade the guarded package or delete task state as a shortcut.

Validate changed rollback logic through focused tests. A second full reverse-migration rehearsal is not required.

## Progress notes

- Verification was narrowed to changed behavior and existing regression suites. Duplicate test matrices, unchanged-provider recertification, and new platform-testing infrastructure were excluded.
- The implementation rechecked installed and pinned sources at cmux `0.64.25`, Hunk `0.22.0`, and Pi `0.85.1`. It added backend-tagged Worktrunk bindings, exact workspace/surface/session reuse, conservative cleanup, native Hunk feedback, delivery receipts, and focused synthetic tests.
- The retirement inventory found zero Herdr-owned task bindings and one live Herdr agent: the session performing the cutover. No other task needed migration. Historical application state and package caches were preserved.
- Cutover removed Herdr and standalone Ghostty from installation declarations, removed their setup, Stow packages, patched Hunk bridge, tests, and operational documentation, installed cmux's Pi hook and Hunk extension, removed the transitional cmux command override, and uninstalled both Homebrew packages. The current Herdr-hosted process may continue only until this session exits.
- Terminal-independent FleetView and headless delegation remain. Herdr project-pane and external-inspector parity was explicitly deferred as unused. The installed Subagents package, guard, model/provider policy, and cleanup restriction remain unchanged.
- The user explicitly authorized retirement without first running the optional physical two-task smoke. Physical key delivery, notifications, authenticated browser behavior, and sleep/wake remain recorded coverage gaps.

## References

- [Existing Worktrunk integration](worktrunk.md).
- [cmux task-workspace runbook](cmux.md).
- [Guarded package ownership and restrictions](agent-tooling-maintenance.md).
- [cmux documentation index](https://cmux.com/llms.txt), [configuration](https://cmux.com/docs/configuration), [session restore](https://cmux.com/docs/session-restore), and [CLI/socket API](https://cmux.com/docs/api).
- [Worktrunk documentation index](https://worktrunk.dev/llms.txt) and [cmux recipe](https://worktrunk.dev/tips-patterns/).
- [Hunk agent workflows](https://github.com/modem-dev/hunk/blob/main/docs/agent-workflows.md).
- [Pi documentation](https://pi.dev/docs/latest). Use installed-version docs for implementation details.
