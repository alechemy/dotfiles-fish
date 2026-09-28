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

## AeroSpace gaps

On the `DELL U4025QW`, each tiled cmux window counts as its number of side-by-side pane columns when deciding whether to remove outer gaps. A two-column cmux window plus one other tiled window reaches three and sets the focused AeroSpace workspace's left and right outer gaps to zero, as does a single three-column cmux window. Stacked panes and tabs within a pane do not add columns. Expansion is immediate; reducing cached column counts waits for two seconds without another matching cmux event. Workspace switches restart that delay, and returning to a wider layout cancels the pending shrink. Ordinary window-count presets and manual gap overrides stay unchanged.

For two side-by-side tiled windows, the worker also divides the available width in proportion to their column counts. Two cmux columns beside an ordinary window get a 2:1 split. Returning to one cmux column restores equal widths after the shrink delay. Sizing runs after the gap reload and targets a window by ID without moving focus. It only reapplies when the pair, column counts, screen width, or gaps change, so ordinary focus events do not undo manual resizing. Stacked, accordion, fullscreen, and larger layouts retain their existing sizes.

`~/.local/bin/aerospace-cmux-gaps.py` reads pane geometry from each native window's selected cmux workspace. It never uses the calling terminal's workspace as the selection. The window-number bridge uses cmux's terminal diagnostics, so a window without a hosted terminal cannot be matched.

cmux's native `dotfiles.aerospace-gaps` automation refreshes a cache of column counts keyed by native window ID on pane and workspace events, then calls the existing gap worker. This preserves cmux-only socket access, since AeroSpace and launchd read only the derived cache. The cache contains no titles or terminal content and is invalidated across boots. Floating windows and other AeroSpace workspaces are excluded by the gap worker. Missing geometry, failed refreshes, or ambiguous identities fall back to ordinary gaps after the same delay. A pending shrink rechecks the layout before applying, and superseded timers cannot change the cache. There is no recurring layout polling; a missed cmux event is corrected by the next matching event.

`scripts/setup-cmux-gaps.py` merges only this rule into the app-owned `~/.cmuxterm/automations.json`, backs up changes, and preserves other rules and a deliberate disabled state. Normal cmux setup installs it. `--install-pi-hook` also reloads automations; otherwise run `cmux automation reload` or restart cmux after setup. To refresh an existing layout immediately, run `/usr/bin/python3 ~/.local/bin/aerospace-cmux-gaps.py --refresh` from a cmux terminal.

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

The shared `code-review` skill opens a dedicated tuicr split when the parent Pi session runs in native cmux. Its bundled `tuicr_review.py` captures the selected comparison. Complete github.com PR URL reviews open native PR sessions with local findings that the user can submit through `:submit`. Local changes and restricted comparisons use private, remote-free Git snapshots. Staged, unstaged, committed, path-filtered, and untracked scopes retain their meaning without changing the source checkout. Independent review axes and the grouped Pi report are unchanged. Text-only requests and unsupported viewers retain the full report.

The parent imports validated findings through tuicr's official review CLI, including native range, file, and review comments. Repeated imports reconcile receipts without duplicating notes. Missing anchors remain report-only. A different comparison gets a new review ID; repeated opens reuse only that review's owned pane.

Ask Pi to show or explain R1 to verify and discuss its saved finding. In tuicr, open `:summary`, select R1, and press Enter. tuicr has no live navigation API, so Pi does not move the cursor or highlight expressions. Save human comments normally and ask Pi to read the review feedback. These panes do not use Hunk's Ctrl+Shift+F delivery shortcut, and reading feedback never clears it or authorizes fixes.

Private patches, snapshot repositories, and isolated tuicr state live under `~/.local/state/pi-code-review/`. Native PR sessions use existing GitHub CLI authentication without copying credentials. They bind the PR repository, number, base/head revisions, and saved diff-content hashes. Remote revision checks surround imports and follow-ups; the helper has no publication command. A review request alone never authorizes a remote draft or submission. tuicr may initially select commits since the last submitted review, so select all commits for a whole-PR review before following or publishing findings.

The helper checks source freshness, exact process and pane ownership, session identity, commit range, and saved file inventory. It cannot inspect every transient UI selection or provide an atomic generation guard. It preserves receipts and reports a raced target change rather than retrying blindly. Partial-hunk reviews remain text-only. See the [tuicr reference](../stow/agents/.agents/skills/code-review/references/tuicr.md) for commands, schemas, storage, and limits.

Homebrew owns tuicr. The helper's persisted-state checks are verified against 0.27.0 and fail closed after a binary upgrade until revalidated. Restow `agents` after adding or removing skill files. Pi reads the replacement skill on its next invocation; reload if discovery needs refreshing.

```sh
stow --restow --no-folding --ignore='__pycache__' --dir=stow --target="$HOME" agents
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_tuicr_review.py
/usr/bin/python3 -m unittest discover -s scripts/tests -p test_code_review_recipes.py
```

The opt-in native check opens one unfocused disposable cmux workspace. It checks all six comparison modes, textconv and unborn cases, range and review comments, human feedback retrieval, idempotency, and reopening without comment loss. It closes only its own workspace and deletes its synthetic artifacts. It makes no model calls or remote writes.

```sh
/usr/bin/python3 scripts/tests/tuicr_review_smoke.py --allow-ui
/usr/bin/python3 scripts/tests/tuicr_pr_smoke.py --allow-ui
```

The PR smoke runs real tuicr with a synthetic `gh` executable. It checks native session binding, local drafts, human feedback, and stale-head refusal without network requests or remote writes. Authenticated forge access and actual publication are outside that test.

### Roll back the trial

The Hunk integration is preserved at commit `cfb9442` and local tag `code-review-hunk-0.22.0`, including the trimmed-context verification fix. To switch back, preserve unrelated work and revert the dedicated `agents: restore tuicr code-review integration` commit. Then run `scripts/restow-changed.sh HEAD^ HEAD` from the primary checkout and reload Pi's skills. Private review artifacts are retained; close old panes before switching workflows.

Hunk remains installed for the existing Git pager, shell shortcuts, and human-led Worktrunk feedback below. Those are separate from the replaced code-review skill. tuicr can remain installed after rollback.

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
