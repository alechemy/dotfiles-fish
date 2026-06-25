---
name: things
description: >-
  Reliable automation of Things 3 (the macOS task manager) via the things-mcp server
  and the Things URL scheme. Use whenever creating, updating, organizing, or bulk-loading
  Things projects, headings, or to-dos — especially multi-item writes, which fail in
  non-obvious ways without these rules. Covers the focus requirement, heading creation,
  the json-truncation trap, DB-confirmed idempotent writes, and a ready helper script.
---

# Automating Things 3 reliably

The Things URL scheme + `things-mcp` are powerful but have sharp edges that silently
drop or wedge writes. Follow these rules; for any bulk add, **use the bundled helper
`things_fill.py`** rather than hand-rolling URL opens.

## The five rules (each one cost real debugging time)

1. **Things must be the frontmost app** or `open things:///…` writes silently no-op.
   Run `open -a Things3` first and keep it focused while writing. (Backgrounded Things =
   nothing lands, no error.)
2. **The MCP and the `add` command cannot create headings** — they only attach to
   headings that already exist. Create headings via the **`json` command with an auth
   token**. Most reliable: create the *project itself* via a `json` `project` create whose
   `attributes.items[]` lists the headings (`{"type":"heading","attributes":{"title":…}}`).
   (Appending headings to an existing project via `json` `update`+`items` is unreliable on
   some builds — prefer create-with-items, or the helper's per-heading fallback.)
3. **Never bulk-fire a big `json` import.** Long URLs (>~4 KB) get **truncated** into a
   modal *"There is a problem with the provided JSON"* sheet, and **a stuck sheet blocks
   ALL further URL processing** until dismissed. Instead add **one to-do per `add`
   command** — short URLs can't truncate.
4. **Confirm every write by reading the Things SQLite DB**, and compute "what's missing"
   from the DB before each write, so retries are **idempotent (no duplicates)**. Do NOT
   trust that a fired `open` landed. DB:
   `~/Library/Group Containers/*/ThingsData-*/Things Database.thingsdatabase/main.sqlite`
   (read-only: `sqlite3.connect("file:…?mode=ro", uri=True)`). Table `TMTask`:
   `type` 0=to-do 1=project 2=heading; `status` 0=open 2=canceled 3=done; `trashed`;
   `project`; `heading` (uuid of parent heading). A to-do under a heading has
   `project=NULL` and `heading=<heading uuid>` — count both `project=P` and
   `heading IN (headings of P)`.
5. **No hard-delete** exists in the URL scheme. `cancel`/`complete` moves items to the
   Logbook (the human empties Trash manually). `add` won't create tags — tags must
   already exist.

## Don't burst opens

macOS coalesces rapid `open` calls to an already-running app, so firing many in a tight
loop drops most. The helper avoids this by firing one `add`, then polling the DB until it
lands, before the next — naturally serialized, never bursted.

## The helper: `things_fill.py`

Idempotent bulk fill that applies all five rules. Write a spec JSON and run it:

```bash
# (only needed if it must CREATE headings)
export THINGS_AUTH_TOKEN=...   # Things → Settings → General → Enable Things URLs → Manage

python3 ~/.claude/skills/things/things_fill.py spec.json            # fill
python3 ~/.claude/skills/things/things_fill.py spec.json --dry-run  # show what's missing
```

`spec.json`:
```json
{
  "project": "TMDB Mobile v1",
  "headings": ["▶️ Now", "Build", "Ship"],
  "todos": [
    {"title": "TMDB-101 · do the thing", "heading": "Build",
     "notes": "context", "tags": ["❗Important"], "checklist": ["step a", "step b"]}
  ]
}
```
`project` is a uuid or exact title. It focuses Things, ensures headings exist, then adds
each missing to-do via the `add` command, DB-confirming each. Re-run anytime — it only
adds what's absent.

## MCP tool notes (`things-mcp`, `uvx things-mcp`)

- **Reads** (reliable): `get_projects`, `get_areas`, `get_headings(project_uuid)`,
  `get_todos(project_uuid)`, `get_tags`, `search_*`. Good for inspecting state — but for
  write-confirmation loops, reading the SQLite DB directly is faster and snapshot-clean.
- **Writes:** `add_todo` works (one at a time) and attaches to an existing heading via
  `heading_id` (preferred) — but its `heading` (by title) is **ignored if the heading
  doesn't exist**, and there is **no add_heading**. `update_todo`/`update_project` set
  attributes (incl. `canceled`/`completed`) but can't create headings.
- If Things gets wedged by stuck sheets, clear them:
  `osascript` → System Events → click `OK`/`Done` on every sheet of every Things window
  (the helper's `dismiss_sheets()` does this).

## Recovery if you wedged it

Symptom: writes stop landing, DB count frozen. Cause: a modal sheet (usually the JSON
error, or the Settings panel) is blocking Things. Fix: dismiss all sheets (above), confirm
`count of sheets of windows` is 0, then resume with the `add`-per-todo + DB-confirm pattern.
