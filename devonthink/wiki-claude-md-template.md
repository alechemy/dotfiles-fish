# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this wiki.

## Overview

This is a personal knowledge base compiled from documents processed by a DEVONthink pipeline. Raw exports arrive in `raw/` as markdown files with YAML frontmatter containing metadata (title, date, type, tags, summary) and document content. The LLM reads these and maintains a structured, interlinked wiki in `wiki/`.

The raw exports are **immutable** — never modify files in `raw/`. The wiki in `wiki/` is entirely LLM-maintained. The human curates sources and asks questions; the LLM does all the writing, cross-referencing, and bookkeeping.

Each raw file includes a `dt_link` in its frontmatter — a `x-devonthink-item://` URL that opens the original document in DEVONthink. Use these when citing sources so the user can jump to the original.

## Directory Structure

```
raw/                    # Immutable exports from DEVONthink (one .md per document)
wiki/                   # LLM-maintained pages
  index.md              # Content catalog — every wiki page listed with a one-line summary
  log.md                # Append-only chronological record of operations
  skipped.md            # UUIDs intentionally not ingested (duplicates, empty content, etc.)
  sources/              # One page per ingested raw document
  entities/             # People, companies, products, places
  concepts/             # Personal documents organized by life domain (taxes, medical, etc.)
  reading/              # Reference material organized by topic (articles, guides, walkthroughs)
  synthesis/            # Cross-cutting analysis, comparisons, timelines
```

## Page Types

### Source Summaries (`wiki/sources/{UUID}.md`)

One page per ingested document, named to match the raw export's UUID. Goes beyond the AI-generated summary — extracts key facts, decisions, amounts, dates, and names. Links out to entity and concept pages.

Every source page **must** include the `dt_link` from the raw file's frontmatter near the top of the page, so the user can click through to the original document in DEVONthink. Format: `[Open in DEVONthink](x-devonthink-item://UUID)`

### Entity Pages (`wiki/entities/{slugified-name}.md`)

Pages about specific people, companies, products, or places that appear across multiple documents. Named with a URL-safe slug (e.g., `abc-plumbing.md`). Includes a brief description and a list of source references.

### Concept Pages (`wiki/concepts/{slugified-name}.md`)

Pages about **personal documents** organized by life domain — things that are _yours_ (tax forms, medical records, receipts, invoices, meeting notes, handwritten notes, financial statements). When you ask "show me my tax documents" or "what did the dentist say", the answer lives in concept pages.

**Splitting rule:** When a concept page accumulates more than ~15 sources, decompose it into more specific sub-topic pages. The original broad page can remain as a hub linking to the sub-topics.

### Reading Pages (`wiki/reading/{slugified-topic}.md`)

Pages about **reference material** organized by topic — things you've _read_ or _saved_ (articles, guides, walkthroughs, bookmarks, research papers, tutorials, product pages). These are external knowledge, not personal records.

The distinction is driven by the `type` field in the raw export frontmatter. Personal document types (Receipt, Invoice, Tax Form, Meeting Notes, Handwritten Note, Letter, Contract, etc.) go in `concepts/`. Reference types (Article, Guide, Walkthrough, Bookmark, Tutorial, Manual, Product Page, etc.) go in `reading/`.

This separation matters because the use cases are different: "what are my tax obligations" (concepts) vs. "what have I saved about photography techniques" (reading) are different questions with different intent. A true-crime article about tax evasion belongs in `reading/`, not alongside your W-2s in `concepts/taxes.md`.

### Synthesis Pages (`wiki/synthesis/{descriptive-name}.md`)

Cross-document analysis, comparisons, timelines, or answers to questions worth preserving. Created when a query produces an answer that shouldn't disappear into chat history.

## Frontmatter Format

All wiki pages use this frontmatter:

```yaml
---
title: "Page Title"
type: source | entity | concept | reading | synthesis
sources: [UUID1, UUID2] # UUIDs of raw/ files that contributed
updated: "2026-04-07"
---
```

## Operations

### Ingest

When told to ingest (e.g., "ingest new files", "process raw"):

1. List files in `raw/` and compare UUIDs against both `wiki/index.md` and `wiki/skipped.md` to find unprocessed sources. A UUID appearing in either file has already been handled — skip it.
2. For each new raw file:
   a. Read the file — frontmatter metadata + document content
   b. If the source is a duplicate (same URL already represented by an existing source page), add the UUID to `wiki/skipped.md` with a note and skip to the next file — do not create a source page
   c. Create a source summary page in `wiki/sources/` extracting key facts, not just restating the summary
   d. Identify entities (people, companies, products, places) and determine whether the source is a personal document (concept) or reference material (reading) based on the `type` field in the raw frontmatter
   e. For each entity/concept/reading topic: create the page if new, or update with new information and a backlink
3. Update `wiki/index.md` with all new or modified pages
4. Append entries to `wiki/log.md`

For incremental ingest (a few new files), process one at a time and discuss notable findings with the user.

**Bulk ingest** (>10 files): Work in batches of ~20. For each file, read the frontmatter first. If there's a `summary` field, use that and skip the content body. If the summary is missing or empty, read the content body. Create/update source, entity, and concept pages, then update the index. After each batch, commit and summarize what you added. Don't discuss individual files unless something is surprising or ambiguous.

### Query

When asked a question:

1. Read `wiki/index.md` to identify relevant pages
2. Read those pages and synthesize an answer with citations (include `dt_link` where available)
3. If the answer represents a durable insight worth keeping, offer to save it as a synthesis page

### Lint

When asked to lint or health-check:

1. Scan for:
   - Source pages missing a `dt_link` (every source page must have one)
   - Source pages with no links to any concept, reading, or entity page (orphaned sources)
   - Sources filed in concepts/ that should be in reading/ (reference material mixed with personal documents) or vice versa
   - Entity/concept pages with redundant flat "Sources" sections duplicating categorized links above
   - Concept pages with >15 sources that should be split into sub-topics
   - Orphan entity/concept pages with no inbound links from other wiki pages
   - Stale claims superseded by newer sources
   - Entities mentioned in multiple sources but lacking their own page
   - Missing cross-references between related pages
   - Duplicate source pages for the same URL (web clip bookmarks, HTML snapshots, and markdown extracts)
2. Report findings — what's healthy, what needs attention
3. Fix automatically where safe (add missing cross-references, create stub pages for unlinked entities)
4. Suggest new sources to look for or questions to investigate

## Cross-Referencing Conventions

- Between wiki pages: `[Page Title](../entities/page-name.md)` (relative paths)
- Citing a source: `[Source: Title](../sources/UUID.md)` with the `dt_link` from frontmatter if the user might want to open the original
- Every source page should link to the entities, concepts, and/or reading pages it mentions in a "Linked Pages" section
- Entity/concept pages should organize sources into meaningful categories (e.g., "Work Items", "Compensation", "Technical Investigations") with inline descriptions. **Do not** add a redundant flat "Sources" section at the bottom when sources are already linked in categorized sub-sections above — the duplication adds noise without value

## Index Format (`wiki/index.md`)

Organized by category. One line per page with a link and summary:

```markdown
# Wiki Index

## Sources

- [Receipt from ABC Plumbing](sources/XXXX-UUID.md) — kitchen sink replacement quote, $2,400
- [Meeting Notes: Q1 Review](sources/YYYY-UUID.md) — quarterly review with team, action items assigned

## Entities

- [ABC Plumbing](entities/abc-plumbing.md) — local plumbing contractor, used for kitchen renovation

## Concepts

- [Home Renovation](concepts/home-renovation.md) — ongoing kitchen remodel project, started 2026-01

## Reading

- [Photography Techniques](reading/photography.md) — AF settings, composition guides, film simulation recipes

## Synthesis

- [Kitchen Renovation Timeline](synthesis/kitchen-renovation-timeline.md) — chronological view of all renovation decisions and costs
```

## Log Format (`wiki/log.md`)

Append-only, most recent entries at the bottom. Each entry starts with a consistent prefix for parseability:

```markdown
## [2026-04-07] ingest | Receipt from ABC Plumbing

- Created source page: sources/XXXX-UUID.md
- Created entity page: entities/abc-plumbing.md
- Updated concept page: concepts/home-renovation.md
- Updated index.md

## [2026-04-07] lint | Health check

- Found 2 orphan pages, added cross-references
- Suggested entity page for "Kitchen Designs Inc" (mentioned in 3 sources)
```

## Style Guidelines

- Entity/concept pages: neutral, encyclopedic tone. Facts over opinions.
- Source summaries: extract actionable information — amounts, dates, decisions, names, next steps. Don't just paraphrase the AI summary from the frontmatter.
- Keep pages concise. A page that's too long should be split into subpages.
- Prefer updating existing pages over creating near-duplicates. Check the index first.
