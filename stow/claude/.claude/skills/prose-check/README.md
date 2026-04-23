# prose-check

Claude Code skill that rewrites prose to remove LLM writing tropes and slop patterns. Takes a file path, DEVONthink record, URL, or pasted text and produces a new file or DEVONthink record with the cleaned prose. The original is never modified.

Rule set derived from the [slop-cop](https://github.com/awnist/slop-cop) detector (48 rules). Upstream source: [LLM_PROSE_TELLS.md](https://git.eeqj.de/sneak/prompts/src/branch/main/prompts/LLM_PROSE_TELLS.md).

## Install

```bash
cd ~/.dotfiles/stow && stow --restow --no-folding --ignore='.DS_Store' --target="$HOME" devonthink
```

Then use `/prose-check` in Claude Code.

## Usage

For a file:

```
/prose-check ~/Developer/blog/draft.md
```

For a DEVONthink record (via the smart rule, runs in the background):

1. Select a markdown/text record in DEVONthink.
2. Run the "Prose-check (On Demand)" smart rule.
3. Progress logged to `~/Library/Logs/prose-check.log`.
4. Output is created as a new record named `<Original Name> (rewritten)` in the **same group as the source**, linked back via the `RewriteSource` custom metadata field. The rewrite record is explicitly opted out of the Lorebook enrichment pipeline (`NeedsProcessing=0` and short-circuit flags set) so `lint-markdown`, `enrich-ai-metadata`, and `post-enrich-and-archive` do not touch it.

For a URL:

```
/prose-check https://example.com/my-article
```

Modes:

- Default (rewrite): produces a new file or DT record, no intervention, no report.
- `--mode review`: scan and report violations without writing a new file.
- `--aggressive`: also apply judgment-tier rules (metaphor crutch, balanced take, dead metaphor, etc.). Default skips these.

## Smart Rule Setup

Create an on-demand smart rule in DEVONthink:

- **Search in**: Lorebook (or the group where your drafts live)
- **Criteria**: Any of:
  - Kind is Markdown
  - Kind is Plain Text
  - Kind is Rich Text
  - Kind is Formatted Note
- **Trigger**: On Demand
- **Actions**: Run AppleScript (external) → `prose-check-on-demand.applescript`

## Requirements

- DEVONthink 4 must be running (for DT smart-rule invocation)
- CLI tools (checked on first run):
  - `defuddle` — web article extraction
  - `pdftotext` — PDF text extraction
  - `pandoc` — DOCX/EPUB conversion
- The Lorebook database with `00_INBOX` group (standard pipeline setup)

## Rule sync

The skill's rule list is regenerated from `src/rules.ts` in the [slop-cop](https://github.com/awnist/slop-cop) repo:

```bash
cd ~/Developer/slop-cop
pnpm tsx scripts/sync-prose-check-rules.ts
```

The script writes to the installed skill at `~/.claude/skills/prose-check/SKILL.md`. After running, copy the updated file back into this stow package and commit:

```bash
cp ~/.claude/skills/prose-check/SKILL.md ~/.dotfiles/stow/claude/.claude/skills/prose-check/SKILL.md
```
