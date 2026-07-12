# TRMNL brief mirror

Mirrors the morning brief onto a TRMNL e-ink dashboard via a private
plugin's webhook. Three pieces:

```
dt-morning-brief.py                 (existing launchd schedule, 4x each morning)
  └─ writes ~/.local/state/devonthink/morning-brief.json   (structured snapshot)
  └─ subprocess: trmnl-push-brief.py
       └─ compacts snapshot to the webhook byte budget (truncation ladder)
       └─ POST https://trmnl.com/api/custom_plugins/<uuid>  (merge_variables)
            └─ TRMNL renders trmnl/dt-brief/src/*.liquid on the device
```

## Data flow

`dt-morning-brief.py` computes each section once as structured data
(`brief_blocks`, `reconnect_overdue`, `birthday_rows`, `review_backlog`,
`journal_status_info`, `on_this_day_rows`) and renders the daily-note
markdown from it, so note and TRMNL can never drift. `build_snapshot`
serializes those intermediates; the snapshot is written atomically on every
non-dry-run for the real today (a `--date` replay never touches it) even
when there is nothing to write to the note — "no meetings" is a displayable
state. Two deliberate divergences from the note: Reconnect is included
daily (the note carries it only on Mondays), and the snapshot carries no
`x-devonthink-item://` links (nothing to click on e-ink).

## Pusher

`trmnl-push-brief.py` (stdlib, `#!/usr/bin/python3`) is a silent no-op
until `~/.config/dt-pipeline/trmnl.conf` exists:

```
TRMNL_WEBHOOK_URL=https://trmnl.com/api/custom_plugins/<plugin-uuid>
TRMNL_PAYLOAD_LIMIT=2048    # optional; TRMNL+ accounts may use 5120
```

TRMNL's webhook caps requests at 2 kB / 12 per hour (5 kB / 30 for
TRMNL+). The pusher fits the payload with a fixed degradation ladder
(`LADDER` — on-this-day tail first, then person detail fields, then list
caps; meetings survive longest) and marks `truncated: true` when any step
ran. Unchanged payloads are never re-POSTed — the last body's sha256 lives
in `~/.local/state/devonthink/trmnl-push-state.json`; a failed push leaves
`status: error` there, so the brief's next scheduled run retries. Network
errors / 429 / 5xx log INFO (self-healing, no 5am watchdog page); other
4xx logs WARNING (misconfiguration, needs a human). No battery gate of its
own: launchd runs arrive as a child of the already-gated brief.

Manual runs: `trmnl-push-brief.py --dry-run` prints the payload, byte
size, and applied ladder steps (works before any config exists);
`--force` bypasses the unchanged-payload dedup.

## Template

Plugin source and local-preview project: `trmnl/dt-brief/` (repo root, not
stowed). Markup follows the TRMNL framework rules (no custom CSS, no
emoji, `title_bar` sibling of one `layout`). Deploy by pasting into the
plugin's markup editor, `trmnlp push`, or TRMNL's MCP server.

## Setup on a new machine (or first time)

1. trmnl.com → Plugins → Private Plugin → create, strategy **Webhook**.
2. Copy the webhook UUID into `~/.config/dt-pipeline/trmnl.conf` (format
   above).
3. Paste `trmnl/dt-brief/src/*.liquid` into the plugin's markup editor
   (per size), or `trmnlp push`.
4. `trmnl-push-brief.py --force` to seed the first screen.

The webhook UUID is written to nothing in this repo — treat it like the
other `~/.config/dt-pipeline` secrets.
