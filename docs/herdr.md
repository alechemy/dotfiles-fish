# Herdr in Ghostty

Ghostty starts Herdr by default. Herdr owns persistent workspaces, tabs, and panes; each pane starts Fish. Run `pi` in a pane as usual. Separate Ghostty windows attach to the same default Herdr server. Use Herdr workspaces to separate projects, and named sessions only when independent servers are needed.

## Everyday keys

| Key | Action |
| --- | --- |
| Cmd+T | Split down. |
| Cmd+D | Split right. |
| Cmd+Shift+T | Create a Herdr tab. |
| Cmd+Shift+H/J/K/L | Focus the pane in that direction. |
| Cmd+Shift+Enter | Zoom the pane. |
| Cmd+1 through Cmd+9 | Switch Herdr tabs. |
| Cmd+Shift+[ or Cmd+Shift+] | Switch to the previous or next tab. |
| Cmd+W | Close the Ghostty window, leaving Herdr running. |
| Ctrl+B, then X | Terminate the Herdr pane. |
| Cmd+Option+W | Close the Herdr tab. |
| Cmd+Ctrl+N | Create a workspace. |
| Cmd+Ctrl+W | Open workspace navigation. |
| Cmd+N | Open another Ghostty window attached to Herdr. |
| Cmd+Shift+W | Close the Ghostty window, leaving Herdr running. |
| Ctrl+B, then Q | Detach, leaving Herdr running. |
| Ctrl+B, then ? | Show Herdr's complete keybindings. |

Cmd+W closes only the outer window. Cmd+N reattaches to the running Herdr session. Ctrl+B, then X terminates a pane without a confirmation prompt.

Ghostty forwards the Herdr shortcuts as CSI-u sequences, and Herdr binds those modified keys directly. The Ctrl+B prefix bindings remain available. Pi keeps Enter and Shift+Enter for newlines and Cmd+Enter or Ctrl+S for submission. Ghostty sends Shift+Enter as CSI-u to preserve its modifier; ESC followed by Return means Alt+Enter and triggers Pi's follow-up submission.

Use `ghostty-shell` for an ordinary Ghostty instance with the original native split shortcuts. Its `shell.conf` overrides both the launch command and Herdr-specific key forwarding. To return permanently to native splits, replace the Herdr command and mapped keybindings in Ghostty's config with the corresponding entries from `shell.conf`.

Mouse selection, split resizing, workspace navigation, and pane menus remain available. The sidebar sorts agents by priority and includes Subagents status text. Notifications go through the outer terminal; Herdr sounds are disabled. The UI uses Ghostty's terminal palette.

## Installation and ownership

Homebrew installs `herdr` through `Brewfile`. The existing Homebrew update schedule owns binary upgrades. Herdr's own version check is disabled; it does not manage the Homebrew installation. Agent-detection manifest updates retain their upstream default.

`scripts/setup-herdr.sh` copies `stow/herdr/_seed/.config/herdr/config.toml` only when the live config is absent, validates it, installs the official Pi integration, and runs the Hunk plugin installer. Setup calls it after Stow. The restow hook also calls it when the Herdr package, setup scripts, or Hunk patch changes. Existing Herdr preferences remain app-owned; later seed changes do not overwrite them. The Hunk installer owns only its marked keybinding block.

The `herdr` Stow package links the local UI-state extension and the shared Herdr skill. `_seed` is excluded. The `ghostty` package links the launch/keybinding configuration and `ghostty-shell`.

Herdr installs its bundled integration at `~/.pi/agent/extensions/herdr-agent-state.ts`, or beneath `PI_CODING_AGENT_DIR` when explicitly set. Reinstalling the integration replaces that managed file. Re-run `scripts/setup-herdr.sh` after reviewing integration changes bundled with a Herdr update. The script does not replace Pi's package list or the guarded local Subagents build.

Launch Herdr from Ghostty rather than a launchd service. A server started in a background or SSH launch context may not have interactive macOS Keychain access. Check `launchctl managername` from a pane when diagnosing this; the normal GUI context is `Aqua`.

## Worktrunk tasks

[Worktrunk](worktrunk.md) allocates sibling task worktrees. From a shell in the repository's workspace, `wt pi new <branch>` opens a named task tab and starts Pi. `wt pi open <branch>` focuses or resumes it. Each worktree keeps its own Hunk review recipient. `wt pi list` refreshes native Pi activity markers before listing worktrees.

After sending Hunk comments and closing a task tab, `wt pi remove <branch>` checks ownership, sessions, panes, processes, and files before removing the checkout and retaining its branch. The launcher is for human shell use. Agents retain Subagents' project-pane and delegation APIs. Subagents uses its official Worktrunk allocator while retaining lifecycle ownership of `pi-subagents/` worktrees.

## Pi and Subagents

The official integration reports Pi lifecycle state and native session identity. `herdr-ui-state.ts` maps Pi's `ui_prompt_start` and `ui_prompt_end` events onto the official `herdr:blocked` sibling contract, which the stable integration does not derive itself. It reports a fixed label, never the dialog title or content, and ignores headless modes. It does not answer or replace dialogs.

The installed Subagents build reports async activity metadata automatically inside Herdr. Its project-pane actions open independent project-owned Pi sessions. Its inspector actions open dashboards for existing children, not interactive child-session attachments. Ordinary delegated runs remain headless, with the existing provider and tool restrictions.

Herdr 0.9.0's Pi integration v8 does not consume Subagents' `herdr:busy` event. The sidebar can show async activity text while the root Pi lifecycle state is idle. Treat the text as additional information, not proof of a running root turn.

Herdr's `agent prompt` submits with Enter. With these Pi bindings, use the Subagents project launcher or explicitly send text and Ctrl+S to an idle Pi pane instead. Do not send submission keys into an approval dialog.

## Hunk review integration

The `jhochenbaum.hunkdiff` Herdr plugin uses the existing `/opt/homebrew/bin/hunk`. From the Pi pane that should receive feedback:

| Key | Action |
| --- | --- |
| Ctrl+B, then f | Open or reuse the worktree's Hunk review split. |
| Ctrl+B, then Shift+A | Review staged changes. |
| Ctrl+B, then Shift+C | Review the latest commit. |
| Ctrl+B, then Shift+B | Review committed branch changes. |
| Ctrl+B, then Shift+F | Send unsent human comments to the associated agent. |

In Hunk, select a line, press `c`, write a note, and save it with Ctrl+S. Send before closing Hunk. Sending is explicit; agent annotations are excluded. Successful sends record comment IDs and remove those comments from Hunk, preventing duplicate delivery. These are local review comments, not GitHub PR reviews.

One review and recipient are tracked per worktree. Opening or reusing the review from a different Pi pane selects that pane as the recipient. Automatic opening and status-driven recipient reassignment are disabled, so another session finishing in the same checkout cannot take over the review. Separate worktrees remain independent.

The local patch submits Pi feedback with Ctrl+S after Herdr's bracketed paste. Enter remains a newline. It rejects missing recipients and Pi panes that are busy, blocked, or in an unknown state; failed sends retain the comments. If submission fails after the paste, inspect Pi's draft before retrying. Other agents retain Herdr's normal submission behavior.

### Review scope and receipts

These actions are distinct. The default `review` uses automatic scope: dirty or
untracked files select working-tree mode; otherwise it selects branch mode when a base
resolves and commits are ahead, or falls back to working-tree mode. `review:staged`
shows index changes, `review:commit` shows the latest commit, and `review:branch`
explicitly compares committed branch changes with a base. None should be described as
covering every task change without checking its displayed target and exclusions. Hunk's working-tree mode
includes untracked files by default; plain `git diff` does not. A committed
`<base>...HEAD` comparison excludes staged, unstaged, and untracked changes. Use the [code-review scope rules](../stow/agents/.agents/skills/code-review/references/scope.md)
for exact Git comparisons and inventory.

The plugin's `[review] base` preference selects a branch-review base. When unset, the
reviewed 0.3.0 resolver tries a usable upstream (`@{u}`), then `origin/HEAD`, then local
`main`, `master`, or `trunk`. Upstream and remote-default candidates ending in the current
branch name are skipped. A local conventional base can still equal HEAD. An invalid
configured base warns and tries automatic resolution; if no base resolves, branch mode
falls back to working-tree mode. That fallback is not successful whole-branch review.
Always verify the displayed target against the intended integration target, especially
for a non-`main` target or divergent history.

To override the comparison explicitly, first validate the intended ref and merge-base,
then reload the exact task worktree's session:

```sh
hunk session reload --repo /path/to/task-worktree -- diff release/integration...HEAD
```

First invoke the review action from the intended Pi pane. Explicit invocation selects
the feedback recipient; a shell alias in an unrelated pane is not equivalent. Sending
from an agent pane selects that pane for the send; sending from the review pane uses
the recorded recipient. Verify the recipient before sending. Use
`hunk skill path` for the installed interaction instructions instead of treating this
reference as a complete CLI manual. Headless Git review needs no Hunk TUI.

Before successful feedback delivery clears comments, record unresolved conclusions in
a local [coding-task handoff receipt](../stow/agents/.agents/skills/handoff/SKILL.md#coding-task-review-receipt).
Keep the integration target, merge-base, reviewed HEAD, scope, tested dirty state,
validation results, and next action tied to that evidence. Recheck after edits or
rebases. Reference existing local artifacts rather than copying patches or transcripts.

### Plugin ownership and updates

`scripts/install-herdr-hunk-diff.sh` fetches plugin 0.3.0 at commit `b063856e85436668a165e511ed16a503ea729752`, applies `scripts/patches/herdr-hunk-diff.patch`, builds it, runs its tests, and links it into Herdr. Source and dependencies live under `~/.local/share/herdr-hunk-diff/<revision>-<patch-hash>`, outside Stow. A failed build leaves the active checkout intact; completed builds are reused. The upstream dependency tree includes its pinned Hunk package, but plugin configuration selects the existing Homebrew executable.

The patch covers Pi submission, explicit recipient reassignment when reusing a pane, and conflict detection for Herdr's array-valued keybindings. The installer uses the plugin's keybinding helper with the bindings above, preserves conflicting user bindings, and reloads Herdr without restarting its server. Do not run the upstream `setup-keys` action: it would replace this block with upstream shortcuts, including a collision with pane swapping.

Plugin preferences at `~/.config/herdr/plugins/config/jhochenbaum.hunkdiff/config.toml` are copied from the tracked seed only when absent. They remain editable and are never replaced during updates. Review the upstream changes, update the pinned revision and patch, and rerun the installer to upgrade. Do not use `herdr plugin install` over this locally linked build.

To disable the integration, run `herdr plugin disable jhochenbaum.hunkdiff`. To remove its shortcuts, invoke `herdr plugin action invoke jhochenbaum.hunkdiff.remove-keys` while it is enabled, then reload the config. Setup will recreate the managed shortcuts when rerun.

## Persistence and private data

Detach keeps processes alive. Server restart stops processes, restores the layout, and attempts native Pi conversation resume. Restored shells and dev servers are not continuations of their old processes. A binary upgrade does not necessarily replace a compatible running server; stop it only when ready to end its pane processes.

Pane-screen history stays disabled. Runtime sessions, logs, socket files, and worktree state remain outside the repository. Pane metadata and Pi session paths can still reveal private project information.

## Verification

Run the focused checks without touching the default server:

```sh
python3 -m unittest discover -s scripts/tests -p 'test_herdr_setup.py'
python3 -m unittest discover -s scripts/tests -p 'test_restow_changed.py'
node --test scripts/tests/test_herdr_ui_state.mjs scripts/tests/test_hunk_patch.mjs
python3 -m unittest discover -s scripts/tests -p 'test_hunk_scopes.py'
HUNK_PLUGIN_ROOT=/path/to/reviewed/plugin node --test scripts/tests/test_hunk_runtime.mjs
python3 scripts/tests/herdr-smoke.py --hunk
```

The offline Hunk tests check Git scope contracts and exact tracked patch bodies. The
opt-in runtime test requires the installer-pinned source plus its existing dependencies;
it verifies source/patch identity and compiles into a disposable directory. It exercises
the actual resolver, dispatcher, status handler, index, submission adapter, and key
installer with fake Herdr/Hunk I/O. It makes no service or model calls and reads no live
review state. It does not prove TUI rendering or physical keyboard delivery.

The smoke test uses a disposable HOME, a named Herdr server, a synthetic Pi session, and a PTY. It makes no model calls. It checks Enter and Shift+Enter newline behavior, Cmd+Enter and Ctrl+S submission, explicit confirmation and cancellation, blocked-state reporting, split/tab keys, detach persistence, and exact native session identity after a server restart. Its temporary path stays short enough for macOS Unix-domain sockets. With `--hunk`, it also links the installed plugin into the disposable profile, starts a separate loopback Hunk daemon, creates a synthetic Git change and a human inline comment through the TUI, and checks explicit sending, blocked-dialog retention, Pi receipt, comment cleanup, and deduplication. It checks repeated configuration preserves user preferences. Omit `--hunk` to test Herdr without the plugin.

These checks do not exercise macOS sleep/wake, desktop notification delivery, or physical keyboard input through Ghostty. Ghostty's own config validator checks both profiles.

## Upstream references

- [Stable documentation index](https://herdr.dev/llms.txt).
- [Installation](https://herdr.dev/docs/install/).
- [Pi integration](https://herdr.dev/docs/integrations/#pi).
- [Session restore](https://herdr.dev/docs/session-state/).
- [Keyboard configuration](https://herdr.dev/docs/keyboard/).
- [Herdr Hunk Diff plugin](https://github.com/jhochenbaum/herdr-hunk-diff).
- [Herdr plugin lifecycle](https://herdr.dev/docs/plugins/).
- [Hunk keybindings](https://hunk.dev/docs/configure/keybindings/).
