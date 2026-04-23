---
name: prose-check
description: Anti-slop prose rewrite/review tool covering 48 LLM writing tropes. Invoke only when the user explicitly uses the `/prose-check` slash command or says phrases like "slop check this," "run prose-check," "rewrite this for slop," or "anti-trope review."
user_invocable: true
---

# prose-check

Anti-slop pass for LLM writing tropes. Default mode rewrites a prose source to remove violations and writes the result as a new file or DEVONthink record, preserving the original. The skill carries the full 48-rule checklist used by the slop-cop detector.

## Configuration

```
DT_DATABASE = Lorebook        # DEVONthink database name (when source is a DT record)
DT_INBOX    = /00_INBOX       # Group for new output records
```

## Trigger

When the user provides prose to clean up: a file path (markdown, txt, docx, pdf), a DEVONthink record reference, a URL, or pasted text. Also on explicit invocation: "slop check this," "rewrite this for slop," "run prose-check."

## Inputs

- **Source**: file path, DEVONthink record UUID, URL, or pasted text.
- **`--dt-source <UUID>`** (optional): DEVONthink UUID of the source record, passed automatically by the on-demand smart rule. Used to set `RewriteSource` as an item link back to the original record.
- **`--mode <rewrite|review|pre-write>`** (optional): defaults to `rewrite`. Use `review` to get a report of violations with suggested fixes but no file output. Use `pre-write` to load the rule set into context before drafting new prose.
- **`--aggressive`** (optional): in rewrite mode, also apply judgment-required rules (metaphor-crutch, balanced-take, dead-metaphor, one-point-dilution, grandiose-stakes). Default is to apply only mechanical and low-risk rules and leave judgment rules untouched.
- **`--output <path>`** (optional, rewrite mode, file sources only): destination path for the rewritten file. Defaults to `<source>.rewritten.<ext>` next to the original.

## Modes

### 1. Rewrite (default)

Input → rewritten output, no intervention. Mirrors the summarize skill's pipeline.

1. Detect input type.
2. Extract text.
3. Dispatch an Opus subagent to rewrite the text against the rule set.
4. Write the output: new file next to the source, or new DEVONthink record in the source's parent group (explicitly opted out of the standard enrichment pipeline).
5. Exit. No report to the user.

### 2. Review

Input → report only, no file output. Used when the user explicitly asks for a review, or when the source is too short to justify a rewrite pass (<100 words). Flag every violation with rule name, offending text, and a concrete suggested rewrite, grouped by category.

### 3. Pre-write

Invoked explicitly (e.g. `/prose-check --mode pre-write`) before the user asks for long-form prose output. Reading this skill loads the full rule checklist into context. Apply the rules while drafting: no em-dashes, no rule-of-three constructions, no filler adverbs, no sycophantic openers, no vague attribution, no reflexive hedging, and the rest of the 48-pattern list below. After drafting, do a self-review pass against the checklist before presenting the draft. No output file is produced in this mode; the drafted prose is the output of the user's original request.

## Step 0: Bootstrap check (first run, rewrite mode with DT output)

Skip this step if the source is a plain file path and output is a file path. Run it only when DEVONthink is involved.

### 0a. Verify DEVONthink is running

```bash
osascript -e 'tell application id "DNtp" to name of databases' 2>/dev/null || echo "NOT RUNNING"
```

If DEVONthink isn't running, ask the user to launch it.

### 0b. Check required CLI tools

Only needed for non-plain-text sources:

```bash
for tool in defuddle pdftotext pandoc; do
  command -v "$tool" >/dev/null 2>&1 || echo "MISSING: $tool"
done
```

For each missing tool, ask before installing. Installs touch the user's system. If declined, warn that the corresponding source type will fail.

## Step 1: Detect input type and extract text

**IMPORTANT:** Do NOT use the Read tool on binary files (EPUB, DOCX, PDF). Convert them first.

### File path — markdown / txt

Read directly with the Read tool.

### File path — PDF

```bash
pdftotext "<path>" /tmp/prose-check/source.txt
```

### File path — DOCX / EPUB

```bash
pandoc "<path>" -t markdown --wrap=none -o /tmp/prose-check/source.md
```

### DEVONthink record (UUID or `--dt-source`)

```bash
osascript -e 'tell application id "DNtp" to plain text of (get record with uuid "<UUID>")'
```

### URL

```bash
defuddle parse "<URL>" --md -o /tmp/prose-check/source.md
```

Extract title, author, domain for output naming:

```bash
defuddle parse "<URL>" -p title
```

### Pasted text

Read directly from the user message.

## Step 2: Determine output destination

| Source type                 | Default output                                                          |
| --------------------------- | ----------------------------------------------------------------------- |
| File path (markdown/txt)    | `<source>.rewritten.<ext>` next to original                             |
| File path (PDF/DOCX/EPUB)   | `<source-stem>.rewritten.md` next to original                           |
| DEVONthink record (by UUID) | New DT record in the **parent group of the source**, linked to source   |
| URL                         | New DT record in `00_INBOX` (with pipeline opt-out flags — see Step 4)  |
| Pasted text                 | New file at `/tmp/prose-check/rewritten-<timestamp>.md`, print the path |

For DT records, use naming convention: `<original name> (rewritten)`. Never place the rewrite record in `00_INBOX` when the source is already in the database; `00_INBOX` is the entry point for the AI enrichment pipeline, which is not designed to handle rewrites and will erase the body content.

## Step 3: Dispatch Opus to rewrite

Dispatch a single **Opus** subagent with the full text and the rule checklist. For text longer than 8k words, chunk by top-level heading or paragraph boundaries (never mid-paragraph, never mid-sentence) and dispatch parallel Opus subagents per chunk, then concatenate in order.

**When dispatching, pass `model: "claude-opus-4-6"`. NEVER use Haiku or Sonnet for the rewrite pass.**

### Subagent prompt

Prime the subagent with this task spec:

```
You are rewriting prose to remove LLM writing tropes. The full rule
checklist is below. Your job:

1. PRESERVE the author's voice, argument, structure, tone, and any
   intentional stylistic choices. Do not restructure paragraphs. Do
   not rewrite the thesis. Do not add material. Do not "improve" the
   prose beyond removing the listed violations.
2. APPLY these rules to the text:
   [paste mechanical + low-risk rules — see checklist below]
3. IF --aggressive is set, also apply:
   [paste judgment rules]
4. OUTPUT: the full rewritten text and nothing else. No commentary,
   no diff, no "here is the rewritten version," no summary of
   changes. Just the clean prose.

A few specific constraints:
- Do not replace an em-dash with another em-dash. Use comma, colon,
  parentheses, or period depending on the relationship.
- Do not break semantic precision to avoid a pattern. If the rule-of-
  three is the natural enumeration, keep it; the goal is to break
  RHYTHMIC/DECORATIVE triples, not precise ones.
- Preserve code blocks, inline code, URLs, quoted speech, and block
  quotes exactly. Never rewrite inside these.
- Preserve markdown structure: headings, lists, tables, footnotes,
  links. Only rewrite the prose content.
- Preserve any domain-specific terminology the author is using
  deliberately, even if it looks like an "invented concept label."
  Only flag labels that feel decorative rather than load-bearing.

[FULL TEXT BEGINS]
...
[FULL TEXT ENDS]
```

### Rule tiers

<!-- TIERS:BEGIN (generated by scripts/sync-prose-check-rules.ts; do not edit by hand) -->

- **Mechanical** (apply by default, low risk of changing meaning): elevated register, filler adverb, "almost" hedge, "in an era of…", em-dash overuse, parenthetical qualifier, unnecessary contrast, "it's important to note", false conclusion, connector addiction, "serves as" dodge, "not x. not y. just z.", gerund fragment litany, superficial analysis, "here's the kicker", pedagogical aside, "imagine a world where", "despite its challenges", bold-first bullets, unicode decoration, throat-clearing opener, sycophantic frame, empathy performance, pivot paragraph, false vulnerability, fractal summaries.

- **Low-risk** (apply by default, minor judgment): overused intensifier, "broader implications", negation pivot, colon elaboration, question-then-answer, hedge stack, listicle instinct, anaphora abuse, vague attribution, listicle in a trench coat, dramatic fragment, triple construction, unnecessary elaboration, false range.

- **Judgment** (skip unless `--aggressive`): metaphor crutch, staccato burst, invented concept label, balanced take, grandiose stakes, historical analogy stack, dead metaphor, one-point dilution.
<!-- TIERS:END -->

## Step 4: Write the output

### File output (file path sources)

Write the rewritten text to the destination path. Preserve the original file untouched. No stdout report to the user beyond the output path being created.

### DEVONthink output (DT-record and URL sources)

**CRITICAL:** The rewrite record must be opted out of the standard Lorebook enrichment pipeline. It is a companion to an existing record, not a fresh import, and does not need AI enrichment, title syncing, or archival. Place it in the **parent group of the source record** (not `00_INBOX`) and set `NeedsProcessing=0` plus short-circuit flags. If the rewrite record enters the pipeline, `lint-markdown` and `post-enrich-and-archive` can race against DEVONthink's buffered `set plain text` write, causing the body to be erased and replaced with just the H1.

```bash
osascript << 'EOF'
set mdContent to do shell script "cat /tmp/prose-check/rewritten.md"
set sourceUUID to "SOURCE-UUID-HERE"
tell application id "DNtp"
    set sourceRecord to get record with uuid sourceUUID
    set destGroup to parent 1 of sourceRecord
    set newRecord to create record with {name:"Original Name (rewritten)", type:markdown} in destGroup
    set plain text of newRecord to mdContent

    -- Opt out of the standard Lorebook enrichment pipeline.
    -- Setting NeedsProcessing=0 (not empty) prevents mark-inbox-needs-processing
    -- from flipping it back to 1. The short-circuit flags stop other pipeline
    -- rules from matching.
    add custom meta data 0 for "NeedsProcessing" to newRecord
    add custom meta data 1 for "NameLocked" to newRecord
    add custom meta data 1 for "AIEnriched" to newRecord
    add custom meta data 1 for "Recognized" to newRecord
    add custom meta data 1 for "Commented" to newRecord

    add custom meta data "Rewrite" for "DocumentType" to newRecord
    add custom meta data ("x-devonthink-item://" & sourceUUID) for "RewriteSource" to newRecord
    return "ok: " & (name of newRecord)
end tell
EOF
```

**`RewriteSource` field** — always set as an item link (`x-devonthink-item://<UUID>`) to the source record:

- If `--dt-source <UUID>` was provided (smart rule invocation): use that UUID directly. The rewrite record is placed in the source's parent group.
- If source was a URL: import the URL as a bookmark record into DT first (see summarize skill for pattern), use the new UUID, and place the rewrite in the same group (typically `00_INBOX` for fresh URL imports; in that case, explicitly set `NeedsProcessing=0` and the short-circuit flags as above to keep the rewrite out of the pipeline).
- If source was a file path: skip DT output; write to disk instead.

Clean up `/tmp/prose-check/` after writing.

### Pasted-text output

Write to `/tmp/prose-check/rewritten-<timestamp>.md` and print only the output path to the user. No diff, no report.

## Voice preservation

Rewrite conservatively. The point is to remove slop, not to impose a house style. Specific guardrails:

- If the author uses em-dashes deliberately and frequently, flag it in review mode but ask before rewriting. In rewrite mode with no confirmation available, still replace (the rule is in the user's global CLAUDE.md; they have opted in).
- If a sentence has genuine nuance that reads like a "balanced take," leave it. The pattern to fix is reflexive immediate softening, not real engagement with counterarguments.
- If an analogy or metaphor is load-bearing to the argument, leave it even if it's mildly clichéd. Cut clichés that are decorative.
- Never change technical terminology, proper nouns, code identifiers, or quoted material.

If the text is <50 words, skip the rewrite and return the text unchanged. Short text rarely contains slop and the false-positive cost is too high.

## The rules

<!-- RULES:BEGIN (generated by scripts/sync-prose-check-rules.ts; do not edit by hand) -->

### Word choice

- **Overused Intensifier** — Words like "crucial," "vital," "robust," "leverage," "delve," etc. are LLM clichés that add noise. Delete it. If the sentence still makes sense, the word was never needed. If it doesn't, rewrite the sentence to show why it matters.
- **Elevated Register** — Using "utilize" instead of "use," "commence" instead of "start," "facilitate" instead of "help.". Replace with the simpler word. "Utilize" → "use." "Commence" → "start." Elevated register performs intelligence rather than demonstrating it.
- **Filler Adverb** — "Importantly," "essentially," "fundamentally," "ultimately," "inherently" signal importance without substantiating it. Remove it. If the sentence still works, the adverb was empty. If it doesn't work without it, the sentence needs to be rewritten to earn the emphasis.
- **"Almost" Hedge** — "Almost always," "almost never," "almost certainly" — hedging instead of committing to the pattern. Commit. "Almost always" → "usually." Or just say "always" and defend the claim. Readers notice when you won't take a stance.
- **"In an Era of…"** — Opening phrase that stalls before reaching the actual argument. Delete this clause entirely and start the sentence at the real point. "In an era of rapid change, companies must adapt" → "Companies must adapt."

### Framing

- **Metaphor Crutch** — Predictable metaphors: "double-edged sword," "tip of the iceberg," "north star," "game-changer," etc. Either find a specific, original image from the actual subject matter, or drop the metaphor and say the thing plainly.
- **"Broader Implications"** — Zooming out to claim significance without substantiation. State the implication explicitly, or cut the phrase. "This has broader implications" says nothing. What are the implications? Say them.
- **Invented Concept Label** — Compound noun + abstract suffix used as an invented analytical term: "the attention paradox," "the trust vacuum," "the context creep.". Either explain the phenomenon in plain terms or use an established name for it.
- **Grandiose Stakes** — Inflating the stakes of an ordinary argument to world-historical significance without substantiation. Scale the claim to match the evidence. If it's not world-historical, don't say so.
- **Dead Metaphor** — The same metaphor or image recurs throughout the piece, becoming mechanical rather than intentional. Keep the instance that earns its place most and cut the rest. The same image used five times is no longer a choice.

### Sentence structure

- **Em-Dash Overuse** — Em-dashes used as catch-all punctuation instead of choosing the right mark. Ask what relationship this dash is expressing. A pause → comma. A list → colon. A parenthetical → parentheses. A new sentence → period. Choose the right tool.
- **Negation Pivot** — "Not X, but Y" / "don't X, but Y" — negation followed by reframe. A hallmark LLM rhetorical structure. Rewrite as a direct positive claim. "We don't constrain through prohibition, but through amplification" → "We constrain through amplification." Lead with what is true, not what isn't.
- **Colon Elaboration** — Short declarative clause, colon, then longer explanation — a mechanical LLM sentence pattern. Either merge into one flowing sentence, or make two separate sentences. The colon-elaboration structure becomes predictable when used repeatedly.
- **Question-Then-Answer** — Rhetorical question immediately followed by its own answer. Delete the question and just make the statement. "What does this mean? It means X." → "This means X."
- **Staccato Burst** — Three or more consecutive very short sentences at matching cadence. Vary the rhythm. Combine some of these sentences, or expand one into a full thought. Uniform short sentences feel like a list of bullet points in disguise.
- **Hedge Stack** — Multiple hedges in one sentence: "perhaps," "arguably," "might," "could," "seemingly," etc. Pick one hedge if you need it, remove the rest. Better: commit to the claim and let the reader evaluate it. Five hedges communicate nothing.
- **Parenthetical Qualifier** — Parenthetical asides that perform nuance without changing the argument. Either make the qualification part of the main sentence (it's important enough to say plainly) or delete it (it wasn't needed).
- **Unnecessary Contrast** — "Whereas," "as opposed to," "unlike" used to restate what the first clause already implied. Delete the contrasting clause. If it adds information the reader didn't have, rewrite it as a direct statement rather than a contrast.
- **"Serves As" Dodge** — Replacing "is/are" with pompous alternatives: "serves as," "stands as," "acts as," "functions as.". Replace with "is" or "are." The pompous substitute performs sophistication without adding meaning.
- **"Not X. Not Y. Just Z."** — 2+ consecutive sentences starting with "Not " — building tension by negating before revealing. Cut the negations and state the positive claim directly.
- **Anaphora Abuse** — 3+ consecutive sentences starting with the same two-word opener. Vary the sentence openings. Anaphora becomes a tic when used more than twice.
- **Gerund Fragment Litany** — 2+ consecutive short sentences (≤8 words) starting with a capital -ing word. Expand these into full sentences or merge them. The gerund litany is a mechanical rhythm.
- **Superficial Analysis** — Trailing participle phrase claiming false significance: ", highlighting its importance," ", underscoring its role," etc. Cut the trailing phrase entirely. If the significance is real, make it a separate sentence with a specific claim.
- **Triple Construction** — Exactly three parallel items: "X, Y, and Z" — LLMs default to threes compulsively. Break the pattern. Use two items or four. Or convert one item into its own sentence to give it more weight.
- **Unnecessary Elaboration** — The sentence makes its point, then keeps going to restate it. Cut at the point where the sentence was done. The restatement dilutes the original impact.
- **False Range** — "From X to Y" constructions where X and Y aren't on a meaningful spectrum, or hollow idioms like "doesn't emerge from nowhere". Either show a real spectrum or cut the framing entirely.

### Rhetorical

- **"It's Important to Note"** — Verbal tic that precedes qualifications — tells the reader what to think before saying the thing. Delete the phrase and just say the thing. "It's important to note that X" → "X."
- **False Conclusion** — "At the end of the day," "in conclusion," "to summarize" — high-school essay signposting. Delete the phrase. The conclusion should land through its content, not be announced. If you need to say "in conclusion," the conclusion isn't clear enough.
- **Connector Addiction** — Every paragraph opened with a transition word: "Furthermore," "Moreover," "Additionally," etc. Delete the transition and let the ideas connect through their content. If the connection isn't obvious without the word, restructure — don't signal.
- **"Here's the Kicker"** — False suspense transitions: "here's the kicker," "here's the thing," "here's where it gets interesting," etc. Delete the transition and state the point directly.
- **Pedagogical Aside** — Teacher-mode phrases: "let's break this down," "let's unpack," "think of it as," etc. Skip the preamble and explain the thing directly. The reader doesn't need to be managed.
- **"Imagine a World Where"** — Opens with a futurist invitation: "Imagine a world," "Imagine if you," "Imagine what would," "Imagine a future.". Start with the actual argument instead of inviting the reader to imagine it.
- **Vague Attribution** — Unnamed authority invocations: "experts argue," "studies show," "observers have noted," "research suggests," etc. Name the experts, cite the studies, or drop the claim. Vague attribution is worse than no citation.
- **"Despite Its Challenges"** — Formula: "Despite [these/its/the] [challenges/obstacles/limitations]..." — conceding without substance. Name the specific challenge. "Despite its challenges" is a throat-clear disguised as an acknowledgment.
- **Throat-Clearing Opener** — First paragraph that adds no information and could be deleted without any loss. Delete the whole paragraph and start at the second one. The real piece almost always begins at the second paragraph.
- **Sycophantic Frame** — Opening that compliments the question or topic before addressing it. Delete the compliment entirely. "Great question! X is important because…" → "X is important because…"
- **Balanced Take** — Every argument immediately followed by a concession that softens it to nothing. Make the argument. Acknowledge genuine counterarguments separately and specifically. Don't reflexively soften every claim — it reads as epistemic cowardice.
- **Empathy Performance** — Generic emotional language ("I understand this can be difficult") applicable to any topic. Delete it, or replace with something specific to this exact situation. Generic empathy is indistinguishable from no empathy.
- **Historical Analogy Stack** — Rapid-fire listing of famous companies or tech revolutions to build false authority by association. Pick one analogy and develop it. A list of analogies proves nothing — it just borrows the aura of many things.
- **False Vulnerability** — Performative self-awareness or simulated honesty ("I'll be honest," "Let's be real") that reads as staged rather than genuine. Real vulnerability is specific and uncomfortable. If it sounds polished, cut it.

### Structural

- **Listicle Instinct** — Lists with exactly 3, 5, 7, or 10 items — LLMs default to these magic numbers. Ask if this really needs a list. If the items have natural prose flow, write them as prose. If it is a list, let it have the number of items it actually has — 4, 6, 9, whichever.
- **Listicle in a Trench Coat** — "The first... The second... The third..." — prose disguising a list. Either use an actual list or rewrite as genuine prose. Don't pretend a list is an argument.
- **Bold-First Bullets** — Markdown list items starting with a bolded phrase: "- **Term**: explanation.". Either use a definition list format or integrate the bold label into prose. This is pure LLM document structure.
- **Unicode Decoration** — The → arrow character used in prose as a decoration or shorthand. Write out the relationship. "Input → Output" → "Input produces Output."
- **Dramatic Fragment** — A standalone paragraph with ≤4 words — used for false dramatic emphasis. Either expand it into a real sentence or absorb it into the surrounding paragraph.
- **Pivot Paragraph** — A one-sentence paragraph containing no new information, only transition. Delete it. Attach the transition thought to either the paragraph before or after, or cut it entirely — the surrounding content should do this work.
- **One-Point Dilution** — The same core argument restated across multiple paragraphs with different words but no new information. Find where the point was made best and cut every restatement. A strong claim once beats the same claim eight times.
- **Fractal Summaries** — Meta-commentary that previews or recaps content rather than delivering it: "In this section we'll explore...", "As we've seen...". Delete the signpost and say the thing. The content should do this work.
<!-- RULES:END -->

## False-positive notes

- **Dramatic fragment**: intentional short paragraphs in minimalist prose are fine. Flag only when short because it's performing emphasis.
- **Triple construction**: a genuine three-item enumeration is fine. Flag when the three feel chosen for rhythm.
- **Balanced take vs. genuine nuance**: real counterarguments deserve a sentence. Flag reflexive softening that negates the original claim.
- **Vague attribution**: "research suggests" is fine when followed by a citation.
- **Metaphor crutch / invented concept label**: leave load-bearing imagery and domain-specific terminology. Cut decorative or inflationary uses.

## Review mode output format

Only used when `--mode review` is set. Otherwise the skill produces no report.

```
## Prose-check review

### Word choice
- **Elevated register** — "We utilized the framework to facilitate..." → "We used the framework to help..."

### Sentence structure
- (none found)

### Rhetorical
- **Balanced take** — "X is valuable, though its value depends on context..." → make the claim; handle the caveat separately if it matters.

### Structural
- (none found)

### Overall
Minor cleanup needed: 3 word-choice fixes, 1 rhetorical fix. Structure is clean.
```

## Model usage

| Task                      | Model                                      |
| ------------------------- | ------------------------------------------ |
| Text extraction           | Scripts (defuddle, pdftotext, pandoc)      |
| Rewrite pass              | **Opus** subagent (parallel for >8k words) |
| Review pass (review mode) | Opus inline (no subagent)                  |
| **NEVER**                 | **Haiku or Sonnet**                        |

## Key rules

1. **Preserve the original.** Never edit the source file in place. Always produce a new file or DT record.
2. **No unprompted report.** In rewrite mode, the output is the rewritten text. Do not print a summary, diff, or changelog to the user unless explicitly asked.
3. **Voice preservation.** Rewrite conservatively. Remove slop; do not impose a house style. Do not restructure paragraphs or rewrite the thesis.
4. **Opus only** for the rewrite and review passes.
5. **Default rule tier is mechanical + low-risk.** Judgment rules require `--aggressive`.
6. **Cross-mode consistency.** Review and rewrite modes use the same rule set and same definitions; the only difference is whether the result is a report or a rewritten file.

## Reference

The underlying rule set comes from the slop-cop detector: https://github.com/awnist/slop-cop (source of truth is `src/rules.ts`). Upstream rule list: https://git.eeqj.de/sneak/prompts/src/branch/main/prompts/LLM_PROSE_TELLS.md.

If rules in `src/rules.ts` diverge from this file, treat `src/rules.ts` as authoritative and resync.

## Related skills

- `summarize` — same architectural pattern (source → new DT record). Good reference for the DEVONthink smart-rule invocation flow.
