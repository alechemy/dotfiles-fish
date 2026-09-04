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

Status: Pending. Depends on checkpoint 1.

- [ ] Distinguish content recall explicitly requested by the user from metadata-only transcript audits without weakening the global privacy rule.
- [ ] Replace unrestricted transcript-to-tool-output examples with structured, bounded, sanitized extraction. Keep `agent-reader` responsible for session-format parsing.
- [ ] Prefer metadata and explicit handoffs; authorize deeper transcript extraction only for gaps in the requested scope. Do not claim arbitrary secrets can be perfectly detected by a generic redactor.
- [ ] Make recall use `~/.context/` outside repositories, matching handoff.
- [ ] Clarify any interactive DEVONthink verification guidance that conflicts with exclusions while preserving trusted pipeline maintenance.
- [ ] Validate with fictional handoffs and synthetic normalized transcripts containing fake secrets and irrelevant workspaces. Confirm shared skills remain usable across retained clients.

Acceptance: Documented commands do not emit whole live transcripts; extracted evidence stays within the requested scope; non-repository handoffs are discoverable; excluded records remain excluded.

### 3. Establish package ownership and maintenance

Status: Pending. Can be researched during checkpoints 1 and 2.

- [ ] Add a short capability table with implementation, pinned version, source-review status, privacy constraints, update procedure, and verification coverage.
- [ ] Review the exact installed Subagents and Web Access sources relevant to execution, credentials, child-provider routing, and output retention. Record limitations rather than implying a complete security audit.
- [ ] Document the separate Homebrew, Mise, Pi-package, and downstream-overlay update paths. Use explicit reviewed version changes rather than unattended extension upgrades.
- [ ] Measure registered tool descriptions and advertised skills for the current Pi package set with synthetic/offline inspection. Record characters or bytes without equating them to measured provider tokens.
- [ ] Verify that choosing a local model is not documented as making external tools offline. Record which tools may contact cloud services.
- [x] Check public agent-reader source for an overlay replacement. The public default branch still points at the overlay's base, `09080db090f0741707652535fd8be8b8df429e4c`, and its README lacks the Pi/JSON commands. Retain the overlay; no equivalent public replacement was established. Recheck when upstream changes. Any needed Git fetch or upstream publication requires user approval.

Acceptance: A fresh setup and an existing setup can reach the same declared package versions, update ownership is explicit, and outstanding source-review or upstream dependencies remain visible.

### 4. Pilot Chrome DevTools through the MCP adapter

Status: Pending. Depends on checkpoints 1 and 3.

- [ ] Review an exact published adapter version, its package dependencies, configuration precedence, command execution, tool filtering, approvals, lifecycle, and output spill behavior.
- [ ] Review and pin an exact official Chrome DevTools server version. Compare the full MCP path with the experimental official CLI against the required browser tasks.
- [ ] Choose a supported isolated Chrome or Chrome for Testing installation. Do not attach the pilot to the everyday browser or copy authenticated profiles.
- [ ] Follow existing installation and Stow conventions. Keep app-owned adapter overrides and caches unstowed; store only portable declarations in tracked configuration.
- [ ] Disable unnecessary scripting, sampling, elicitation, and automatic host-config imports for the pilot. Configure telemetry and CrUX opt-outs and deliberate lifecycle/approval behavior.
- [ ] Test filtering and headless approval refusal with a synthetic MCP server before browser access.
- [ ] Exercise navigation, DOM interaction, screenshot, console/network inspection, and a short performance trace against a local fictional fixture. Verify process shutdown, reconnect, and output cleanup behavior.
- [ ] Record whether the pilot is accepted, deferred, or rejected. Preserve the Claude browser fallback until parity is demonstrated.

Acceptance: Pi performs the required browser tasks without a private profile, unexpected tool activation, or unreviewed version drift. The server and adapter can be disabled cleanly. Unsupported-browser behavior is not mistaken for adapter failure.

### 5. Evaluate private work MCP in Pi

Status: Pending. Depends on checkpoint 4.

- [ ] Inventory existing work definitions using sanitized structural metadata only.
- [ ] Evaluate adapter transport and authentication compatibility without printing endpoints, environment values, or credentials.
- [ ] Keep any adopted definitions and work documentation in the ignored work package. Avoid importing all legacy host configuration globally.
- [ ] Validate configuration and synthetic transport behavior first. Request a bounded real-backend validation scope before making business-data queries or mutations.
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
- The next implementation slice is checkpoint 2, recall and handoff privacy alignment.
