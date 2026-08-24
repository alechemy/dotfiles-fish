---
name: handoff
description: Compact the current conversation into a handoff document another session can pick up from. Use at a phase boundary, before /clear, or when the user says "hand this off" or "write a handoff".
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarising the current conversation so a fresh session can continue the work.

## Where it goes

`.context/<topic>-handoff.md`, relative to the repository root (`git rev-parse --show-toplevel`); outside a repository, `~/.context/`. Create `.context/` if it does not exist.

`<topic>` is a kebab-case slug for the work, not the date and not the session id — `entity-filing-candidates-rework`, `aerospace-gaps-cpu`. The directory is conventionally gitignored: a handoff is local working state, so never `git add` one.

Writing to the repo rather than a temp directory is what makes the document survive long enough to be worth writing, and is what `recall` looks for first.

## What goes in it

Include a "suggested skills" section naming which skills the next session should invoke.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

State the current branch, any uncommitted work, and the single next action, so the next session can orient without re-deriving it.

Redact any sensitive information: API keys, tokens, passwords, personally identifiable information.

**Reply:** the path written, and the next action in one line.
