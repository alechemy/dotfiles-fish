---
name: summarize
description: Summarize any content (YouTube video, article, whitepaper/PDF, podcast episode, book chapter, etc.) into a rich DEVONthink markdown record with section-by-section breakdowns. Imports the summary into DEVONthink and flows through the standard pipeline.
user_invocable: true
---

# Summarize

Universal content summarizer. Takes any input — YouTube video, web article, whitepaper/PDF, epub book, podcast, lecture — and produces a rich markdown summary imported into DEVONthink's Lorebook inbox and processed by the standard pipeline (AI enrichment → action items → daily notes → archive → wiki export).

## Requirements

**CLI tools** — install before first use, or let Step 0 walk you through it:

| Tool                     | Purpose                                    | Install                                       |
| ------------------------ | ------------------------------------------ | --------------------------------------------- |
| `yt-dlp`                 | YouTube/podcast download + metadata + subs | `brew install yt-dlp` or `pip install yt-dlp` |
| `defuddle`               | Web article extraction                     | `npm install -g defuddle`                     |
| `pdftotext`              | PDF text extraction                        | `brew install poppler`                        |
| `pandoc`                 | EPUB / DOCX → markdown                     | `brew install pandoc`                         |
| `mlx_whisper` (optional) | Local audio transcription fallback         | `pip install mlx-whisper`                     |

Alternative to `mlx_whisper`: set `ELEVENLABS_API_KEY` to use ElevenLabs Scribe for transcription.

## Configuration

```
DT_DATABASE = Lorebook        # DEVONthink database name
DT_INBOX    = /00_INBOX       # Group for new records (pipeline entry point)
```

## Trigger

When the user provides content to summarize: a URL (YouTube, article, blog), a PDF/file path, pasted text, or a reference to content in DEVONthink.

## Inputs

- **Source**: URL, file path, or pasted text
- **`--original-path <path>`** (optional): The original file path before conversion. When binary formats (EPUB, DOCX) are pre-converted to markdown by the caller, this preserves the original filename for record naming (e.g. "Grossman, Lev - The Bright Sword.epub" → use for author/title extraction).
- **`--dt-source <UUID>`** (optional): DEVONthink UUID of the source record, passed automatically by the on-demand smart rule. Used to set `SummarySource` as an item link back to the original record.
- **Audience** (optional): defaults to "general reader." User may specify (e.g. "high school student", "expert", "5-year-old")
- **Depth** (optional): defaults to "full." User may request "tldr only", "section-by-section", or "deep dive"

## Step 0: Bootstrap check (first run)

Before doing any work, verify the environment is ready. **Skip any check that already passes** — only prompt the user when something is actually missing. Do not re-run Step 0 on subsequent invocations if the initial setup succeeded.

### 0a. Verify DEVONthink is running

```bash
osascript -e 'tell application id "DNtp" to name of databases' 2>/dev/null || echo "NOT RUNNING"
```

If DEVONthink isn't running, ask the user to launch it.

### 0b. Check required CLI tools

```bash
for tool in yt-dlp defuddle pdftotext pandoc; do
  command -v "$tool" >/dev/null 2>&1 || echo "MISSING: $tool"
done
```

For each missing tool, tell the user what's missing and **ask before installing** — installs touch the user's system. Use the install commands from the Requirements table above. If the user declines, note which tools are missing and warn that the corresponding content types (YouTube, web articles, PDFs, EPUBs) will fail until installed.

Once Step 0 passes, proceed to Step 1.

## Step 1: Detect content type and extract text

**IMPORTANT:** Do NOT use the Read tool on binary files (EPUB, DOCX, audio, video). These must be converted to text using the appropriate CLI tool below. Only use Read for plain text or markdown files.

### YouTube video

```bash
# Set to your default browser: chrome, firefox, safari, arc, brave
BROWSER=chrome

# Get metadata
yt-dlp --cookies-from-browser "$BROWSER" \
  --print "%(id)s|%(title)s|%(duration)s|%(upload_date)s|%(view_count)s|%(channel)s|%(channel_id)s" \
  --no-download "<URL>"

# Try auto-subtitles first (fastest, free)
yt-dlp --cookies-from-browser "$BROWSER" \
  --write-auto-sub --sub-lang en --sub-format json3 \
  --skip-download -o "/tmp/summarize/%(id)s" "<URL>"
```

If auto-subs exist, extract text from the JSON3 file. If not, or if quality is poor:

- Download audio and transcribe (ask user: local mlx_whisper or ElevenLabs Scribe)

### Web article / blog post

```bash
defuddle parse "<URL>" --md -o /tmp/summarize/article.md
```

If defuddle is not installed: `npm install -g defuddle`

Extract title, author, date, domain from defuddle metadata:

```bash
defuddle parse "<URL>" -p title
defuddle parse "<URL>" -p domain
```

### PDF

```bash
pdftotext "<path>" /tmp/summarize/paper.txt
```

If `pdftotext` is not available: `brew install poppler`

### EPUB (books)

```bash
# Extract full text as markdown (preserves chapter structure)
pandoc "<path>" -t markdown --wrap=none -o /tmp/summarize/book.md

# If you need chapter boundaries, extract the TOC:
pandoc "<path>" -t json | python3 -c "
import json, sys
doc = json.load(sys.stdin)
for block in doc['blocks']:
    if block['t'] == 'Header':
        level = block['c'][0]
        text = ''.join(
            item['c'] if item['t'] == 'Str' else ' ' if item['t'] == 'Space' else ''
            for item in block['c'][2]
        )
        print(f'L{level}: {text}')
"
```

**Chapter splitting strategy for books:**

1. Extract full text with `pandoc` → markdown
2. Identify chapter boundaries from headers (epubs have built-in TOC structure that pandoc preserves as `#`/`##` headers)
3. Split into one chunk per chapter
4. Dispatch parallel Opus subagents — **one per chapter** — same as any other long content
5. A typical book (60-100k words, 15-30 chapters) produces chapters of ~3-5k words each — well within subagent context limits

**For very long books (>30 chapters):** batch chapters into groups of ~5 per subagent to keep the number of parallel agents manageable. Each subagent summarizes its batch and returns section summaries.

**CRITICAL — Book summary depth requirement:**

- Each chapter MUST get its own dedicated `## Chapter N: Title` section with a **substantial** summary (300-600 words per chapter depending on chapter length)
- Do NOT batch multiple chapters into a single brief paragraph — every chapter gets its own detailed treatment
- Include key arguments, data points, examples, and quotes from each chapter
- A 10-chapter book should produce ~3000-6000 words of summary content (excluding frontmatter/tldr)
- A 30-chapter book should produce ~5000-10000 words
- Think of each chapter summary as a standalone mini-essay that captures the chapter's core contribution
- The goal is that someone reading the summary should understand what each chapter argues, not just what the book is "about" at a high level

### Other files (txt, docx, etc.)

For `.docx`: `pandoc "<path>" -t markdown --wrap=none -o /tmp/summarize/doc.md`

For plain text: read directly.

### Pasted text / DEVONthink record

Read directly from user message or extract from DEVONthink via:

```bash
osascript -e 'tell application id "DNtp" to plain text of (get record with uuid "<UUID>")'
```

## Step 2: Determine output structure

Based on content type, choose the appropriate metadata:

| Content type     | DocumentType | Extra info for body                                         |
| ---------------- | ------------ | ----------------------------------------------------------- |
| YouTube video    | `Summary`    | `recording` URL, `views`, `channel`, `duration`, `uploaded` |
| Article / blog   | `Summary`    | `source` URL, `author`, `published`                         |
| Whitepaper / PDF | `Summary`    | `authors`, `affiliations`, `source`, `published`            |
| EPUB / book      | `Summary`    | `author`, `published` (year), `isbn`                        |
| Podcast episode  | `Summary`    | `recording` URL, `show`, `hosts`, `guests`, `duration`      |
| Lecture / talk   | `Summary`    | `speaker`, `recording` URL                                  |

The record gets `NeedsProcessing=1` and `NameLocked=1` when imported. The pipeline handles tags, summary metadata, archival, daily note linking, and wiki export.

## Step 3: Analyze structure, determine depth, and plan sections

Read the full extracted text. Identify the natural sections/chapters/topics.

### 3a. Determine summary depth from source length

Summary length must be **proportional** to the source material. A 10-minute video and a 3-hour documentary should not produce the same size summary. Use the source word count to determine the target summary word count:

| Source word count | Source examples                           | Target summary words | Sections | TLDR          |
| ----------------- | ----------------------------------------- | -------------------- | -------- | ------------- |
| <1,500            | 5-min video, short article                | 200–400              | 1–2      | 2 sentences   |
| 1,500–5,000       | 10–20 min video, blog post, short paper   | 500–1,200            | 3–5      | 3 sentences   |
| 5,000–15,000      | 30–60 min video, long article, whitepaper | 1,500–3,000          | 5–8      | 3–4 sentences |
| 15,000–40,000     | 1–3 hr video/podcast, long paper          | 3,000–6,000          | 8–15     | 4–5 sentences |
| 40,000–80,000     | Short book, multi-hour series             | 5,000–10,000         | 15–25    | 5 sentences   |
| 80,000+           | Full book (200+ pages)                    | 8,000–15,000         | 20–40    | 5 sentences   |

**The ratio is roughly 1:5 to 1:10** — a 10,000-word source should produce ~1,500–2,500 words of summary. Denser/more technical content skews toward the higher end; conversational/repetitive content skews lower.

**For videos/podcasts**, estimate source words from duration: ~150 words/minute for conversational, ~120 words/minute for interviews with pauses, ~170 words/minute for scripted/narrated content. Or just use the actual transcript word count.

**Per-section depth**: each section's word budget should be proportional to its share of the source material. A section covering 20% of the transcript gets ~20% of the summary word budget. Adjust up for particularly dense/important sections, down for filler/repetitive ones.

### 3b. Plan sections and dispatch

**For long content (>3000 source words):** dispatch parallel **Opus** subagents — one per section — to summarize simultaneously. Each subagent gets:

- The section text
- The audience level
- A **specific word count target** (calculated from 3a above)

**For short content (<3000 source words):** spawn a single **Opus** subagent to summarize the full content directly.

**When dispatching any subagent**, pass `model: "claude-opus-4-6"` to ensure Opus handles all summarization. **NEVER use Haiku or Sonnet.**

## Step 4: Assemble and import the summary note

### Structure

```markdown
> **TL;DR:**
> [Overview — sentence count per Step 3a depth table. What is it about, who made it, what are the key takeaways?]

## [Section 1 Title]

[Summary paragraphs]

## [Section 2 Title]

[...]
```

### Formatting rules

1. **No `# Title` heading** — the record name is the title
2. **`> **TL;DR:**`** blockquote for the overview (not Obsidian `[!tldr]` callout syntax)
3. **`> **Notable quote:**`** blockquotes for notable quotes (not Obsidian `[!quote]` callout syntax). Include speaker and source location if available
4. **Timestamps** on topic headings and quotes when available (YouTube, podcasts)
5. **Use actual Japanese/Chinese characters** for non-English words, not romanization

### Audience adaptation

- **High school / college student**: plain language, analogies, explain jargon inline
- **General reader**: balanced — explain key terms but don't over-simplify
- **Expert**: technical language fine, focus on novel contributions and critiques

### Importing to DEVONthink

Write the assembled markdown to a temp file, run `lint-markdown-file` on it so the summary arrives in house style, then import via AppleScript with `Recognized=1` and `Commented=1` pre-set. Pre-flagging keeps Extract: Native Text Bypass from matching the record and firing a mutation storm on it while DT's UI is still rendering the new arrival; the rule would set those same flags after running the same lint, so doing it here (before import) is equivalent and avoids the race:

```bash
# Pre-lint the summary file in place (installed by the DEVONthink dotfiles stow)
~/.local/bin/lint-markdown-file /tmp/summarize/summary.md

osascript << 'EOF'
set mdContent to do shell script "cat /tmp/summarize/summary.md"
tell application id "DNtp"
    set targetDB to database "Lorebook"
    set destGroup to get record at "/00_INBOX" in targetDB
    set newRecord to create record with {name:"Note Title (summary)", type:markdown} in destGroup
    set plain text of newRecord to mdContent
    add custom meta data 1 for "NeedsProcessing" to newRecord
    add custom meta data 1 for "NameLocked" to newRecord
    add custom meta data 1 for "Recognized" to newRecord
    add custom meta data 1 for "Commented" to newRecord
    add custom meta data "Summary" for "DocumentType" to newRecord
    add custom meta data "x-devonthink-item://SOURCE-UUID-HERE" for "SummarySource" to newRecord
    return "ok: " & (name of newRecord)
end tell
EOF
```

**`SummarySource` field** — always set this as an item link (`x-devonthink-item://<UUID>`) to the source record:

- **If `--dt-source <UUID>` was provided** (smart rule invocation): use that UUID directly.
- **Otherwise** (direct CLI invocation): import the source into DEVONthink first, then use the new record's UUID:

  ```bash
  # For URL-based sources: create a bookmark record
  osascript << 'EOF'
  tell application id "DNtp"
      set targetDB to database "Lorebook"
      set destGroup to get record at "/00_INBOX" in targetDB
      set srcRecord to create record with {name:"source title", type:bookmark, URL:"https://..."} in destGroup
      add custom meta data 1 for "NeedsProcessing" to srcRecord
      return uuid of srcRecord
  end tell
  EOF

  # For file-based sources: import the file
  osascript << 'EOF'
  tell application id "DNtp"
      set targetDB to database "Lorebook"
      set destGroup to get record at "/00_INBOX" in targetDB
      set srcRecord to import "/path/to/file.pdf" to destGroup
      add custom meta data 1 for "NeedsProcessing" to srcRecord
      return uuid of srcRecord
  end tell
  EOF
  ```

  The source record enters the pipeline alongside the summary.

Adjust `name`, `DocumentType`, and `SummarySource` per Step 2. Clean up `/tmp/summarize/` after import.

**Name conventions** — always append `(summary)` so the derived record is visually obvious alongside its source (mirrors the `(rewritten)` convention used by prose-check):

- DT record source (invoked via `--dt-source <UUID>`): `<source record's name> (summary)`
- YouTube/podcast: `Channel Name — Video Title (summary)`
- Books: `Author Name — Book Title (summary)`
- Articles/PDFs: `<title> (summary)`

## Model usage

| Task                  | Model                                 |
| --------------------- | ------------------------------------- |
| Content extraction    | Scripts (defuddle, pdftotext, yt-dlp) |
| Section summarization | **Opus** subagents (parallel)         |
| **NEVER**             | **Haiku or Sonnet**                   |

## Key rules

1. **No `# Title` headings** — the record name is the title
2. **Parallel Opus subagents** for long content — one per section
3. **Audience-appropriate language** — match the user's requested level
4. **Always set `SummarySource`** — item link to the source DT record; use `--dt-source` UUID if provided, otherwise import the source into DT first and use the new record's UUID
5. **`> **TL;DR:**`** blockquote is mandatory — every summary starts with a bold TL;DR overview blockquote. Do NOT use Obsidian callout syntax (`[!tldr]`, `[!quote]`, etc.) — DEVONthink doesn't render it
6. **Pipeline integration** — the record gets `NeedsProcessing=1` and `NameLocked=1`
