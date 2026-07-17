# Drafts actions

Script bodies for the four live Drafts actions. Drafts stores actions in its
own sync store, not in files, so **these repo copies are the canonical source**:
after editing a script here, paste it over the action's Script step in Drafts
(action → edit → Script step). Going the other way — trusting whatever a Drafts
backup/import restores — resurrects stale bodies; always re-paste from the repo
after an import (see MIGRATION.md §5).

| Action (Drafts)  | Script | Does |
|------------------|--------|------|
| Quick Jot        | `drafts-quick-jot.js` | Inserts the draft as a timestamped bullet into today's DEVONthink daily note. |
| Capture Person Fact | `drafts-capture-fact.js` | Sends the draft to `20_ENTITIES/_Facts`; the entity layer resolves the named person and files the fact. |
| New Note → Inbox | `drafts-new-inbox-note.js` | Sends the draft to the DEVONthink inbox as a Markdown record. |
| New Note → Archive | `drafts-new-archive-note.js` | Sends the draft to the DEVONthink `99_ARCHIVE` group as a Markdown record. |

**Capture Person Fact setup:** run `~/.local/bin/dt-entity-bootstrap` once — it
creates `20_ENTITIES/_Facts` and prints its group UUID. Paste that UUID into
`FACTS_GROUP_UUID` at the top of `drafts-capture-fact.js` before pasting the
script into the action. The same UUID works on Mac and iOS (DEVONthink sync
keeps it stable); canary one capture from each device into `_Facts` before
relying on it. Name the person as specifically as you can — the person's full
name or a known alias in the capture is what lets the fact auto-file rather than
wait as a review proposal. See `devonthink/docs/entities.md` → "Fact capture".

An earlier "Append to Daily Note" action (AppleScript + Shortcuts bridge) was
retired in April 2026, superseded by Quick Jot; its files were removed from
this directory and live only in git history. If a `~Append to Daily Note`
wrapper action or an "Append to Daily Note" Shortcut still exists on a
machine, both are orphans and safe to delete.
