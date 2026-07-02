# Drafts actions

Script bodies for the three live Drafts actions. Drafts stores actions in its
own sync store, not in files, so **these repo copies are the canonical source**:
after editing a script here, paste it over the action's Script step in Drafts
(action → edit → Script step). Going the other way — trusting whatever a Drafts
backup/import restores — resurrects stale bodies; always re-paste from the repo
after an import (see MIGRATION.md §5).

| Action (Drafts)  | Script | Does |
|------------------|--------|------|
| Quick Jot        | `drafts-quick-jot.js` | Inserts the draft as a timestamped bullet into today's DEVONthink daily note. |
| New Note → Inbox | `drafts-new-inbox-note.js` | Sends the draft to the DEVONthink inbox as a Markdown record. |
| New Note → Archive | `drafts-new-archive-note.js` | Sends the draft to the DEVONthink `99_ARCHIVE` group as a Markdown record. |

An earlier "Append to Daily Note" action (AppleScript + Shortcuts bridge) was
retired in April 2026, superseded by Quick Jot; its files were removed from
this directory and live only in git history. If a `~Append to Daily Note`
wrapper action or an "Append to Daily Note" Shortcut still exists on a
machine, both are orphans and safe to delete.
