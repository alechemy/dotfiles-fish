# cmux task-workspace integration

cmux is the terminal and workspace owner for human Worktrunk tasks. VSCodium remains the editor, Pi owns implementation and delegation, Worktrunk owns checkout allocation, and Hunk owns local review state. Herdr was retired after the migration inventory found no remaining Herdr-owned task bindings.

## Startup and setup

cmux uses libghostty for terminal rendering and reads the stowed `~/.config/ghostty/config` for terminal settings. The `cmux` Stow package owns that file; it starts Fish without Herdr, clears stale Herdr environment variables at the terminal boundary, and contains no standalone Ghostty window or split bindings. There is no cmux-specific Ghostty override.

`scripts/setup-cmux.sh` removes only known transitional command overrides from `~/Library/Application Support/com.cmuxterm.app/config.ghostty`. It backs up and deletes a file containing only those overrides or comments, and refuses symlinks, unknown commands, or unrelated settings so app-specific configuration cannot silently remain active. Before Stow runs, `scripts/migrate-cmux-config-link.py` removes only the exact retired `stow/ghostty` link and refuses unrelated links. `setup-cmux.sh` also copies the reviewed Hunk extension from `scripts/cmux/worktrunk-feedback.ts`.

Normal setup runs:

```sh
scripts/setup-cmux.sh --install-pi-hook
```

The flag additionally runs cmux's official `hooks setup pi --yes`. The official hook owns cmux lifecycle state, notifications, and application restart restoration. `worktrunk.ts` separately owns Worktrunk markers and authenticated Hunk feedback delivery.

## Task identity and launch

Run task commands from a native cmux terminal inside the repository:

```fish
wt pi new feature/example
wt pi open feature/example
wt pi list
```

A cmux-owned task record contains:

- canonical common Git directory and worktree path;
- exact branch name;
- exact cmux workspace and surface IDs;
- exact Pi session ID after Pi starts;
- launch process identity while startup is in progress.

Names and focus are never identity. `new` uses `cmux new-workspace --cwd --command --name --focus`. `_start` binds the IDs supplied by cmux before replacing itself with Pi. `open` verifies the stored workspace and surface against full cmux inventory, then reuses a live matching session or starts the recorded Pi session in that surface. After application restore, only the same recorded Pi session may rebind the task to one unambiguous replacement workspace and surface after the old pair disappears. Missing, conflicting, stale, or partial identity otherwise fails closed.

Allocation and startup failures retain the worktree and any created workspace for inspection. An unresolved workspace-creation request cannot be retried automatically because the original command may still complete.

## AI findings beside the diff

The shared `code-review` skill automatically opens a dedicated Hunk split when the parent Pi session runs in native cmux. Its bundled `hunk_review.py` captures the selected Git comparison before review, binds the viewer to the exact Pi session and cmux terminal, and imports only the parent's validated findings. It preserves the existing independent review axes and grouped terminal summary. Text-only requests and unavailable or unsupported viewers retain the complete text report.

Ask Pi to review a commit or PR as usual, then ask it to show or explain R1. The helper navigates to that finding's saved Hunk comment after checking the reviewed content, displayed patch, and Hunk generation. Other navigation and expression highlights use Hunk's installed skill with the exact verified session ID. Human comments can be read on request without clearing them. The [inline-review reference](../stow/agents/.agents/skills/code-review/references/hunk.md) documents commands, findings schema, recovery, and limits.

A repeated open reuses the same review's pane; it does not adopt another Hunk window. Each different comparison has its own review ID. Findings without valid diff anchors stay in the report. Repeated imports reconcile existing notes, and uncertain writes are not blindly retried. `hunk diff` alone is unstaged-only for tracked files; the all-uncommitted helper mode passes the pinned HEAD explicitly.

Private patches and finding receipts live in `~/.local/state/pi-code-review/`, outside Stow and Git. The helper has no automatic retention cleanup. Native Hunk comparisons retain their normal source navigation; unborn all-uncommitted comparisons and repositories with Git textconv drivers use saved patches. Partial-hunk selections remain text-only. Simultaneous human reloads can race Hunk's CLI, so generation checks before and after writes detect the race without claiming atomic exclusion.

After adding the helper or reference files, restow only the `agents` package from the primary checkout. Apply only the feedback-extension update with `scripts/setup-cmux.sh --hunk-only`; this leaves app-owned cmux settings and Pi hooks untouched. New Hunk processes load the extension. Pi reads the updated skill on its next review invocation. Reload Pi if its skill discovery needs refreshing.

```sh
stow --restow --no-folding --ignore='__pycache__' --dir=stow --target="$HOME" agents
scripts/setup-cmux.sh --hunk-only
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_hunk_review.py
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_code_review_recipes.py
node --test scripts/tests/test_hunk_worktrunk_feedback.mjs
```

The opt-in native check creates one unfocused disposable cmux workspace, exercises all six comparison modes with synthetic findings, verifies navigation and idempotent import, then closes only its own workspace and removes its synthetic artifacts. It makes no model calls or remote writes:

```sh
/usr/bin/python3 scripts/tests/hunk_review_smoke.py --allow-ui
```

## Review feedback

The setup-managed `~/.config/hunk/extensions/worktrunk-feedback.ts` registers **Send human feedback to task Pi** on Ctrl+Shift+F. It snapshots the active review and sends only saved human notes. The Worktrunk helper requires:

- one exact live Pi record for the task's workspace, surface, and session;
- an idle recipient;
- a private Unix socket and random capability held by that Pi process;
- an explicit matching delivery receipt.

The payload fingerprint includes the resolved path and line anchor as well as comment content. Comments are removed from Hunk only after a matching receipt and only if the review generation and revision are unchanged. Removal selects the exact Hunk process/session instead of a repository selector, so multiple windows and patch-backed reviews cannot redirect cleanup. Busy recipients, connection failures, changed reviews, multiple sessions, and ambiguous acknowledgements retain comments. A delivery that might have reached Pi is recorded as uncertain and cannot be retried automatically. Inspect the task conversation before resolving it manually.

## Cleanup

After review and integration, send or preserve comments, stop Pi and task services, and close the cmux task workspace. From another worktree's native cmux shell:

```fish
wt pi remove feature/example
```

Removal refuses live Pi records, cmux surfaces that still match the task binding, unsent or uncertain feedback, processes whose cwd remains below the worktree, dirty files, and ignored files. It does not close a workspace or kill a process. The local branch is retained.

## Recovery and limits

cmux's official hook restores conversations, not arbitrary process state. Dev servers and other task processes may need to be restarted after an application relaunch. Worktrunk accepts replacement cmux IDs only from the exact recorded Pi session and only when the old surface is absent.

Local backups made by `setup-cmux.sh`, cmux session state, Pi transcripts, Hunk review state, and Worktrunk task receipts remain outside Git. Do not delete them as part of routine setup or cleanup.

Synthetic checks cover startup merging, task identity, duplicate-launch refusal, restore rebinding, feedback receipts and uncertainty, cleanup refusal, and branch retention. Physical key delivery, desktop notifications, authenticated GitHub browser behavior, and sleep/wake remain user-assisted checks.
