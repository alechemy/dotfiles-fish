---
name: things
description: >-
  Reliable automation of Things 3 on macOS through its URL scheme, SQLite database,
  and AppleScript. Use when creating, updating, organizing, or bulk-loading Things projects,
  headings, or to-dos, especially multi-item writes. Covers focus-free writes, heading
  creation, URL truncation, DB-confirmed idempotency, and a bundled helper.
---

# Automating Things 3 reliably

The Things URL scheme has sharp edges that silently drop or wedge writes. Follow these
rules; for any bulk add, use the bundled `things_fill.py` helper rather than hand-rolling
URL opens.

## The six rules (each one cost real debugging time)

1. **Fire writes in the background with `open -g`.** `open -g things:///…` delivers the
   write **without activating Things**, so it never steals focus or — with a tiling WM
   (aerospace) / Spaces — yanks you to another workspace. Things does **not** need to be
   frontmost: measured, `open -g` lands **6/6** with the frontmost app unchanged. Plain
   `open` / `open -a Things3` *activates* Things — do not use them. If Things isn't running,
   `open -g` launches it in the background (`open -g -j -a Things3` to pre-warm hidden);
   rule 4's DB-confirm catches the rare cold-start delay. *(The old "must be frontmost"
   lore predates trying `-g`; the Apple-Event path — `osascript -e 'tell application
   "Things3" to make new to do …'` — is an equally focus-free alternative.)*
2. **The `add` command cannot create headings** — it only attaches to headings that
   already exist. Create headings via the **`json` command with an auth
   token**. Most reliable: create the *project itself* via a `json` `project` create whose
   `attributes.items[]` lists the headings (`{"type":"heading","attributes":{"title":…}}`).
   (Appending headings to an existing project via `json` `update`+`items` is unreliable on
   some builds — prefer create-with-items, or the helper's per-heading fallback. Confirmed
   silent no-op on Things 3.21 (2026-07) with a valid token and no error sheet. Working
   fallback for an *existing* project: UI-script it — `open things:///show?id=<project>`,
   click File ▸ New Heading via System Events, keystroke the title + Return. The keystroke
   may not commit (empty-titled heading in the DB); headings aren't an AppleScript class,
   but `set name of to do id "<heading-uuid>" to "…"` renames one anyway — use that to fix
   the title, then re-run the helper for the to-dos.)
3. **Big `json` imports truncate — batch under a size guard.** A json URL over ~4 KB gets
   **truncated** into a modal *"There is a problem with the provided JSON"* sheet, and **a
   stuck sheet blocks ALL further URL processing** until dismissed. So bulk writes go either
   one-per-`add` (short URLs can't truncate) or, much faster, as **`json` batches each kept
   under ~3.5 KB** — the helper does the latter: it packs to-dos into batches, measuring the
   encoded URL length before every `open`, and DB-confirms each batch. Two JSON-create
   gotchas the helper encodes (both cost a wedged sheet to learn): **omit the `operation`
   field on a create** (it belongs only on an `update`; including it on a create rejects the
   whole batch), and **checklist items must be objects**
   `{"type":"checklist-item","attributes":{"title":…}}` — the plain-string form the `add`
   command accepts is silently dropped in `json`. A single to-do whose own json URL would
   still exceed the guard (e.g. a huge note) falls back to the compact `add` command.
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
6. **Percent-encode params with `quote(v, safe='')` — never `urlencode`/`quote_plus`.**
   Things' URL parser does **not** treat `+` as a space; it stores it literally, so
   `urlencode` silently saves the title `A B` as `A+B` (same for notes). The helper's `add`
   does this right; when you hand-roll an `add`/`update`/`json` URL,
   encode each value with `urllib.parse.quote(str(v), safe="")`. To skip URL assembly (and
   its encoding) entirely for a one-off, use AppleScript `make new to do` — params pass
   directly, no escaping.

## Don't burst opens

macOS coalesces rapid `open` calls to an already-running app, so firing many in a tight
loop drops most. The helper never bursts: the json-batch pass fires **one `open` per batch**
(a handful total for dozens of to-dos) and the single-`add` sweep fires one at a time, each
followed by a DB-confirm poll before the next — naturally serialized.

## The helper: `things_fill.py`

Idempotent bulk fill that applies all six rules. Write a spec JSON, then run the helper
from this skill directory:

```bash
python3 ./things_fill.py spec.json            # fill
python3 ./things_fill.py spec.json --dry-run  # show what's missing
```

The helper reads `THINGS_AUTH_TOKEN` from the environment or from the managed export in
`~/.zshenv`. The token is needed only to create headings. Do not print, prompt for, or
hardcode it. If it is unavailable, rerun `~/.dotfiles/scripts/build-things-config.sh` with
1Password unlocked; ordinary `add` writes do not need it.

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
`project` is a uuid or exact title. It ensures Things is running (in the background, never
foregrounded), ensures headings exist, then fills missing to-dos in two passes: with a token
present it creates them in size-guarded **`json` batches** (a handful of `open`s for dozens
of to-dos), then an idempotent **single-`add` sweep** (`open -g`) mops up any straggler and
is the sole path when no token is set. Every write is DB-confirmed. Re-run anytime — it only
adds what's absent (~2 s for two dozen to-dos, vs. ~17 s pre-batching).

To-do titles must be unique across the requested project, including across headings.
The helper uses the exact title as its idempotency key. An existing active title in any
heading counts as present. Duplicate requested titles are rejected before database or app
access; rename them explicitly rather than expecting heading-based identity. Unknown CLI
options are rejected. `--dry-run` reads project state but does not load the auth token or
invoke the app.

The helper is **project-scoped** — every to-do gets a `list-id`, so it can't place a
project-less to-do into Inbox/Anytime/Someday. For a one-off unfiled to-do, don't
hand-roll a URL (that's how the `+`-encoding trap in rule 6 bites); use AppleScript
`make new to do … with properties {name:…}` and set the list, e.g.
`osascript -e 'tell application "Things3" to make new to do with properties {name:"…", notes:"…"}'`
(lands in Inbox; `move … to list "Anytime"` to file it).

## Recovery if you wedged it

Symptom: writes stop landing, DB count frozen. Cause: a modal sheet (usually the JSON
error, or the Settings panel) is blocking Things. Fix: dismiss all sheets (above), confirm
`count of sheets of windows` is 0, then resume with the `add`-per-todo + DB-confirm pattern.
