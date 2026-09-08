# DEVONthink 4.4 adoption plan

## Objective

Resolve the compatibility issues identified in the 4.4 audit, validate the changes that affect the PKM pipeline, and evaluate useful features without replacing working automation on the strength of release notes alone.

This document tracks implementation and adoption decisions. The [README](../README.md) remains the architecture reference, and the [runbook](runbook.md) remains the recovery guide.

Closeout status: Code fixes and adoption decisions are documented. The [handoff](#handoff-to-the-agent-tooling-plan) is prepared but not released. DT44-02 recovery validation is the only remaining completion gate; it needs an approved execution scope or explicit deferral. Deferred feature checks are not pending setup tasks.

## Baseline and evidence

The audit covered only the 4.4 section of `~/Downloads/Read Me : Version History.pdf`, pages 1 through 12, plus repository implementation and the installed 4.4 scripting dictionary and help. At audit time, this Mac had DEVONthink 4.4 and macOS 27.0 installed.

Established findings:

- A synthetic unclosed-frontmatter input produced two H1 headings after one pass through `sync-markdown-h1.py` and three after a second pass. Both H1 implementations treat unclosed frontmatter as extending to the end of the file, unlike 4.4's first-blank-line recovery.
- The custom CSS requests non-wrapping desktop code blocks, and its JavaScript selects a theme independently of DEVONthink's new Color Theme setting. The resulting visual behavior has not been checked.
- The existing narrow MCP client successfully negotiated its supported protocol and retrieved metadata-field definitions from 4.4. This was not a complete response-validation or privacy-canary test.
- A native database archive job exists, contrary to the README's backup section. Recent successful execution and restore usability have not been established.
- The installed help gives AI skills separate tool permissions and says they can use MCP even when it is disabled in AI → Chat. The scripting dictionary still accepts explicit chat roles despite removal of the GUI Role popup.

No production-record inspection, database migration, or feature adoption was part of that audit. The checks below distinguish source evidence, synthetic tests, and live validation.

## Authority and constraints

- This planning step authorizes the plan, not production mutations or feature adoption. Select a checkpoint and its execution scope in a subsequent iteration. Obtain explicit scope before live database tests, restores, AI calls that send content off-device, installations, or changes to application settings.
- Preserve the driver/follower split, pipeline flags, import pre-flagging, UUID identity, archive-on-success behavior, content-hash deduplication, and LF-safe record writes.
- Boox transcription and entity extraction remain local-only. Keep the shared inference lock, memory/power gates, and deterministic entity writes. New AI features must not bypass them.
- Preserve all existing Chat/MCP exclusions and redaction. Interactive agents must not bypass exclusions through AppleScript, JXA, direct database files, or another transport. Use fictional fixtures in an explicitly approved disposable database; excluded production content requires user inspection in the UI.
- Keep real third-party data, credentials, resolved configuration, runtime state, and raw private logs out of tracked evidence. Record counts and sanitized outcomes instead.
- Preserve the existing three-operation MCP interface and disabled HTTP login item. Broader access requires a separate decision. [Agent tooling plan checkpoint 6](../../docs/agent-tooling-plan.md#6-harden-devonthink-and-decide-transport-ownership) owns client hardening and transport evaluation and starts only after this plan's handoff. This plan does not depend on that checkpoint or on the tooling plan's Chrome DevTools adapter pilot. Native DT skills can be evaluated without changing external-agent access.
- Follow the [seeded configuration rules](../../docs/dotfiles-reference.md#seeded-config-copy-if-absent-not-stowed). Do not symlink app-owned settings or copy live preferences wholesale. Capture deliberate portable changes and exclude runtime bookkeeping.
- Keep Git publication, fetches, pulls, and GitHub writes behind explicit approval in the current turn.

## Tracking protocol

Each iteration works on one bounded checkpoint or named subtask. Before starting, reread its implementation, callers, tests, and subsystem instructions, then check for concurrent repository changes.

Use these states:

- **Ready** means the next local task is defined and has no unresolved prerequisite.
- **Pending** means work remains, possibly including approved live validation.
- **In progress** means an iteration is actively working on it.
- **Blocked** requires a named dependency or user decision and a condition for resuming.
- **Complete** requires the stated acceptance evidence.
- **Deferred** or **Rejected** records a deliberate adoption decision, its reason, and when to reconsider it.

Check individual subtasks as they finish. Keep implementation and live validation separate; an offline test does not complete a GUI or database check. Evaluations can finish with a decision to retain the existing implementation.

At the end of each iteration, update the checkpoint status and append a short progress entry with the scope, evidence, decision, remaining limitation, and next action. Record relevant test/file/commit references when available, without raw logs. Before committing, run `git diff --check` and the staged betterleaks scan. Pipeline changes require the documented DEVONthink test suite; launchd or interpreter changes also require `scripts/lint-launchd-plists.sh`.

## Work queue

| ID | Checkpoint | Status | Dependency or approval boundary |
| --- | --- | --- | --- |
| DT44-01 | Align frontmatter handling in both H1 helpers. | Complete. | Offline parser/injection regressions and full pipeline suite verified. |
| DT44-02 | Correct recovery documentation and verify archive recovery. | Documentation and ZIP integrity complete; restore blocked. | Agree on an isolated restore environment and approve its scope. |
| DT44-03 | Validate Markdown rendering and custom themes. | Preprocessing complete; GUI evaluation deferred by user. | Retain current styling; revisit for a rendering problem or a proposed style replacement. |
| DT44-04 | Validate PDF routing, metadata searches, and relevant vendor fixes. | Offline review complete; native checks deferred by user. | Retain existing behavior; revisit a reproduced routing/search/reliability problem. |
| DT44-05 | Compare native web extraction with defuddle. | Complete at API-review scope; retain defuddle. | Revisit for a demonstrated extraction problem or a documented pre-import Markdown API. |
| DT44-06 | Audit native AI skills and decide whether to pilot one. | Complete at audit/retain scope; no new skill. | Revisit a concrete reference-synthesis need before proposing a trial. |
| DT44-07 | Evaluate attribute-date diagnostics. | Deferred. | Revisit after DT44-04 if a concrete diagnostic question remains. |
| DT44-08 | Evaluate page-aware PDF research and annotations. | Deferred. | Choose a real research need and approved fixtures first. |
| DT44-09 | Evaluate linked-note PDF/LaTeX export. | Deferred. | Choose an export need and approve any TeX installation. |
| DT44-10 | Check DT-backed AI output quality and cost. | Deferred. | Select a provider and bounded synthetic/public examples first. |

The suggested sequence is DT44-01, the documentation portion of DT44-02, DT44-03, and DT44-04. Then decide whether DT44-05 and DT44-06 merit adoption. Deferred items are optional and do not block the core compatibility work.

## Core checkpoints

### DT44-01. Align frontmatter handling in both H1 helpers

Status: Complete.

The relevant files are `stow/devonthink/.local/bin/sync-markdown-h1.py`, `stow/devonthink/Library/Application Scripts/com.devon-technologies.think/Smart Rules/sync-h1-and-filename.applescript`, their callers, and `devonthink/tests/test_sync_markdown_h1.py`. The release-note change is on page 9.

- [x] Retain the audit reproduction as a failing regression with an unclosed opening delimiter, a blank line, and an existing H1.
- [x] Define the recovery policy against 4.4 documentation. Cover valid frontmatter, unclosed frontmatter with and without a blank line, and blank lines inside properly closed frontmatter.
- [x] Apply the same policy to both implementations without changing unrelated heading/name behavior. Share fixture expectations where practical rather than introducing an abstraction solely to reduce duplication.
- [x] Verify repeated-run idempotence, fenced code, empty input, body preservation, and CR/LF/CRLF handling. Exercise the AppleScript parser independently of production record writes.
- [x] Check whether the live/seeded rule invokes the external implementation or contains an embedded copy. Capture a changed seed only if necessary.
- [x] Run focused regressions and `/usr/bin/python3 -m unittest discover -s devonthink/tests -t devonthink/tests`. If a disposable GUI canary is needed, record it separately from offline completion.

Evidence: `devonthink/tests/test_sync_markdown_h1.py` retains ten shared frontmatter fixtures across LF, CR, and CRLF, plus focused replacement/injection and empty-input checks. The corrected test harness reproduced 64 failing assertions before the implementation changes. All 14 focused tests and the full 1,047-test pipeline suite pass. The full AppleScript compiles with `osacompile`; its real parsing/injection handlers are exercised with a stub record writer, not a production database.

Policy: A closing delimiter takes precedence over blank lines inside valid frontmatter. Without a closing delimiter, the first empty or whitespace-only line ends the block. Without either, frontmatter extends to EOF; inserting a missing heading introduces a blank separator so later passes find that heading. AppleScript now normalizes CRLF without doubling line breaks and safely trims whitespace-only lines. Python retains its empty-input no-op, while AppleScript retains its empty-record heading creation.

Bindings: Sanitized structural inspection confirmed that both seeded and live save rules reference `sync-h1-and-filename.applescript` externally. Both installed helpers resolve through Stow to the changed source files. No seed refresh is required. GUI rule firing and repair of previously affected production records remain outside this parser-fix validation.

Acceptance: Both helpers find the same intended heading under the agreed recovery policy. Repeated execution adds no headings and preserves content and existing valid-input behavior. Required regression cases are retained.

Recovery: Keep changes confined to the parsers and their bindings. Any later production repair of already duplicated headings is a separate task, not an automatic migration.

### DT44-02. Correct recovery documentation and verify archive recovery

Status: Documentation and approved archive integrity check complete. Blocked on an agreed isolated restore environment and scope.

Review `stow/devonthink/.local/bin/dt-database-archive.sh`, its launchd template, setup wiring, the README backup section, and the runbook's restore procedure. The version-retention fix is on page 10; APFS image changes are on page 5.

- [x] Correct the README to describe the actual native archive job, seven-day interval, four-archive retention, verification/ZIP checks, and driver/power constraints. Distinguish CloudKit sync, Time Machine, native archives, and per-document versions.
- [x] Reconcile the runbook with that implementation. Document how to verify a recent success without treating a loaded agent or success marker alone as proof of recovery.
- [x] Under approved scope, inspect sanitized archive metadata and validate a recent archive's integrity. Record archive age and outcome without dumping logs or database content.
- [ ] Agree on an isolated restore destination and procedure that cannot join production sync or trigger production automation. Validate the procedure before opening the restored database.
- [ ] Perform the approved restore check, including record identity, internal links, and representative attachment readability. Report sensitive-content checks through user UI confirmation where required, then clean up only the agreed test copy.
- [ ] Confirm current separate/off-device backup coverage of the database and archives.
- [ ] Record whether encrypted/revision-proof database changes are relevant. If they are, verify follower OS compatibility before proposing any format change.

Evidence: The README and runbook now match the archive script, launchd template, setup role wiring, and shared power gate. The installed 4.4 backup and database-repair help confirms the distinction between sync, internal metadata backups, and full archives, and places verification under File → Verify & Repair Database. The runbook separates metadata/ZIP checks, an isolated offline restore drill, and promotion to production. It documents same-day archive filename reuse and the additive behavior of entity/GitHub cache rebuilds. No live archive, backup coverage, encryption settings, or restore was inspected during this documentation step.

Archive evidence: On 2026-09-05 UTC, approved read-only inspection found four dated archives. The newest was dated 2026-08-29, measured 414,493,785 bytes, and was approximately 6.63 days old by modification time. `/usr/bin/unzip -tq` returned zero with all member output suppressed. The file's identity, size, and modification time were unchanged after the check. Nothing was extracted or opened in DEVONthink. Older archives were inventoried but not integrity-tested. Separate backup coverage, encryption settings, and restore usability remain unverified.

Acceptance: Documentation matches the code, a recent archive's integrity is known, and restore usability has explicit evidence. Keep this checkpoint open or blocked until the remaining restore evidence is recorded.

Recovery: Never overwrite or register the restored copy as the production database. No APFS conversion is planned merely because 4.4 supports it.

### DT44-03. Validate Markdown rendering and custom themes

Status: Preprocessing complete. The user approved deferring the broader GUI evaluation because its setup is disproportionate while retaining the current styling. Native rendering, editing round-trips, and DTTG compatibility remain untested, not passed. Revisit if normal use reveals a rendering problem or a custom-style replacement is proposed.

Review `Readable-Universal.css`, `theme-toggle.js`, note templates, the Markdown lint helpers, and the README's GUI-state checklist. Relevant changes appear on pages 2, 4, 9, and 11.

- [x] Build a fictional fixture covering nested lists, multiline tasks, tables, callouts, inline/fenced code, long code lines, MathJax, footnotes, links, and the extended syntax the custom CSS anticipates.
- [x] Correct tilde preprocessing so it preserves code spans, fenced and indented code, and literal link destinations. Retain prose escaping pending native-renderer evidence.
- [x] Retain the current CSS, theme-toggle JavaScript, and prose escaping. Apply only the demonstrated preprocessing fix.
- Deferred by user: Native/custom light/dark, narrow-pane, and Side-by-Side comparisons.
- Deferred by user: WYSIWYG editing and source round-trips, including lists and tasks consumed by pipeline parsers.
- Deferred by user: Native raw-tilde evaluation and DTTG checks. These are prerequisites for removing the corresponding shared behavior, not for retaining it.

Evidence: [`markdown-rendering.md`](../tests/fixtures/markdown-rendering.md) and its local SVG provide fictional inputs. The [rendering checklist](markdown-rendering-checklist.md) defines native/custom light/dark comparisons, editing probes, source preservation checks, and separate DTTG validation. Static checks confirmed LF input, required sections, a local asset, and no SVG scripts or external resource references. The tracked fixture was not modified by the offline preprocessing test.

Resolved preprocessing finding: `lint-markdown-file` now loads the stowed `~/.config/dt-pipeline/markdownlint-rules.cjs` custom rule. It uses markdownlint's existing micromark parser instead of global substitution. Code spans, fenced and indented code, link destinations/reference identifiers, frontmatter, and HTML blocks retain their literal tildes. Single prose tildes are still escaped; pairs and existing escapes are preserved. The helper tolerates unfixable lint violations but returns nonzero for missing rules or formatter execution failures.

Regression evidence: The 11 focused tests produced 29 failing assertions against the original helper and pass with the correction. They cover nested/unclosed fences, multiline code spans, escaped backticks/backslashes, Unicode offsets, reference links, placeholder-like text, line endings, repeat-run stability, and the full rendering fixture. The complete 1,058-test suite passes. The new rule was linked with Stow. Native GUI behavior remains untested; the prose workaround and shared CSS/JS remain in place.

Accepted scope: The preprocessing correction has source-preservation regressions, and the current rendering configuration is retained. The user's deferral resolves the remaining evaluation scope without claiming visual compatibility. MultiMarkdown removal does not trigger an untested template rewrite.

Recovery: Record the prior non-secret theme/style selections and preserve the existing CSS/JS until the replacement is accepted. Reverting rendering must not rewrite note bodies.

### DT44-04. Validate routing, searches, and relevant vendor fixes

Status: Offline review complete. The user approved retaining the current routing, query construction, and content-hash behavior and deferring native PDF/search/vendor-fix checks. Those outcomes remain unverified. Revisit a reproduced problem rather than requiring a blanket database exercise.

Review the seeded extraction rules, `entity-dt-bridge.js`, Boox identity lookup, entity-filing change detection, and their tests. Relevant release notes are on pages 2, 6, and 9 through 11.

- [x] Review routing documentation, seeded rule presence/enabled state, bridge queries, Boox identity/pre-flagging, and entity-filing change detection. Add focused coverage only for gaps in the existing offline tests.
- [x] Test the real bridge handler's exact post-filter with mocked candidates for multiword/punctuation-bearing `SourceFile` values, near matches, and `DocumentType`. Check repeated Boox updates preserve UUID and local-processing flags.
- [x] Retain PDF routing and query syntax. No defect requiring a change was demonstrated by this source review. Keep exact metadata post-filtering, content hashes, and concurrent-edit guards.
- Deferred by user: Native `has text layer`, word-count, and extraction comparisons across image-only, born-digital, empty-text, mixed-page PDFs, and non-PDF inputs.
- Deferred by user: Native metadata retrieval completeness, exact-phrase/repeated-word searches, NOT behavior, and Mentions. Mocked candidates do not establish server retrieval behavior.
- Deferred by user: Version rotation, macOS 27 PDF annotation retention, and idle CPU checks.

If revisited, obtain a bounded database-test scope outside production automation, sync, and AI processing. Any justified routing change must update both branches together, preserve `OCR - Apply` and local-only Boox pre-flagging, and add regressions before capturing seeded rules.

Offline evidence: The README retains the complementary word-count routes and `OCR - Apply`; both corresponding rules are present and enabled in the tracked seed. The seed's opaque criteria/action payload was not decoded, and no live rule firing was tested. The new `has text layer` property does not by itself justify replacing a route that also handles non-PDF inputs.

The 4.4 release notes add custom-metadata support for the `:` matches operator with quoted or unquoted text. They do not require replacing the bridge's existing unquoted `==` lookup. `FindByFieldExactFilter` in `test_bridge_hardening.py` executes the real handler with mocked search results and confirms case-sensitive exact filtering, missing-field rejection, and database-root scoping. Operator-like fixture values test filtering only, not native query parsing or retrieval completeness. The new Boox test confirms repeat updates use the same UUID and retain all local-processing flags. Existing entity-filing tests cover modified-source selection and the content-hash short circuit. The full 1,062-test suite passes.

Decision: Keep the implementation. Exact-phrase/NOT/Mentions retrieval, mixed-page PDFs, annotation retention, version rotation, and idle CPU remain vendor release-note claims rather than locally verified outcomes. A reproduced problem can justify focused native validation; no blanket GUI exercise is requested by this offline review.

Accepted scope: Offline checks support retaining the implementation. The user's deferral resolves the remaining native evaluation scope without claiming vendor behavior was verified. Preserve that distinction in the handoff.

Recovery: Remove only test records in the approved database. Any production-rule change requires DT44-02 recovery evidence and a backup of the prior portable rule configuration.

### DT44-05. Compare native web extraction with defuddle

Status: Complete at API-review scope. Retain SingleFile and defuddle. Native output quality, speed, network behavior, and Reader view have not been benchmarked or trialed.

- [x] Review the current official documentation index, installed 4.4 scripting dictionary/help, defuddle's official CLI documentation, `run_defuddle`, the import pass, and both capture callers.
- [x] Compare the documented inputs, outputs, and database effects against the pre-import extraction boundary.
- [x] Record a retain decision and the conditions for reconsidering it. A native conversion pilot is not required for this decision.

### API comparison

| API | Documented behavior | Fit with the existing import flow |
| --- | --- | --- |
| Defuddle `parse <local-file> --markdown --output <file>` | Reads saved HTML and writes a Markdown file. | Matches `run_defuddle` and leaves validation, image-data stripping, optional chat rewriting, and linting before import. |
| DEVONthink `create Markdown from` | Takes a URL, optional readability/agent/referrer/destination/name parameters, and returns a new record. It has no HTML `source` parameter in the 4.4 dictionary. | The documented web-resource path downloads and creates a record. Local-file URL support and its network behavior were not tested; it is not a documented raw-HTML-to-Markdown-text replacement. |
| DEVONthink `convert record ... to markdown` | Converts an existing record and creates another record, optionally in a destination group. | Can operate on an imported HTML snapshot, but requires database records before the existing file-based preprocessing finishes. Adopting it would require redesigning and validating that sequence. |
| DEVONthink HTML text helpers | `get text of` returns plain text; `get rich text of` returns rich text; `get metadata of` returns metadata. `create formatted note from` accepts HTML `source` but creates a record. | These do not document an HTML-source-in/Markdown-text-out operation. |

The installed dictionary documents native conversion, but no standalone HTML-source-to-Markdown-text command matching the current boundary. This is a statement about the reviewed public API, not proof that the app has no internal converter or that native extraction is inferior.

### Retained behavior and limits

`run_defuddle` uses a temporary HTML copy, a 60-second timeout, and a minimum-content check of 20 words after discounting images, links, and URLs. The caller keeps the HTML snapshot when extraction fails and sends that snapshot through enrichment instead. Markdown preprocessing finishes before the single import pass establishes bookmark/snapshot/extract links and processing flags. The batch caller moves captures out of the watched folder and passes the existing bookmark UUID; retry adoption and capture timestamps remain part of the import contract.

The temporary copy also keeps the Node-based converter away from TCC-protected Downloads. Replacing the extractor must preserve that operational constraint, compression/size limits, snapshot retention, bookmark adoption, cross-links, pre-flagging, failure/retry behavior, and AI-chat provenance. One AppleScript pass is not a database rollback transaction; its partial-import recovery still matters.

Reader view is a separate manual convenience. Installed help describes loading a decluttered view and capturing it through Tools → Capture. That is not evidence of parity with Chromium's saved SingleFile snapshot, including authenticated or dynamically loaded content. No Reader or authenticated-clipping trial is requested by this review.

Evidence sources: The [official handbook index](https://www.devontechnologies.com/support/download/extras) lists DEVONthink 4.4. The detailed API evidence comes from `/Applications/DEVONthink.app/Contents/Resources/DEVONthink.sdef`, including the `convert type` enumeration, and its installed `DEVONthink.help` page `pgs/documents-html.html`. Defuddle's [official README](https://github.com/kepano/defuddle#readme) documents the local-file CLI path. The hosted handbook PDF exceeded the fetch tool's size limit, so its contents were not used as evidence.

Acceptance: The documented API comparison supports retaining the existing converter without an output-quality claim. Revisit for a demonstrated extraction failure or a documented API that fits pre-import processing. Any replacement proposal needs an approved public/synthetic corpus and evidence for fidelity, completeness, failure behavior, and the full import contract before adoption.

Recovery: Keep the existing converter path available until replacement parity is demonstrated. Never delete the only captured source while testing extraction.

### DT44-06. Pilot one read-only AI skill and define ownership

Status: Complete at audit/retain scope. The user approved retaining the current pipeline without adding a native skill. A selected-reference comparison remains a possible future convenience, but a reusable prompt may be sufficient. Revisit only for a concrete need; no pilot is pending.

Scope: Review of DEVONthink 4.4's documented feature and permissions, the app-shipped `Validate Wiki.js` example, and the repository's existing automation. This was not an inventory of the user's live AI Library or a test of its current settings. No skill was installed, invoked, or exported.

- [x] Review native prompts, roles, skills, tool permissions, automatic use, scripting, and export/sync behavior.
- [x] Compare candidate uses with existing pipeline ownership and the external read-only helper.
- [x] Define the smallest useful candidate and the conditions for an optional trial.
- [x] Record the user's retain decision: add no native skill. A future trial would separately require permission to change settings and send the chosen inputs to the chosen model.

### Where skills would help

| Candidate | Fit with this repository | Recommendation |
| --- | --- | --- |
| Compare selected reference documents and identify disagreements or missing evidence. | Adds content synthesis inside DEVONthink. The external shared `devonthink` skill intentionally exposes metadata/search, not document bodies. | Best candidate, but start with a reusable prompt over supplied text. A multi-step skill needs a concrete retrieval task that a prompt cannot handle conveniently. |
| Generate summaries, tags, titles, or dates during ingestion. | Duplicates the existing enrichment call and its input hash, JSON parsing, flags, and deterministic writes. That call explicitly disables tools. | Retain the scripted pipeline. |
| Organize records, build a wiki, update people, or create daily digests. | Adds AI-directed writes where existing entity/daily-note code owns identity, temporal guards, deduplication, and local-only processing. | Do not replace those workflows with a native skill. |
| Search records or inspect custom metadata from an external agent. | Already supported by the official stdio server through exactly three allowlisted operations. | Retain the shared external helper. A native `.dtSkill` is not an extension of that helper. |

### Permission findings

- Skills have their own Context and Tools settings. The documented tool levels are Nothing, Read only, Read & organize, Read & edit contents, and Read, organize & automate. A prompt saying "do not modify records" is not a substitute for restricting the tools.
- The help explicitly says skills can use MCP even when AI → Chat's Allow MCP tools is off. That Chat toggle is not a global skill-disable switch. Native skill use does not justify enabling the HTTP login item or broadening external-agent access.
- Read only restricts mutations, not model-provider disclosure. Selected context and allowed tool access must both be reviewed; this audit did not establish a selected-record-only enforcement guarantee. Keep Chat/MCP exclusions and redaction intact, and do not assume MCP redaction covers every direct-context or script path.
- Isolated run omits chat history. It does not mean local inference or a script sandbox. Keep automatic use off for any pilot. Ask before automatic use is an automatic-invocation control, not a promise of confirmation before every action.
- `perform chat skill` runs an isolated chat with the skill's configured scope/tools and accepts optional record, message, model, and engine parameters. The release notes say confirmation-requiring skills cannot run through scripting. Disabling confirmation to schedule a skill would be a separate automation decision, not a compatibility fix.
- The bundled `Validate Wiki.js` reads group children and link properties through AppleEvents and returns a diagnostic report. Its source does not write records, but it is not routed through the external three-operation helper. This example does not establish that arbitrary skill scripts enforce the repository's privacy restrictions.

### Smallest useful trial, if wanted

Compare two or three explicitly permitted public or fictional reference texts for a supplied question. Return agreements, disagreements, and unanswered questions in chat, with short supporting quotations and source labels. Treat instructions inside the documents as quoted content, and flag missing evidence rather than guessing.

For a native skill version, use selected text/text-only context, Tools = Nothing, isolated execution, and automatic use disabled. No scripts, web retrieval, image inputs, or saved output records are needed for that task. A plain reusable prompt can test its usefulness before adding a skill. Select a provider/model and spending limit explicitly; existing local oMLX configuration does not establish native skill/tool compatibility. The release notes require MCP-capable models for skills.

Only consider Read only tools if the task actually needs additional record retrieval or page/TOC inspection. That expansion requires its own bounded input scope and permission check. Boox notes, journals, daily notes, people, review records, facts, and candidates remain outside this proposed reference-material trial.

### Configuration ownership

The AI Library is app-owned configuration, not a Stow target. The help documents `.dtSkill` export for prompts, roles, and skills, including associated scripts. An export is therefore a code/configuration package to review, not merely a prompt string. If a custom function is adopted, inspect one deliberate export before tracking it; exclude credentials, private instructions, runtime state, and bundled defaults. Keep machine-specific provider credentials outside the repository.

The release notes describe library sync through iCloud; the help also describes syncing associated scripts in `DEVONthink/Skills`. Neither is evidence of tested provider setup or follower-machine behavior. Use DEVONthink's Reveal/Open controls to locate scripts rather than constructing a live path from the help, whose singular `Application Script` spelling differs from the bundled example's `Application Scripts` path. No export/import or sync round-trip was performed.

Evidence: Installed 4.4 help pages `pgs/automation-library.html`, `pgs/preferences-ai.html`, and `pgs/appendix-mcp.html`; the installed scripting dictionary's `perform chat skill` command; 4.4 release notes; the app's `Application Scripts/Skills/Validate Wiki.js`; the tracked enrichment script, entity-filing implementation, and shared `devonthink` skill/client. These establish documented capabilities and source-level behavior, not live permission enforcement or output quality.

Accepted scope: The audit identifies useful scope, overlap, permission risks, and ownership; the user approved retaining the current approach without a pilot. Any later pilot must demonstrate usefulness and bounded permissions before becoming a dependency. External-client hardening remains in the agent tooling plan.

Recovery: Disable/remove only an explicitly approved pilot and its artifacts if one is later created. The production pipeline must not depend on it.

## Optional follow-ups

### DT44-07. Attribute-date diagnostics

Status: Deferred until DT44-04 identifies a useful question.

- [ ] Define a concrete diagnostic view, such as recently changed processing flags.
- [ ] Characterize attribute-date behavior for body, comment, metadata, rename, and sync changes with fixtures.
- [ ] Add and capture a smart group only if it provides information the existing views do not.

Acceptance: The view answers the named question without changing entity filing's timestamp/hash logic. Record a retain-or-reject decision if no new view is needed.

### DT44-08. Page-aware PDF research and marking

Status: Deferred pending a chosen research use case.

- [ ] Trial table-of-contents retrieval and selected-page extraction on an approved long public PDF. Verify that restricted pages are the actual input, rather than relying on prompt instructions alone.
- [ ] Assess citation accuracy and whether relevant middle sections improve on the current head/tail enrichment window.
- [ ] Evaluate highlighting supporting passages on a disposable copy, with explicit write approval.

Acceptance: A useful on-demand workflow has bounded input, reliable citations, and known cost. Automatic enrichment changes, MCP expansion, and production annotations remain separate decisions.

### DT44-09. Linked-note export

Status: Deferred pending an export need.

- [ ] Build a fictional linked-note packet with metadata, WikiLinks, transclusions, and images. Inspect LaTeX output before requesting a TeX installation.
- [ ] If PDF generation is wanted, approve the dependency and compare output with the existing CSS-based print workflow.
- [ ] Document link portability, merged-document ordering, image handling, and any DTTG limitations.

Acceptance: The packet works for its intended recipient. Do not assume exported links remain useful outside DEVONthink or that LaTeX reproduces the custom stylesheet.

### DT44-10. DT-backed AI quality and cost

Status: Deferred pending a selected provider and test budget.

- [ ] Test enrichment and AI-conversation rewriting with bounded synthetic/public inputs under the selected 4.4 model.
- [ ] Check JSON response handling, explicit roles, redaction effects, title/date quality, truncation, and no-tool behavior.
- [ ] Measure reported usage and caching where available. Retain the input-hash cache and content cap unless a separate comparison justifies changes.

Acceptance: The selected model produces usable outputs within the agreed budget. Findings apply to DT-backed calls, not direct oMLX Boox/entity extraction. Provider migrations are explicit decisions rather than automatic responses to the model catalog.

## Handoff to the agent tooling plan

Status: Prepared, not released. Closeout documentation and adoption decisions are complete. DT44-02 recovery validation is the sole remaining gate for [agent tooling checkpoint 6](../../docs/agent-tooling-plan.md#6-harden-devonthink-and-decide-transport-ownership). Tooling checkpoints 4 and 5 remain independent.

- [x] Consolidate checkpoint outcomes, changed files, evidence, and unverified behavior.
- [x] Record retained configuration, known version evidence, and ownership boundaries.
- [x] Distinguish offline fixtures from live checks and identify the absence of privacy-canary evidence.
- [x] Record the external-client baseline and the work owned by tooling checkpoint 6.
- [ ] Resolve the remaining DT44-02 scope through approved execution or explicit deferral, then release this handoff and update the tooling dependency. Closeout approval alone does not resolve that choice.

### Closeout summary

| Checkpoint | Disposition | Evidence or limitation |
| --- | --- | --- |
| DT44-01 | Complete. | Both H1 parsers have retained regression coverage, including real AppleScript handlers with a stub writer. Existing production records were not repaired. |
| DT44-02 | Open recovery task. | Backup documentation corrected; the 2026-08-29 archive passed a read-only ZIP check on 2026-09-05. Restore usability, current separate/off-device backup coverage, and encryption/revision-proof relevance remain unverified. |
| DT44-03 | Preprocessing complete; GUI checks deferred by user. | Keep current CSS/JS and prose escaping. Source-preservation checks passed; native rendering, WYSIWYG, and DTTG comparisons were not performed. |
| DT44-04 | Offline review complete; native checks deferred by user. | Keep routing and queries. Mocked search candidates exercise exact filtering, not native retrieval completeness. Vendor PDF/search/reliability fixes remain unverified locally. |
| DT44-05 | Complete at API-review scope; retain SingleFile/defuddle. | Documented native conversion creates records rather than matching the pre-import file boundary. No native fidelity or performance comparison was performed. |
| DT44-06 | Complete at audit/retain scope; add no native skill. | Permission and ownership review only. No live library inventory, skill run, export round-trip, or enforcement test was performed. |
| DT44-07 through DT44-10 | Deferred. | Attribute-date diagnostics, page-aware research, linked-note export, and AI quality/cost evaluation need a concrete use case before reconsideration. |

### Changed files and reusable evidence

- H1 fixes: [Python helper](../../stow/devonthink/.local/bin/sync-markdown-h1.py), [AppleScript helper](../../stow/devonthink/Library/Application%20Scripts/com.devon-technologies.think/Smart%20Rules/sync-h1-and-filename.applescript), and [shared regression cases](../tests/test_sync_markdown_h1.py).
- Tilde preprocessing: [lint helper](../../stow/devonthink/.local/bin/lint-markdown-file), [parser-backed rule](../../stow/devonthink/.config/dt-pipeline/markdownlint-rules.cjs), and [regressions](../tests/test_lint_markdown_file.py). The new rule is linked through Stow. The [Markdown fixture](../tests/fixtures/markdown-rendering.md), [local image](../tests/fixtures/markdown-rendering.svg), and [deferred visual checklist](markdown-rendering-checklist.md) remain available for a future reported problem.
- Identity/lookup coverage: [bridge tests](../tests/test_bridge_hardening.py) and [Boox tests](../tests/test_boox_process.py). These use fictional inputs and mocked database I/O; neither lookup nor Boox runtime behavior was changed in that review.
- Operational documentation: [README](../README.md), [database restore runbook](runbook.md#database-restore), this plan, and the linked tooling checkpoint. Archive verification code was reviewed, not modified.

The last full pipeline validation ran `PIPELINE_MANUAL=1 /usr/bin/python3 -m unittest discover -s devonthink/tests -t devonthink/tests` and passed 1,062 tests. Earlier focused runs and compilation evidence remain in the checkpoint sections. Closeout edits are documentation-only. These references describe the local DEVONthink 4.4 work, not a published release; recheck affected evidence after subsequent code changes.

### Retained configuration and ownership

- Version evidence is DEVONthink 4.4 on macOS 27.0, with the official server bundled with that app. Protocol negotiation and metadata-field discovery succeeded during the audit. A separate server-reported implementation version was not retained in this plan; record it with the current app version when tooling checkpoint 6 begins.
- Keep word-count routing, `OCR - Apply`, local-only Boox/entity extraction, import pre-flagging, deterministic record writes, content hashes, and existing CSS/JS. Keep SingleFile/defuddle and the current scripted enrichment. This plan adopted no native AI skill and did not inventory the user's live library.
- Portable scripts and style files belong to Stow; app-owned selections, credentials, database exclusions, and runtime state do not. Any future custom `.dtSkill` export needs inspection before tracking because it may bundle scripts. The native library is not a replacement for the shared external `devonthink` skill.
- The retained external interface is the [shared helper](../../stow/agents/.agents/skills/devonthink/scripts/devonthink_read.py) over official stdio, with metadata-field discovery, UUID/name record search, and custom-metadata reads only. The [setup script](../../scripts/setup.sh) disables the unused HTTP login item. Preserve exclusions and redaction. Live launchd/privacy settings were not rechecked during closeout; this records the retained baseline, not a fresh enforcement result.
- No DEVONthink privacy canary was completed under this plan. Earlier handshake/field-discovery evidence and the new mocked query cases are not privacy tests. Current server schemas, response bounds, exclusions, and redaction need the separate tooling work and any required private-data approval.

Tooling checkpoint 6 owns output projection/count enforcement, bounded transport frames, safe errors, protocol/schema validation, and the adapter comparison. It must keep unapproved tools unavailable and preserve cross-client use. Its adapter decision also uses checkpoint 4's outcome. This handoff does not claim any of that hardening is implemented.

### Remaining recovery decision

Choose one disposition for the remaining DT44-02 work:

1. Approve an isolated restore scope using the [runbook](runbook.md#isolate-a-restore-drill-before-opening-the-copy), including the destination, sync/automation isolation, permitted inspection, and cleanup. Confirm separate/off-device backup coverage and whether encrypted/revision-proof database changes matter. Record the results and any remaining limitations.
2. Explicitly defer the remaining DT44-02 validation, retain the unverified recovery/coverage items as a named follow-up with a revisit condition, and approve releasing the handoff with that limitation.

Until that decision is recorded, the restore remains open and tooling checkpoint 6 remains blocked. The archive's CRC result is not evidence that it can be restored successfully.

Acceptance: Release the handoff only after the remaining recovery disposition is explicit. Deferred native feature checks do not require further setup. The tooling work starts with the retained configuration and evidence above, not with a claim that all vendor behavior or privacy enforcement has been tested.

## Progress record

- The 4.4 audit established the baseline above. The user requested a formal plan for iterative work. This document records six core checkpoints and four optional evaluations; implementation and adoption remain pending.
- DT44-01 is complete. Retained fixtures reproduced frontmatter duplication, AppleScript CRLF splitting, and whitespace-trimming failures before the fixes. Validation covers real pure handlers and Python CLI behavior without production record writes; rule-binding inspection confirmed external source references.
- DT44-02's documentation portion is complete. The README describes the implemented archive job; the runbook now requires restore isolation and distinguishes cache reconciliation from additive rebuild commands. Separate backup coverage and restore usability remain unverified.
- The user approved read-only archive metadata and ZIP integrity checks. Four archives were found; the newest, dated 2026-08-29, passed ZIP integrity verification on 2026-09-05 UTC without extraction or database access.
- The next DT44-02 action is to agree on an isolated restore environment and approve its scope.
- DT44-03 fixture preparation is complete. A fictional Markdown note, local SVG, and comparison checklist are ready.
- DT44-03 preprocessing now uses a parser-backed tilde rule. Regression checks confirm literal code/fence preservation and retained prose escaping.
- The user approved proceeding without the broader DT44-03 GUI exercise. Retain existing styling and record native rendering, editing, and DTTG checks as deferred, not passed. Revisit for an observed problem or a proposed replacement. Continue with DT44-04 offline review; this decision does not authorize database tests or defer the separate restore check.
- DT44-04 offline review supports retaining current routing and query construction. Four added tests cover exact metadata filtering and repeated Boox update identity/pre-flagging; the full suite passes. Native retrieval and vendor reliability checks remain unverified. The next local task is DT44-05's conversion-API review, not database setup.
- DT44-05 is complete at API-review scope. Official documentation confirms native Markdown conversion, but the reviewed commands produce database records rather than a pre-import Markdown file. Retain SingleFile and defuddle; no native quality/performance comparison or feature adoption was claimed. DT44-06's native-skill decision is next, with no AI call or configuration change authorized by this review.
- DT44-06's documentation/source audit is complete. Native skills have independent tool permissions and can use MCP despite the Chat toggle being off. Existing ingestion/entity/daily workflows should retain their scripted ownership. Selected-reference comparison is the strongest optional use, initially as a reusable prompt without tools. Recommend no native skill adoption yet; the user's disposition remains pending. Live settings, model output, exports, sync, and permission enforcement were not tested.
- The user set the sequence: finish this plan before starting the DEVONthink phase of the agent tooling plan. The handoff above is that dependency; the Chrome DevTools and private work MCP evaluations remain independent.
- The user approved closeout documentation, retaining the current pipeline without a native skill, and deferring the remaining native PDF/search/vendor-fix checks. The handoff now consolidates files, evidence, configuration ownership, and explicit unverified behavior. DT44-02 recovery validation remains open; closeout approval did not authorize a restore or select its deferral. Tooling checkpoint 6 stays blocked only on that disposition.
- The session closes with the completed work packaged locally. Recovery validation and external MCP hardening remain documented follow-ups for a future session.
