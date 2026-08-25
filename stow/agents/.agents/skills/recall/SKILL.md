---
name: recall
description: Reconstruct recent working context from handoff documents, normalized local agent transcripts, live repository state, and available shared records. Use for "recall my work on X", "catch me up", "what have I been working on", or "where did I leave off".
compatibility: Requires agent-reader with JSON transcript commands, git, and jq. GitHub CLI and shared-record integrations are optional.
disable-model-invocation: true
---

# Recall

Rebuild the user's recent working context before work resumes. Return a tight capsule of current state and the next action; do not start implementing.

## 1. Prefer an explicit handoff

Check `.context/*-handoff.md` at the repository root, newest first. A handoff that covers the topic and is newer than the relevant code changes is usually sufficient. Verify it against live state, report that recall came from the handoff, and mine transcripts only for gaps.

A request to reopen one known session belongs to the harness's resume command, not this workflow.

## 2. Fix the scope

Determine the workspace, topic, and time window from the request. Default to the active workspace and the last seven days. State the scope before searching. Never inspect another workspace without being asked, and never silently narrow an explicit request for all history.

## 3. Discover and normalize transcripts

Use agent-reader as the parser rather than reimplementing harness formats:

```bash
agent-read list --json
agent-read transcript --cli <cli> --session <id>
```

The session index contains every locally available supported harness. Filter it by `cwd`, `last_activity`, and the requested window; filter by `cli` only when the user names a harness. Do not assume a fixed harness set. Exclude the current session when it is identifiable, along with obvious test or evaluation sessions.

Search candidate normalized transcripts for the topic before reading them in full. Read one or two candidates directly. If the environment provides independent workers and more candidates genuinely need review, parallelize distinct sessions with at most four workers; otherwise process them sequentially. Parallel workers are an optimization, never a requirement.

For each relevant session, capture:

- the user's goal;
- decisions and evidence;
- completed and open work;
- corrections, failed approaches, and unresolved problems;
- branches, commits, pull requests, tickets, and paths.

Keep raw transcript text out of the final response. Cite findings by harness and short session ID, such as `pi 01a03a0d`, `claude f945ded5`, or `copilot 358ffd28`.

## 4. Search available shared records when useful

For a named feature, subsystem, file, or bug, search relevant configured records such as an issue tracker, wiki, incident system, or local knowledge base. Use an available reviewed integration or CLI; no particular MCP server is required. Skip unavailable sources without treating them as a blocker, and state which sources informed the result. Pure activity recall with no named target needs no shared-record sweep.

Focus shared-record searches on current state, prior failed fixes, reversions, and symptoms still being reported.

## 5. Verify current state

Transcripts and tickets are history. Verify surfaced branches, commits, pull requests, and working-tree claims with `git`, `gh`, or the relevant local CLI. Read a normalized full transcript when the answer depends on exact actions or errors rather than a session summary.

## Output contract

Stay on the requested topic and keep the result within one screen when possible.

- **Capsule:** at most five bullets describing the work and its current state.
- **Threads:** one line per thread with a concrete status such as `[merged #N]`, `[open PR #N]`, `[committed <hash>]`, `[in flight <branch>]`, `[verified, uncommitted]`, or `[planned, not started]`.
- **Problems:** at most five recurring or unresolved issues, including reverted fixes when relevant.
- **Next move:** one concrete action.

End by saying whether the brief came from a handoff, transcript mining, or both. Sanitize secrets and private context before any public output.
