---
name: recall
description: "Reconstruct recent working context from handoff documents, your Claude Code and Copilot CLI transcripts, live git/gh state, and the shared record (tickets, prior fixes, incidents), then hand back a tight current-state brief. Use for 'recall my work on X', 'catch me up', 'what have I been working on', 'where did I leave off', before starting or resuming work."
disable-model-invocation: true
---

# Recall

**Before you start or resume work, rebuild the user's recent working context and hand back a tight capsule of where things stand now and what to do next.** Use for "recall my work on X", "catch me up", "what have I been working on", or "where did I leave off".

Keep it tight and on-topic. Read only what the in-scope threads need, then stop. Heavy reading fans out to subagents; the main thread keeps only their findings and the final brief.

Context lives in three records. A **handoff document** is the cheapest and most accurate, because a past session wrote it deliberately. Your **transcripts** hold what you did and decided, at much higher cost to read; they span two agents, Claude Code and Copilot CLI, and work on one repo is routinely split across both. The **shared record** holds what happened around the same code under other names: symptoms users keep reporting, fixes that shipped and got reverted, errors still firing in prod.

## 0. Read the handoff first

Check `.context/*-handoff.md` at the repository root before touching a transcript, newest first. The `handoff` skill writes these.

A handoff that covers the named topic and is newer than the last commit on the branch is usually the whole answer. Read it, verify it against live state (step 4), and skip the mining entirely. Say that you did.

Mine transcripts only for what the handoff does not cover, or when none exists — which will be most sessions.

## 1. Classify, then route

Resuming one specific known session is `/resume`, not this. A human-readable summary of your work is a different task. Recall loads working context across recent sessions before you act. If the user already gave you a full state capsule (paths, branch, the change), use it and skip the mining.

## 2. Lock the scope before searching

Pin the window ("recent" is a real range, default the last 7 days), the topic if named, and the workspace (default the active one; never read another project's transcripts without being asked). State the scope back. Never quietly turn "all" into "recent N".

## 3. Fan out across transcripts

**Search both agents.** `agent-read list` prints Claude Code and Copilot CLI sessions in one stream, newest first, with a `CLI` column, the project, and a short id. That listing is the index; never order by UUID filename, which is unsorted. Narrow with `--cli claude` or `--cli copilot` only when the user names one.

The two layouts differ, and Copilot's is the cheaper to scope:

- **Claude Code** — `~/.claude/projects/<slug>/<uuid>.jsonl`, one JSON object per line, one line per message. `<slug>` is the absolute workspace path with every `/` and `.` replaced by `-`, so `/Users/alec/.dotfiles` becomes `-Users-alec--dotfiles`. The project is recoverable only from that slug.
- **Copilot CLI** — `~/.copilot/session-state/<uuid>/`, holding `events.jsonl` plus a `workspace.yaml`. Read `workspace.yaml` first: it carries `cwd`, `git_root`, `repository`, `branch`, a human-readable `name`, and `created_at`/`updated_at`, so you can scope by project, branch, and time without opening the transcript at all. In `events.jsonl` each line has `type`, `timestamp`, `data`; the ones that carry the story are `user.message` and `assistant.message`. Skip `tool.execution_*`, which is the bulk of the file, unless the question is specifically what a past session ran.

**Cap the fan-out at 4 subagents, and say how many you are spawning before you spawn them.** For one or two candidate sessions, skip the fan-out and read directly.

Tell every subagent to grep for the topic first and read only matching sessions and only their relevant regions, and to skip the current session plus obvious noise (subagent, eval, and test sessions). Each returns one block per session: topic, the user's goal, decisions, open threads, struggles and corrections, and artifacts (PRs, tickets, branches), each citing the session id. Raw transcripts stay in the subagents.

## 4. Sweep the shared record

Whenever the topic names a feature, file, subsystem, area, or bug, search Jira and Confluence for it. A named target carries history that never appears in your own transcripts, and that history is the point of the sweep.

Steer the question toward "what's the current state, what's been tried and didn't hold, and what are users still reporting", not "why was this built this way". One search per source, run in parallel with the transcript mining. A null result is a finding; an unavailable MCP is a finding you state. If the `why` skill is installed, reuse its per-source playbooks rather than reinventing each query vocabulary.

Skip this step only for pure activity recall with no named target ("what did I do this week"), where your own history and live state are the entire answer.

## 5. Verify against live state

A transcript or a stale ticket is history, not current truth. Take the PRs, branches, and tickets that the mining and the sweep surfaced and check them with `git` and `gh`. When the answer hinges on what a past session actually did (the tools it ran, files it read, errors it hit), read the full transcript rather than a trimmed copy.

## 6. Write the brief

Group by thread. Stay on the named topic.

## Output contract

Lead with the capsule, then the thread status, then the problems, then the next move. Deeper detail goes below or gets cut.

- **Capsule.** At most 5 bullets. What this work is and where it stands overall.
- **Threads.** One line each, prefixed with exactly one status tag: `[merged #N]`, `[open PR #N]`, `[in flight <branch>]`, `[verified, uncommitted]`, `[reverted #N]`, or `[planned, not started]`. A thread with no tag is not done yet, so tag it.
- **Problems.** At most 5, the recurring ones. Include symptoms users keep reporting and any fix that shipped and was reverted, so the next attempt starts where the last one failed.
- **Next move.** The single most useful next action, concrete.

An adjacent feature or ticket stays out unless it blocks this one. When the capsule and thread lines outgrow a screen, cut detail before you cut threads.

Cite transcript findings by agent and session id (`copilot 358ffd28`, `claude f945ded5`) and shared-record findings by their source (PR #, ticket ID, page URL). Sanitize private context before any public output.

**Reply:** the brief, to the contract above, and whether it came from a handoff or from mining.
