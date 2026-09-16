---
name: herdr
description: Manage Herdr workspaces, tabs, panes, and agent status when the user asks to use Herdr. Use for terminal layout, project sessions, and Herdr troubleshooting.
---

# Herdr

Read the installed operating guide with `herdr --skill`, then the relevant command group's help. Use `herdr --help` for discovery, not bare `herdr`, which attaches a client.

Control ordinary sessions only from inside Herdr, with `HERDR_ENV=1`. Use inherited socket context and explicit pane IDs. Setup verification may create an isolated named session with a disposable HOME and config; never stop the user's default server to test a change.

## Local conventions

Ghostty owns windows; Herdr owns the panes and tabs inside them. Herdr starts Fish, and Pi runs normally in those shells. Cmd+N from Herdr invokes `ghostty-shell` to open a separate Ghostty instance with native splits and no Herdr. Launching the default profile with `open -na Ghostty` attaches another Herdr window.

The installed `pi-subagents` package owns delegation and its provider restrictions. Prefer its `project.open` action for project-owned Pi sessions and `inspector.open` for existing async children. An inspector is a dashboard, not a child-session attachment. Do not bypass the delegation guard by starting a substitute agent through the terminal.

Herdr uses Ctrl+; as its prefix instead of stock Ctrl+B, paralleling AeroSpace's Hyper+; service mode. Prefix `w` closes the pane, with stock `x` retained as an alias; workspace navigation uses Cmd+Ctrl+W. Other stock prefix actions remain available. Cmd+Shift+H/J/K/L focuses panes, while Hyper+H/J/K/L focuses AeroSpace windows.

Pi uses Enter for a newline and Cmd+Enter or Ctrl+S to submit. Herdr's `agent prompt` sends Enter, so do not use it to submit to Pi with these bindings. For an explicitly requested prompt to an existing idle Pi pane, inspect its agent state first, send the quoted text with `herdr pane send-text "$pane_id" "$prompt"`, then submit with `herdr pane send-keys "$pane_id" ctrl+s`. A write receipt is not proof of a started turn. Never send prompts or approval keys into a blocked dialog.

Keep one writer per checkout and use worktrees for concurrent writers. Keep background panes unfocused. Pane and agent listings can contain private paths and task labels; report only the metadata needed for the task. Never print raw live transcript matches.

## Hunk reviews

The `jhochenbaum.hunkdiff` plugin opens the existing Homebrew Hunk in a review split. Ctrl+;, then `f` opens or reuses the review from the intended recipient's Pi pane; Ctrl+;, then Shift+F sends human comments explicitly. One recipient is tracked per worktree. Automatic status-based reassignment is disabled. The local plugin patch handles Pi's Ctrl+S submission and retains comments when delivery fails or Pi is blocked or busy.

For agent-side inspection and annotations, run `hunk skill path` and read that installed skill. Keep review comments local unless the user explicitly authorizes remote publication.

## Configuration and maintenance

The dotfiles repository's `docs/herdr.md` records keybindings, setup ownership, and verification commands. Herdr owns its live `~/.config/herdr/config.toml`; the repository carries a copy-if-absent seed. The official Pi integration is installed by `scripts/setup-herdr.sh`. The adjacent `herdr-ui-state.ts` reports blocking Pi dialogs through the official sibling event contract.

Homebrew owns binary updates. A compatible running server may keep the older version until restarted. Stopping a server kills its pane processes; detaching does not. Do not restart it without explicit permission.
