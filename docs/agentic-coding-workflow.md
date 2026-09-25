# Agentic coding workflow

## The model

The local workflow separates checkout, terminal, conversation, review, and editing ownership:

- **Worktrunk** gives each human task its own checkout and branch.
- **cmux** owns task workspaces and terminal surfaces.
- **Pi** owns implementation, conversations, and delegation.
- **Hunk** owns local review comments.
- **VSCodium** remains available for code navigation and manual editing.
- **libghostty** renders cmux terminals using the stowed terminal settings; the standalone Ghostty app is retired.

A typical repository has one control shell in the primary checkout and one cmux workspace per task worktree. A workspace is terminal state; a worktree is a directory. Closing one does not remove the other.

## Start and return to tasks

Open a native cmux terminal in the repository, then run:

```fish
wt pi new feature/export --base main
wt pi new fix/search --base main --no-focus
wt pi open feature/export
wt pi list
```

`new` allocates a sibling worktree and branch, creates a named cmux workspace, binds its initial terminal surface, and starts a fresh named Pi conversation. It does not copy uncommitted source-checkout changes. `open` selects the recorded workspace, reuses its live Pi session, or resumes the exact recorded conversation. Names and focus are display state, not identity.

The launcher is for human shells. Pi uses managed delegation or an explicitly requested `project.open`; it does not run `wt pi new` or clear the launch guard. Worktrunk project-command approval and Pi project trust remain separate decisions. Review both before accepting them.

A failed or timed-out launch keeps the checkout and any recorded cmux identity. Unknown launch outcomes cannot be retried automatically because the original workspace might still start. Inspect the retained state instead of creating another writer.

For ordinary shell navigation, use `wt switch`, `wt switch <branch>`, and `wt switch -`.

## Fish shortcuts

Interactive Fish shells load these abbreviations from
`stow/fish/.config/fish/conf.d/abbrs.fish`. Space or Enter expands them to the full
command, so arguments and flags remain visible and editable.

| Shortcut | Expansion | Purpose |
| --- | --- | --- |
| `pn` | `wt pi new` | Create a task worktree and start Pi in its cmux workspace. |
| `po` | `wt pi open` | Select or resume the recorded task workspace and conversation. |
| `pl` | `wt pi list` | Refresh and display worktree activity. |
| `ws` | `wt switch` | Open the picker, or navigate to a supplied branch. |
| `wb` | `wt switch -` | Return the shell to its previous worktree. |
| `hd` | `hunk diff` | Review unstaged tracked changes and untracked files. |
| `hs` | `hunk diff --staged` | Review staged changes. |
| `hc` | `hunk show HEAD` | Review the latest commit. |
| `hwatch` | `hunk diff --watch` | Watch unstaged tracked changes and untracked files. |
| `prm` | `wt pi remove` | Remove an inactive task checkout through the guarded helper. |
| `wmerge` | `wt merge --no-commit --no-rebase --no-remove` | Integrate locally without automatic commits, rebasing, or checkout removal. |

`hb <base>` is a Fish function in `stow/fish/.config/fish/functions/hb.fish`.
It requires exactly one explicit commit-ish base, verifies that it shares an
ancestor with HEAD, then runs `hunk diff <base>...HEAD`. Invalid refs and unrelated
histories stop before Hunk opens. This scope excludes staged, unstaged, and
untracked changes; use `hunk diff HEAD` to review all unfinished work against HEAD, or `hs` and `hd` for separate index and working-tree reviews.

For example, from a native cmux repository shell:

```fish
pn feature/export --base main
pl
po feature/export
```

In a second terminal at the task worktree, use `hwatch` while Pi edits or
`hb main` to review committed branch changes. `po` selects the task's cmux
workspace; `ws` only navigates the current shell and does not start Pi.
Send saved Hunk feedback with Ctrl+Shift+F when the bound Pi session is idle.

Use `wmerge main` from a clean task checkout after review and verification.
Project hooks still run and require inspection for remote writes. After stopping
Pi and task services and closing the task workspace, use `prm feature/export`
from another worktree. Cleanup retains the branch and all existing refusal checks.

New shells load the abbreviations automatically. To refresh an existing shell:

```fish
source ~/.config/fish/conf.d/abbrs.fish
```

Fish autoloads `hb` after its new file is linked. From the primary dotfiles
checkout, link newly added Fish files with
`stow --restow --no-folding --dir=stow --target="$HOME" fish`. Do not run Stow
from a linked task checkout.

Run the isolated shortcut regressions with
`/usr/bin/python3 -m unittest discover -s scripts/tests -p test_dev_shortcuts.py`.
They use disposable Git history and a Hunk stub, without launching task sessions.

## Work with Pi

In Pi:

- Enter and Shift+Enter insert newlines.
- Cmd+Enter or Ctrl+S submits.
- `/subagents-fleet` shows foreground and recent asynchronous runs.
- `/subagents-doctor` runs delegation diagnostics.
- `/subagent-cost` reports current parent and child cost.

An idle root session can still have active children. Check Fleet before treating a task as settled. The installed guarded Subagents build retains its provider, role, tool, and cleanup restrictions. Managed write worktrees that can enter automatic cleanup remain restricted until their separate preservation candidate is installed and approved. This is independent of human `wt pi` worktrees.

Keep one writer per checkout. A second terminal may run tests or services, but do not launch another editing agent against the same worktree.

## Review beside Pi

When asking Pi for a code review in cmux, the shared skill opens tuicr automatically. A complete GitHub PR URL review uses a native PR session; local or restricted comparisons use an isolated snapshot. Validated findings become local comments in that session. Ask Pi to show or explain R1 to verify and discuss it, then use tuicr's `:summary` picker to jump to the comment. Save human comments and ask Pi to read the review feedback. The grouped summary remains in Pi, including findings without valid anchors. See [AI findings beside the diff](cmux.md#ai-findings-beside-the-diff) for scope checks, rollback, and private artifact storage.

For native GitHub PR sessions, inspect the findings and use `:submit` when you want to publish. Asking Pi to review does not authorize publishing, including a remote draft. Select all commits for whole-PR reviews if tuicr has automatically selected only commits since your last review. Snapshots have no forge binding and cannot publish. GitHub CLI must already be authenticated; other forges and GitHub Enterprise are not yet supported by the skill's native binding.

### Separate human-led Hunk workflow

The existing shell shortcuts and Git pager still use Hunk during the tuicr skill trial. For a human-led review, open Hunk in the task worktree using the scope you intend to inspect:

```fish
hunk diff
hunk diff HEAD
hunk diff --staged
hunk show HEAD
hunk diff main...HEAD
```

Bare `hunk diff` compares tracked working files with the index and includes untracked files. `hunk diff HEAD` shows the net staged and unstaged changes against HEAD, plus untracked files. These differ from staged-only, latest-commit, and whole-branch comparisons. `main...HEAD` excludes staged, unstaged, and untracked work, so inspect those separately when they belong to the task. For another integration target, validate the ref and merge base and use that exact comparison.

Create and save human inline comments in Hunk. Ctrl+Shift+F invokes **Send human feedback to task Pi**. The extension:

1. snapshots the current review;
2. selects only saved `source: user` notes;
3. verifies one exact idle Pi recipient for the worktree's recorded cmux workspace, surface, and session;
4. sends through that Pi process's private local receiver;
5. removes comments only after a matching delivery receipt and unchanged review revision.

Busy, blocked, disconnected, changed, ambiguous, and uncertain states retain comments. A delivery that might have reached Pi is not retried automatically. Inspect the conversation before resolving it manually. Local Hunk comments are not GitHub review comments and do not authorize remote writes.

Before clearing important review feedback or completing a task, retain unresolved findings in a local [coding-task receipt](../stow/agents/.agents/skills/handoff/SKILL.md#coding-task-review-receipt). Record the target, base or merge base, reviewed HEAD, scope and exclusions, tested dirty state, checks, unresolved findings, service ownership, and next action. Reference existing artifacts rather than copying private transcripts.

## Commit and integrate

Prepare commits explicitly under the repository's signing, message, hook, and secrets-scan rules. A local commit neither integrates nor publishes the task.

For conservative local integration, run this from the clean task checkout:

```fish
wt merge main --no-commit --no-rebase --no-remove
```

The target must fast-forward. If a separate rebase is needed, use:

```fish
git rebase --no-update-refs main
```

Resolve conflicts, rerun relevant checks, and review the new result. Remote writes still require explicit current-turn authorization. Inspect project hooks because they can also perform remote writes.

## Stop and remove a task

Use this order:

1. Send or preserve outstanding Hunk comments.
2. Finish, review, commit, and integrate any resulting edits.
3. Quit Pi and stop task services through their own shutdown commands.
4. Close the cmux task workspace.
5. From another worktree's native cmux shell, run:

```fish
wt pi remove feature/export
```

Removal refuses live Pi records, cmux surfaces that still own the task, unsent or uncertain feedback, current-user processes whose cwd remains below the worktree, uncommitted files, and unexplained ignored files. It never closes a workspace or kills a process. Use `--discard-ignored` only after inspecting and preserving anything needed.

The helper retains the branch, including unmerged commits. Delete it separately only after confirming integration or intentional discard:

```fish
git branch -d feature/export
```

Direct `wt remove` and `git worktree remove` bypass these checks. Never bulk-remove Subagents-owned `pi-subagents/` worktrees.

## Recovery

cmux's official Pi hook owns lifecycle display, notifications, and application-level conversation restoration. The Worktrunk extension separately owns worktree markers and verified Hunk delivery.

After cmux restores an application session, only the exact Pi session already recorded for a task may rebind it to one unambiguous replacement workspace and surface after the old pair disappears. A missing, conflicting, or stale identity fails closed. Conversation restoration does not imply that an earlier process or dev server survived.

For dotfiles, only the primary checkout owns live HOME links. Integrate experimental changes there before running setup or Stow.

## Compact reference

| Where | Command or key | Purpose |
| --- | --- | --- |
| cmux repository shell | `wt pi new <branch> --base main` | Start an isolated task. |
| cmux repository shell | `wt pi open <branch>` | Select or resume a task. |
| Any repository shell | `wt pi list` | Refresh and display task activity. |
| Fish shell | `wt switch` | Open the worktree picker. |
| Pi | Cmd+Enter or Ctrl+S | Submit a message. |
| Pi | `/subagents-fleet` | Inspect delegated runs. |
| Pi | Ask for a code review, then "show R1." | Open an annotated tuicr PR session or snapshot and discuss the finding. |
| tuicr PR session | Use `:submit` after inspecting the findings. | Publish the selected review to GitHub. |
| tuicr | `:summary`, select R1, then Enter. | Jump to the finding. |
| Pi | Ask to read the review feedback. | Read saved human tuicr comments without clearing them. |
| Hunk | `c`, then Ctrl+S | Save a human comment. |
| Hunk | Ctrl+Shift+F | Send saved human comments to the bound idle Pi. |
| Task shell | `wt merge main --no-commit --no-rebase --no-remove` | Integrate locally. |
| Another cmux worktree | `wt pi remove <branch>` | Remove an inactive task checkout. |

## Verification limits

Synthetic tests cover task identity, duplicate-launch refusal, restore rebinding, feedback targeting and receipts, uncertainty, cleanup refusal, and branch retention. Physical cmux key delivery, desktop notifications, authenticated GitHub browser behavior, and sleep/wake remain user-assisted checks. These gaps do not relax cleanup or delivery safeguards.

See [cmux integration](cmux.md), [Worktrunk integration](worktrunk.md), and [dotfiles architecture](dotfiles-reference.md).
