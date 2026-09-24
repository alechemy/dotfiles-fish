# Agent tooling maintenance

Pi is the primary client. The repository owns portable declarations, not credentials, sessions, generated settings, or private work definitions. Version pins identify releases; they do not certify dependencies or make external tools offline.

## Capability and ownership

The initial checkpoint 3 snapshot used Pi `0.84.4`, Context7 `0.1.2`, Subagents `0.65.0`, and Web Access `0.27.0`. The 2026-09-09 closeout used Pi `0.85.1` and captured the already installed Plannotator `0.27.12` as an exact pin. Plannotator was subsequently uninstalled and removed from the authoritative fragment. The original three package versions remain unchanged. The current declaration also includes `@gotgenes/pi-anthropic-auth@3.3.1` and `@sting8k/pi-vcc@0.8.0`, checked offline against Pi 0.87.1.

| Capability and implementation | Declaration | Bounded source-review status | Privacy and cloud constraints | Update owner and verification |
|---|---|---|---|---|
| Pi CLI and native tools | Homebrew `pi-coding-agent`, unversioned Brewfile entry. Inspected host `0.84.4`. | Official installed docs and selected package/skill loader code; not a host audit. | Model requests and sessions have separate owners. Install telemetry opt-out does not disable update checks or extension networking. | Homebrew. Merge/restow regression tests cover portable settings, not full machine setup or provider behavior. |
| Native Context7, two tools, `context7-docs`, `/c7-docs` | `npm:@upstash/context7-pi@0.1.2` | Retains the prior source review; registration, descriptions and HTTP client checked here. | Queries and library identifiers go to `context7.com`; never send secrets, personal data, or proprietary code. Optional environment key comes from the generated Fish export. | Explicit reviewed Pi package bump. Offline description projection; no authenticated request test in this checkpoint. Keep native integration. |
| Subagents, `subagent`, `bg_wait` and `subagent_supervisor` | Local guarded `pi-subagents@0.65.0`; see the scout trial below. | Execution, child routing, credentials, external runners and retention paths reviewed. Important limits below. | Child/provider/fallback choices can leave the local machine. Project discovery is not demonstrably gated by Pi trust. Artifacts, sessions and async results persist separately. | Explicit reviewed Pi package bump. Static path review and default-description projection only; no child, provider, cleanup or external CLI enforcement test. |
| Web Access, four default tools | `npm:pi-web-access@0.27.0` | Search, credential, extraction, proxy and retention paths reviewed. Authenticated PDF defect remains. | Search, synthesis, PDF/video providers and curator assets can contact cloud services. Cookies default off. Authenticated PDFs must not be used. | Explicit reviewed Pi package bump. Static review and default-description projection only; no browser, provider or retention test. |
| Anthropic subscription compatibility | `npm:@gotgenes/pi-anthropic-auth@3.3.1` | Published manifest and all 16 source modules reviewed; published package tested through Pi 0.87.1's loader. | Shapes OAuth prompts and billing markers; retries a version-floor rejection once. Included-plan billing is not guaranteed. Direct compat callers bypass it. | Explicit reviewed package bump, coordinated with the Pi host version. Credential-free loader and fake-transport checks; no live model or billing verification. |
| Automatic VCC compaction and session recall | `npm:@sting8k/pi-vcc@0.8.0`; stowed `pi-vcc-config.json`. | Published archive matched upstream source; registration, configuration, compaction hook, commands, and recall loading inspected. | No summarization request or background worker. Recall returns unredacted session content to the active model; debug snapshots stay disabled. | Explicit reviewed package bump. Credential-free loader, synthetic compaction and recall checks; no local-model quality or latency benchmark. |
| Worktrunk task worktrees and native Pi activity | Homebrew `worktrunk`; Git-owned launcher, shared skill, Fish wrapper, seed, and native extension. Verified with Worktrunk 0.77.0 and Pi 0.85.1. | Allocation, approvals, session ownership, removal checks, and linked-dotfiles guards inspected. | Activity contains process identity and status, not conversation text. Project hooks retain the user's authority; approval is not a sandbox. | Homebrew owns the CLI. Repository owns integration. Disposable Python/Node regressions, focused cmux/Hunk lifecycle fixtures, and the installed Subagents allocator smoke; see [Worktrunk](worktrunk.md). |
| Shared skills, recall filter and handoffs | Git-owned `stow/agents/.agents/skills/`. Unslop records upstream revision `bdf7aa355337897f167153e05069aca505dae17c`. | Checkpoint 2 privacy review retained; this checkpoint measures advertised descriptions, not all skill bodies/helpers. | Recall content needs explicit user scope. Audits stay metadata-only. Sanitization is heuristic. Handoffs use repo `.context/` or `~/.context/`, never staged. | Review repository edits; restow `agents` only for added/removed files. Synthetic recall/filter/recipe tests and prior isolated Pi skill-loader check. Teach, recall and handoff support model selection; grill-me and simplify-review remain command-only. |
| DEVONthink shared read helper | Git-owned `stow/agents/.agents/skills/devonthink/scripts/devonthink_read.py`; checked against DEVONthink 4.4, server `devonthink-mcp` 1.0.0. | Three-operation request and response boundaries hardened using vendor tool definitions and bundled Apps response handling. | Only official stdio reads. Projection/count enforcement, bounded transport/output, and generic errors limit disclosure. Vendor exclusions/redaction remain authoritative; singleton metadata identity is request-bound. | Repository owns helper; vendor owns server. Focused synthetic regressions and live field-discovery canary pass. Live search/metadata privacy canaries remain deferred. |
| Things shared automation helper | Git-owned `stow/agents/.agents/skills/things/things_fill.py`; Things app via Mac App Store Brewfile entry, not pinned. | Retained helper and skill, with selected source inspection rather than a new full review. | Local URL/AppleScript writes and read-only DB confirmation. Token stays environment/generated-export backed. App sync and model disclosure remain separate. | Repository owns helper, vendor owns app. Retained documented workflow evidence; no live DB, UI or mutation test here. Keep helper, not MCP replacement. |
| `agent-reader` parser overlay | Exact base, patch digest and patched tree in [overlay README](../patches/agent-reader/README.md). | Reviewed Pi/JSON overlay retained; no equivalent public replacement established. | Parses private local sessions only when recall authorizes it. Installer probes help, not transcript contents. | Repository installer owns build/install; explicit upstream replacement review. Exact-source checks, 50 upstream synthetic fixtures and installer regressions. |

Pi is the active coding client. Claude Code's Homebrew installation, Stow configuration, editor integrations, project compatibility links, ACP bootstrap, and work-MCP merger are retired. Historical Claude transcripts and private app state remain available to recall. Pi's Anthropic provider and Claude models remain independent of the removed CLI. The separate Copilot CLI adapter is unchanged. Private work MCP definitions remain inactive outside tracked configuration; no replacement work MCP client is activated. DEVONthink retains its hardened stdio helper. Anki/filesystem MCP stay retired; no Pi comment stripper is added.

[Agent-browser](agent-browser.md) is the default interactive browser tool, managed through Homebrew and a shared CLI skill. Its explicit configuration selects a separate Google Chrome automation browser. The Claude Code configuration, including its Chrome DevTools plugin declaration, is removed. The [Chrome DevTools pilot](agent-browser-pilot.md) remains historical evidence; its proposed Pi adapter was never activated and is no longer awaiting adoption. Agent-browser's short local installation check does not extend the pilot's source-review claims to the replacement.

## Bounded closeout

The 2026-09-09 closeout captures four installed package pins and the portable Copilot context-window override, reconciles the live managed settings without replacing unrelated fields, and finishes the retained DEVONthink helper. Recovery is tracked separately in [DT44-02](../devonthink/docs/devonthink-4.4-plan.md#dt44-02-correct-recovery-documentation-and-verify-archive-recovery). The current local archive passed ZIP integrity; verifying a separate copy waits for the configured Time Machine disk. The isolated restore drill is explicitly deferred.

Plannotator is retired. The bounded review before removal found that its planning write gate restricted native write/edit tools to Markdown inside the working directory, but left bash and other extension tools available. It was not a sandbox. The review did not run browser flows or audit all bundled code and dependencies. The offline projection no longer requires its installation or manifest.

The DEVONthink helper enforces 2 MiB newline-ended frames, 8 MiB total transport, eight queued raw frames, 64 JSON nesting levels, and 256,000 characters for complete stdout including indentation and newline. Search emits only UUID/name and cannot exceed the requested limit. Metadata batches emit only requested UUID/metadata pairs without duplicates; a singleton retains the vendor's bare dictionary without an echoed identity. Field discovery emits only documented definition properties. Vendor schemas do not define metadata values, so valid bounded JSON is retained. Unknown envelopes and backend errors fail safely. Search and metadata shapes come from the bundled bibliography-workbench source and synthetic tests, not private record reads. Shutdown reaps the owned server; descendant-held pipes remain a documented limit.

The unused HTTP MCP login item was found loaded, then disabled and unloaded. The hardened stdio helper still completed field discovery. The Web Access authenticated-PDF restriction is now in the active shared instructions, and browser authentication is unconfigured. No vendor PDF fix is claimed. Broader extension-registration, installation, browser/work parity, privacy-canary, and fresh-machine checks are deferred until the relevant workflow or upgrade needs them.

## Private work evaluation

This is historical evaluation evidence. Claude Code and its work-MCP merger are now retired; the private fragment remains inactive, and no Pi transport replacement is activated.

Checkpoint 5 retains the existing work definition and client. The detailed evaluation and proposed approval scope stay in the ignored work package. Source and fictional stdio checks establish configuration compatibility, not working credentials, backend permissions, business-result parity or normal-client adoption. The hosted alternative remains source-reviewed only; its dynamic execution and interactive-app behavior need separate evaluation.

The shared opt-in synthetic runner now checks literal environment values, explicit overrides and inherited adapter-parent values through the real adapter. `literalEnv: true` preserves values containing command/interpolation syntax but does not clear the inherited environment. The fixture's outer credential marker stays absent because the runner clears its environment before importing Pi or the adapter. This is not a property of normal Pi sessions. Existing filtering, headless refusal, unsolicited-request refusal and output-cleanup checks remain in place.

Before any live canary, review the exact server and dependency set, establish server-side read-only and exact-tool restrictions, and obtain a bounded authentication/read scope. Review handler defaults and nested requests too: a read-tagged tool can update view history or fetch related objects. The approved scope must cover actual requests, not just top-level arguments. Keep credentials in their existing private owner and review any expression syntax before translating configuration. Backend responses, adapter spills and Pi sessions have separate retention requirements. Client parity and production activation remain open.

## Restrictions from the source review

### Subagents 0.65.0

- Use reviewed projects and role definitions. Default `agentScope: "both"` allows project defaults, model overrides and extension paths into child launches. The inspected paths do not consult `isProjectTrusted` or `defaultProjectTrust`. `agentScope: "user"` narrows launch sources but is not filesystem isolation and can still involve project-settings reads. Pi's `defaultProjectTrust: "ask"` does not establish a Subagents launch gate.
- An async workflow can still launch foreground children. Those children do not load ambient extension providers; declaring their tool names does not load them. Verify the child provider-loading mode before dispatch, or give a core-tool-only role parent-fetched public evidence. A child can write a useful artifact yet finish with a missing-tool failure, which stops a sequential workflow.
- Native foreground children share a process. Background runners and external CLIs still have the user's authority and inherited environment. Native foreground launch disables ambient extensions but accepts explicit configured extensions. `extensions: []` does not disable separate tool-derived or `subagentOnlyExtensions` inputs. Permission rules default unspecified tools to allow and exclude bash policy; this does not establish an installed sandbox.
- A local parent or explicit local child model is not an egress guarantee. Review provider-qualified role models, fallback lists and strict model scope. External CLI providers, external jobs, tools and opt-in pruned-fork summarization have independent routing and credential access. Fresh context can still include project context.
- Default artifacts include input, output, transcript and metadata. Seven-day cleanup is throttled, best-effort and nonrecursive; it misses nested `outputs/<runId>/...` and host logs. Child session JSONL has Pi ownership. Separate async retention defaults to 30 days but excludes active, referenced, resumable and other retained work. Neither interval promises erasure. External logs are bounded, not universally sanitized. Opt-in sharing uploads session HTML through `gh gist create`; it requires separate publication approval.

Evidence relative to the installed `pi-subagents` root: `src/agents/agents.ts`, `src/agents/agent-scope.ts`, `src/runs/shared/{child-tool-plan,child-session,permissions,model-fallback,model-scope,external-cli-runner,external-cli-contract}.ts`, `src/runs/foreground/{execution,subagent-executor}.ts`, `src/shared/{artifacts,types,pruned-fork}.ts`, and `src/runs/background/{async-retention,subagent-runner}.ts`. Review covered selected high-risk paths, not every line of these files, scheduler/UI path, dependency, provider implementation or external executable.

### Web Access 0.27.0

**Do not fetch authenticated PDFs with this release.** This restriction also lives in the active shared `AGENTS.md`, rather than only this maintenance reference. Browser-authenticated readable PDF extraction can send the entire document to Datalab or Gemini. `cache: "off"` does not prevent extracted Markdown writes under temporary storage. This contradicts the package's local-only authenticated-content promise. A reviewed fix must reject this path or provide local-only extraction with no-persistence support. Synthetic regressions must cover both PDF URL suffixes and response content types, asserting zero provider uploads and zero Markdown/cache writes. No runtime fix or config change is part of this checkpoint.

- Automatic search can fall through several providers. Anonymous Exa is hosted HTTP JSON-RPC; Ollama search uses Ollama Cloud. UI search defaults to summary review, whose model selection and fallback are independent of the active chat model. PDF and local-video paths can upload full files. Hosted-page fallback opt-ins do not gate these other paths.
- Credential sources resolve lazily, but availability checks can resolve Pi authentication. Trusted `!command` sources execute a shell command with timeout/output limits, not isolation. Keep the tracked Gemini source command-backed. Browser-cookie opt-in can copy cookie databases and invoke password stores; never enable it casually.
- Registration replaces process-global `fetch`. With a configured package proxy, unrelated global-fetch callers can use curl too. Curl receives headers/proxy credentials through argv and stages bodies in temporary files. This review did not establish actual live proxy use.
- Fetched-body cache uses private modes and a one-hour logical lifetime with opportunistic pruning, not timed deletion. Pi session entries and ordinary tool results survive cache expiry. PDF Markdown has no package-managed expiry or explicit private modes. Cloud-file deletion is best-effort; backend retention is unverified.
- Curator defaults to tokenized loopback HTTP. Remote binding is opt-in and supplies no TLS. The page loads Google Fonts and moving `marked@15` JavaScript from jsDelivr without integrity metadata. Those resources, external CLIs and caret-ranged dependencies are outside the package pin.

Evidence relative to `pi-web-access`: `index.ts`, `credential-source.ts`, `gemini-search.ts`, `summary-review.ts`, `summary-model-scope.ts`, `utils.ts`, `chrome-cookies.ts`, `auth-fetch.ts`, `extract.ts:525-527,1129-1147,1233-1237`, `pdf-extract.ts:185-232,324-332`, `gemini-pdf-extract.ts`, `video-extract.ts`, `storage.ts`, `curator-page.ts` and `curator-server.ts`. The review also traced selected provider/GitHub helpers. It did not audit every dependency, CLI, browser component or remote service.

## Anthropic subscription compatibility

The settings fragment pins `@gotgenes/pi-anthropic-auth@3.3.1`, reviewed and tested offline against Pi 0.87.1. The 3.x line requires Pi 0.86 or newer and handles its XML-sectioned prompts and mid-conversation system messages. Homebrew updates Pi independently, so recheck compatibility when either version changes. The release check covered every published version and dist-tag; 3.3.1 was the stable release with no published prerelease.

The reviewed archive has SHA-256 `5fe2145e14e0f1d35e7200a282d954a22cc9e968ffcf557c04c3c08f4d6ee90c`. Its manifest has no runtime dependencies or install hooks. Review covered all 16 source modules, including provider registration, prompt preservation, version recovery, configuration reads, and debug logging. Leave `PI_ANTHROPIC_AUTH_DEBUG` unset because it can print prompt excerpts. The description projection gates the manifest and every source module; this package adds no model-callable tools or skills.

The default registration changes only the built-in Anthropic transport. Native login, refresh, model discovery, and API-key behavior remain owned by Pi. OAuth shaping preserves ordinary project instructions, rules, custom sections, and assistant content while removing Pi's anchored documentation and preamble. This is unofficial request compatibility, not proof of included-plan billing or Anthropic's permission. Keep Pi's extra-usage warning enabled. Interactive turns, compaction, and `ctx.modelRegistry.streamSimple()` use the wrapper; direct `pi-ai/compat.streamSimple` callers and children without the extension do not.

The billing version starts at 2.1.280 and adopts a newer version from Pi's request headers. A recognized HTTP 400 version-floor rejection triggers one retry against the same endpoint with the same credentials. The required version is remembered in memory. No registry lookup, CLI execution, or configuration write is involved. Fish no longer pins `PI_ANTHROPIC_AUTH_CLAUDE_CODE_VERSION`: a fixed override disables this recovery and can become stale. Existing shells retain previously exported values, so run `set -e PI_ANTHROPIC_AUTH_CLAUDE_CODE_VERSION` in Fish before restarting Pi.

Extra-provider shaping is opt-in through global or trusted-project `extensions/pi-anthropic-auth/config.json` files. Keep their provider lists absent or empty for the default Anthropic-only setup. A global empty list does not block trusted-project additions. Names are validated syntactically, not checked as subscriptions; naming another provider can replace its specialized transport. Removed names remain active until `/reload`. Mid-conversation framing that appears literally inside custom instructions remains a parsing edge case.

Installation requires separate approval. Install the exact pin with npm lifecycle scripts disabled from a controlled directory, preserving unrelated live settings rather than blindly merging the fragment. Then restart Pi and check `/anthropic-auth:status` for version `3.3.1` and a resolved built-in transport. Native `/login anthropic` remains the authentication path. Credentials stay in Pi's private `auth.json`; no Claude Code installation is needed.

The repeatable offline check accepts either the reviewed unpacked archive or the installed package:

```bash
PI_PACKAGE_ROOT=/path/to/pi-coding-agent \
PI_ANTHROPIC_AUTH_ROOT=/path/to/pi-anthropic-auth \
node --test scripts/tests/test_pi_anthropic_auth.mjs
```

It clears inherited environment settings, uses a disposable HOME, and loads only this extension. Synthetic transcripts use Pi's current prompt builder and system-message format. Fake HTTP responses check initial and updated prompt shaping, instruction preservation, API-key pass-through, caller hooks, version synchronization, bounded retries, remembered version requirements, and override behavior. No live credentials, model requests, or billing checks are involved.

## Automatic VCC compaction

`@sting8k/pi-vcc@0.8.0` is installed and pinned. It was initially checked on Pi 0.85.1; the all-provider configuration uses the same synthetic checks on Pi 0.86.1. The Stow package owns `~/.pi/agent/pi-vcc-config.json`. This release only creates the config or fills missing keys with direct writes; it does not atomically replace the file. All six keys are declared. Recheck config ownership before upgrading.

`overrideDefaultCompaction: true` and `skipForProviders: []` let VCC handle Pi's `/compact`, threshold compaction, and overflow recovery on all providers, including newly added providers. Pi still owns the trigger and continuation; VCC replaces the summary step, so the two compactors do not run independently. To restore native compaction for one provider, add its exact provider ID to `skipForProviders`. To restore native compaction everywhere, set `overrideDefaultCompaction: false`. Explicit `/pi-vcc` still uses VCC in either case.

Smart tail retention is enabled; debug snapshots and the legacy automatic continuation are disabled. Pi 0.85.1 already resumes automatically. Explicit `/pi-vcc` bypasses provider exclusions, and `/pi-vcc keep:N` retains an explicit number of user turns. Native foreground local roles disable ambient extensions and do not gain VCC from this parent installation.

Restart Pi once after installation to load the extension. VCC rereads its configuration on each compaction, so later config edits need no restart. No manual compaction command is required during normal use on local or cloud models. Model quality and end-to-end latency remain unmeasured.

The `vcc_recall` tool and `/pi-vcc-recall` command search the current session, with active-lineage scope by default. They return transcript text, not credential-redacted excerpts; expansion can return unbounded content. The package's sanitizer removes terminal control characters only. These commands are not a replacement for the shared sanitized recall workflow or a safe transcript-audit tool. Recall output becomes model context, including when a session switches to a cloud provider. Keep `debug: false` because debug snapshots include transcript excerpts under `/tmp`.

The reviewed archive has SHA-256 `d3f0e271ab8bb8cecb082b4efa7b6f37404406d9e95a7a76bbc5247a5ffb9e7c`. All 85 archive files match upstream revision `303e89dbaf69833012066d6f3d7dcbb8255be81e` and the installed package. The manifest has no runtime dependencies or lifecycle scripts. Installation disabled npm lifecycle scripts and preserved existing package bytes and all runtime settings except the package addition. This is a bounded source inspection, not an audit of every extraction heuristic or dependency.

The repeatable offline check uses only synthetic history and a disposable HOME:

```bash
PI_PACKAGE_ROOT=/path/to/pi-coding-agent \
PI_VCC_ROOT="$HOME/.pi/agent/npm/node_modules/@sting8k/pi-vcc" \
node --test scripts/tests/test_pi_vcc.mjs
```

It checks the tracked pin and flags, Pi extension registration, automatic VCC compaction on local, cloud, and a fictional new provider, provider switches, explicit VCC compaction, unchanged source history, and synthetic recall. It makes no model requests and reads no live transcripts. The description projection includes `vcc_recall` and gates seven reviewed VCC source and manifest files.

## Copilot local delegation

The local delegation build uses the `pi-subagents` source checkout at `~/Developer/pi-subagents`, branch `feat/copilot-local-scout`, building on the existing same-provider guard. Its installed package belongs at `~/.pi/agent/local/copilot-delegation/node_modules/pi-subagents`. The settings fragment uses a relative local package source so a normal merge cannot silently replace the guard with the published npm release. The prior `~/.pi/agent/local/copilot-scout` and `~/.pi/agent/local/provider-guard` installations remain available for rollback. The active installation directory retains its tarball and `build-receipt.json`; the private pre-trial settings backup remains in the earlier scout directory. The installed files were compared byte-for-byte with the tarball and reviewed source; the installed Pi SDK loaded the package in an isolated, credential-free loader check.

The fragment explicitly selects the installed build's official Worktrunk allocator. Subagents retains lifecycle ownership of `pi-subagents/` branches and suppresses Worktrunk project hooks during allocation. Before installing a changed package, run
`node scripts/tests/worktrunk-subagents-smoke.mjs --candidate-root /path/to/reviewed/pi-subagents`.
The candidate is mandatory: the test never falls back to the active installation.
It imports only the candidate's worktree modules with existing loader dependencies
and uses disposable repositories and an isolated environment, without a child model.
The preservation matrix checks binary replay, stale or corrupt evidence, capture
and validation failures, and setup-failure retention for native and Worktrunk
allocation. A candidate pass does not activate that build or extend its
model/provider permissions. Keep the installed managed-cleanup restriction until
the accepted candidate is separately approved and deployed.

A preservation-only recovery candidate is retained at source commit
`11a0d35342788be29f307160a75c96a10869befa` in the local Subagents source repository.
Its tested runtime commit is `c5b64a0c5c5991843f49ad3d6b6cd86ba471b1b0`; the
follow-up restores only three receipt-verified baseline documentation files.
The recovery directory is
`~/Developer/pi-subagents.w01-preservation/tmp/w01-evidence/recovery-11a0d3534278`.
It contains the package, committed-source archive, inventories, signed lineage,
validation records, `SHA256SUMS`, and `RECOVERY.md`. Preserve these together and
back them up before rebuilding a machine; a local commit ID alone is not a
remote recovery source.

The package SHA-256 is
`a8ac66f22c5483fb42181de850cbcb3287c84f89aa406a8a782ba921abeba394`.
The source archive SHA-256 is
`ae3579e12a31a03cac05c61ed921852aa7729d9e8494f0d97b5a3e369bf23630`.
All 304 package files match that source; only the two reviewed preservation
modules differ from the old installed artifact. The source archive includes
baseline tests, the preserved role definition, and documentation omitted from
the initial code-only snapshot. Packing used offline mode with lifecycle scripts
disabled; it did not install anything.

This candidate is **not installed or activated**. The preservation matrix,
provider/role regressions, and synthetic integration checks passed, but a full
credential-free SDK loader check and fresh-machine restoration are still pending.
Do not treat archive hashes as dependency-closure or host-runtime proof. Follow
`RECOVERY.md`, retain the old package and receipt for rollback, and obtain separate
approval before installation, package-source changes, or a Pi restart.

The guard permits top-level Copilot-to-local launches only for canonical `scout` and `local-editor` roles. The configured exception is `subagents.modelScope.localDelegation`, with `rootProvider: "github-copilot"` and exact per-role model allowlists. Global scope remains enforced and strict with `allow: ["inherit-provider"]`. Provider-specific role defaults select Qwen; the scope authorizes or rejects the selected model. The local exception does not apply to nested callers, other roles, or other root providers. Both roles are fresh-context leaves with no fallbacks or ambient extensions. Scout is read-only. Local editor adds `edit` and `write`, but not shell access; the Copilot parent reviews the diff and runs validation. Original launch identity prevents resume even after routing policy changes. Broad implementation, ambiguous changes, and substantive review stay on Copilot.

The tracked configuration currently selects `omlx/Qwen3.8-27B-oQ8e-mtp`. The models fragment marks it as reasoning-capable and selects `compat.thinkingFormat: "qwen-chat-template"`, with `supportsDeveloperRole: false`. Capability does not enable thinking: the local roles retain `thinking: false`, which Pi serializes as `chat_template_kwargs.enable_thinking: false`. Direct thinking-enabled requests send `true`. The models merger applies these tracked overrides to the matching generated model entry because Pi's native `modelOverrides` do not override custom entries in `models`.

In oMLX's model settings, leave `enable_thinking`, `reasoning_effort`, and `preserve_thinking` out of `forced_ct_kwargs`. Existing thinking defaults may remain enabled for clients that omit request controls. Saving a changed setting can detach the active profile; reapplying a profile that forces these keys will break request control again. Use oMLX's authenticated local admin API or UI so changes update both the running server and its app-owned settings. Keep those live files and profiles out of Git.

Verify the merge with `python3 -m unittest discover -s scripts/tests -p test_merge_pi_settings.py`. The opt-in, network-free serializer check is `PI_PACKAGE_ROOT=/path/to/pi-coding-agent node --test scripts/tests/test_pi_qwen_thinking.mjs`; it uses fictional inputs and a fake transport to check both thinking-off and thinking-on requests. End-to-end local-role validation still requires a Copilot-rooted session and one bounded scout run under the existing guard.

oMLX still owns the credential-bearing `models.json`; do not copy it into the repository. The guard requires the resolved local model endpoint to use numeric loopback `127.0.0.1` or `[::1]`; DNS names such as `localhost` are rejected. A loopback endpoint is not a network sandbox for the server behind it, so oMLX must remain configured for local inference.

Build and install only reviewed source into a new local directory, with npm lifecycle scripts disabled. Keep the currently loaded installation intact until the session ends. Before activation, run the guard's synthetic foreground/background and policy tests, then the dotfiles merge and measurement tests. Apply the settings fragment only after the local package exists, and restart Pi to load the new implementation. `/subagents-models scout` and `/subagents-models local-editor` show the selected role models; they do not prove launch authorization or savings.

Fresh-machine setup cannot obtain this unpublished local branch from npm. Restore the reviewed source checkout and local package before starting Pi with local delegation enabled. Package upgrades and rollback are explicit operations, not automatic fallback to npm. Rollback restores the previous package source and Copilot role models, removes the local exception and role overrides, and restarts Pi. Do not clear the same-provider guard.

In Copilot-rooted sessions, use the local scout only for short, mechanical fact gathering over one narrow source area or question, including one caller trace, test discovery, or configuration extraction. Merge-conflict analysis, cross-cutting synthesis, architecture decisions, and broad repository reconstruction stay on Copilot; use the same-provider `delegate` or `reviewer` only when delegation adds value. Delegate before reading the same material in the parent; use direct tools for a single quick lookup. Give exact source paths and request a short answer with file and line references. Cap foreground local runs at three minutes and do not retry timed-out local work. Prefer the local editor for small, well-specified changes with explicit files or a narrow source area and clear acceptance criteria. Suitable work includes mechanical refactors, configuration or documentation edits, and focused bug fixes that need no new product, architecture, security, or scope decision. The parent inspects every resulting diff and runs the relevant checks. Run one local role at a time. Evaluate total Copilot usage, elapsed time, and correction work on representative tasks. Synthetic guard tests do not establish measured token savings.

## Update procedures and convergence

1. Homebrew owns the Pi CLI, not Pi self-update or Mise. `Brewfile` declares `pi-coding-agent` without a version. `scripts/setup.sh` runs `brew bundle`, configures `brew autoupdate` with upgrade/cleanup, immediate execution and AC gating, then changes its schedule to 06:00 daily. Existing autoupdate jobs are not fully reconfigured. Pi can therefore move while extension pins stay fixed. After a host update, review installed Pi docs and check extension compatibility, especially Subagents' private loader-cache access. Trace the loader's transitive imports and run a credential-free isolated import check before accepting SDK compatibility; unchanged loader APIs alone are insufficient. This is not a reproducible host-version lock.
2. Mise owns the tools in `stow/mise/.config/mise/config.toml`. Setup runs `mise install --yes`. The existing daily `stow/mise/.local/bin/update-npm-tools.sh` now updates global runtimes within reviewed release lines and declared npm CLIs, including PostHog. It verifies global-only configuration, preserves exact pins, uses `--no-prune`, checks executable resolution and blocks Node version changes when unmanaged globals remain. AC gating, overlap protection and overdue login catch-up apply; `--force` bypasses only power and recency checks. The weekly audit reports reviewed pins and agent-reader without upgrading them. This updater does not manage Pi packages. See [software updates](software-updates.md) for ownership, reports and deployment.
3. Pi packages require explicit reviewed version changes. Inspect the proposed exact published source, manifest/dependency changes, execution/credential/routing/retention paths and relevant installed Pi docs before approving installation. Update `stow/pi/.pi/agent/settings.fragment.json` and the measurement's reviewed-source gates together. Run synthetic checks in disposable homes. Never refresh digests merely to silence a drift failure.
4. Once installation is separately approved, stop affected Pi sessions, recheck portable settings through the architecture reference's allowlist, then run the focused `scripts/merge-pi-settings.sh`. It installs nothing and replaces complete arrays while preserving unrelated runtime fields. Install each changed exact spec with `pi install npm:<name>@<version>` from a controlled, nonproject directory. That command also writes runtime settings; rerun the fragment merge to restore authoritative array order and filters, verify package manifests against the fragment, then restart Pi. A fragment edit alone is not proof that installed bytes or a running session changed. Avoid the separate legacy `npx pi-subagents` installer, which can clone or pull an extension checkout.
5. Fresh setup merges the five retained package declarations but does not eagerly install Pi packages. Pi 0.84.4's `dist/core/package-manager.js` checks installed npm versions against configured ranges in `resolvePackageSources` and installs missing/mismatched sources when permitted. Thus fresh and existing setups have a path to the declared top-level versions, through approved explicit installs or later permitted startup resolution. Offline mode, skipped/failed installs, project overrides and older host behavior can prevent convergence. This checkpoint inspected manifests and source, not a fresh-machine or live convergence run. npm transitive dependencies, host Pi, external CLIs and services are not fully locked or fully reviewed.
6. Overlay updates follow `scripts/install-agent-reader.sh` and [its README](../patches/agent-reader/README.md). Update the exact base, allowed patch paths, patch/constraint digests and patched tree together, then rerun synthetic installer and upstream fixtures before approved installation. `--force` deliberately rebuilds/reinstalls; normal setup may retain a help-compatible existing CLI without proving identical provenance. Prefer this installer over generic `uv tool upgrade` as the overlay owner. Replace the overlay only with a reviewed exact public commit containing equivalent Pi discovery, normalized JSON, limits and tests. Remote reads, including Git fetches, are allowed by the global policy. Upstream publication still requires explicit approval in the current turn. The pinned Python/dependency inputs do not pin Homebrew uv or universal wheel bytes.

`stow/pi/.pi/web-search.json` remains a tracked credential-command declaration. Web Access curator commands can rewrite that file through its stowed path. Review intentional portable changes; do not capture resolved secrets or treat runtime provider/UI changes as approved repository policy. Changing its storage pattern requires a separate decision.

Official installed documentation used here is under `/opt/homebrew/Cellar/pi-coding-agent/0.84.4/libexec/lib/node_modules/@earendil-works/pi-coding-agent/docs/`: `packages.md`, `extensions.md`, `skills.md`, `settings.md`, `session-format.md` and `quickstart.md`. The current [official package docs](https://pi.dev/docs/latest/packages), checked through Context7, also confirm exact npm pins are skipped by package updates and extensions have full system access. General `pi update --all` is not this repository's reviewed package-bump procedure.

## Repeatable offline description projection

Run from the repository with managed Python 3:

```bash
python3 scripts/measure-agent-tooling.py \
  --packages-root "$HOME/.pi/agent/npm/node_modules" \
  --subagents-root "$HOME/.pi/agent/local/copilot-delegation/node_modules/pi-subagents"
python3 -m unittest discover -s scripts/tests -p test_measure_agent_tooling.py
```

The helper reads only named package source/manifests, the tracked fragment and shared skill files. It executes no JavaScript, extension registration, tools, hooks, credential commands, subprocesses, models or network requests. It needs no live Pi startup, disposable runtime loader or dependency installation. Synthetic tests use temporary source trees. Its description totals cover all five declared packages and shared skills. Anthropic Auth contributes no tool or skill descriptions; its command and provider payload changes are outside the description totals. The `unmeasured_packages` output field is empty.

This is a source-derived default-registration projection, not observed runtime registration. It assumes an ordinary parent after `session_start`, default Subagents description mode, enabled `bg_wait`, and all four default Web Access tools. This includes `subagent_supervisor`, which `src/extension/index.ts:1102` registers through `src/intercom/native-supervisor-channel.ts:598-603,707-708,842-846`. The helper reads those sources without invoking the hook. The tracked Web Access template changes only a credential source. Live configuration, environment variables, project resources and runtime activation are intentionally outside the measurement.

The table, advertised-skill list and fingerprint below record the checkpoint 3 snapshot. Current policy also advertises `teach`, `recall` and `handoff`; only `grill-me` and `simplify-review` remain command-only. Rerun the helper for current counts and fingerprint.

| Description text | Tools | Characters | UTF-8 bytes | Advertised skills | Skill characters | Skill UTF-8 bytes |
|---|---:|---:|---:|---:|---:|---:|
| Context7 0.1.2 | 2 | 2,435 | 2,435 | 1 | 778 | 784 |
| Subagents 0.65.0 | 3 | 5,582 | 5,606 | 2 | 462 | 462 |
| Web Access 0.27.0 | 4 | 2,483 | 2,485 | 0 | 0 | 0 |
| Shared skills | 0 | 0 | 0 | 3 | 608 | 608 |
| Total | 9 | 10,500 | 10,526 | 6 | 1,848 | 1,854 |

Characters are Unicode code points, not JavaScript UTF-16 units or provider tokens. Counts include default dynamic description fragments: Subagents' named guidance constants, enabled-wait suffix selection, and Web Access's storage note/tool-name list. Custom/compact/full description overrides and disabled/renamed tools are not modeled.

At checkpoint 3, advertised skills were `context7-docs`, `pi-subagents`, `council-mode`, `devonthink`, `things` and `unslop`. Five discovered skills were excluded by `disable-model-invocation: true`: `grill-me`, `handoff`, `recall`, `simplify-review` and `teach`. Package discovery follows the actual manifests: Context7 and Subagents declare `./skills`; Web Access's manifest declares no skills. The fragment has no package filters. The helper stops at `SKILL.md` roots, skips hidden/dependency directories and ignores root Markdown in the shared skills directory. Changed manifest paths/filters, ignore rules, symlinks, collisions and unsupported measured-field syntax require review rather than an approximate fallback.

These totals exclude schemas and parameter descriptions, `promptSnippet`, `promptGuidelines`, tool names/labels, skill names, XML escaping/wrappers/location text, skill bodies/helpers, built-in tools, commands, prompts, role definitions, dynamic resources and provider serialization. They are not full context cost or measured provider tokens. Checkpoint 3 accepts this source projection as its offline baseline. Observed extension registration remains a checkpoint 7 follow-up, not a result established here. The parent separately compared the skill descriptions and manual exclusions with Pi 0.84.4's official offline skill loader; they match.

The JSON output lists per-item sizes and input SHA-256 values. Its input-set fingerprint is `1952df70fcfed2b8314f7313433bdddbd0e0b5ad30502f89f1cb06bb2df6f916`. Reproduce it by sorting input labels lexically, joining `sha256 + two spaces + label + LF`, encoding UTF-8 and hashing that byte string. Package labels identify their package regardless of install location. Subagents files come from the explicit `--subagents-root`; the other package files come from `--packages-root`. This prevents a stale npm copy from standing in for the active local guard. The `shared/skills/` labels refer to tracked shared skills, and `tracked/settings.fragment.json` identifies the Pi fragment. This identifies the bounded measurement source set, not every file in the broader security-path review, dependency resolution or a reproducible transitive installation. The helper hard-checks 39 package source and manifest files before projecting. The temporary Plannotator manifest gate was removed with the package. Skill bytes are measured and fingerprinted on each run. The historical fingerprint above is not the current declaration fingerprint.
