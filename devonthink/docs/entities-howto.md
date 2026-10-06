# Using the entity layer

People have Markdown records in `20_ENTITIES/People`, with dated information and links to its source. The morning brief shows meetings and reconnect suggestions. Deliberate captures save directly; passive discovery keeps its configured review policy.

This guide describes the capture release after installation and activation. Until then, the running pipeline retains its installed behavior. The operator procedure is in [the implementation and deployment guide](entity-capture-implementation.md).

## Capture something

Use **Capture Person Fact** in Drafts on Mac or iPhone. Write naturally, for example:

> Wren moved to Denver. Her daughter starts college in October.

Single names are valid. A uniquely matching existing person receives the note. A new person gets a record under the name you wrote, without a guessed surname. An empty roster does not block deliberate capture. Short notes, lists, and mundane information are retained in full. A daughter mentioned as news about Wren does not need her own record.

New captures appear in the Person's Biographical Log instead of separate Captured sections. Earlier Captured sections remain until the explicit migration is applied. The primary person's name is normally implicit in their bullets. Original notes remain available through source links.

Exact matches with compatible dates share one bullet while retaining both sources. For a possible match, the app shows **From your note** beside **Already saved**. Choose **Already recorded; keep this entry** only if the existing entry covers every detail, including who it concerns and any dates. Otherwise, choose **Add as new information**. Expand **Original wording and other options** to keep your submitted wording as a separate entry.

If local comparison fails or is unavailable, the note waits without asking you to resolve unverified matches. New edits or reassignments can also report Waiting while the local worker compares them. Review never claims Saved before verified filing.

After verified filing, the processing day's daily note contains a short receipt linking to the person, original note, and correction view. The saved contribution keeps the capture's original date. Capturing information does not update `LastContact` or automatically overwrite city, employer, email, or other structured fields.

The action's acknowledgement confirms delivery rather than filing. iOS confirms record creation through its callback. macOS confirms dispatch only. The daily-note receipt confirms filing.

Processing waits for the driver Mac, AC power, normal memory pressure, an available local model, and any configured idle requirement. An arrival notification can start eligible processing. The 30-minute scheduled run recovers missed notifications. Neither path bypasses those gates.

## Answer an occasional question

Open the entity review app at `http://localhost:8080/entities/` on the Mac, or its configured private tailnet address on your phone.

If two people could be Wren, choose the intended person using their name, city, and employer. Use **Someone new** when you mean another person. Correct a mistaken email in the note and its subject email field. Several subjects need separate passages covering the note; add or remove subject rows as needed. A single subject receives the whole note.

If a note has no written name, choosing an existing person is enough to answer who it concerns. Creating a new person still requires their name in the note. **Decide later** keeps the note and its question intact. Technical delays appear separately and do not claim that the note was saved.

For a migration question about an older note that already has Person filings, **Keep existing filings** closes the question without copying its text again or changing those Person records. The app labels the outcome **Existing filings kept**. Edit those older Person records directly if they need correction. Changing the original note reopens a question.

Permanent ignore and filing suppression remain separate decisions. Restore an ignored identity or change `FilingSuppressed` in DEVONthink before asking the automation to file against it.

## Find or correct saved information

Open the person from a receipt, search their name in DEVONthink, or follow the original-note link. Recent outcomes appear in the review app; an older receipt's detail link opens its capture directly.

Use **Wrong person** to reassign the capture or revise its wording. The automation verifies the destination before removing its old contribution. Use **Undo this capture's filing** to remove only that capture's support. A shared assertion stays while another source supports it. Historical or explicitly kept manual content stays even without active support. Original sources, other captures, populated Person records, and unrelated metadata stay intact.

If you edited the contribution directly, the app asks whether to keep that edited text and finish. It also stops if the destination changed during an interrupted correction. Review the displayed conflict before choosing to preserve those changes. A source edit keeps the earlier captured version in the operation history.

Person records remain editable Markdown. Add aliases when you learn another spelling or nickname. A unique first name can resolve without an alias, but competing names still require a choice. Do not erase a machine-owned contribution merely to retry filing; use its correction view.

## Optional Things reminders

Set `THINGS_SYNC=on` in `~/.config/dt-pipeline/entities.conf` to receive an unscheduled reminder for an unanswered capture question. It links to the review app and does not include the captured text or person's name. `THINGS_PROJECT` chooses the project, defaulting to `Entity Filing`.

Answer in the review app. Completing, canceling, deleting, or moving a reminder never approves, ignores, or discards entity data. A dismissed reminder is not recreated for the same question revision. A changed question may receive a new reminder, and the old one is retired. Cleanup that needs `THINGS_AUTH_TOKEN` waits without changing entity decisions.

Ordinary passive candidates and successful captures do not create Things tasks. The old task-completion approval protocol is disabled in this release; the migration command retires its old reminders.

## Passive discovery and contact tracking

Meeting notes, handwritten notes, and daily jots retain `FILING_MODE`, defaulting to review. Use the review app for their proposals and candidate decisions. Unknown passive candidates remain quiet until you choose to track them. Deliberate capture does not promote their accumulated historical sightings.

After the passive matching update is installed, possible paraphrases show **New extracted information** beside **Already saved**, with dates. Choose an existing entry only if it covers every detail; otherwise keep a separate entry. Waiting comparisons have no approval control. Save edited proposals for local comparison rather than approving them immediately. Moving a Waiting proposal to Approved in DEVONthink does not bypass that requirement. Older candidate sightings with unverifiable provenance stay blocked and intact; ask the operator for fresh source-grounded extraction or capture.

Calendar entity processing ignores events with more than 10 raw invitees, including self, rooms, and resources. Exactly 10 remains eligible. The calendar and Messages passes maintain contact dates; a captured fact alone is not evidence of contact.

For reconnect suggestions, set `Relationship` to `family`, `close-friend`, `friend`, or `colleague`. Set `EntityStatus=dormant` to stop reconnect suggestions. `FilingSuppressed` pauses filing; `BriefingSuppressed` controls the generated briefing. These controls have different purposes.

## Initial setup

Run `~/.local/bin/dt-entity-bootstrap` to create the entity groups and their privacy exclusions. For the Drafts action, put the printed `_Facts` group UUID into `FACTS_GROUP_UUID` in `drafts/drafts-capture-fact.js`, then replace the action's Script step with that canonical script. The UUID remains stable across DEVONthink sync.

Manual Person templates are still useful for your inner circle. Passive extraction retains `MIN_ROSTER`; deliberate capture does not require a seeded roster. Aliases and curated email metadata improve resolution without authorizing guesses.

Installation, metadata merging, old-capture migration, activation, and the bounded Mac/iOS verification procedure are separate operator steps documented in [entity-capture-implementation.md](entity-capture-implementation.md). Do not activate from a linked task worktree.
