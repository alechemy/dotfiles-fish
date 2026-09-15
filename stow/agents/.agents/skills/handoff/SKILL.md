---
name: handoff
description: Compact the current conversation into a handoff document another session can pick up from. Use at a phase boundary, before clearing context, or when the user says "hand this off" or "write a handoff".
---

Write a handoff document summarising the current conversation so a fresh session can continue the work. If the invocation includes arguments, use them as the next session's intended focus.

## Where it goes

Use `.context/<topic>-handoff.md` at the repository root. Outside a repository, use `~/.context/<workspace-slug>-<topic>-handoff.md`. Create the directory if needed. Resolve the location in Bash, matching `recall`:

```bash
set -euo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || root=
workspace=${root:-$(pwd -P)}
context=${root:-$HOME}/.context
```

`<topic>` is a kebab-case slug for the work, not a date or session ID, such as `widget-launch`. Outside repositories, choose a workspace slug that distinguishes the current absolute directory from other nonrepo work. If a filename already exists, check its workspace and topic headers before replacing it; choose a distinct workspace slug on collision. Never overwrite another workspace's handoff.

The directory is conventionally gitignored. A handoff is local working state, so never `git add` one.

Writing to the repo rather than a temp directory is what makes the document survive long enough to be worth writing, and is what `recall` looks for first.

## What goes in it

Start with these exact single-line headers so `recall` can select a handoff without reading unrelated bodies:

```text
Workspace: /tmp/fictional-widget
Topic: widget-launch
Updated: 2026-07-24T18:00:00Z
```

Use the resolved `$workspace`, the topic slug, and the current UTC timestamp. Include the covered time range in the body. These headers are required both inside and outside repositories; the nonrepo directory holds multiple workspaces. The `recall` skill's shell lookup checks workspace and topic exactly before opening a file.

Include a "suggested skills" section naming which skills the next session should invoke.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

State the current branch and uncommitted work when in a repository. Outside one, state the workspace and relevant local artifacts instead. Give the single next action so the next session can orient without re-deriving it.

## Coding-task review receipt

For a coding task, include a compact receipt in this local handoff, or link an existing
receipt in `.context/`. Noncoding handoffs need no Git fields. Use the
[code-review scope rules](../code-review/references/scope.md); do not substitute the last
commit or working-tree diff for whole-task review.

Record:

- Intended integration target, selected base revision and merge-base, current branch,
  and exact reviewed HEAD.
- Review scope: comparison command, selected paths, and exclusions. State whether
  staged, unstaged, and untracked changes were included. `<base>...HEAD` covers committed
  branch changes only. An invalid-base fallback is not whole-branch review.
- Validation commands, results, and tested state. Identify tested dirty state separately
  from HEAD with a local artifact reference or content fingerprint. A commit ID alone
  cannot identify unstaged or untracked test inputs.
- Unresolved findings and decisions, including anything not verified. Record these
  conclusions before successful Hunk feedback delivery clears the comments.
- Relevant session/run references and existing evidence paths. Reference artifacts;
  do not copy patches, private transcripts, or all Hunk comments into the receipt.
- Service ownership: task-owned services/resources still running, who may stop them,
  and resources that must remain untouched. Use `none` when applicable. Checkout
  removal does not stop a service or authorize stopping shared services.
- One next action, including any review or validation that remains.

Recheck the receipt after edits or rebases. On recall, compare the current HEAD, dirty
state, and integration target with the recorded state before relying on the results.
Mark changed evidence stale and rerun the affected checks; do not silently relabel an
old review as current. For live Hunk interaction, use `hunk skill path` and invoke review
from the intended Pi pane to select its feedback recipient. Headless Git review needs
no Hunk TUI.

Write only sanitized task context suitable for a later agent session. Omit API keys, tokens, passwords, personal identifiers, unrelated private prose, and raw tool/config/transcript dumps. Reference sensitive artifacts by safe descriptions rather than copying their contents. When safe summarization is uncertain, request user-provided sanitized context.

**Reply:** the path written, and the next action in one line.
