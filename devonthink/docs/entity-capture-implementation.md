# Entity capture implementation and deployment

The implementation includes deliberate filing, questions, correction, reminder-only Things behavior, explicit old-source migration, and arrival notification. Installation and the five-source migration completed on the driver Mac. Five synthetic attribution checks passed against the configured local model. The arrival Smart Rule is configured, and permanent automatic filing is enabled on the driver Mac after explicit approval. Mac capture, ambiguity, and correction checks passed. The user waived the iOS check; it remains unverified.

## Data and recovery

`entity_capture.py` resolves source names and explicit emails against the current roster. Single names and an empty roster are supported. Contrary identifiers, competing short names, ignored identities, and filing suppression stop automatic writes. The model cannot supply independent surname or identity evidence. An explicit existing-person choice can identify an unnamed note.

A single subject receives the whole source text, including lists and mundane information. Several subjects require non-overlapping passages covering the note. The local model identifies main subjects; optional enrichment is unnecessary. Deliberate capture does not modify structured biographical fields or `LastContact`. New Person initialization sets `EntityType=Person` and an initially empty `EntityStatus=active`, without restoring cleared fields on committed records.

The source's `CaptureOperation` stores its version, UUID, text hash, original text and date, targets, frozen contributions, progress, receipts, and earlier source versions. Local state is a cache, and generic `EntityFiled` is not completion proof. Retries validate current controls without repeating inference. Invalid operations stop without overwriting evidence.

`CaptureStatus` is a derived discovery index. Nonterminal operations and unindexed older operations are retrieved separately from the newest 200 completed records. Only verified filed or undone sources move to `_Facts/Filed`; sources manually moved outside `_Facts` keep their location. Source UUIDs remain stable for provenance and direct detail links.

Contribution identities include a versioned source, revision, name, date, and passage hash. The original source/revision/name anchor format still decodes. Source edits and interrupted pending replacements preserve the original evidence and independently hashed edit baseline. Replacement recovery retains intervening source versions and refuses a second plan while the frozen replacement is unfinished. Editors retain the source hash from when editing began; refreshing cannot authorize overwriting a newer source. Reloading a changed note explicitly discards the draft and adopts the new baseline.

Correction writes and verifies destinations before removing unchanged original contributions. Removal compares the source text, operation, original Person body, and destination bodies. Interrupted removals retain their expected post-removal hashes. Manual contribution edits require an explicit keep-edited decision, including destinations written before an interrupted correction completed. Recovery records that conflict before replay and leaves explicitly preserved blocks unchanged. Other captures, manual paragraphs, metadata, and populated Person records are never rolled back or deleted. Receipt dates and destinations are frozen before appending, including correction receipts across midnight.

## Questions and reminders

The review app shows waiting, technical delay, questions, and recent outcomes. It supports person selection, Someone new, Decide later, editable email evidence, passage-row addition/removal, wrong-person correction, and capture-scoped undo. Saved is returned only after verified application.

`THINGS_SYNC=on` mirrors only actionable questions as unscheduled generic reminders. The `Entity question v2:` marker binds each reminder to source and question revision. Names and capture text are omitted. Completion, cancellation, trash, disappearance, and moved tasks never decide entity data. Old-revision references remain until cleanup succeeds. A source omitted from the bounded history is read directly before its reminder can close; unreadable sources do not imply resolution.

Legacy proposal/candidate task interpretation and map reconstruction are disabled. The migration closes their old tasks as housekeeping, including already-completed, canceled, trashed, or confirmed missing tasks. Database failures remain distinct from confirmed deletion.

## Migration

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

Tests use fictional records, temporary state, mocked APIs, and stubbed logging. Actual JXA handlers run under `osascript` with fake DEVONthink objects, including interruption during Person initialization. Python correction tests interrupt every orchestrated mutation and cover source changes, edited destinations, original-body preservation, independent source baselines, passage splitting, ignored retry controls, and receipt recovery across midnight. Reminder tests cover dismissal, omitted sources, revision replacement, missing auth, current-task retry retention during obsolete-task cleanup, and deleted legacy tasks. Migration tests cover distinct plans, source revision checks, repeated-person sources, and unreadable Things state. Arrival tests cover coalescing and follower refusal. A Node-driven frontend regression exercises the page's actual refresh and Save functions, retaining the editor's original revision and rejected draft. A synthetic Chromium fixture also verified stale-save rejection, explicit reload, and a 390-pixel layout with labelled full-width controls.

The configured local model passed five synthetic checks using the production capture prompt and parser: a short single-name note, incidental-relative news, separate subjects, source spelling despite a fuller roster name, and explicit email evidence. The checks verified exact mentions, complete passages, source-grounded resolution, and email attribution under the existing driver, power, memory, and inference locks. They do not establish accuracy on the private backlog or all possible notes.

Live installation verified both metadata fields, recursive capture inventory, the review server, and launchd consumption of an arrival notification. All five historical captures already had Person contributions; migration retained them as questions rather than filing them again. Legacy reminder cleanup confirmed 81 terminal tasks and canceled 21 remaining old reminders. The five new question reminders were confirmed through the installed worker's Things state.

The migration CLI's direct terminal invocation lacked Things database access. Resuming the same reviewed plan through a one-shot LaunchAgent used the pipeline's existing stable-interpreter permission context and completed cleanup. The temporary job was removed. Database access failures remain distinct from confirmed task deletion.

The installed Mac Drafts action, arrival Smart Rule, and worker filed a complete fictional note and verified its receipt. A mononym matching two fictional People produced a question without filing. A correction through the installed review API verified the new destination, removal of the original contribution, retained source history, and the correction receipt.

These checks exposed a coalesced-notification wake-up bug. The arrival helper now refreshes the watched directory even when the single empty pending file already exists. A regression failed before the fix, and launchd consumed the existing request after the directory refresh. The fix is installed from the primary checkout.

Things Cloud convergence and iOS capture remain unverified. The user waived the iOS check. Fictional test-fixture cleanup remains outstanding. The Calendar and Contacts live canaries are excluded under this task's privacy boundary. The 10-invitee calendar limit counts raw entries; it cannot detect a hidden distribution list behind one address.

The release is integrated into the primary checkout, with pre-existing edits preserved separately from the implementation commits. Installation and migration were authorized after implementation delivery. Permanent automatic filing is enabled for captures added after the approved, persisted machine-local activation timestamp. Earlier unregistered captures remain outside automatic routing; existing operations retain their recovery behavior. The temporary expiry job was removed, and unrelated configuration was preserved. The private activation receipt is `~/.local/state/devonthink/entity-capture-activation.json`. Publication remains local.
