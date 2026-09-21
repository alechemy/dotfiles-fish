---
name: worktrunk
description: Manage task worktrees with Worktrunk alongside Pi, cmux, and Hunk. Use for creating isolated tasks, reopening task terminals, inspecting worktree status, integrating branches, or cleaning up completed worktrees.
---

# Worktrunk

Run `wt --version` and the relevant command's `--help` before using unfamiliar options. The installed binary owns syntax. Read `~/.dotfiles/docs/worktrunk.md` for the combined workflow and installation ownership.

## Ownership

Worktrunk allocates worktrees. cmux owns human task workspaces and terminals. Pi owns implementation and delegation. Hunk owns local review comments.

Keep one writer per checkout. A Bash tool's `wt switch` cannot change the running Pi session's working directory. Start a separate session at the chosen worktree instead.

The installed Subagents build uses its official Worktrunk allocator. It reserves `pi-subagents/` branches and retains setup, evidence, resume, and cleanup ownership. Use Subagents' managed worktree and handoff APIs for delegated work. Its allocator deliberately skips Worktrunk project hooks. Preserve the existing model/provider guard and extension restrictions.

## Human task terminals

These commands are for the human's Fish shell inside a native cmux terminal whose cwd is in the repository:

```fish
wt pi new feature/auth
wt pi new fix/login --base main --no-focus
wt pi open feature/auth
wt pi list
```

`wt pi new` creates a fresh branch and starts Pi in a named cmux workspace. `open` selects the exact recorded workspace or resumes the recorded Pi conversation in its exact terminal surface. Neither command submits a task prompt or answers a trust dialog. A failed launch retains its worktree and partial workspace binding; inspect them before retrying. `open` only accepts worktrees allocated by `new`, refuses stale or ambiguous cmux identifiers, and never chooses a destination from focus or titles.

Do not invoke this human launcher from an agent or unset its agent-detection environment variables. Use Subagents `project.open` with the intended cwd for an explicitly requested independent project session, or ordinary managed delegation. Worktree allocation alone does not authorize a new agent process.

For manual shell navigation, use `wt switch`, `wt switch <branch>`, and `wt switch -`. Existing ordinary Git worktrees remain usable without adoption by the launcher.

## Approvals and preparation

`wt pi new` refuses unapproved or stale project commands. Review `wt config approvals list` and the actual project configuration before deliberately recording approval. Do not add `--yes` to get an agent past a prompt. Worktrunk approval is separate from Pi project trust. Declining an ordinary Worktrunk approval can continue an operation without its project commands, so it is not a mandatory validation gate.

Preserve existing dirty work in the source checkout. New worktrees start from committed history. Choose `--base` explicitly when needed. Copy only reviewed local files, never an entire ignored tree containing credentials or runtime state. Keep project-specific dependency setup and dev-server commands in reviewed project configuration rather than global launch hooks.

## Review and integration

Open Hunk for the intended task worktree, save human comments, and invoke **Send human feedback to task Pi** with Ctrl+Shift+F. The extension sends only `source: user` notes to the one recorded, idle Pi session. A verified receipt is required before Hunk removes comments. Busy, stale, multiple-recipient, disconnected, and uncertain-delivery states retain comments and fail closed. Keep comments local unless remote publication is explicitly authorized.

Open the installed Hunk skill with `hunk skill path` for agent-side review commands and
completion checks. Use an explicit `<base>...HEAD` comparison for committed branch review. Verify its
base against the intended integration target; an invalid-base fallback is not whole-task
review. Reload the exact worktree with an explicit comparison when needed. Branch
`<base>...HEAD` excludes staged, unstaged, and untracked changes; review those separately
under the [code-review scope rules](../code-review/references/scope.md).

Before completion or successful feedback delivery clears comments, retain unresolved
conclusions and a local [coding-task receipt](../handoff/SKILL.md#coding-task-review-receipt).
Record reviewed and tested state separately and recheck after edits or rebases. Reference
existing evidence rather than copying patches, private transcripts, or all comments.

Prepare commits explicitly, inspect recent commit style, and run the relevant checks. The seeded Worktrunk configuration disables automatic commits, rebasing, and removal during merge. For an explicit conservative local integration:

```sh
wt merge --no-commit --no-rebase --no-remove
```

The tree must be clean and the target must fast-forward. If rebasing is needed, use `git rebase --no-update-refs` under the existing Git policy. Preserve signed commits and Git hooks. Remote writes still require the user's current-turn instruction; project hooks can perform remote writes too, so inspect them before running an operation.

## Cleanup

Send outstanding Hunk comments before closing the task workspace. Stop its Pi session and dev servers, then close the workspace. From another worktree's cmux shell:

```fish
wt pi remove feature/auth
```

The helper requires its own task ownership record and refuses live Pi sessions, cmux surfaces, unsent or uncertain Hunk feedback, processes with a cwd beneath the target, uncommitted changes, and ignored files. It retains the branch even when unmerged. Inspect and preserve ignored files before deliberately passing `--discard-ignored`. Delete a retained branch separately only after confirming that its work is integrated or intentionally discarded.

These checks cover processes visible to the current user, the connected cmux inventory, and saved local review ownership. They are not a filesystem lock against external writers, processes that moved their cwd, or another user. Direct `wt remove` and `git worktree remove` bypass the helper's additional checks. Never bulk-remove `pi-subagents/` worktrees with either command.

## Status and dotfiles

The native Pi extension reports working, idle, and blocked markers without prompts or transcript contents. In cmux it also records exact workspace, surface, and Pi session identities and exposes a private per-session feedback socket. Only interactive sessions participate. Multiple sessions in a worktree aggregate; one shutdown cannot clear another's marker. `wt pi list` removes dead or reused-PID records before displaying status. cmux's official Pi hook owns application-level lifecycle display and session restoration; Worktrunk's extension owns worktree markers and authenticated Hunk delivery. Subagents remains authoritative for headless child activity.

Run dotfiles setup and Stow only from the primary checkout. Its restow hooks skip linked worktrees, including older branches with unguarded worker scripts. Commit experimental dotfiles changes and integrate them into the primary checkout before applying them to HOME.
