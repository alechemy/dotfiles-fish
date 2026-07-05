---
name: things
description: >-
  Reliable automation of Things 3 (the macOS task manager) via the things-mcp server
  and the Things URL scheme. Use whenever creating, updating, organizing, or bulk-loading
  Things projects, headings, or to-dos — especially multi-item writes, which fail in
  non-obvious ways without these rules. Covers background (focus-free) writes via `open -g`,
  heading creation, the json-truncation trap, DB-confirmed idempotent writes, and a helper.
---

# Automating Things 3 reliably

The Things URL scheme + `things-mcp` are powerful but have sharp edges that silently
drop or wedge writes. Follow these rules; for any bulk add, **use the bundled helper
`things_fill.py`** rather than hand-rolling URL opens.

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
2. **The MCP and the `add` command cannot create headings** — they only attach to
   headings that already exist. Create headings via the **`json` command with an auth
   token**. Most reliable: create the *project itself* via a `json` `project` create whose
   `attributes.items[]` lists the headings (`{"type":"heading","attributes":{"title":…}}`).
   (Appending headings to an existing project via `json` `update`+`items` is unreliable on
   some builds — prefer create-with-items, or the helper's per-heading fallback.)
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
   does this right (`things_fill.py:127`); when you hand-roll an `add`/`update`/`json` URL,
   encode each value with `urllib.parse.quote(str(v), safe="")`. To skip URL assembly (and
   its encoding) entirely for a one-off, use AppleScript `make new to do` — params pass
   directly, no escaping.

## Don't burst opens

macOS coalesces rapid `open` calls to an already-running app, so firing many in a tight
loop drops most. The helper never bursts: the json-batch pass fires **one `open` per batch**
(a handful total for dozens of to-dos) and the single-`add` sweep fires one at a time, each
followed by a DB-confirm poll before the next — naturally serialized.

## The helper: `things_fill.py`

Idempotent bulk fill that applies all six rules. Write a spec JSON and run it:

```bash
# (only needed if it must CREATE headings)
export THINGS_AUTH_TOKEN=...   # Things → Settings → General → Enable Things URLs → Manage

python3 ~/.claude/skills/things/things_fill.py spec.json            # fill
python3 ~/.claude/skills/things/things_fill.py spec.json --dry-run  # show what's missing
```

> **The token is already provisioned in the env here — don't re-prompt for it or hardcode
> it.** `$THINGS_AUTH_TOKEN` is rendered from 1Password into `~/.zshenv` by
> `~/.dotfiles/scripts/build-things-config.sh` (run by `setup.sh`), and Claude Code's Bash
> tool runs zsh, which sources `~/.zshenv`. So `update` / `json` / `cancel` and
> heading-creating writes work with no `export` step. If it ever resolves empty, re-run
> that build script (needs an unlocked `op`); the `add` command never needs the token.

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

The helper is **project-scoped** — every to-do gets a `list-id`, so it can't place a
project-less to-do into Inbox/Anytime/Someday. For a one-off unfiled to-do, don't
hand-roll a URL (that's how the `+`-encoding trap in rule 6 bites); use the MCP `add_todo`
or AppleScript `make new to do … with properties {name:…}` and set the list, e.g.
`osascript -e 'tell application "Things3" to make new to do with properties {name:"…", notes:"…"}'`
(lands in Inbox; `move … to list "Anytime"` to file it).

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
