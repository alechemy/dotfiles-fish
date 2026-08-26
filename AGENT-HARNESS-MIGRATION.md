<!-- markdownlint-disable MD013 -->

# Agent harness migration plan

Last updated: 2026-08-25

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
- [x] Perform hands-on TUI smoke tests in a disposable offline Pi session. Real selectors resumed a synthetic session and navigated its tree without summarization; `/compact` persisted a normal compaction entry through an in-process mock model with no network request.
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

- [x] Retain Claude Code as a Max-plan fallback; the dormant summarize workflow no longer requires it.
- [x] Retain standalone Copilot CLI until its active work-only MCP and Zed integrations move or are retired; Pi already replaces its ordinary terminal coding role.
- [x] Keep provider authentication and model selection out of shared skills and instructions.
- [x] Use `AI_AGENT` for generic child-process attribution where needed.
- [x] Keep `PI_*` variables inside Pi-specific adapters rather than shared workflows.

Current decision: both legacy harnesses remain compatibility adapters during migration. Their recent session history is still active, Claude remains a Max-plan fallback, and Copilot owns work-only MCP and Zed integrations. No migration goal requires uninstalling Copilot; reassess either harness only if its remaining workflows become unused.

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

- [x] Put retained canonical copies under `stow/agents/.agents/skills/`.
- [x] Replace Claude-specific commands and paths.
- [x] Preserve helper scripts beside retained skills and use relative paths; only `things` has a helper.
- [x] Keep `disable-model-invocation` for explicit-only `handoff` and `teach`.
- [x] Validate all retained Agent Skills with Pi's loader.
- [x] Expose each canonical skill to Claude Code through a symlink rather than a copy.
- [x] Test Pi's `/skill:<name>` expansion path with arguments for every retained skill.

### Adapt before moving

#### `batch-review`

- [x] Delete it: Claude command history and transcripts record no invocation, and neither Pi nor Copilot history records one. Do not adapt its argument, web-fetch, or Qobuz fallback assumptions without demonstrated demand.

#### `teach`

- [x] Retain it: Claude transcripts record one explicit `/teach` invocation after installation.
- [x] Remove the assumption that Explore subagents exist; parallel exploration is optional and capability-based.
- [x] Keep the explanation contract without carrying a second global prose policy.

#### `things`

- [x] Retain it: transcripts record repeated `things_fill.py` use across three sessions.
- [x] Replace absolute Claude paths with a helper path relative to the skill directory.
- [x] Remove `things-mcp` assumptions: no transcript records an MCP tool invocation and no active MCP configuration remains; the helper, URL scheme, SQLite reads, and AppleScript cover the retained workflow.
- [x] Keep the documented idempotency and heading edge cases.

#### `recall`

- [x] Add Pi session discovery and parsing through `agent-reader`'s normalized index and transcript commands.
- [x] Remove the assumption that work spans only Claude Code and Copilot CLI; discover supported harnesses from the local index.
- [x] Make shared-record integrations optional capabilities rather than hard MCP assumptions.
- [x] Update citations to identify Pi, Claude, or Copilot sessions explicitly.

#### `summarize`

- [x] Inventory every caller: the Claude skill, Fish function, DEVONthink smart rule and seed, user-facing documentation, direct 1Password token read, and extraction dependencies.
- [x] Retire the workflow instead of porting its fixed Opus and Claude Task-agent assumptions to Pi.
- [x] Delete every invocation path and remove dependencies used only by this workflow.
- [x] Preserve `SummarySource` as historical metadata for existing records; no new summary import path remains.

### Formula-provided skills

- [x] Remove the special `hunk-review` setup link: no Claude, Pi, or Copilot transcript records an invocation since installation. Retain the independently useful Hunk CLI and its formula-provided skill source without loading the dormant skill into any harness.

## Phase 5: make session recall harness-neutral

- [x] Add Pi's version-3 JSONL session format to `agent-reader` (`429868b`).
- [x] Index Pi session id, cwd, optional name, timestamps, model/provider, user messages, and assistant messages.
- [x] Preserve support for historical Claude and Copilot transcripts while those records remain useful.
- [x] Prefer explicit handoff files before transcript mining.
- [x] Update `recall` to discover available harnesses from agent-reader rather than assuming a fixed pair.
- [x] Add synthetic schema-faithful fixtures and extractor tests for all retained session formats.
- [x] Retain old Claude/Copilot parsing while their historical records remain useful; reassess only when those records are intentionally retired.
- [x] Expose a normalized JSON session index and transcript command for recall and other local consumers (`6105b5c`). JSON indexing defaults to all sessions, the human table remains bounded to 20, `--limit 0` means all, and invalid negative limits fail instead of silently narrowing history.
- [x] Make the unpublished changes reconstructible without modifying their remote: setup verifies an exact public base plus a checksummed source/test overlay, runs 50 synthetic tests under pinned constraints, and installs a locally manifested wheel from a stable private cache.

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
- [x] Defer a Pi stripper integration until normal Pi use demonstrates a need; revisit after several days of coding rather than porting the hook speculatively.
- [x] Delete `agent-stub-scan`; its observed hits were noisy and did not catch a genuine incomplete implementation.
- [x] Do not port the stub scan to Pi.
- [x] Remove `hook-audit` with the last prompt-submit hook.
- [-] Retain Claude's Bash grep guard while Claude Code remains and the PTY-rendering workaround is still needed.
- [x] Remove Claude's Terse output style and comment-policy lifecycle injections; the canonical global instructions are the one generation source for every retained harness.

## Phase 7: add external capabilities only on demonstrated demand

### MCP and tool integrations

- [x] Remove the filesystem MCP server from Claude and work Copilot configuration. Pi ships native read, write, edit, bash, grep, find, and ls tools; Claude transcripts recorded zero filesystem MCP calls, and Copilot's 13 historical calls used only operations covered by those native tools.
- [x] Retain Context7 through the official native Pi extension. Historical use is substantial (104 Copilot and 3 Claude tool calls). Source-review and pin `@upstash/context7-pi` 0.1.2, which registers two native tools, a skill, and `/c7-docs` without MCP; remove the redundant homegrown CLI/shared skill plus the old Copilot and Zed registrations. The active tools contribute 4,467 schema characters and the skill description 778 characters; that cost is justified by demonstrated use. This still removes a 250-character Context7 reminder from each Copilot turn (observed in 91 retained reminders) before any deferred tool schema is requested.
- [x] Retire Anki MCP. Across 246 retained Claude, Copilot, and Pi transcripts (20,077 structured tool calls, 2026-06-28 through 2026-08-25), no Anki tool call was recorded. No tracked or gitignored workflow depends on it. Remove it from the personal Claude MCP fragment, explicitly retire the stale live `ankimcp` key during the additive merge, and remove the enabled AnkiMCP add-on from Anki's active add-on directory on this machine. Preserve the Anki app, profiles, decks, media, and other user data, which are outside the agent-harness integration.
- [x] Retain DEVONthink through a lazy shared read-only skill. Five recorded calls in one Claude session used only metadata-field discovery, record search, and custom-metadata reads. A bundled stdlib client now allowlists exactly those three operations over DEVONthink's official stdio server, preserving the vendor's Chat/MCP exclusions and redaction without a third-party bridge; the broad 59-tool Claude registration and final personal MCP fragment are retired.
- [x] Keep Atlassian configuration in the gitignored work package. `git ls-files stow-work` exposes only `.gitkeep`; the Claude fragment, Copilot config, 1Password-backed builder, generated environment, and identifying documentation remain ignored. Live Claude state is app-owned and mode 0600, while the Copilot config and Fish environment resolve into the ignored package. No work endpoint is tracked.
- [x] Retain the existing pinned Claude Chrome DevTools plugin and add no Pi browser integration yet. Sixty calls across five Claude sessions demonstrate real Claude demand, while 115 Copilot and 10 Pi sessions contain none. The recorded official plugin snapshot is Chrome DevTools MCP 1.4.0 at commit `6a9466378c13b6ccba91b54091ea83a5ca37a8db`; it exposes 29 tools totaling 23,336 bytes of wire-format definitions. A Pi bridge or direct-CDP extension would add a second full-permission supply chain without demonstrated Pi demand. Revisit on the first real Pi browser task, starting with the narrow official Chrome CLI path if navigation, evaluation, and screenshots are enough.
- [x] Review third-party extension source before installation. Context7 0.1.2 was reviewed and pinned before addition; the browser audit reviewed the retained official Claude plugin snapshot and installed no Pi package.
- [x] Measure tool-schema and system-prompt cost before and after each addition. Context7, DEVONthink, and the retained browser plugin have recorded wire or prompt measurements above; no unmeasured Phase 7 capability was added.
- [x] Prefer Pi's dynamic tool loading for expensive, rarely used capabilities. DEVONthink is an on-demand skill with no registered tool schema, and the unused Pi browser surface was not added. Context7 remains native because its 107 historical calls justify two focused tools.

#### DEVONthink audit and implementation

Evidence collected 2026-08-25:

- The official `llms.txt` path returns 404. The current documentation index is [Handbooks and Extras](https://www.devontechnologies.com/support/download/extras), which links the [DEVONthink 4.3.2 manual](https://download.devontechnologies.com/download/devonthink/4.3.2/DEVONthink%20Manual.pdf). DEVONthink 4.3.2 ships the official MCP server and documents stdio plus local or remote HTTP transports, record/group/database exclusion from Chat and MCP, optional sensitive-content redaction, and AppleScript/JXA and URL automation. It documents no native DEVONthink CLI.
- Neither DEVONtechnologies' current documentation nor Pi's package catalog lists an official Pi integration. The catalog's generic MCP bridges are third-party packages with full local execution authority; none was installed or needed for this audit.
- The retained transcript index contains five DEVONthink calls, all from one Claude session on one day. The only operations were `list_custom_metadata_fields` twice, `search_records` twice, and `get_record_custom_metadata` once. No mutation operation was recorded.
- The installed official 4.3.2 server advertises 59 tools. Direct `initialize` and `tools/list` measurements show 2,395 characters of server instructions and 80,694 characters of compact tool definitions. The three demonstrated tool definitions total 5,566 characters, or 6.9% of that advertised schema. A client may defer schemas, so these are protocol-surface measurements rather than a claim that Claude injects all 83,089 characters into every turn.

Options considered:

- **Retain the broad Claude MCP registration:** preserves vendor maintenance, exclusions, and redaction, but keeps 56 unused operations available and does not serve the normal Pi path.
- **Write a direct AppleScript/JXA tool:** avoids third-party code and MCP, but DEVONthink does not document Chat/MCP exclusions or redaction as applying automatically to arbitrary scripts. Recreating those privacy boundaries would be custom security work.
- **Install a generic Pi MCP bridge:** could load tools lazily, but there is no official DEVONthink or Pi package, the bridge would run with full local authority, and broad MCP access is not justified by five read-only calls.
- **Use a focused local client for the official stdio MCP:** retain DEVONthink's own privacy and record-access enforcement while exposing only the three demonstrated read operations. The client can start on explicit use, so no MCP schema or server instructions need to be model-visible between uses.

Implemented checkpoint: the shared `devonthink` skill bundles a stdlib-only client for DEVONthink's built-in `--stdio` transport. It hard-allowlists the three demonstrated read operations, fixes search results to UUID and name, caps search results at 100, metadata batches at 50 records, and responses at 256,000 characters, and exposes no arbitrary MCP method or mutation escape hatch. Sixteen synthetic client tests cover all three calls, argument revalidation, protocol negotiation, colliding server pings, invalid frames and results, bounds, errors, timeouts, and refusal of mutation or expanded search fields. Six merge tests verify retirement and preservation behavior plus fail-closed handling of invalid, unreadable, non-file, chmod-failed, and move-failed state. Pi's loader reports no diagnostics, `/skill:devonthink` expands with arguments, and privacy-preserving live canaries verified field listing, an empty synthetic search, one metadata read, and exclusion behavior without printing record values. The skill adds no registered tool schema: its 228-character description contributes 381 characters to Pi's always-loaded system prompt, while the 1,825-character body expands only on use. The broad Claude MCP registration is retired. An otherwise-unused full-tool HTTP login item remained listening after that removal, so setup now disables it and stops it when loaded, warning on a real shutdown failure; live canaries still pass with the service disabled and do not restart it. This uses no third-party package and makes no DEVONthink pipeline or database change.

### Subagents

- [x] Do not add subagents merely to match Claude Code. The migration's independent read-only audits and implementation review showed that parallel workers can help with bounded research, but they did not establish a need for a permanent Pi subagent architecture.
- [x] Identify a workflow that cannot be handled cleanly in one Pi session. None is demonstrated: the useful audit tasks can run serially, and parallelism was an optimization rather than a functional requirement.
- [x] Compare explicit `pi -p` workers, tmux sessions, and a reviewed extension only after that demand gate. Explicit print-mode workers would be the smallest disposable option; tmux provides manual independent sessions; an extension would add persistent orchestration code with full local authority. With no qualifying workflow, none should be added.
- [x] Account for nested model usage and subscription routing by adding no nested Pi calls. Any future worker design must state which provider/account pays for child calls instead of assuming Claude's Max-plan routing.
- [x] Add the smallest implementation that solves the demonstrated case. The current answer is no implementation; use one Pi session until a concrete task proves otherwise.

## Phase 8: retire legacy harness infrastructure

The usage audit retains both legacy harnesses as thin adapters. As of 2026-08-25, the local index contains 121 Claude sessions through that day and 115 Copilot sessions through the prior day; the fixed 2026-08-19 through 2026-08-25 window includes 36 Claude, 10 Copilot, and 11 Pi sessions. Pi is the normal path, but inactivity does not justify uninstalling either fallback.

### Standalone Copilot CLI candidates

- [x] Retain the `copilot-cli` Brewfile cask. It still owns the ignored work-only Atlassian config, the Zed `copilot-acp` server, a terminal abbreviation, and recent Copilot sessions. Pi's Copilot-backed models do not replace those clients.
- [x] Retain the already-thin `stow/copilot/` adapter: one canonical-instruction symlink and one shared comment-gate hook.
- [x] Retain the ignored Copilot work MCP config and comment hook while their current consumers remain. Neither contains a personal capability that should move into shared Pi context.
- [x] Preserve Claude and Copilot transcript parsing while `recall` needs 236 indexed legacy sessions and both histories remain current.

### Claude Code candidates

- [x] Retain the `claude-code@latest` Brewfile cask as the explicit Max-plan fallback. Recent terminal/SDK sessions and the demonstrated Chrome DevTools workflow independently justify it; retirement still requires a direct user decision.
- [x] Retain the reduced `stow/claude/` adapter. Shared instructions and five skills are symlinks; the remaining settings, keybindings, grep guard, comment hook, and browser plugin are Claude-specific and either configured or independently demonstrated.
- [x] Retain `merge-claude-mcp.sh` and its setup integration while Claude consumes the ignored work-only Atlassian fragment. The merge has no personal MCP fragment left.
- [x] Remove personal Claude MCP fragments when their capabilities are migrated or retired; Context7 is native to Pi, DEVONthink is a lazy shared skill, and Anki is retired.
- [x] Retain both Claude Zed ACP entries until direct usage can distinguish them. Seven recent `sdk-cli` sessions are consistent with ACP use but cannot identify the client. The registry entry's dead executable path was corrected to Homebrew's installed Claude binary; the custom bridge remains provisioned rather than deleting a possibly load-bearing workflow.
- [x] Retain the live Ghostty-scoped Karabiner restart binding because safe metadata has no invocation counter and cannot prove it unused. It remains isolated from general Karabiner, display, and AeroSpace rules.
- [x] Retain terminal submit mappings. Ghostty and VSCodium's CSI-u Super+Enter path now provides Pi's configured submit route as well as Claude's; it is no longer Claude-only. Claude's own keybindings remain inside its thin adapter. Ghostty's separate Shift+Enter escape-CR mapping has no tracked consumer, but safe metadata cannot prove it unused, so retain it pending an interactive Ghostty/Pi/Claude key smoke rather than attributing it to Pi.
- [x] Remove the summarize automation's direct Claude token read with its final caller; no generated token configuration was tracked.

### Load-bearing items that must not be deleted blindly

- Work-only Atlassian configuration
- Historical transcripts needed by `recall`
- Any Zed workflow still used for agent sessions

## Phase 9: final validation and documentation

- [x] Validate clean-machine reconstruction at the safe component boundary available on this machine. A temporary `HOME` cannot safely contain setup's shared Homebrew, sudo/PAM, launchd, defaults, and service mutations, so the full-system smoke is deferred to the next disposable VM or new-machine snapshot. Isolated Stow, generated-config builders, the exact-base `agent-reader` install and upgrade, and setup-owned component tests all pass without copying credentials or runtime state.
- [x] Verify Stow creates only intended links and prunes deleted ones. A clean archived tree created 173 links whose targets all remained inside the tree, and a disposable-package restow removed a deleted source link.
- [x] Verify no secrets or generated runtime state are tracked. Full tracked-history Betterleaks, forbidden runtime filename checks, the Pi tracked-file allowlist, and work-package isolation all pass.
- [x] Verify OpenAI, Copilot, and oMLX model selection. Pi's cached catalog resolves every configured route, the live merged settings match the tracked fragment, and an offline RPC cycle returns to the start after the four configured routes across those three providers.
- [x] Verify direct Anthropic remains outside model cycling. It is authenticated and available through `/model`, but absent from `enabledModels` and from a complete offline RPC cycle.
- [x] Verify global and project instructions load exactly once. Pi's exported loader returns one global `AGENTS.md` plus the applicable root and nested project files, prefers each `AGENTS.md`, and loads no Claude compatibility path.
- [x] Verify every retained skill through its justified safe boundary. Pi loads and expands `devonthink`, `handoff`, `recall`, `teach`, and `things` with zero diagnostics. DEVONthink passes its 16-test protocol suite and prior privacy-safe live canaries; recall parses a disposable Pi transcript; local-oMLX Pi turns produced a compliant ignored handoff and evidence-citing teach response in fictional repositories; Things completed both single-add and JSON-batch flows against a synthetic SQLite/fake-URL backend; and Context7's pinned registered tools completed anonymous public resolve/query calls. Real Things writes and repeated private-backend reads are operational actions, not migration gates.
- [x] Verify `/resume`, `/tree`, `/compact`, model selection, and model cycling in the real Pi TUI with disposable version-3 sessions and offline mock models. The selectors resumed and navigated synthetic history, compaction persisted the expected built-in entry without a provider request, Ctrl+L and Ctrl+P changed models, and a Kitty-negotiated PTY accepted Ghostty's configured Super+Enter submit and Shift+Enter/Enter newline bytes. A physical macOS Ghostty key event remains an opportunistic hardware smoke, not a blocker.
- [x] Rebuild the live generated Zed settings with authenticated 1Password injection. The mode-0600 ignored output has no unresolved references or `${HOME}` placeholder, the live symlink resolves to it, and `CLAUDE_CODE_EXECUTABLE` now names the existing `/opt/homebrew/bin/claude`.
- [x] Verify battery-sensitive background behavior remains gated appropriately. The real gate matches current AC state, `--urgent` overrides it, and a temporary exact-copy harness passes AC/urgent while skipping battery, UPS, and unknown power and failing open only when `pmset` cannot report.
- [x] Run shell, Fish, JSON, relevant unit, and secret scans. The clean checkpoint passes repository-wide Bash/Fish syntax, strict JSON plus JSONC formatting, Python compilation, launchd lint, focused agent tests, the full DEVONthink suite, whitespace checks, and full tracked-history Betterleaks.
- [x] Update `README.md`, `AGENTS.md`, setup comments, and migration documentation. Final review found the root routing/current architecture accurate, corrected one retired Summarize-doc reference and the shared Super+Enter comment, and records retained adapters and remaining blockers here.
- [x] Retain compatibility symlinks while their final consumers remain. Claude and Copilot are active retained adapters, so deleting their global/project/skill compatibility links would be destructive rather than cleanup.

## Migration closure

Phases 1–9 are complete at the accepted safe validation boundary. Pi is the normal entry point, Claude remains the Max-plan and browser-debugging fallback, and Copilot remains a thin adapter for its active work-only and Zed workflows. `agent-reader` is reconstructible from its reviewed public base and tracked overlay without modifying the separate remote. Retained skills and Pi's session/model TUI paths have deliberate disposable evidence, and the live Zed configuration has been regenerated successfully.

A full shared-system setup run belongs on the next disposable macOS VM or new-machine snapshot. Physical Ghostty key emission, real Things writes, real-history navigation, and real-provider compaction should be checked only during natural use. Their configuration, synthetic control flow, and privacy boundaries already pass, so they do not keep this migration open.

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

This definition is satisfied by the tracked reconstruction path plus isolated component-level clean-install evidence. The intentionally deferred full-system and physical-input smokes above are operational follow-ups, not unresolved harness design or migration work.
