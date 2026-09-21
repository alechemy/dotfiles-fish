# Worktrunk with Pi, cmux, and Hunk

Worktrunk owns worktree allocation and navigation. cmux owns persistent human task workspaces and terminal surfaces. Pi owns implementation and delegation. Hunk owns local review feedback. Use one cmux workspace per interactive task worktree. The retirement inventory found no remaining legacy task bindings.

## Everyday workflow

From a Fish shell in a native cmux terminal whose cwd is inside the repository:

```fish
wt pi new feature/auth
wt pi new fix/login --base main --no-focus
wt pi open feature/auth
wt pi list
```

`new` allocates a fresh branch and sibling worktree, creates a named cmux workspace, and starts Pi in its initial terminal surface. It leaves the source checkout and any existing changes untouched. Type the task into Pi normally. `open` selects the exact recorded workspace, reuses its live Pi session, or resumes the recorded Pi conversation in its recorded terminal. Repeated opening does not create another writer.

The helper requires the calling cmux surface to be real and the shell cwd to belong to the same repository. It stores canonical repository, worktree, branch, workspace, surface, and Pi session identities under the worktree Git directory. Titles and focus are display state, not identity. Startup trust and login dialogs remain yours to answer. A startup failure retains the allocated worktree and any partial cmux binding; stale or incomplete identities fail closed for inspection instead of creating another writer.

`wt switch` opens Worktrunk's picker. `wt switch <branch>` navigates to an existing worktree, and `wt switch -` returns to the previous one. For an ordinary worktree outside the task launcher's ownership, `wt switch <branch> -x pi` launches Pi in the current shell pane. The launcher does not adopt or delete those worktrees.

Sibling paths use `{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}`. Existing repositories stay where they are. Work repositories retain the `~/Work` layout. The picker uses noninteractive Delta output; Git's normal pager remains Hunk.

The [Fish shortcuts](agentic-coding-workflow.md#fish-shortcuts) cover task launch,
reopening, navigation, Hunk review, integration, and guarded removal.

## Review, integrate, and remove

Open Hunk in the intended task worktree and save human inline comments normally. Ctrl+Shift+F invokes the native `worktrunk-feedback` Hunk extension. It snapshots the review, selects only `source: user` notes, resolves their current file and line anchors, and sends them to the one recorded idle Pi session for that worktree. Pi acknowledges receipt through a private per-session Unix socket before Hunk removes any comment. Busy, disconnected, stale, multiple-recipient, changed-review, and uncertain-delivery cases retain comments and fail closed. Delivery IDs are recorded below the worktree Git directory, so an acknowledged retry is idempotent.

The default Hunk shortcut uses automatic scope: dirty or untracked changes select
working-tree mode; a clean branch with a resolved base and commits ahead selects branch
mode. Otherwise it falls back to working-tree mode. Ctrl+; then Shift+B explicitly
selects committed branch review. Staged, working-tree, latest-commit, and branch scopes are
separate. Verify the displayed base against the intended integration target; invalid
or missing-base fallback is not whole-branch review. For an explicit comparison:

```sh
hunk session reload --repo /path/to/task-worktree -- diff release/integration...HEAD
```

The committed comparison excludes staged, unstaged, and untracked changes. Review
those separately when they belong to the task. Follow `hunk skill path` for live
interaction and the shared code-review scope rules for Git-only review.

Before sending feedback or completing a task, keep a local
[coding-task receipt](../stow/agents/.agents/skills/handoff/SKILL.md#coding-task-review-receipt)
with the target, base/merge-base, reviewed HEAD, scope/exclusions, tested dirty state,
validation, unresolved findings, session/run references, service ownership, and next
action. Recheck it after edits or rebases. Record unresolved conclusions before a
successful send clears comments. Reference existing artifacts; do not copy private
transcripts or entire comment threads.

Prepare commits with Pi or Git under the existing signing, message, and secrets-scan rules. The seeded configuration makes `wt merge` a clean-tree, fast-forward-only local integration that keeps the task worktree. Explicit flags preserve that behavior even after editing your preferences:

```sh
wt merge --no-commit --no-rebase --no-remove
```

Rebase separately with `git rebase --no-update-refs` when needed. Your global `rebase.updateRefs=true` otherwise also affects Worktrunk's rebase command. Worktrunk merge does not fetch or publish by itself, but configured hooks can. Inspect hooks and retain the current-turn approval requirement for all remote writes.

Send Hunk comments, stop task processes, and close the task workspace. From another worktree's cmux shell:

```fish
wt pi remove feature/auth
```

Removal checks the launcher's ownership record, live interactive Pi records, exact cmux inventory, unsent or uncertain review delivery state, current-user process working directories, Git status, and ignored files. It refuses ambiguous or active work. If ignored files remain, inspect and preserve anything needed before using `--discard-ignored`. The branch is always retained, including unmerged commits. Delete it separately after confirming integration or intentional discard.

The helper never kills a process or closes a workspace during removal. Its checks cannot prove that no external writer will race the operation, or find every process that opened a file and then changed directory. Direct Worktrunk/Git removal bypasses these extra checks. Keep one writer per checkout and use the helper for task cleanup.

## Native Pi activity

`stow/worktrunk/.pi/agent/extensions/worktrunk.ts` reports native Pi lifecycle events through the bundled `wt-pi` helper. It uses `agent_settled`, not `agent_end`, so automatic retries and queued continuations do not announce a settled session early.

| Marker | Meaning |
| --- | --- |
| 🤖 | An interactive Pi session is working. |
| 💬 | Interactive Pi sessions are idle. |
| ❗ | An interactive Pi session has a blocking extension UI prompt. |

Blocked takes precedence over working, then idle. Each extension instance has a random identity. The helper serializes per-worktree updates and aggregates surviving sessions, so one session's shutdown cannot erase another's state. It identifies processes by PID and process start time, which avoids treating sleep as session exit or PID reuse as continued activity. Native records carry exact workspace, surface, Pi session, process, and private feedback receiver identities. Reopening exempts at most one record that matches the complete task binding; extra sessions or missing identity block reuse. A 30-second heartbeat refreshes status. Shutdown removes only that instance's record. `wt pi list` prunes dead records before running `wt list`.

A manual Worktrunk marker that differs from the integration's last marker takes precedence. Clearing a manual marker allows the next lifecycle event or heartbeat to resume activity reporting. A SIGKILL can leave a marker until the next update or `wt pi list`. Changing the branch beneath a live Pi session requires quitting that session before starting another; shutdown also clears owned markers after a detached checkout.

The extension ignores print, JSON, and RPC modes. cmux's official Pi hook owns cmux lifecycle state, notifications, and application restart restoration. The Worktrunk extension separately owns worktree markers and verified Hunk delivery; Subagents remains authoritative for headless child activity. Each marker subprocess has a two-second timeout; failures warn once per failure streak and do not fail the Pi turn. It sends no prompts, dialog titles, task descriptions, or transcript contents. No model calls are made for status or commit messages.

Worktrunk 0.77.0's advertised Pi plugin is an Oh My Pi hook. Its installer writes `hooks/pre/worktrunk.ts`, which native Pi does not discover. This repository's native extension is independently owned; do not install the upstream Oh My Pi hook as a replacement.

## Subagents

**Current restriction:** Do not use managed Subagents write worktrees that can enter
automatic cleanup. The isolated W01 candidate passed its preservation tests, but the
installed package has not been replaced. Full SDK loader verification and separately
approved installation/activation remain pending. Candidate test success alone does
not lift this restriction. Human `wt pi` task worktrees are separate: they retain
branches and refuse dirty checkout removal.

Pi's settings fragment explicitly selects `subagents.worktreeProvider = "worktrunk"`. The guarded Subagents build already implements this allocator; no delegation adapter or package patch is added.

Subagents calls `wt switch --create ... --no-cd --no-hooks --format json` and verifies the returned branch, commit, repository, and path. Its `pi-subagents/` branches remain Subagents-owned. Setup hooks, model/provider restrictions, handoff evidence, resume, and cleanup stay under Subagents. Installing Worktrunk no longer changes allocation implicitly through the upstream `auto` setting.

The human launcher rejects the `pi-subagents/` namespace and requires a separate task ownership record for reopening or removal. It also refuses launches from agent-marked environments. Agents use Subagents `project.open` for an explicitly requested independent visible session, or managed delegation for child work. The launcher guard is a misuse check, not a sandbox against an agent deliberately clearing its environment.

## Configuration and installation ownership

Homebrew core owns `worktrunk`; the existing Homebrew update schedule owns binary upgrades. Homebrew also owns cmux and Hunk. No new tap, server, LaunchAgent, LLM provider, or always-on status service is installed.

`stow/fish/.config/fish/conf.d/worktrunk.fish` loads the installed binary's Fish wrapper in interactive shells. Homebrew supplies Fish completions. `wt-pi` is on PATH through the `bin` package and therefore available as Worktrunk's custom `wt pi` subcommand. Its stdlib Python implementation and instructions live in the shared `worktrunk` skill. Python must be at least 3.9.

`scripts/setup-worktrunk.sh` copies the config seed only when `${XDG_CONFIG_HOME:-$HOME/.config}/worktrunk/config.toml` is absent, with mode 600. Existing regular files remain app-owned. Symlink targets are rejected. Worktrunk atomically saves this file during its own preference updates, so it must not be stowed directly. Seed edits affect new machines, not existing preferences.

Setup and the restow dispatcher own the integration. To apply it explicitly from the primary dotfiles checkout:

```sh
stow --restow --no-folding --dir=stow --target="$HOME" worktrunk fish bin agents cmux
scripts/setup-worktrunk.sh
scripts/setup-cmux.sh --install-pi-hook
scripts/merge-pi-settings.sh
```

The `cmux` Stow package owns the shared libghostty terminal settings and starts Fish directly. `setup-cmux.sh` backs up and removes known transitional app-specific command overrides, refuses files containing unrelated settings, and copies the reviewed Hunk extension from `scripts/cmux/worktrunk-feedback.ts`. Normal setup installs cmux's official Pi hook. Reload or restart existing Pi and Hunk sessions to discover their extensions. Custom Pi agent directories require installing the extension into that profile separately, as with other Stow-managed Pi resources.

Approvals remain in Worktrunk's local `approvals.toml`. The helper's task binding and activity records live beneath each worktree's Git directory in `wt-pi/`; its launch lock lives in the common Git directory. Worktrunk owns its own Git-config state, caches, logs, and trash. None of these files belongs in Stow or Git. They may contain private paths or branch names even though activity records contain no conversation text.

Project preparation belongs in reviewed `.config/wt.toml` commands. The launcher refuses unapproved or stale project commands before allocation and checks the destination afterward. It never passes `--yes`. Worktrunk approval is independent of Pi trust; ordinary Worktrunk commands can continue without project hooks when approval is declined. Avoid global copying of ignored files or hooks that launch more agents.

## Dotfiles safety

`setup.sh` refuses linked checkouts. The restow worker and the three restow-triggering Git hooks skip them. Hook-level checks also protect old task branches whose worker script predates this guard. Experimental dotfiles stay isolated until integrated into the primary checkout. Never run Stow directly from a task worktree.

## Verification

```sh
python3 -m unittest discover -s scripts/tests -p 'test_worktrunk.py'
python3 -m unittest discover -s scripts/tests -p 'test_cmux_setup.py'
python3 -m unittest discover -s scripts/tests -p 'test_restow_changed.py'
python3 -m unittest discover -s scripts/tests -p 'test_merge_pi_settings.py'
node --test scripts/tests/test_worktrunk_activity.mjs scripts/tests/test_hunk_worktrunk_feedback.mjs
node scripts/tests/worktrunk-subagents-smoke.mjs --candidate-root /path/to/reviewed/pi-subagents
```

All checks use synthetic data. Worktrunk tests use disposable Git repositories and
homes. The Subagents smoke test requires an explicit reviewed source candidate;
it does not default to the installed package. It loads only worktree modules,
uses an isolated environment, and launches no model. Its native and Worktrunk
cases check exact binary replay, unsafe evidence retention, setup-failure
retention, hook suppression, and explicit discard boundaries. Existing Pi/Jiti
loader dependencies are read-only inputs; `--pi-loader-root` can select their
existing Pi package root containing `package.json`. Passing the candidate test does not install or
activate it, and does not lift the installed managed-cleanup restriction.

Physical cmux keyboard delivery, desktop notifications, and macOS sleep/wake are not automated by these checks.

## Upstream references

- [Worktrunk documentation index](https://worktrunk.dev/llms.txt).
- [Switch and agent launch](https://worktrunk.dev/switch/).
- [Shell integration](https://worktrunk.dev/shell-integration/).
- [Configuration and approvals](https://worktrunk.dev/config/).
- [Custom subcommands](https://worktrunk.dev/extending/).
- [Merge behavior](https://worktrunk.dev/merge/).
- [Oh My Pi hook source at 0.77.0](https://github.com/max-sixty/worktrunk/blob/v0.77.0/dev/pi-plugin.ts).
