# Agent tooling consolidation plan

## Objective

Keep Pi as the primary coding tool, preserve model choice and shared skills, and make the actual setup reproducible. Fix the configuration and privacy defects found in the post-migration review before adding MCP capabilities.

This plan follows the review of `aa0657e`. It replaces neither the architecture reference nor the completed harness-migration history. Update its checkpoints with evidence as work proceeds.

## Authority and constraints

- Local source changes and focused tests are authorized. Keep publication, Git fetches and pulls, and changes to separate upstream repositories behind explicit user approval.
- Keep credentials, resolved configuration, real transcripts, browser profiles, and identifying work configuration out of git and tool output. Inspect live settings through an explicit non-secret field allowlist.
- Test setup and hooks in disposable repositories and homes. Do not run full machine setup against this Mac for validation.
- Review exact package source before introducing or upgrading executable integrations. Pin installed versions without describing a version pin as a completed source review.
- Preserve DEVONthink's official server, exclusions and redaction. No interactive AppleScript, JXA, or direct-database bypass is allowed.
- Preserve Claude and Copilot compatibility until their remaining workflows are covered and retirement is explicitly approved.
- Use one writer per working tree. Add regression tests before fixes when practical. Commit only intentional changes after the repository's checks and staged secrets gate.

## Decisions

1. The Pi fragment remains authoritative for portable settings, including complete package and model-cycle arrays. Runtime writes do not silently become repository policy. Capture deliberate changes back into the fragment; do not union arrays because that would defeat removals.
2. Preserve the current startup model and provider routing. Capture the current model cycle and non-secret portable preferences, including the telemetry opt-out and opt-in project trust.
3. Pin the currently installed Context7, Subagents, and Web Access versions first. This freezes existing behavior while source-review and upgrade procedures are documented.
4. Pilot the MCP adapter with Chrome DevTools before changing DEVONthink. Web retrieval remains separate from interactive browser control.
5. Treat work MCP migration and DEVONthink transport replacement as evaluations with explicit acceptance gates. An evaluation can conclude that the retained implementation is preferable.
6. Keep the existing Things helper and native Context7 integration. Leave Anki and filesystem MCP retired. Do not add Pi comment stripping without a demonstrated need.

## Checkpoints

### 1. Reconcile and protect Pi configuration

Status: Complete.

- [x] Capture the live portable settings through a field allowlist and add the missing Subagents package.
- [x] Pin the installed package versions and preserve the intended model cycle, UI preferences, telemetry opt-out, and project-trust behavior.
- [x] Require exactly one readable JSON object per Pi merge input. Reject malformed, multi-document, non-object, empty, symlinked, and non-file inputs without replacing live settings.
- [x] Preserve unrelated runtime fields, intentional array replacement, atomic writes, and no-op behavior.
- [x] Dispatch generated-config rebuilds even when a change touches only the rebuild script, without changing opt-in Stow activation or deletion behavior.
- [x] Add disposable-HOME merge tests and disposable-repository hook tests, including script-only changes.
- [x] Update the architecture reference with settings ownership and safe capture instructions. Apply only the focused settings merge to the live machine after validation.

Acceptance: Setup reconstructs the intended portable configuration; rerunning the merge is idempotent; deliberate package removals work; script-only merger changes invoke the rebuild; invalid inputs preserve the previous bytes.

### 2. Align recall, handoff, and privacy instructions

Status: Complete.

- [x] Distinguish content recall explicitly requested by the user from metadata-only transcript audits without weakening the global privacy rule.
- [x] Replace unrestricted transcript-to-tool-output examples with structured, bounded, sanitized extraction. Keep `agent-reader` responsible for session-format parsing.
- [x] Prefer metadata and explicit handoffs; authorize deeper transcript extraction only for gaps in the requested scope. Do not claim arbitrary secrets can be perfectly detected by a generic redactor.
- [x] Make recall use `~/.context/` outside repositories, matching handoff.
- [x] Clarify any interactive DEVONthink verification guidance that conflicts with exclusions while preserving trusted pipeline maintenance.
- [x] Validate with fictional handoffs and synthetic normalized transcripts containing fake secrets and irrelevant workspaces. Confirm shared skills remain usable across retained clients.

Acceptance: Documented commands do not emit whole live transcripts; extracted evidence stays within the requested scope; non-repository handoffs are discoverable; excluded records remain excluded.

### 3. Establish package ownership and maintenance

Status: Complete for bounded source review and the offline baseline. Observed extension registration and fresh/existing installation checks remain in checkpoint 7.

- [x] Add a short capability table with implementation, pinned version, source-review status, privacy constraints, update procedure, and verification coverage.
- [x] Review the exact installed Subagents and Web Access sources relevant to execution, credentials, child-provider routing, and output retention. Record limitations rather than implying a complete security audit.
- [x] Document the separate Homebrew, Mise, Pi-package, and downstream-overlay update paths. Use explicit reviewed version changes rather than unattended extension upgrades.
- [x] Measure registered tool descriptions and advertised skills for the current Pi package set with synthetic/offline inspection. Record characters or bytes without equating them to measured provider tokens. The accepted baseline projects default registration from checked source without executing extensions; it is not observed runtime registration.
- [x] Verify that choosing a local model is not documented as making external tools offline. Record which tools may contact cloud services.
- [x] Check public agent-reader source for an overlay replacement. The public default branch still points at the overlay's base, `09080db090f0741707652535fd8be8b8df429e4c`, and its README lacks the Pi/JSON commands. Retain the overlay; no equivalent public replacement was established. Recheck when upstream changes. Any needed Git fetch or upstream publication requires user approval.

Acceptance: A fresh setup and an existing setup can reach the same declared package versions, update ownership is explicit, and outstanding source-review or upstream dependencies remain visible.

### 4. Pilot Chrome DevTools through the MCP adapter

Status: Complete for the isolated pilot. Pi `0.85.1`, adapter `2.32.1` and signed Chrome `152.0.7977.83` passed the fixed fictional browser checks. Live adoption, normal Pi/TUI verification and Claude fallback retirement remain deferred. See [the pilot record](agent-browser-pilot.md).

- [x] Review an exact published adapter version, its package dependencies, configuration precedence, command execution, tool filtering, approvals, lifecycle, and output spill behavior.
- [x] Review and pin an exact official Chrome DevTools server version. Compare the full MCP path with the experimental official CLI against the required browser tasks. Adapter 2.32.1 and Chrome server 1.8.0 remain pinned test inputs, not live packages.
- [x] Choose a supported isolated Chrome installation. Google-signed Chrome `152.0.7977.83`, acquired through Homebrew's download-only path, passed strict codesign and Gatekeeper checks. Both launches used fresh temporary profiles; the everyday browser and authenticated profiles remain separate.
- [x] Follow existing installation and Stow conventions. Opt-in test resources remain outside Stow/setup; app-owned overrides, caches and profiles remain unstowed. Homebrew owns the approved Pi host upgrade. Live activation requires a separate decision.
- [x] Disable unnecessary scripting, sampling, elicitation, and automatic host-config imports for the pilot. Execute Chrome with telemetry and CrUX opt-outs. Keep the separately permitted fixed browser calls distinct from approval-refusal tests.
- [x] Test filtering and headless approval refusal with a synthetic MCP server before browser access.
- [x] Exercise navigation, DOM interaction, screenshot, console/network inspection and automatic/explicit performance traces against a local fictional fixture. Verify EOF/SIGTERM shutdown, reconnect, profile removal and owned output cleanup.
- [x] Accept the isolated pilot and retain its limits. Catalog assertions observe SDK-normalized fields, not raw-wire metadata. Preserve the Claude browser fallback until normal-client parity is demonstrated.

Acceptance: The fixed tasks pass through official Pi extension loading and actual adapter calls without an existing private profile or unexpected SDK-visible tool activation. Owned runtime cleanup and disabling the opt-in runner are verified. This does not establish live Pi model/TUI parity or justify global adapter activation.

### 5. Evaluate private work MCP in Pi

Status: Source and synthetic evaluation accepted; live migration deferred. The existing definition and client are retained. The private evaluation records the candidate, source limitations and approval gates. An exact server/dependency review and separately approved backend canary remain necessary.

- [x] Inventory existing work definitions using sanitized structural metadata only.
- [x] Evaluate adapter transport and authentication configuration without printing endpoints, environment values, or credentials. Credential validity, permissions and hosted runtime behavior remain unverified.
- [x] Keep work documentation in the ignored work package. No production definition was adopted or activated, and no legacy configuration was imported globally.
- [x] Validate fictional stdio configuration delivery through the actual adapter, retaining filtering, headless refusals and cleanup checks. The hosted alternative remains source-reviewed only. Record a bounded real-backend approval scope before making business-data queries or mutations.
- [ ] Retain the old client until authentication, needed tools, permissions, and results are verified. Evaluate Zed separately; MCP parity does not imply editor-client parity.

Acceptance: Necessary work tools function through Pi under the same confidentiality requirements, or a specific blocker and retained fallback are documented. No identifying work data is tracked.

### 6. Harden DEVONthink and decide transport ownership

Status: Pending. Client hardening can precede checkpoint 4; adapter replacement depends on it.

- [ ] Keep the three-operation interface and official stdio server as the baseline.
- [ ] Add synthetic regression tests for output shape, UUID/name projection, result counts, bounded transport frames, and safe error reporting. Inspect actual documented response envelopes before enforcing a schema.
- [ ] Harden the retained helper so its implementation matches its stated bounds. Preserve shared CLI use across Pi and retained clients.
- [ ] Compare adapter-backed access with the helper on exact tool allowlisting, fixed search fields, batch limits, response filtering, protocol compatibility, sampling, and sensitive spill files.
- [ ] Replace custom protocol handling only if those constraints and cross-client use remain practical. Otherwise retain the hardened helper and record the maintenance tradeoff.
- [ ] Keep the unused HTTP login item disabled. Perform vendor privacy canaries only under a bounded, approved private-data validation scope.

Acceptance: The chosen implementation enforces the declared interface rather than merely requesting limited results, preserves vendor exclusions, and has tests for its failure behavior. A broader interface requires an explicit user decision.

### 7. Close verification and maintenance follow-ups

Status: Pending. Depends on accepted earlier checkpoints.

- [ ] Run focused tests, relevant shell/JSON checks, isolated Stow verification, and the repository's required checks for changed subsystems.
- [ ] Compare observed extension registration with the checkpoint 3 source baseline in a disposable credential-free environment that blocks external side effects. The offline skill-loader comparison is already covered.
- [ ] Exercise fresh/existing top-level package convergence under an approved installation scope. Static source and manifest checks do not prove successful installation; host and transitive versions remain separately owned.
- [ ] Resolve or explicitly retain the package restrictions in `docs/agent-tooling-maintenance.md` before final acceptance. Authenticated PDF use requires a reviewed upload/persistence fix; Subagents trust, fallback and cleanup claims need synthetic verification. Decide separately whether Web Access's app-rewritten config should move to a generated or fragment-merge pattern. Installed/upstream changes and publication need separate approval.
- [ ] Confirm tracked configuration contains no credentials or runtime state and live portable fields match the accepted fragment through a redacting parser.
- [ ] Keep full setup validation open until a disposable macOS VM or new-machine snapshot is available. Record the environment, authentication boundaries, and expected outcome before running it.
- [ ] Check physical Ghostty keys and real-client workflows during normal use rather than synthesizing private production work for coverage.
- [ ] Leave only specific deferred decisions and environment-dependent checks in this plan. Record completion evidence without copying raw logs or private content.

Acceptance: The primary Pi workflow is reproducible at every tested boundary; browser and work adoption decisions are documented; deferred hardware and private-backend checks remain explicit rather than being marked complete.

## Progress record

- The user approved planning and beginning the consolidation work. Checkpoint 1 is implemented, independently reviewed, and applied through the focused settings merger.
- Regression tests reproduced the old merger's multi-document and fragment-symlink acceptance, FIFO blocking, and script-only rebuild skips before the fixes. The new suites are `scripts/tests/test_merge_pi_settings.py` and `scripts/tests/test_restow_changed.py`. The full scripts suite passes 62 tests under managed `python3`; its unrelated top-hits module requires `tomllib`, so Apple's Python 3.9 is not suitable for full-suite discovery. Focused merge/restow tests also pass under Apple's Python.
- Independent review found no defects in the implementation. The parent reran the tests and reconciled a concurrent live default-model change to `openai-codex/gpt-6-astra` before application. The six-entry cycle retains its existing order. Subagents 0.65.0 and Web Access 0.27.0 are now declared and pinned alongside Context7 0.1.2.
- A field-allowlisted preflight confirmed that the current portable preferences, package identities/order, and installed versions still matched the intended capture. The live merge preserved unrelated runtime fields; its second invocation left the settings inode and modification time unchanged. The merger does not coordinate locks with Pi's own runtime saves.
- Shellcheck was unavailable. Bash syntax checks, behavioral tests, and whitespace checks cover this slice; static shell lint remains an environment limitation.
- Public source inspection confirmed that the agent-reader default branch remains at the overlay base. Adapter research identified published `pi-mcp-adapter@2.32.1` at source revision `10a45367e033a32026987a75d6f401e37340c86f` as a pilot candidate. Its release-specific README was reviewed, but executable source/dependency review and installation remain pending.
- Checkpoint 2 adds one stdlib filter over agent-reader normalized JSON. Exact workspace/client/session scope, explicit date windows or all-history, metadata defaults, opt-in bounded prose, unknown-date counts, and pagination replace unrestricted transcript output. Handoff lookup now matches leading workspace/topic headers in repo-root `.context/` or nonrepo `~/.context/`, with tracing disabled. DEVONthink journal guidance requires user inspection in the UI rather than an interactive exclusion bypass.
- Independent review found a fenced-block disclosure when shorter delimiters or delimiter lines with trailing text closed an outer fence. The parent reproduced four failures, then made closing delimiters require the opening character, sufficient length, and no non-whitespace suffix. The retained regression also checks mixed fence characters and longer valid closers. Earlier regressions cover dump continuations, quoted handoff headers, and inherited shell tracing.
- The parent verified 85 scripts tests under managed Python and 23 focused recall tests under Apple's Python. Disposable Stow validation and the focused live `agents` restow linked the new helper. Pi's offline skill loader reports no diagnostics; recall, handoff, and simplify-review remain manual, and retained Claude paths resolve to the shared skills. Commit `3ade2da`'s instructions and simplify-review remain unchanged.
- Recall sanitization remains heuristic, topic matching is literal, normalized dates may identify prompts rather than responses, and JSON input is held in memory. These limitations are documented; output bounds do not imply streaming input limits or a guarantee against arbitrary private prose.
- Checkpoint 3 adds `docs/agent-tooling-maintenance.md` and `scripts/measure-agent-tooling.py`. Separate source reviewers traced Subagents 0.65.0 and Web Access 0.27.0 execution, credentials, routing and retention. An independent final review covered the documentation and measurement helper. This is a bounded source review, not a full security audit or dependency certification.
- Web Access authenticated PDF fetching remains prohibited because its extraction path can upload authenticated bytes and persist Markdown despite local-only and cache-off promises. Subagents project discovery is not demonstrably gated by Pi trust; explicit local model selection is not a locality lock, and cleanup does not cover every output/session path. The maintenance reference records restrictions and separate update owners. Documentation does not patch runtime behavior.
- The accepted source-derived default baseline counts nine tool descriptions at 10,500 Unicode code points and 10,526 UTF-8 bytes, plus six advertised skill descriptions at 1,848 code points and 1,854 bytes. Five manual skills are excluded. It includes ordinary session-start supervisor registration and excludes schemas, prompt metadata, wrappers and provider serialization. No provider tokens were measured. Pi's official offline skill loader independently matches the skill descriptions and manual exclusions.
- Parent and independent review corrections cover omitted supervisor registration, `.fdignore` handling, YAML non-string measured fields and helper paths. Synthetic regressions reproduced the measurement defects before correction. The parent verified 100 scripts tests, 15 focused tests under Apple's Python, and an unchanged 27-input measurement fingerprint. Earlier phases, package pins, installed sources and runtime settings remain unchanged.
- Top-level installation convergence is supported by Pi 0.84.4 source and matching installed manifests, not a fresh-machine test. The parent accepts the bounded offline baseline and retains observed extension registration and installation checks in checkpoint 7.
- Checkpoint 4 records a deferred browser pilot in `docs/agent-browser-pilot.md`. Exact source reviews covered adapter 2.32.1 and Chrome server 1.8.0. The locked synthetic test installation used 39 packages from a 56-entry integrity-verified graph, omitted optional native/JAR packages, and ran no lifecycle hooks. The runner checks package bytes, lock, Pi loader and versions before execution.
- Independent implementation review found a process-group exit race between probing and signaling. The parent reproduced both first-signal and later-signal cases, corrected cleanup, and reran 109 scripts tests, nine focused Apple-Python tests and both actual synthetic adapter modes. Six headless calls were refused, five separately permitted fictional calls dispatched, five fixture starts matched five exits, and ten unsolicited requests were rejected. Private spills survived adapter teardown and were removed with the owned runtime trees.
- The official CfT archive matched its expected bundle identifier/version and arm64 architecture but could not pass the agreed strict bundle-signature gate. Its ad-hoc/linker-signed packaging lacks sealed resources; this is not evidence of tampering or browser incompatibility. The archive/app were removed after static diagnostic evidence was recorded. No Chrome server/browser code ran, and all browser tasks remain pending.
- Adapter cold-cache startup, Apps filtering bypass and persistent-output limitations remain documented. The finite Pi-loader checks are not a model session, live TUI test or general permission boundary. Existing package/model settings and browser fallback remain unchanged.
- The user supplied the standard Google Chrome `152.0.7977.83` DMG through Homebrew. Its Google LLC signature, bundle identity, universal architecture and Gatekeeper assessment passed without relaxing the signature gate. The recorded digest identifies the downloaded bytes; Homebrew's moving URL and `sha256 :no_check` do not provide an independent checksum guarantee.
- Homebrew had replaced Pi `0.84.4` with `0.85.0`. An approved compatibility review missed a transitive import, and the synthetic retry failed before adapter registration. The exact official npm archive confirms that the unbundled loader reaches `experimental/server.js`, which imports undeclared `@earendil-works/pi-server`. The normal bundled CLI was not tested and is not claimed broken. The attempted host-gate update was reverted.
- Independent review accepted the documented blocked outcome. The parent verified the official archive integrity, five installed-source matches, missing dependency declaration, restored host-version refusal, 110 offline scripts tests and nine focused Apple-Python tests. The exact source/dependency/diagnostic/app-extraction workspaces were removed after review; the user's Homebrew DMG remains intact. Browser execution, profiles and browser-task results remain absent.
- The user approved the Homebrew upgrade to Pi `0.85.1`. Source review confirmed removal of the broken import chain; installed-source comparison and a cleared-environment loader import passed. The runner now pins the loader, root index and transitive main entrypoint. Both synthetic modes passed before browser execution; Node and adapter/server dependency pins remain unchanged.
- The parent reran nineteen fixed browser calls through official Pi extension loading and the actual adapter. Two connections matched 27 SDK-normalized tools and the twelve-tool allowlist. DOM interaction, a 1280x720 PNG, console/network retrieval, header redaction and both trace modes passed. The second loopback origin received zero requests. Four server/browser exits, two removed profiles and all twenty observed owned process exits passed without forced cleanup.
- Independent review found a process-title race in descendant tracking. The parent reproduced it, switched ownership to PID/start time, preserved PID-reuse refusal and made ambiguous cleanup retain its runtime. The retained regressions and full rerun passed, including 120 scripts tests and eighteen focused Apple-Python tests. The category-only catalog expectation adjustment follows the negotiated legacy SDK; discarded wire annotations remain outside runtime visibility.
- The secrets scanner mistook two `press_key` catalog SHA-256 digests for API keys. A filter matches only that rule, file, field shape and those two exact public digests. Disposable-repository tests confirm that changed hashes, other fields and other paths still produce findings.
- Checkpoint 4 is accepted for the isolated pilot. Live model/TUI checks, production configuration and fallback retirement remain separate decisions, with cold-cache startup, Apps filtering and output-retention limits still documented. The next planned checkpoint is the private work MCP evaluation; real-backend access requires its own bounded approval.
- Checkpoint 5's source and synthetic evaluation is accepted after independent review. The existing private definition remains unchanged, and the detailed comparison and proposed canary stay in the ignored work package. Actual server dependencies, authentication, permissions, nested requests and result parity remain approval gates; live migration is deferred.
- Both actual synthetic modes passed after restarting the parent on Pi `0.85.1`. Six boolean receipts verify literal values, explicit overrides, adapter-parent inheritance and exclusion of an outer fictional credential. Existing refusal/call/spill counts and owned cleanup are unchanged. The parent reran 121 scripts tests and nineteen focused Apple-Python tests. The original research workflow failed on missing child extension tools; its preserved public-source report is separate from the successful fresh implementation review.
