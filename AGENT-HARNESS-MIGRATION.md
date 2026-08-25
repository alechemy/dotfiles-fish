<!-- markdownlint-disable MD013 -->

# Agent harness migration plan

Last updated: 2026-08-24

This plan combines the original Pi adoption plan with the later decision to make the shared agent configuration provider- and harness-agnostic. It is the cross-session source of truth for this migration.

Checklist meanings:

- `[x]` — implemented and verified in the current working tree
- `[-]` — partly complete
- `[ ]` — pending

The completed work is currently uncommitted. Commit it as a checkpoint before beginning the larger directory and skill moves.

## Goal

Use Pi as the daily coding harness while keeping model choice independent from the harness:

- OpenAI Codex through ChatGPT Pro is the default.
- GitHub Copilot provides subscription-backed alternatives.
- oMLX provides local models through the same `/model` interface.
- Claude Code may remain as a narrow fallback for the included Claude Max allowance.
- Shared instructions and skills live outside every provider-specific directory.
- Harness adapters contain only behavior that genuinely depends on that harness.
- Dead hooks, duplicated prompts, unused MCP servers, wrappers, and integrations are deleted rather than ported automatically.

## Constraints and decisions

- Pi's third-party Anthropic OAuth uses paid extra usage, not the included Claude Max allowance. Direct Anthropic must remain outside normal model cycling unless that changes.
- GitHub Copilot models may be used through Pi, subject to the company's third-party OAuth and data-use policy.
- Pi and oMLX own runtime files under `~/.pi/agent/`. OAuth credentials, sessions, model stores, generated model configuration, and backups must remain outside Stow and git.
- Project trust remains opt-in. Do not globally trust project-local extensions or packages.
- Do not add Pi packages, MCP bridges, subagents, or custom planning systems until a real workflow requires them and the source has been reviewed.
- Do not reproduce Claude-specific workarounds in Pi unless the underlying failure also exists in Pi.
- Configuration should be measured by usefulness and prompt cost, not by parity with an older harness.

## Target architecture

```text
stow/agents/.agents/
├── AGENTS.md                 # Canonical user-level instructions
└── skills/                   # Harness-neutral Agent Skills

stow/pi/.pi/agent/
├── AGENTS.md                 # Symlink to the canonical instructions
├── keybindings.json          # Pi-only input behavior
└── settings.fragment.json    # Portable Pi preferences; merged, not stowed

stow/claude/.claude/          # Optional thin Claude Code compatibility adapter
├── CLAUDE.md                 # Symlink to the canonical instructions
└── settings.json             # Only settings genuinely required by Claude Code

stow/copilot/.copilot/        # Delete if standalone Copilot CLI is retired
└── copilot-instructions.md   # Otherwise symlink to canonical instructions
```

Projects should eventually use `AGENTS.md` as their canonical context file. If Claude Code remains installed, `CLAUDE.md` should be a compatibility symlink rather than a second source of instructions.

## Provider strategy

| Use | Provider/model path | Status |
| --- | --- | --- |
| Daily driver | `openai-codex/gpt-5.6-sol:high` | Active |
| Claude alternative without direct Anthropic billing | `github-copilot/claude-opus-5:high` | Active |
| Independent second opinion | `github-copilot/gemini-3.1-pro-preview:high` | Active |
| Local work | `omlx/*:off` | Active |
| Included Claude Max usage | Claude Code application | Decision pending |
| Direct Anthropic in Pi | Available through `/model`, excluded from cycle | Intentional |

## Phase 1: establish Pi without importing legacy complexity

### Installation and authentication

- [x] Add `pi-coding-agent` to `Brewfile`.
- [x] Authenticate OpenAI Codex, GitHub Copilot, and Anthropic in Pi.
- [x] Confirm oMLX appears as a Pi provider.
- [x] Keep `auth.json` machine-local and mode 600.
- [x] Add a setup reminder when the three subscription logins are missing.

### Portable Pi configuration

- [x] Add `stow/pi/`.
- [x] Add Pi keybindings: Enter inserts a newline; Cmd+Enter or Ctrl+S submits.
- [x] Add a tracked settings fragment with the default model, reasoning level, theme, and scoped models.
- [x] Add `scripts/merge-pi-settings.sh`.
- [x] Merge portable settings over Pi's live app-owned settings while preserving runtime fields such as `lastChangelogVersion`.
- [x] Run the merge from `setup.sh`.
- [x] Rerun the merge from `restow-changed.sh` when the fragment or merge script changes.
- [x] Exclude the settings fragment itself from Stow.
- [x] Keep live `settings.json`, `models.json`, `models-store.json`, backups, sessions, and OAuth state out of Stow and git.

### Shared instructions

- [x] Make the existing global instruction text harness-neutral.
- [x] Link Pi's global `AGENTS.md` to that instruction source.
- [x] Move the canonical text to `stow/agents/.agents/AGENTS.md`; retained harnesses expose it through compatibility symlinks.

### Intentional omissions

- [x] Install no Pi packages.
- [x] Add no Pi extensions.
- [x] Load no legacy Claude skill directory into Pi.
- [x] Add no MCP bridge.
- [x] Add no subagent or plan-mode implementation.
- [x] Keep Pi's default compaction, retry, project-trust, and tool behavior.

## Phase 2: verify the baseline and simplify local-model access

- [x] Verify Pi loads both the global instructions and the project's context file.
- [x] Verify the active default is OpenAI Codex with high reasoning.
- [x] Verify all four scoped model entries resolve in the intended order.
- [x] Verify the Pi keybinding manager resolves Enter, Super+Enter, and Ctrl+S correctly.
- [x] Verify Ghostty emits the Kitty Super+Enter sequence Pi expects.
- [x] Verify the current session is indexed for `/resume`.
- [x] Verify the current version-3 session tree can be loaded for `/tree`.
- [x] Verify compaction is enabled with Pi's default reserve and recent-context values.
- [ ] Perform optional hands-on TUI smoke tests for `/resume`, `/tree`, and `/compact` during a natural session boundary.
- [x] Verify oMLX models appear in ordinary Pi without a wrapper.
- [x] Verify `pi --model omlx/<model>` selects the local provider without making a model request.
- [x] Delete `pi-local`.
- [x] Delete `claude-local`.
- [x] Delete the shared model-resolution helper made obsolete by those wrapper removals.
- [x] Remove their Stow links.

## Cleanup already completed

- [x] Delete the always-on `unslop` skill.
- [x] Delete `unslop-inject.sh`.
- [x] Remove all per-prompt, post-compaction, and subagent unslop injections from Claude Code settings.
- [x] Remove `unslop` dependencies from `recall` and `teach`.
- [x] Prune the live unslop Stow links.
- [x] Confirm no active unslop references remain.

## Phase 3: create the provider-agnostic core

This phase replaces the Claude-centric directory layout without removing useful behavior.

### Canonical user instructions

- [x] Create `stow/agents/.agents/AGENTS.md` as the canonical global instruction file.
- [x] Move the current harness-neutral contents from `stow/claude/.claude/CLAUDE.md` into it.
- [x] Point `stow/pi/.pi/agent/AGENTS.md` at the canonical file.
- [x] Retain Claude Code and replace its `CLAUDE.md` with a compatibility symlink.
- [x] Retain standalone Copilot CLI for now and point `copilot-instructions.md` at the same canonical file.
- [x] Update setup, Stow documentation, and architecture documentation.
- [x] Verify each retained harness loads exactly one copy of the global instructions.

### Project context files

- [x] Rename the repository root `CLAUDE.md` to `AGENTS.md`.
- [x] Add a `CLAUDE.md` compatibility symlink for Claude Code.
- [x] Apply the same pattern to `devonthink/CLAUDE.md`.
- [x] Update internal links and references.
- [x] Verify Pi prefers `AGENTS.md` and does not load the compatibility symlink as duplicate context.

### Keep adapters thin

- [x] Retain Claude Code as a Max-plan fallback and for the still-load-bearing summarize workflow.
- [x] Retain standalone Copilot CLI until its active work-only MCP and Zed integrations move or are retired; Pi already replaces its ordinary terminal coding role.
- [x] Keep provider authentication and model selection out of shared skills and instructions.
- [x] Use `AI_AGENT` for generic child-process attribution where needed.
- [x] Keep `PI_*` variables inside Pi-specific adapters rather than shared workflows.

Current decision: both legacy harnesses remain compatibility adapters during migration. Their recent session history is still active, Claude owns the summarize workflow, and Copilot owns work-only MCP and Zed integrations. No migration goal requires uninstalling Copilot; reassess either harness only if its remaining workflows become unused.

### Reduce always-loaded project context

- [x] Move the detailed dotfiles architecture from root `AGENTS.md` to `docs/dotfiles-reference.md`.
- [x] Move detailed DEVONthink implementation notes to `devonthink/docs/agent-reference.md`.
- [x] Keep concise task routing and repository-wide constraints in the two context files.
- [x] Reduce project-specific context from 8,006 to 316 words at the root and from 10,791 to 573 words under `devonthink/`, excluding unchanged global instructions.

## Phase 4: migrate, adapt, or delete skills

Pi natively discovers Agent Skills under `~/.agents/skills`. Do not point Pi at the complete legacy `~/.claude/skills` directory.

### Move after small portability edits

- [x] Delete `blast-radius`: no recorded invocation since installation.
- [x] Delete `diagnosing-bugs`: no recorded invocation, and its core failing-test rule already exists globally.
- [x] Move `handoff`: explicit-only and retained as the preferred input path for later recall.
- [x] Delete the `music-doctor` skill: no recorded invocation since May; retain the independently used CLI engine.
- [x] Delete `wait-what`: no recorded invocation and ordinary conversation already covers the request.

For each moved skill:

- [x] Put the retained canonical copy under `stow/agents/.agents/skills/handoff/`.
- [x] Replace Claude-specific commands and paths.
- [x] Preserve helper scripts beside retained skills and use relative paths; `handoff` has no helper.
- [x] Keep `disable-model-invocation` for explicit-only `handoff`.
- [x] Validate the Agent Skills frontmatter with Pi's loader.
- [x] Expose canonical `handoff` to Claude Code through a symlink rather than a copy.
- [x] Test Pi's `/skill:handoff` expansion path with arguments.

### Adapt before moving

#### `batch-review`

- [ ] Replace Claude's `$ARGUMENTS` assumption with the Agent Skills argument convention.
- [ ] Replace `WebFetch` and web-search assumptions with an explicit reviewed tool or CLI path.
- [ ] Verify Qobuz fallback behavior before enabling it in Pi.

#### `teach`

- [ ] Remove the assumption that Explore subagents exist.
- [ ] Make parallel exploration optional and capability-based.
- [ ] Keep the useful explanation contract without carrying a second global prose policy.

#### `things`

- [ ] Replace absolute `~/.claude/skills/...` paths with relative skill paths.
- [ ] Decide whether the helper and Things URL scheme eliminate the need for `things-mcp`.
- [ ] Keep the documented idempotency and heading edge cases.

#### `recall`

- [ ] Add Pi session discovery and parsing.
- [ ] Remove the assumption that work spans only Claude Code and Copilot CLI.
- [ ] Make shared-record integrations optional capabilities rather than hard MCP assumptions.
- [ ] Update citations to identify Pi sessions.

#### `summarize`

- [ ] Inventory every caller before changing it: Fish function, DEVONthink smart rule, documentation, token setup, and direct skill use.
- [ ] Remove fixed Opus and Claude Task-agent assumptions.
- [ ] Decide whether Pi should summarize in one session, use explicit worker processes, or retire the workflow.
- [ ] Preserve DEVONthink import metadata and pipeline behavior if migrated.
- [ ] Delete the workflow instead of partially porting it if it is no longer used.

### Formula-provided skills

- [ ] Decide whether `hunk-review` is useful in Pi.
- [ ] If useful, expose the formula's skill under `~/.agents/skills` and avoid a Claude-only setup path.
- [ ] Otherwise remove its special setup linking.

## Phase 5: make session recall harness-neutral

- [ ] Add Pi's version-3 JSONL session format to `agent-reader`.
- [ ] Index Pi session id, cwd, name, timestamps, model/provider, user messages, and assistant messages.
- [ ] Preserve support for historical Claude and Copilot transcripts while those records remain useful.
- [ ] Prefer explicit handoff files before transcript mining.
- [ ] Update `recall` to search available harnesses rather than a fixed pair.
- [ ] Add tests with redacted fixtures for all retained session formats.
- [ ] Decide when old Claude/Copilot transcript support can be retired.

## Phase 6: reassess completion and comment gates

Do not port the existing hook stack automatically. First decide which outcomes remain valuable.

### Current findings

- The comment policy already exists in the canonical global instructions.
- A home-directory scan found one explicit `.uncommentrc.toml` consumer: `~/Work/tmdb-mobile`.
- The deterministic stripper therefore remains useful to that opted-in repository.
- The stub scan's recorded hits were repeated false positives from TODO text quoted in a review document, not unfinished implementations.
- Pi exposes direct tool and lifecycle events, so any future Pi enforcement should not parse transcripts.

### Decisions and work

- [x] Scan unindexed repositories for `.uncommentrc.toml` consumers.
- [x] Retain `agent-strip-comments`, `comment-gate-init`, `uncomment-clean`, `uncomment-scoped`, the Copilot comment hook, and the pinned `uncomment` installation for the active consumer.
- [ ] Decide whether the opted-in repository needs a small event-driven Pi stripper integration.
- [x] Delete `agent-stub-scan`; its observed hits were noisy and did not catch a genuine incomplete implementation.
- [x] Do not port the stub scan to Pi.
- [x] Remove `hook-audit` with the last prompt-submit hook.
- [-] Retain Claude's Bash grep guard while Claude Code remains and the PTY-rendering workaround is still needed.
- [x] Remove Claude's Terse output style and comment-policy lifecycle injections; the canonical global instructions are the one generation source for every retained harness.

## Phase 7: add external capabilities only on demonstrated demand

### MCP and tool integrations

- [ ] Remove the filesystem MCP server wherever Pi's native file tools make it redundant.
- [ ] Evaluate Context7 as a focused CLI-backed skill rather than an always-loaded MCP server.
- [ ] Evaluate DEVONthink and Anki as narrow tools or lazily loaded integrations.
- [ ] Keep Atlassian configuration in the gitignored work package.
- [ ] Decide whether browser debugging needs Chrome DevTools MCP, a Pi package, or no integration.
- [ ] Review all third-party extension source before installation.
- [ ] Measure tool-schema and system-prompt cost before and after each addition.
- [ ] Prefer Pi's dynamic tool loading for expensive, rarely used capabilities.

### Subagents

- [ ] Do not add subagents merely to match Claude Code.
- [ ] Identify a workflow that cannot be handled cleanly in one Pi session.
- [ ] Compare explicit `pi -p` workers, tmux sessions, and a reviewed extension.
- [ ] Account for nested model usage and subscription routing.
- [ ] Add the smallest implementation that solves the demonstrated case.

## Phase 8: retire legacy harness infrastructure

This phase depends on the Claude fallback decision and successful migration of any load-bearing workflows.

### Standalone Copilot CLI candidates

- [ ] Remove the `copilot-cli` Brewfile cask if Pi fully replaces it.
- [ ] Remove `stow/copilot/`.
- [ ] Remove the Copilot MCP config and comment hook.
- [ ] Preserve historical transcript parsing only as long as `recall` needs it.

### Claude Code candidates

- [ ] Remove the `claude-code` Brewfile cask only if the Max-plan fallback is no longer wanted.
- [ ] Reduce or remove `stow/claude/` after shared instructions and skills move out.
- [ ] Remove `merge-claude-mcp.sh` and its setup integration when no Claude MCP consumer remains.
- [ ] Remove personal Claude MCP fragments when their capabilities are migrated or retired.
- [ ] Remove the Claude-specific Zed agent and its setup/build dependency if unused.
- [ ] Remove the Karabiner Claude restart binding if unused.
- [ ] Remove Claude-specific terminal key mappings that no retained tool needs.
- [ ] Remove generated Claude token setup only after every direct Claude automation is gone.

### Load-bearing items that must not be deleted blindly

- DEVONthink's summarize smart rule and import path
- `stow/fish/.config/fish/functions/summarize.fish`
- `devonthink/docs/summarize.md` and related user-facing documentation
- Work-only Atlassian configuration
- Historical transcripts needed by `recall`
- Any Zed workflow still used for agent sessions

## Phase 9: final validation and documentation

- [ ] Run setup in a clean or isolated home directory.
- [ ] Verify Stow creates only intended links and prunes deleted ones.
- [ ] Verify no secrets or generated runtime state are tracked.
- [ ] Verify OpenAI, Copilot, and oMLX model selection.
- [ ] Verify direct Anthropic remains outside model cycling.
- [ ] Verify global and project instructions load exactly once.
- [ ] Verify every retained skill works from Pi.
- [ ] Verify `/resume`, `/tree`, `/compact`, and model cycling in the TUI.
- [ ] Verify battery-sensitive background behavior remains gated appropriately.
- [ ] Run shell, Fish, JSON, relevant unit, and secret scans.
- [ ] Update `README.md`, `AGENTS.md`, setup comments, and migration documentation.
- [ ] Delete compatibility symlinks after their final consumer is gone.

## Immediate next checkpoint

Phase 4's first skill cohort is complete (`2bf342a`): invocation history showed no use of the five candidates, so only explicit-only `handoff` moved to the shared skill directory because recall depends on its artifacts. `blast-radius`, `diagnosing-bugs`, the `music-doctor` skill, and `wait-what` were deleted; the independently used `music-doctor.py` engine remains.

Next:

- [ ] Audit invocation history for `batch-review`, `teach`, and `things` before adapting any of them.
- [ ] Delete dormant skills rather than porting them.
- [ ] Move each retained skill in a separate reviewable commit or tightly related checkpoint.
- [ ] Keep `recall` for the Phase 5 session-format work instead of partially adapting it now.
- [ ] Treat `summarize` as its own load-bearing workflow audit because Fish and DEVONthink call it directly.
- [ ] Decide the formula-provided `hunk-review` setup after the user-authored skill audit.

The working tree may contain changes from parallel sessions. Keep unrelated changes out of migration commits.

## Definition of done

The migration is complete when:

- Pi is the normal entry point for cloud and local models.
- Shared instructions and skills contain no unnecessary provider or harness assumptions.
- Every provider-specific directory is a thin adapter or has been deleted.
- No always-on prompt injection duplicates standing instructions.
- No transcript parser exists where direct lifecycle events can do the job.
- No unused MCP server or tool schema loads by default.
- No wrapper exists for behavior Pi already exposes directly.
- Claude Code remains only if its included Max allowance justifies the fallback.
- Standalone Copilot CLI remains only if it provides a workflow Pi cannot.
- Setup on a new machine reconstructs the intended configuration without copying credentials or runtime state.
