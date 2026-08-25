---
name: devonthink
description: >-
  Search DEVONthink and inspect custom metadata through its official
  privacy-enforcing server with a bundled read-only client. Use when finding
  DEVONthink records, listing custom metadata fields, or reading record custom
  metadata.
compatibility: >-
  Requires DEVONthink 4.3 or later on macOS with the built-in MCP server.
---

# DEVONthink read access

Use the bundled `scripts/devonthink_read.py` client. Resolve it relative to
this `SKILL.md`. It starts DEVONthink's official stdio MCP server only for the
request and exposes exactly three read operations. It has no create, update,
move, trash, content, chat, research, or arbitrary MCP method.

## Privacy boundary

DEVONthink's own server performs every read, so records and groups excluded
from Chat and MCP stay excluded. The configured sensitive-content redaction
remains in force. Treat an exclusion error as intentional. Never bypass it
with AppleScript, JXA, direct database access, or another tool. Return only the
fields needed for the user's request, and summarize private metadata rather
than echoing a large raw response.

## Commands

List custom metadata field definitions:

```bash
python3 ./scripts/devonthink_read.py fields
python3 ./scripts/devonthink_read.py fields --include-disabled
```

Search records. Results contain only UUID and name. The default limit is 20,
and the client refuses limits over 100:

```bash
python3 ./scripts/devonthink_read.py search '<DEVONthink query>'
python3 ./scripts/devonthink_read.py search '<query>' \
  --database-uuid <UUID> --group-uuid <UUID> --limit 20 --sort score
```

Read custom metadata after a search returns record UUIDs. A call accepts at
most 50:

```bash
python3 ./scripts/devonthink_read.py metadata <UUID> [<UUID> ...]
python3 ./scripts/devonthink_read.py metadata <UUID> --database-uuid <UUID>
```

The helper writes one JSON value to stdout and diagnostics to stderr. It
refuses responses over 256,000 characters. Keep search queries focused and
request a small result set first. Use `--offset` for another page. Do not use
this skill for record content or mutations; those capabilities were not
justified by historical use.
