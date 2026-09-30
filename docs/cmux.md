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

## Pi convenience commands

Pi's settings fragment pins `npm:pi-cmux@0.1.24`. The package adds session-launch commands, terminal splits and tabs, and browser panes with user-written annotations. It does not connect Pi to cmux Computer Use.

The stowed `~/.pi/agent/extensions/pi-cmux.ts` disables package notifications, sidebar updates, and automatic model-generated titles before invoking the installed package's entry point. The package declaration uses `extensions: []` to prevent a second direct load. Applying preferences inside Pi also covers package-launched sessions, whose shell command forwards only `PATH`. cmux's official hook retains lifecycle display, notifications, and restoration. Worktrunk retains activity records and Hunk feedback delivery. Run `/reload` or restart Pi after installation.

Use `/cmo <command>` or `/cmt <command>` for requested tool views and `/cmb <url>` for a requested cmux browser pane. Browser annotations poll only panes opened by that Pi session, and each submitted note requires confirmation in Pi. Page scripts can forge notes; approve only notes you recognize. Native cmux Design Mode must be off. Hunk remains the review viewer and feedback owner; `agent-browser` remains the default browser automation workflow.

Session commands need separate ownership decisions. `/cmn`, `/cmv`, and `/cmh` create independent Pi sessions in the current checkout, not isolated worktrees. `/cmcv` and `/cmch` fork the conversation while retaining the source session; their `-c` option creates a Git worktree outside the tracked Worktrunk launcher. `cmux_start_pi` exposes the same choices to the agent. These commands do not transfer a `wt pi` task binding or apply Subagents' provider and execution guards. Do not use them for managed delegation or tracked task handoffs, and never create a second writer in one checkout. Keep `wt pi new/open/remove` for human tracked tasks and Subagents for managed delegation. A partial launch may leave a workspace behind; inspect it before retrying.

The published archive has no dependency lifecycle scripts or runtime dependencies beyond Pi peers. Selected registration, lifecycle, launch, configuration, and browser-annotation paths were source-reviewed. `scripts/tests/test_pi_cmux.mjs` checks the filtered pin, then optionally loads the stowed entry point and reviewed package through Pi's loader in a disposable HOME to verify enforced preferences, registration, and quiet lifecycle behavior. This does not verify live session launches or browser annotation delivery.

## Native Computer Use in Pi

The stowed `~/.pi/agent/extensions/cmux-cua/` extension connects Pi to cmux's bundled native MCP proxy. Loading Pi, discovering the extension, and starting or restoring a session do not start a proxy, request permissions, or perform GUI work.

Use `/cmux-cua on` with an image-capable model to enable desktop tools for the current Pi session, `/cmux-cua status` to inspect activation, and `/cmux-cua off` to revoke them and close that session's proxy. Activation performs only MCP discovery. A direct user request must still identify the app and task before the agent operates it. UI text and screenshots can reach the selected model provider and ordinary Pi session history; this is not a local-only privacy guarantee.

The adapter verifies the live originating terminal and workspace, cmux process ancestry, bundled executable, private native socket, current configuration, and forced `DisableComputerUse` policy in the app and release domains. It reads the token through an owner-only, single-link, no-follow file descriptor and passes it only in the proxy's environment. It preserves forced-proxy mode, external permission flow, cursor branding, exact surface identity, and Pi process ownership. cmux retains helper startup, recovery, onboarding, daemon admission, generation tracking, and disconnect cleanup.

Fourteen desktop tools use the `mcp__cmux_cua__` prefix and retain native schemas. Calls are serialized, authority is rechecked before dispatch, and image results and structured element tokens remain available to Pi. Session, permission, recording, and configuration tools are excluded. Runtime checks also reject explicit session/private fields, screenshot file outputs, debugging ports, and additional launch arguments, including inside action groups. Use inline screenshots and current element tokens. A native error, lost connection, changed authority, cancellation after dispatch, or timeout requires explicit reactivation; an uncertain action must be inspected before retrying. Reload and session replacement revoke activation. There is no automatic reconnect or helper fallback.

The inspected installation is cmux 0.64.25, build 106, with `cmux-cua` 0.7.1 and MCP protocol `2025-06-18`. Permission-free discovery returned 39 native tools; the adapter exposes 14. The installed Pi 0.87.1 SDK also completed discovery through the extension. After the user completed onboarding, the approved live test passed through the active Pi extension. It launched Calculator, read an inline screenshot and structured element tokens, clicked the grounded controls for `100 + 105`, and verified `205` in a fresh screenshot and accessibility tree. The nine clicks ran as one ordered action group without navigation or layout changes. Calculator remained open. This verifies app launch, state capture, token-based actions, action groups, and screenshot delivery to the selected model; the other desktop tools and live cancellation remain unverified.

Run the synthetic checks and the permission-free SDK check with the installed reviewed Pi package:

```sh
PI_PACKAGE_ROOT=/opt/homebrew/Cellar/pi-coding-agent/0.87.1/libexec/lib/node_modules/@earendil-works/pi-coding-agent node --test scripts/tests/test_pi_cmux_cua.mjs
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s scripts/tests -p test_pi_cmux_cua_policy.py
PI_PACKAGE_ROOT=/opt/homebrew/Cellar/pi-coding-agent/0.87.1/libexec/lib/node_modules/@earendil-works/pi-coding-agent node scripts/tests/pi_cmux_cua_smoke.mjs --discovery-only
```

The separately approved live check uses the same smoke script with `--allow-ui`. It inspects Calculator, clicks 100 + 105, verifies 205 from a fresh state with inline screenshots, and closes only its proxy. It leaves Calculator open. Missing permissions, ambiguous targets, or failed operations stop the test. The script uses an isolated in-memory Pi SDK session, blocks model/network requests, and saves no screenshots or desktop content. It retains the current provider/model identity without reading live credentials or changing provider configuration.

After adding the extension files, restow only `pi` from the primary checkout, then reload Pi. This does not merge settings or change cmux hooks, Worktrunk records, or Hunk ownership.

```sh
stow --restow --no-folding --ignore='__pycache__' --dir=stow --target="$HOME" pi
```

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

### Review tool choice

Hunk is the sole supported viewer for agent-assisted and human-led reviews. The tuicr trial is retired after startup, performance, and diff-binding problems. Do not maintain a parallel tuicr workflow or suggest it as a fallback.

The final tuicr integration remains in Git at `8628421`, tagged locally as `code-review-tuicr-final`, for historical reference. Existing private review artifacts remain under `~/.local/state/pi-code-review/`. Local Hunk findings do not publish to GitHub; publication remains a separate explicitly authorized action.

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
