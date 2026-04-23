# Wiki Integration

The pipeline optionally exports archived documents to an LLM-maintained wiki at `~/Wiki/`. This implements the "LLM Wiki" pattern — where an LLM incrementally builds and maintains a structured, interlinked knowledge base from your documents, compounding knowledge over time instead of re-deriving it on every query.

## Architecture

```
DEVONthink Pipeline                      Wiki Layer
┌─────────────────────────┐
│ Ingest → OCR → Enrich → │──export──→  ~/Wiki/raw/      (immutable source exports)
│ Tasks → Daily Notes →   │                │
│ Archive (99_ARCHIVE)    │                ▼
└─────────────────────────┘           ~/Wiki/wiki/     (LLM-maintained pages)
                                           │
                                      ~/Wiki/CLAUDE.md (schema/conventions)
```

- **Export:** The [Export: Wiki Raw](../README.md#export-wiki-raw) smart rule writes one markdown file per archived document to `~/Wiki/raw/`, with YAML frontmatter containing all pipeline metadata and a `dt_link` back to the original record.
- **Compilation:** An LLM agent (e.g., Claude Code opened in `~/Wiki/`) reads raw exports and maintains structured wiki pages — source summaries, entity pages, concept pages, and cross-document synthesis.
- **Lint:** Periodic health checks where the LLM scans for orphan pages, missing cross-references, and gaps in coverage.

## Browsing the Wiki in DEVONthink

The wiki lives on disk at `~/Wiki/` but is indexed into the Lorebook database (not imported — files stay on disk, DT creates references). This means Claude Code writes to `~/Wiki/wiki/` and DEVONthink sees the changes automatically on the next index update.

The wiki is indexed into Lorebook (same database) rather than a separate database so that:

- **Wikilinks work.** DT's `[[double-bracket]]` wikilinks only resolve within the same database. Wiki entity pages can reference archived documents by name and the links are clickable.
- **See Also is stronger** intra-database — DT's AI surfaces connections between wiki pages and source documents.
- **Shared tag pool.** Wiki concept pages and source documents using the same tags creates natural groupings in smart groups and tag browsing.
- **Single sync and backup.**

**Smart rule safety:** All pipeline rules are scoped to `00_INBOX` or `Lorebook Inbox/Root`. The only database-wide rule is `Util: Lock Name on Rename`, which is harmless on wiki pages (just sets `NameLocked=1` if you manually rename one).

Index setup:

1. In Lorebook, create a group `20_WIKI` at the root level
2. File → Index Items → select `~/Wiki/wiki/` → import into `20_WIKI`
3. Optionally also index `~/Wiki/raw/` into a `20_WIKI/raw` subgroup for browsing raw exports

DT re-scans indexed folders periodically, or force it with File → Update Indexed Items. The wiki's internal `[markdown links](relative/path.md)` work as clickable links in DT's viewer. The `dt_link` values in source pages are clickable `x-devonthink-item://` links back to the original archived document.

## Setup

```bash
# 1. Scaffold the wiki directory
./scripts/init-wiki.sh

# 2. Create WikiExported (Boolean) in DEVONthink → Settings → Data → Custom Metadata

# 3. Create the "Export: Wiki Raw" smart rule in DEVONthink (see above)

# 4. Restow to install the export script
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink

# 5. Index the wiki into Lorebook
#    In DEVONthink: create 20_WIKI group, then File → Index Items → ~/Wiki/wiki/

# 6. Open Claude Code in ~/Wiki and start ingesting
cd ~/Wiki && claude
# Then: "ingest new files in raw/"
```

See [`wiki-claude-md-template.md`](../wiki-claude-md-template.md) for the full wiki schema.
