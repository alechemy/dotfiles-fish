# Entity capture implementation receipt

This branch is an implementation in progress. Capture automation is not ready
for activation. The default capture and review workflow remains unchanged.

## Implemented

- A separate deliberate-capture resolver supports new and existing single-name
  People, aliases, accent/case normalization, and an empty roster. It resolves
  identifiers from the original text, not model-expanded names. Competing short
  names and contradictory email evidence remain unresolved.
- Single-subject captures preserve all submitted wording. Several subjects need
  non-overlapping, source-grounded passages covering the whole note. Optional
  enrichment is not required. Captures do not use the passive omission prompt
  or its input truncation. No structured fields or contact dates are changed.
- An explicit `CAPTURE_AUTO_AFTER` timestamp separates new captures from the
  backlog. An empty value disables the new path. It is never inferred from the
  worker date or reset after state loss. Passive roster and filing-mode guards
  remain in place.
- The source's `CaptureOperation` metadata stores a versioned, frozen operation
  before destination writes. Compare-before-write checks protect both source
  revision and operation state. Retries repair interrupted Person initialization
  using a deterministic temporary name and durable creation anchor.
- Contributions have source/revision-specific anchors. Edited or removed
  verified contributions stop replay. Successful contributions receive one
  processing-day receipt with Person and source links. Contribution dates retain
  the original capture date. Receipts use the existing generated-note classifier
  in Python and JavaScript and cannot re-enter extraction.
- Calendar events with more than 10 raw invitees are excluded from every
  calendar entity-processing path, including historical contact backfill.
  Exactly 10 remains eligible. The count includes self, rooms, and resources.
  Existing candidates and contact dates are retained.

## Remaining implementation

Milestones 1 and 2 have their capture foundation. Milestone 3 has frozen-operation
replay and mutation fault tests. Milestone 4 has receipts only.

Before release, implement capture-scoped undo and reassignment, a bounded recent
capture registry, explicit source-revision correction, durable exceptional
questions and technical-delay views in the review app, reminder-only Things
semantics, preview/resumable migration with legacy-task fences, and a gated
arrival trigger. Unresolved captures currently retain their source and retry;
they do not yet have the new question UI. Source changes after filing stop for
correction rather than appending a second interpretation. Structured-field
extraction is intentionally omitted from this initial capture path so correction
can operate on unchanged source contributions without restoring metadata.

The existing default path still has its legacy review and Things semantics.
Do not enable the new path as a substitute for completing those milestones.

## Verification and deployment boundary

Tests use fictional data, temporary state, stubbed logging, and mocked bridge
records. The JavaScript tests execute the actual capture handlers under
`osascript` with fake DEVONthink objects. They inject interruption after record
creation, body initialization, each metadata initialization, rename, and
contribution writing. Python replay tests interrupt every orchestrated mutation,
including operation persistence and receipt writing.

Local inference attribution quality, DEVONthink property/index behavior, and
Mac/iOS action behavior require a bounded fictional-data check after the release
is ready. The Calendar and Contacts live canaries are outside this task's privacy
boundary. Distribution-list membership is unavailable through the event data,
so the invitee limit cannot detect a hidden large audience behind one address.

Activation and migration have not run. The eventual activation must happen from
the primary checkout after a separate local integration decision:

1. Finish and review all remaining milestones, including the migration CLI.
2. Merge `CaptureOperation` from the tracked metadata seed using
   `scripts/seed-devonthink-config.sh` while DEVONthink is closed, then restart
   DEVONthink. Use the seed reconciler's read-only preview first.
3. Install the finished files through the primary checkout's normal setup/Stow
   workflow. A task worktree must never own live HOME links.
4. Persist `CAPTURE_AUTO_AFTER=<local activation timestamp>` in the machine-local
   `~/.config/dt-pipeline/entities.conf`. Leave it empty until the full release
   passes its safety checks. The timestamp comparison uses DEVONthink's precise
   addition timestamp, not its date-only source date.
5. Preview and explicitly apply the forthcoming capture migration. There is no
   authorized production migration command yet. The existing
   `--migrate-candidates` command converts the old calendar ledger only and is not
   a capture-backlog migration.
6. Agree on one fictional Mac capture, one iOS capture, one ambiguity, and one
   correction to verify the activated release. Applying a changed canonical
   Drafts action requires replacing its Script step in the app.
