---
name: slop-review
description: Review a codebase's prose surface — READMEs, docs, comments, public strings, package metadata — for AI-written slop, audience leaks, internal/private leakage, redundancy/drift, inaccuracy, scaffold leftovers, and LLM-generation detritus. Invoke only when the user explicitly uses `/slop-review` or asks to review a repo for slop / smells / sloppy comments / stale or inaccurate docs.
user_invocable: true
---

# slop-review

A reviewer's lens for the text humans read *around* code — not the logic. It flags writing that was authored by (or for) an AI agent and leaked into human-facing material, plus the inaccuracies, redundancy, internal-detail leakage, and leftovers that accumulate in agent-assisted repos.

It is a **review** tool: by default it reports findings and recommends exact changes; it applies them only when asked. It does not hunt for correctness bugs (use `/code-review`) or rewrite long-form prose against the 48 writing tropes (use `/prose-check`). The two are complements — `slop-review` decides *what* in a codebase is slop; `prose-check` is a deeper rewrite engine for a single prose document.

## The north-star test

For every sentence, comment, and claim, ask three questions:

1. **Would a specific human reader — the one this file is actually for — write this unprompted, and need it?**
2. **Is every concrete claim in it true against the repo right now?**
3. **If this repo went public tomorrow, is this safe to expose** — no private names, ticket IDs, internal roadmap, or team politics?

If the text is reassurance, emphasis, preemptive defense, restatement of an adjacent diagram/table/code, or an instruction aimed at a model — it's slop. If a load-bearing claim hasn't been checked against ground truth, check it before trusting it (or before flagging its opposite). If it would leak internal detail to a public reader, cut or neutralize it (category **H**) — that test ignores the human/agent audience split.

**AI prose over-signals.** It closes loops ("…, nothing else."), pre-defends scope ("no Turborepo, no web"), restates what it just showed, and answers objections no reader raised — because it optimizes for sounding complete, not for informing a particular person. That instinct is the thing you are hunting.

## Audience determines everything

The same sentence can be correct in one file and slop in another. Audience is set by the **file's purpose**, not its words.

- **Agent-facing files** — `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `GEMINI.md`, `.claude/skills/**`, copilot/aider instruction files. Here, "do not substitute the stack", "never push without say-so", "the session scratchpad" are *correct* — the reader is a model. **Do not flag these as audience leaks.**
- **Human-facing files** — `README.md`, `CONTRIBUTING.md`, `DECISIONS.md`/ADRs, package `README`s, `--help` text, user-visible strings, error messages, code comments. A new teammate reads these. Agent-process language here is a leak.

Before flagging, identify which kind of file you are in. The most common false positive is flagging legitimate agent-instruction text inside an agent-instruction file.

Audience exempts only *process* text, not *content*. Confidential material — personal names, ticket IDs, internal roadmap, private tooling, cross-team politics — is out of place even in `AGENTS.md`/`CLAUDE.md`, where the rule above would otherwise tell you to stand down. That is category **H**, and it is not gated by audience.

## The taxonomy

Tag each finding with its category.

**A — Audience leak.** Agent-process or model-meta text in a human-facing file.
- Model-meta: "training data", "training cutoff", "knowledge cutoff", "as an AI".
- Agent workflow: "never push without explicit say-so", "draft locally; the maintainer publishes", "session scratchpad, never /tmp", "never write to Jira directly", and onboarding prose whose real content is "go read AGENTS.md".
- Guardrail enumerations that read as model instructions ("`View` not `div`, `Text` not `span`, `onPress` not `onClick`, …" as an exhaustive do/don't list).
- *Test:* would this make sense to a human teammate if no AI were in the loop? If it only parses as an instruction to a model, it's a leak. *(Real case: a contributing guide's "Never push, open a PR, or write to Jira without explicit say-so. Draft locally; the maintainer publishes." — that is a verbatim agent safety rule, meaningless as a human contribution norm.)*

**B — Preemptive / defensive over-signal.** Sentences that defend an unchallenged boundary or manufacture closure.
- Negative-scope callouts: "no Turborepo, no web", "not a from-scratch X", "this is the mobile app **only**".
- Closure fragments: "Two packages, nothing else.", "That's it.", "—by design.", "no more, no less."
- Restating an adjacent diagram, table, or code block in prose.
- *Test:* new information, or reassurance/emphasis/pre-defense? Apply the unprompted-human check.

**C — Inaccuracy / unverified claim.** The highest-severity category; verify, don't guess.
- Versions, counts, dates that drift from the lockfile / `package.json` / `git log`.
- Documented paths, directories, commands, scripts, or env vars that don't exist.
- Confident factual claims that are simply wrong. *(Real case: a README stating the app "is not endorsed or certified by" the very vendor that does endorse it.)*
- *Rule:* check every load-bearing claim against the repo (filesystem, lockfile, manifests, history) before trusting it or asserting the opposite. No speculation.

**D — Redundancy / drift risk.** The same fact copy-pasted where copies will diverge.
- A version or rule stated three ways across files; a CI gate documented inconsistently with what CI runs.
- *Distinguish from DRY-by-design:* an `@import` expansion, or a generated artifact guarded by a drift test, is a **single source** — not redundancy. Only flag genuine copy-paste that can drift.

**E — Comment noise.** (Mirror the project's own comment conventions; match its density.)
- Changelog narration: "// in OnInit, not the constructor, so …", "// fixed null check", contrasting code with a prior version.
- Restating what the code plainly says; scaffold/boilerplate comments.
- *Any tracked file with comments — not just `.ts`/`.js` source.* Config dotfiles (`.prettierignore`, `.gitignore`, `.editorconfig`), CI/workflow YAML, and build config (`babel.config.js`, `metro.config.js`) accrete the same restate-the-obvious noise: `# Tool-managed lockfile` above `pnpm-lock.yaml`, or `# Dependencies (Prettier already skips node_modules)` above `node_modules/`. Same bar — does the comment explain non-obvious behavior or document an entity?
- *Protect, never flag:* API/function/class docs, genuinely non-obvious decisions, and the reasoning behind tricky logic. The goal is signal, not zero comments.

**F — Scaffold / placeholder leftover.** `create-*-app` dead assets, demo screens/routes, lorem/placeholder copy, a template `LICENSE` with the wrong holder, wiring-proof stores nothing consumes, stray `TODO`/`FIXME`, debug `console.log`.
- **LLM-generation detritus:** stray wrapper tags leaked into a committed file (`</content>`, `<file>`, `<answer>`), trailing tool-call markup or a "Here is the updated file:" preamble, duplicated frontmatter, or a fenced ` ```markdown ` wrapper around an entire `.md`. Literal and cheap to grep — sweep for these across all tracked files, not just the prose surface.

**G — Marketing / filler prose.** Generated-sounding connective tissue.
- Feature-list run-ons ("Browse …, search …, dig into …, and keep …").
- Empty intensifiers and hollow rationale ("pinned deliberately", "carefully chosen", "X has the full rationale").
- Self-congratulatory naming of the team's own code: "the crown jewel", "the heart of the app", "our beautiful/elegant X". Say what it does, not how proud you are of it.

**H — Internal / private leakage.** Confidential or team-internal detail that does not belong in version control — orthogonal to audience, so flag it even in agent-facing files.
- Personal names and interpersonal/political context: "exists to satisfy maintainers Travis & Tim", "never assign them X", turf and who-owns-what.
- Internal tracker IDs and tickets: `JIRA-123`, `ABC-45`, "TMDB-105 stays open".
- Private roadmap / phase planning and codenames for unshipped features: "deferred to v2/v3", "Layer 3 later", an internal feature name.
- References to private tooling or boards: a personal task manager, an internal-only tracker, "the dev's board", a named internal design file.
- Cross-team status and politics: "pending the web team's buy-in", "held until legal signs off".
- Dated internal decisions / status stamps: "decided 2026-06-24", "Status (2026-06-25): …", "verified June 2026".
- *Test:* if the repo went public tomorrow, would this name someone, embarrass someone, or expose internal process? If so, cut or neutralize it — keep the engineering signal, drop the private specifics (e.g. "out of scope for now: notifications, video, ads" instead of a v2/v3 roadmap).

## Method

1. **Classify audience** for each file in scope (human vs agent). This gates categories A and B.
2. **Scope the surface.** Prioritize, in order: top-level `README`/`CONTRIBUTING`/onboarding docs → ADRs/decision docs → package `README`s and metadata → public strings and `--help`/error text → comments in any tracked file → scaffold/asset leftovers. "Comments" is not just `.ts`/`.js` source — config dotfiles (`.prettierignore`, `.gitignore`, `.editorconfig`), CI/workflow YAML, and build config carry the same noise; don't let a markdown-and-source sweep skip them. Do **not** slop-review business logic; you are reviewing prose, not behavior.
3. **Verify before flagging.** For every category-C candidate, check ground truth (`git ls-files`, the lockfile, `package.json` scripts, the actual path). Confirm a smell before reporting it — a plausible-but-wrong flag is worse than a miss.
4. **Classify severity.** Separate **Confirmed** (verified, clearly slop) from **Optional polish** from **Judgment call** — text that smells AI-written but is load-bearing (e.g. a "the web app is a separate stack" line that is the actual *reason* for an architecture choice). Flag judgment calls; do not auto-delete them.
5. **Don't over-correct.** Recommend the minimal change that removes the smell while preserving real information. When in doubt, surface it rather than cut it.
6. **Report, then apply on request.** Default output is the findings report. Apply edits only when the user says so; keep the original meaning intact.

## Scaling

- **Small target** (a file or two): review inline.
- **Whole repo:** fan out parallel read-only agents by lane — (a) doc accuracy vs ground truth, (b) audience leaks + over-signal + internal/private leakage, (c) comment noise across all tracked files (source **and** config dotfiles/CI YAML), (d) scaffold/asset leftovers + generation detritus — then dedupe and adversarially re-verify the load-bearing findings before reporting. One level of delegation is enough.

## Output format

Lead with a one-line verdict (clean / minor polish / needs a pass). Then findings, most important first, **Confirmed** before **Optional** before **Judgment call**. For each:

- **Category** (A–H) and tag.
- `file:line` and a short quote of the offending text.
- **Why** it's a smell (which test it fails).
- **Exact recommended change** (the replacement text, or "delete").

Close with a short "deliberately kept" list when you left smell-adjacent text in place on purpose, so the reasoning is visible and the user can overrule it.

## Arguments

- `/slop-review` — review the repo's human-facing surface + comments (default).
- `/slop-review <path>` — restrict to a file or directory.
- `/slop-review --diff` — review only what the working tree / branch changed (good as a pre-commit gate).
- `/slop-review --fix` — after reporting, apply the Confirmed findings to the working tree; leave Optional and Judgment-call items for the user. Never commit or push as part of the skill.

## Self-discipline

This skill is itself human-facing prose. Hold it to its own north-star test: no closure fragments, no pre-defense, no restating. If a future edit adds a "that's it — nothing more" flourish here, it has failed its own review.
