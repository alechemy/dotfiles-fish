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
