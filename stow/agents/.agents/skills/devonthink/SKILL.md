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
and the client refuses limits over 100 or responses exceeding the requested
limit:

```bash
python3 ./scripts/devonthink_read.py search '<DEVONthink query>'
python3 ./scripts/devonthink_read.py search '<query>' \
  --database-uuid <UUID> --group-uuid <UUID> --limit 20 --sort score
```

Read custom metadata after a search returns record UUIDs. A call accepts at
most 50. Batch responses must contain only requested UUIDs, without duplicates,
and cannot exceed the requested count:

```bash
python3 ./scripts/devonthink_read.py metadata <UUID> [<UUID> ...]
python3 ./scripts/devonthink_read.py metadata <UUID> --database-uuid <UUID>
```

The helper writes one JSON value to stdout and generic diagnostics to stderr.
It validates responses before emitting anything. Search rows retain only
`uuid` and `name`; metadata batch rows retain only `uuid` and `metadata`.
Vendor array and `results` envelopes are preserved, but extra envelope and row
properties are dropped. Field definitions retain `identifier`, `type`,
`disabled`, optional `title`, and optional string-array `values` for set fields.
Disabled fields require `--include-disabled`.

DEVONthink returns a bare metadata dictionary for a single requested UUID.
The helper preserves that dictionary. Its identity is bound by the request,
not independently verified from an echoed UUID. Metadata values can be any
valid JSON within the bounds below; the vendor publishes no value schema.

The complete stdout JSON, including indentation and the final newline, cannot
exceed 256,000 Unicode characters. The tool's returned JSON text has the same
character cap. Before JSON parsing, transport reads are capped at 2 MiB per newline-ended
frame, 8 MiB per server session, and eight queued raw frames. JSON nesting is
limited to 64 levels. Malformed or unknown result shapes, invalid UTF-8,
nonfinite numbers, reader failures, and limit violations fail without stdout.
Backend error text is never echoed.

This behavior is checked against DEVONthink 4.4's bundled `mcp-tools.json`,
help, and `Apps/bibliography-workbench/index.html` response handling. The
official stdio server reported `devonthink-mcp` version `1.0.0` and negotiated
protocol `2025-03-26`. Live checks covered initialization, tool schemas, and
field-definition shapes only. Search and metadata behavior have synthetic
coverage based on the vendor source, not live record reads. Exclusion and
redaction enforcement remain the vendor's responsibility and were not tested
with private records. Older supported installations may fail closed if their
response shapes differ.

Keep search queries focused and request a small result set first. Use
`--offset` for another page. Do not use this skill for record content or
mutations; those capabilities were not justified by historical use.
