# Entity capture implementation and deployment

The implementation includes deliberate filing, questions, correction, reminder-only Things behavior, explicit old-source migration, and arrival notification. Installation and the five-source migration completed on the driver Mac. Five synthetic attribution checks passed against the configured local model. The arrival Smart Rule is configured, and permanent automatic filing is enabled on the driver Mac after explicit approval. Mac capture, ambiguity, and correction checks passed. The user waived the iOS check; it remains unverified.

## Data and recovery

`entity_capture.py` resolves source names and explicit emails against the current roster. Single names and an empty roster are supported. Contrary identifiers, competing short names, ignored identities, and filing suppression stop automatic writes. The model cannot supply independent surname or identity evidence. An explicit existing-person choice can identify an unnamed note.

A single subject receives the whole source text, including lists and mundane information. Several subjects require non-overlapping passages covering the note. The local model identifies main subjects; optional enrichment is unnecessary. Deliberate capture does not modify structured biographical fields or `LastContact`. New Person initialization sets `EntityType=Person` and an initially empty `EntityStatus=active`, without restoring cleared fields on committed records.

The source's `CaptureOperation` stores its version, UUID, text hash, original text and date, targets, frozen contributions, progress, receipts, and earlier source versions. Local state is a cache, and generic `EntityFiled` is not completion proof. Retries validate current controls without repeating inference. Invalid operations stop without overwriting evidence.

`CaptureStatus` is a derived discovery index. Nonterminal operations and unindexed older operations are retrieved separately from the newest 200 completed records. Verified filed, undone, or explicitly retained legacy sources move to `_Facts/Filed`; sources manually moved outside `_Facts` keep their location. Source UUIDs remain stable for provenance and direct detail links.

Contribution identities include a versioned source, revision, name, date, and passage hash. The original source/revision/name anchor format still decodes. Source edits and interrupted pending replacements preserve the original evidence and independently hashed edit baseline. Replacement recovery retains intervening source versions and refuses a second plan while the frozen replacement is unfinished. Editors retain the source hash from when editing began; refreshing cannot authorize overwriting a newer source. Reloading a changed note explicitly discards the draft and adopts the new baseline.

Correction writes and verifies destinations before removing unchanged original contributions. Removal compares the source text, operation, original Person body, and destination bodies. Interrupted removals retain their expected post-removal hashes. Manual contribution edits require an explicit keep-edited decision, including destinations written before an interrupted correction completed. Recovery records that conflict before replay and leaves explicitly preserved blocks unchanged. Other captures, manual paragraphs, metadata, and populated Person records are never rolled back or deleted. Receipt dates and destinations are frozen before appending, including correction receipts across midnight.

## Questions and reminders

The review app shows waiting, technical delay, questions, and recent outcomes. It supports person selection, Someone new, Decide later, editable email evidence, passage-row addition/removal, wrong-person correction, and capture-scoped undo. Saved is returned only after verified application. Keep existing filings closes a previous-filing migration question after verifying that existing Person records still reference the unchanged source. It records their body hashes, writes no new contributions or receipts, and labels the terminal outcome Existing filings kept. Source edits reopen a question after processing; the intervening view reports waiting rather than success.

`THINGS_SYNC=on` mirrors only actionable questions as unscheduled generic reminders. The `Entity question v2:` marker binds each reminder to source and question revision. Names and capture text are omitted. Completion, cancellation, trash, disappearance, and moved tasks never decide entity data. Old-revision references remain until cleanup succeeds. A source omitted from the bounded history is read directly before its reminder can close; unreadable sources do not imply resolution.

Legacy proposal/candidate task interpretation and map reconstruction are disabled. The migration closes their old tasks as housekeeping, including already-completed, canceled, trashed, or confirmed missing tasks. Database failures remain distinct from confirmed deletion.

## Unified biographical filing update

The v2 update is integrated and installed on the driver. Its private preview plans eight Person normalizations, with two unanswered semantic questions and six skipped records. Migration has not applied record writes. Answer those questions in the review app before applying the resulting frozen plan. The deployment history below describes the earlier capture release.

The configured local model rejected synthetic comparison requests with HTTP 507. Model-assisted comparison remains unverified. Failed comparisons retain possible candidates for explicit review rather than authorizing equivalence.

New captures file assertions under `## Biographical Log`, without separate Captured sections. A conservative splitter preserves every source span. It omits a leading primary name only for recognized subject-relative wording, leaving quotations, links, compound subjects, and other people's names intact. Unsupported structures retain source wording. Original sources and operation history remain unchanged.

Each assertion's hidden `bio:v2` marker binds it to the containing Person UUID, an immutable content/date baseline, and independent capture, passive, or protected legacy references. Literal ownership examples use a reversible render-only escape; their evidence stays exact. Moving an owned assertion into a blockquote removes its active ownership. Copies in another Person do not authorize writes.

Exact assertions with compatible temporal context share one visible bullet. Cross-day or suggested paraphrase matches require **Same complete assertion** confirmation. **Keep separate** retains both, and **Keep source wording** retains the submitted wording. Source observation dates do not prove event dates. The local model only suggests candidates. Novel edits and reassignments queue analysis under the worker's existing resource and inference locks; the review app reports Waiting until verified filing.

Undo removes that capture's references. Another capture or passive source keeps the assertion alive. Historical and keep-edited assertions stay protected, including when they have no remaining references. Only unchanged capture-created assertions lose their visible bullet with their last support. Corrections verify destination references before removing superseded references. Frozen v1 operations continue using their original blocks on retries.

Future passive Person facts use subject-relative wording and provenance-aware exact attachment. Passive paraphrase comparison is deferred. Passive review policy, field-transition history, Event logs, and LastContact behavior are unchanged.

### Explicit v2 migration

`entity-biographical-migrate` is separate from `entity-capture-migrate` and `--migrate-candidates`. It inventories all registered operations, including sources moved outside `_Facts`, and recognized machine Person log entries. Preview freezes ordered body and operation writes, source/control snapshots, candidate baselines, code digest, and scope. It never rewrites edited capture blocks, hand-authored lines, unverified legacy fingerprints, unknown ownership, or unfinished corrections. Those contribute to skipped/conflicted counts.

Only the operator may run the following after code review and integration into the primary checkout. Keep detailed artifacts outside Git; the CLI prints counts, a plan digest, and status only. Do not paste private plans, decisions, source bodies, or review responses into agent context.

1. Preserve unrelated primary-checkout edits and integrate the reviewed task diff without replacing those files wholesale. Run the synthetic tests. From the primary checkout only, restow the `devonthink` package with `stow --dir=stow --target="$HOME" --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' devonthink` because this update adds helpers. Restart the existing entity review server to load its Python modules. No metadata or launchd schema change is required; keep the existing activation boundary.
2. On the driver, with its existing resource gates satisfied, create the private preview:

   ```sh
   export PIPELINE_MANUAL=1
   STATE="$HOME/.local/state/devonthink"
   /usr/bin/python3 "$HOME/.local/bin/entity-biographical-migrate" --preview \
     --output "$STATE/biographical-preview.json"
   ```

3. Inspect sanitized counts. Pending semantic questions appear in the existing private review app. Review freezes a newly digested plan, preserves the original, and reports Reviewed or Waiting, never Saved. Partial review is supported. After reviewing, use the reported current plan ID and its fixed registration at `$STATE/entity-biographical-plans/<plan-id>.json`. The CLI alternative accepts an operator-created mode-600 decision file with `plan_id` and `answers`, keyed by frozen source UUID and question key:

   ```sh
   /usr/bin/python3 "$HOME/.local/bin/entity-biographical-migrate" --resolve \
     --plan "$STATE/biographical-preview.json" \
     --decisions "$STATE/biographical-decisions.json" \
     --output "$STATE/biographical-reviewed.json"
   ```

   Each choice is `separate`, `source`, or a candidate ID from that exact frozen question. Use the private UI rather than asking an agent to author decisions. Resolve performs no inference or entity writes. Superseded or changed plans are rejected.
4. Apply the exact current, fully answered registered artifact with `--apply --plan "$PLAN"`. Unanswered plans refuse application. Private plans include exact before/after evidence; retain them and `$STATE/entity-biographical-<plan-id>.json`, the recoverable journal.
5. After interruption, use `--recover --plan "$PLAN"` with the same code and exact artifact. Recovery performs no inference. The durable `entity-biographical-fence.json` pauses affected sources and People across crashes, while unrelated ingestion and read-only review remain available. Never delete the fence to force a retry.
6. To reverse application, use `--rollback --plan "$PLAN"`. Repeat that command to recover an interrupted rollback. Rollback restores frozen bodies and operations only if every affected record still matches a valid checkpoint; later edits or new support stop it. Do not reinstall older code while the fence is active.

A nonzero pending count is unfinished migration. Skipped/conflicted records stay intact and must be reported as residuals. `{"status":"stopped"}` is sanitized failure output, not permission to regenerate a plan over partial work. This procedure authorizes no publication or remote write.

## Earlier capture migration

`entity-capture-migrate` previews `_Facts` recursively, including filed sources. It reports dispositions, source UUIDs, previous filing references, candidate references, and modification stamps without printing captured text. Keep the preview private.

After installation, create a reviewed preview in a private state directory:

```sh
mkdir -p "$HOME/.local/state/devonthink"
/usr/bin/python3 "$HOME/.local/bin/entity-capture-migrate" --preview \
  --output "$HOME/.local/state/devonthink/capture-preview.json"
```

Review its dispositions and scope. Applying requires that exact reviewed artifact:

```sh
/usr/bin/python3 "$HOME/.local/bin/entity-capture-migrate" --apply \
  --plan "$HOME/.local/state/devonthink/capture-preview.json"
```

Each source/modification-stamp plan has its own `entity-capture-migration-v2-<plan-id>.json` journal. Reusing the same plan resumes it. A narrower or different plan cannot silently resume another plan's sources. Current ignored controls and People are refreshed before resolving each source. Changed sources require a new preview; frozen operations recover without inference. Previous legacy filings become questions rather than duplicate contributions. Ignored and upstream-pending evidence stays intact.

Once a capture is filed or durably questioned, migration removes only its source-specific candidate sighting. Other sightings remain, and empty candidate/review representations move to private retired groups rather than being deleted. Event proposals remain untouched. Model-unavailable captures keep their legacy representations until later processing can retain an answerable question or complete filing.

Apply honors driver, AC, normal-memory, local-model availability, and inference/process/candidate locks. It does not turn on capture automation. Legacy Things cleanup needs its configured auth token for open-task cancellation; failure does not become consent or erase entity evidence.

## Installation and activation

Run these steps only after separate approval and local integration into the primary checkout. Never run setup or Stow from the task worktree.

1. Preserve unrelated primary-checkout work before integration. Leave `CAPTURE_AUTO_AFTER` empty while installing and checking the release.
2. From the primary checkout, preview seed drift with `scripts/reconcile-devonthink-seed.sh`. Quit DEVONthink and run `scripts/seed-devonthink-config.sh` to merge missing `CaptureOperation` and `CaptureStatus` definitions without replacing existing definitions. Restart DEVONthink.
3. Install the new files through the primary checkout's normal setup/Stow workflow. Build LaunchAgent definitions with `scripts/build-launchd-plists.sh`. Rebootstrapping the entity worker is necessary for its new watch path:

   ```sh
   mkdir -p "$HOME/.local/state/devonthink/entity-capture-arrivals"
   launchctl bootout "gui/$(id -u)/com.user.entity-filing"
   launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.user.entity-filing.plist"
   ```

4. Configure a DEVONthink Smart Rule for arriving or modified Markdown records under `20_ENTITIES/_Facts`, executing `~/Library/Application Scripts/com.devon-technologies.think/Smart Rules/entity-capture-arrival.applescript`. This rule configuration requires app setup; the release supplies its script, not a fabricated opaque rule blob. Keep the scheduled 30-minute worker for missed events and sync events that do not fire the rule.
5. Agree on a bounded fictional-data check of one Mac capture, one iOS capture, one ambiguity, and one correction. Also verify local-model main-subject extraction on synthetic short, relative-containing, and split-subject prompts without changing shared model configuration.
6. Preview and explicitly apply the old-capture migration. Retain the private preview and its journal for interrupted cleanup. Existing `--migrate-candidates` handles the old calendar ledger and is not this capture migration.
7. Persist `CAPTURE_AUTO_AFTER=<approved local activation timestamp>` in machine-local `~/.config/dt-pipeline/entities.conf`. Use `yyyy-mm-ddTHH:MM:SS`; do not infer or reset it on restart. Newness uses DEVONthink's precise addition timestamp. A deliberately future boundary can limit canary routing to new fictional records before general activation.

The Drafts Script step is unchanged. Its `_Facts` UUID must already be configured. iOS confirms record creation through the callback; Mac confirms dispatch only. Neither reports verified filing before the receipt.

Arrival notifications create one private, content-free pending file. The LaunchAgent watches its directory and the worker consumes it. Notifications coalesce, preserve resource gates and locks, and do not set `PIPELINE_MANUAL`. Scheduled processing remains the recovery path. There is no additional polling service.

## Verification boundary

Tests use fictional records, temporary state, mocked APIs, and stubbed logging. Actual JXA handlers run under `osascript` with fake DEVONthink objects, including interruption during Person initialization. Python correction tests interrupt every orchestrated mutation and cover source changes, edited destinations, original-body preservation, independent source baselines, passage splitting, ignored retry controls, and receipt recovery across midnight. Reminder tests cover dismissal, omitted sources, revision replacement, missing auth, current-task retry retention during obsolete-task cleanup, and deleted legacy tasks. Migration tests cover distinct plans, source revision checks, repeated-person sources, and unreadable Things state. Retention tests cover source-bound acknowledgement, unchanged Person bodies, crash recovery, reminder cancellation, source edits before and after worker processing, and rejection of duplicate filing. Actual JXA tests verify retained-source retirement and optional body comparisons before editing or trashing records. Arrival tests cover coalescing and follower refusal. A Node-driven frontend regression exercises the page's actual refresh and Save functions, retaining the editor's original revision and rejected draft. A synthetic Chromium fixture also verified stale-save rejection, explicit reload, and a 390-pixel layout with labelled full-width controls.

The configured local model passed five synthetic checks using the production capture prompt and parser: a short single-name note, incidental-relative news, separate subjects, source spelling despite a fuller roster name, and explicit email evidence. The checks verified exact mentions, complete passages, source-grounded resolution, and email attribution under the existing driver, power, memory, and inference locks. They do not establish accuracy on the private backlog or all possible notes.

Live installation verified both metadata fields, recursive capture inventory, the review server, and launchd consumption of an arrival notification. All five historical captures already had Person contributions; migration retained them as questions rather than filing them again. Legacy reminder cleanup confirmed 81 terminal tasks and canceled 21 remaining old reminders. The five new question reminders were confirmed through the installed worker's Things state.

The migration CLI's direct terminal invocation lacked Things database access. Resuming the same reviewed plan through a one-shot LaunchAgent used the pipeline's existing stable-interpreter permission context and completed cleanup. The temporary job was removed. Database access failures remain distinct from confirmed task deletion.

The installed Mac Drafts action, arrival Smart Rule, and worker filed a complete fictional note and verified its receipt. A mononym matching two fictional People produced a question without filing. A correction through the installed review API verified the new destination, removal of the original contribution, retained source history, and the correction receipt.

These checks exposed a coalesced-notification wake-up bug. The arrival helper now refreshes the watched directory even when the single empty pending file already exists. A regression failed before the fix, and launchd consumed the existing request after the directory refresh. The fix is installed from the primary checkout.

The five historical migration questions are closed through Keep existing filings. Their Person bodies remained unchanged. Closeout moved three fictional captures and four fictional People to Trash and removed six fixture receipt lines while preserving unrelated Person and daily-note content. Read-only Things verification through a temporary LaunchAgent confirmed all six related question reminders canceled, with none open. The temporary verification and cleanup jobs were removed.

Things Cloud convergence and iOS capture remain unverified. The user waived the iOS check. The Calendar and Contacts live canaries are excluded under this task's privacy boundary. The 10-invitee calendar limit counts raw entries; it cannot detect a hidden distribution list behind one address.

The release is integrated into the primary checkout, with pre-existing edits preserved separately from the implementation commits. Installation and migration were authorized after implementation delivery. Permanent automatic filing is enabled for captures added after the approved, persisted machine-local activation timestamp. Earlier unregistered captures remain outside automatic routing; existing operations retain their recovery behavior. The temporary expiry job was removed, and unrelated configuration was preserved. The private activation receipt is `~/.local/state/devonthink/entity-capture-activation.json`. The capture release is published on `main`.
