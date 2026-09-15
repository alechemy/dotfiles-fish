# Agentic coding workflow

## The mental model

Your workflow separates code, terminals, and conversations:

- **Worktrunk** gives each task its own checkout and Git branch.
- **Herdr** keeps the task's terminals running and organizes them into tabs.
- **Pi** works on the code and maintains the conversation.
- **Hunk** lets you inspect changes and send line-specific feedback to Pi.
- **Ghostty** displays the whole thing.

A typical project looks like this:

```text
Ghostty window
└── Herdr workspace: myapp
    ├── Control tab
    │   └── Fish shell in the primary checkout
    ├── Task tab: feature/export
    │   ├── Pi in the export worktree
    │   └── Hunk reviewing that worktree
    └── Task tab: fix/search
        └── Pi in a separate search worktree
```

**A tab is a terminal layout. A worktree is a directory containing code.** Closing a tab does not delete the worktree. Changing a shell's directory does not relocate an already-running Pi session.

The walkthrough below uses a fictional `~/Work/myapp` repository with a `main` branch. Substitute your repository's path and target branch. These commands are for you to run.

## 1. Open the project's workspace

Your Ghostty configuration already starts or attaches to Herdr. You normally do **not** type `herdr` again inside it.

Press **Cmd+Ctrl+W** and select the project's existing workspace.

For a project without a workspace, run this in a Fish shell inside Herdr:

```fish
herdr workspace create --cwd ~/Work/myapp --label myapp --focus
```

The new workspace starts with a shell in that directory. Keep this first tab as your control shell for task creation, status, and cleanup.

You can click tabs and panes to focus them and drag split borders to resize them. The first keyboard shortcuts worth learning are:

| Key | Action |
| --- | --- |
| Cmd+Shift+T | Create a tab. |
| Cmd+D | Split right. |
| Cmd+T | Split down. |
| Cmd+Shift+H/J/K/L | Focus left/down/up/right. |
| Cmd+Shift+Enter | Zoom or unzoom the current pane. |
| Cmd+1 through Cmd+9 | Select a tab. |

Notice that **Cmd+T splits down** in your setup. It does not create a tab.

## 2. Give the task its own checkout

Suppose the feature is CSV export.

In the control shell:

```fish
wt pi new feature/export --base main
```

This creates a branch from local `main`, allocates a sibling checkout, opens a named Herdr tab, and starts a fresh Pi session there.

The original checkout stays untouched. Its uncommitted changes do not accompany the new task. The new Pi conversation also does not inherit the conversation you were having elsewhere.

Answer any startup trust or login prompts yourself. Two separate approvals may matter:

- Worktrunk approval allows the repository's configured preparation commands.
- Pi trust allows project-local Pi configuration and executable extensions.

Review the commands or resources before approving them. The launcher deliberately refuses unapproved Worktrunk project commands.

These `wt pi new` and `wt pi open` commands are **human-shell launchers**. Pi uses its managed session and delegation tools rather than running those launchers itself.

## 3. Tell Pi what success means

In the new Pi pane, write something like:

> Add CSV export to the filtered results page. Export only the visible columns, preserve the current sort order, and handle commas, quotes, and line breaks correctly. Follow the existing download pattern and add focused tests. Leave the changes uncommitted for review.

In your configuration:

- **Enter** inserts a newline.
- **Cmd+Enter** or **Ctrl+S** submits the prompt.

Herdr reports whether Pi is working, idle, or blocked waiting for an answer. Idle means the turn has settled, not that the implementation is correct.

From the control shell, this gives a cross-task overview:

```fish
wt pi list
```

Its native Pi markers distinguish working, idle, and blocked sessions. Herdr also shows Subagents activity text, which can indicate child work even when the root Pi session looks idle.

Keep one writer per checkout. Managed Subagents write worktrees that can enter automatic cleanup are currently restricted: the installed guarded build can delete a checkout even when its captured binary patch cannot be replayed. Do not use that cleanup path until the reviewed preservation fix passes its binary, stale-patch, and failure-retention tests. This is separate from the human `wt pi` helper, which retains branches and refuses dirty work. Do not bulk-clean either kind of retained task.

## 4. Review the changes in Hunk

Wait until Pi settles. From **the Pi pane that should receive your feedback**, press:

**Ctrl+B, release, then `f`.**

Hunk opens or reuses a review split for that worktree. Starting from the intended Pi pane establishes the feedback recipient.

In Hunk:

1. Select a relevant line.
2. Press `c`.
3. Write a specific comment.
4. Press **Ctrl+S** to save it.

For example:

> This exports the unfiltered collection. Use the same filtered and sorted rows that the table displays.

Saving the note does **not** submit it to Pi. To send your saved comments, press:

**Ctrl+B, release, then Shift+F.**

The integration submits the human comments to the associated Pi session. Successful delivery removes those comments from Hunk to prevent duplicate sending.

If Pi is busy or showing a blocking dialog, delivery is refused and the comments remain. Resolve that state, then send again. If a failure happened after pasting into Pi, inspect its draft before retrying.

These comments are local. They are not published GitHub reviews.

### When the default review looks empty

The default shortcut reviews working-tree changes. Once Pi commits the work, that view can be empty.

To inspect the complete committed task, ask Pi:

> Reload this worktree's Hunk review with `diff main...HEAD`.

Or run this from a shell in the task checkout:

```fish
hunk session reload --repo . -- diff main...HEAD
```

That comparison shows the committed task changes since the shared ancestor with `main`. It does not include subsequent uncommitted edits.

## 5. Iterate, then commit deliberately

After Pi addresses the comments, inspect the updated diff and request any missing checks.

When satisfied, tell Pi:

> Run the relevant checks and review the final diff. Commit only this task's changes, following the repository's recent commit-message style. Keep signing and hooks enabled. Do not push.

Your Git configuration signs commits. For dotfiles, the commit process also needs the staged betterleaks scan and `git diff --check`.

A commit records the task on its branch. It does not integrate it into `main` or publish it.

## 6. Integrate locally

From a shell in the **task checkout**, with Pi settled and no other writer active:

```fish
wt merge main --no-commit --no-rebase --no-remove
```

You can create that shell with Cmd+D, then confirm its directory and branch with `pwd` and `git status`.

The command means:

- Integrate the current task branch **into `main`**.
- Do not automatically commit or squash anything.
- Do not rewrite commits through an automatic rebase.
- Keep the task checkout afterward.

This direction differs from `git merge main`, which would bring `main` into the current branch.

The working tree must be clean, and `main` must be able to fast-forward:

```text
Before:  A──B           main
             └──C──D   feature/export

After:   A──B──C──D     main, feature/export
```

If another task has advanced `main` along a different path, the command refuses. That is the point of the conservative settings.

Rebasing is a separate decision. If you choose it, the task-side command is:

```fish
git rebase --no-update-refs main
```

Then resolve any conflicts, rerun checks, review the result, and retry integration. `--no-update-refs` prevents your global Git preference from also moving other local branches.

Local integration and publication remain separate. Pi needs an explicit current-turn instruction to push or create a PR. Review configured hooks too, because hooks can perform remote operations.

## 7. Close the task, then remove its checkout

Use this order:

1. Send outstanding Hunk comments.
2. Finish any resulting Pi work. If it changed the code after integration, review, commit, and integrate those changes too.
3. Submit `/quit` in Pi and stop task dev servers.
4. Close the task tab with **Cmd+Option+W**.
5. Return to the control shell.

Then run:

```fish
wt pi remove feature/export
```

The helper refuses removal while it finds active panes, Pi sessions, relevant processes, uncommitted changes, or ignored files.

If ignored files block removal, inspect them first. They might be disposable dependencies, but they might also contain local data. Use `--discard-ignored` only after making that distinction.

**Removal retains the branch**, including any unmerged commits. After confirming integration, you can separately delete the branch from the primary checkout:

```fish
git branch -d feature/export
```

Do not use generic bulk cleanup on Subagents-owned `pi-subagents/` worktrees. The helper's checks reduce mistakes, but they are not a lock against every external writer.

## Parallel work and coming back later

To start an independent second task without switching focus:

```fish
wt pi new fix/search --base main --no-focus
```

Its separate checkout prevents the two Pi sessions from editing the same files. It does not eliminate merge conflicts later.

To return to an existing task:

```fish
wt pi open feature/export
```

The launcher focuses its existing tab or resumes Pi when appropriate. Use `open`, not another `new`. This applies while the task worktree still exists, before removal.

For a break, **detach rather than clean up**:

- **Ctrl+B, then `q`** detaches while processes keep running.
- **Cmd+Shift+W** closes the Ghostty window while Herdr remains running.
- Opening Ghostty again attaches to the default Herdr server.

Restarting the Herdr server is different. It stops processes and attempts layout and Pi conversation restoration. A resumed conversation is not a continuously running process.

## Compact reference

| Where | Command or key | Purpose |
| --- | --- | --- |
| Control shell | `wt pi new <branch> --base main` | Start an isolated task. |
| Control shell | `wt pi open <branch>` | Return to or resume a task. |
| Control shell | `wt pi list` | Inspect task activity. |
| Fish shell | `wt switch` | Open the worktree picker. |
| Fish shell | `wt switch <branch>` | Move that shell to a worktree. |
| Pi editor | Cmd+Enter or Ctrl+S | Submit a message. |
| Pi editor | `/hotkeys` | Show Pi shortcuts. |
| Intended Pi pane | Ctrl+B, then `f` | Open its Hunk review. |
| Herdr | Ctrl+B, then Shift+A | Review staged changes. |
| Herdr | Ctrl+B, then Shift+C | Review the latest commit. |
| Hunk | `c`, then Ctrl+S | Write and save a comment. |
| Herdr | Ctrl+B, then Shift+F | Send saved human comments. |
| Task shell | `wt merge main --no-commit --no-rebase --no-remove` | Integrate locally. |
| Herdr | Cmd+Option+W | Close the task tab. |
| Another checkout | `wt pi remove <branch>` | Remove an inactive task checkout. |
| Herdr | Ctrl+B, then `?` | Show Herdr shortcuts. |

If task creation fails halfway, inspect the retained tab and `wt pi list` before retrying. If reopening reports another active agent, resolve that existing session rather than starting another writer.

For dotfiles specifically, **only the primary checkout owns your live HOME links**. Integrate experimental changes before applying them. Never run setup or Stow from a task worktree.

## References

These commands were checked against Herdr 0.9.0, Worktrunk 0.77.0, Pi 0.85.1, and Hunk 0.22.0, together with the tracked local configuration.

- [Herdr configuration and workflow](herdr.md).
- [Worktrunk integration and cleanup rules](worktrunk.md).
- [Dotfiles architecture and safety rules](dotfiles-reference.md).
- [Upstream Worktrunk merge documentation](https://worktrunk.dev/merge/), also consulted through Context7's `/max-sixty/worktrunk` documentation.
