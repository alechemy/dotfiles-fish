---
name: code-tour
description: Create short, code-anchored CodeTour walkthroughs for a commit, PR, changeset, or unfamiliar subsystem. Use when the user asks for a code tour or an editor walkthrough rather than a standalone explanation or bug review.
license: MIT
compatibility: CodeTour for VS Code or VSCodium, Git, and Python 3.
metadata:
  source: https://github.com/github/awesome-copilot/tree/main/skills/code-tour
  revision: 9ce814859eaa473178a1463ee3aa0c54a8860b86
---

# Code tours

Adapted from GitHub's `awesome-copilot` skill. The bundled validator and schema are unchanged upstream files at the revision above. Local instructions favor short, behavior-oriented walkthroughs and passive navigation. CodeTour extension `vsls-contrib.codetour` supplies the player.

Before writing a tour, read `../unslop/SKILL.md`, relative to this skill directory. Apply it to the tour titles and descriptions as well as the response to the user.

## Establish the comparison

Read the repository instructions, README, Git status, and the requested diff before choosing stops. Preserve existing work. Create only `.tour` files in the target repository; do not edit application code, settings, Git ignore rules, or agent instructions. Do not stage, commit, publish, or export embedded source unless requested.

For one commit, compare its parent with that commit, not the whole branch. Resolve both to full SHAs. Ask which parent to use for a merge commit. For a branch or PR, establish the intended base and compare its merge-base with the reviewed head. Record this exact scope in the tour description.

Pin committed tours with `ref` set to the full reviewed commit SHA. Read and verify anchors against that revision. Never switch branches or discard work to make the tour fit. A commit ref does not snapshot uncommitted edits: CodeTour can use editable working files when HEAD matches the ref. Check for changes to referenced files and flag any mismatch. For an uncommitted tour, explain that anchors can drift and must be regenerated after edits.

## Investigate, then choose the route

Read changed implementations, relevant callers, definitions, and tests. Use requirements supplied by the user to establish intended behavior; distinguish those requirements from inferred intent. Do not fetch unrelated tickets or private documents just to fill a tour.

Start with an orientation tour of roughly 6 to 8 stops. For a large changeset, add optional chapters of roughly 5 to 8 stops each. Group by behavior or a design decision, not alphabetical file order. Follow the route that makes this particular change understandable; there is no mandatory model-first or test-last order.

Open with a plain-language account of what changes for the user or system and why these files are involved. Explain that behavior before introducing callbacks, counters, providers, or other implementation details. Keep the exact comparison available without letting Git bookkeeping dominate the opening.

Include unchanged interfaces, callers, or fixtures when they explain a change. Label unchanged context explicitly; do not prefix every stop with "New code", "Modified behavior", or "New tests". Where it matters, explain the actual before/after difference instead. Put a relevant test near the behavior it exercises. Prefer representative paths to exhaustive coverage, but explicitly name omitted areas at the end. An orientation tour is not a completed review.

## Write the stops

Use `.tours/<descriptive-name>.tour`. Read [the schema](references/codetour-schema.json) before authoring. Use these fields by default:

- The root has `title`, `description`, `ref`, and `steps`.
- A source stop has `title`, `file`, `line`, and `description`.
- Paths are repository-relative, without a leading slash, `./`, or `..` components. Line numbers and selection coordinates are 1-based.
- Start on a real file or directory so the reader immediately sees code or repository structure.
- Optional `nextTour` values must exactly match another tour's title. Use them only for an intended sequence. For optional detours, CodeTour supports tour references such as `[Session lifecycle]` in Markdown descriptions.

Each source stop should explain why this code matters and point out the relevant detail. Prefer two or three sentences about one idea. Sixty words is a ceiling, not a target; do not compress several mechanisms into jargon to fit it. Do not force every stop into the same sentence pattern or add a transition sentence when the reading order already makes the connection clear.

Introduce a technical term through a concrete behavior or failure scenario, then name the corresponding identifier. For example, explain that signing out can overtake an unfinished sign-in before discussing epoch checks. Keep necessary technical distinctions, but do not assume that naming a mechanism explains it. Use titles that describe behavior rather than abstract categories.

Most stops should offer an explanation and a specific observation, not a quiz. Use at most one optional comprehension question per chapter by default, preferably at its end. Omit it when it adds little. Do not state the answer and immediately ask the reader to rediscover it, or ask vague questions about why something needs "different reasoning". Answers must be checked against code or tests, not treated as an authoritative model score.

Anchor each stop where its main claim is visible. Use a focused selection when useful. If a helper is essential, give it a direct stop or a link to another anchored stop; do not tell the reader to search a large file for it. If it is incidental, briefly explain its contribution without assigning another reading task.

Avoid praising the implementation, paraphrasing obvious syntax, or declaring correctness. Write as though showing a colleague the code, without inventing personal experience.

Keep tours passive. Do not emit `commands`, `view`, `when`, `stepMarker`, `command:` links, runnable `>>` shell links, environment interpolation, or embedded source. CodeTour supports executable interactions; a walkthrough does not need them. Do not insert source comments as anchors. Prefer verified line numbers over regex anchors for commit-specific tours.

Close with the changed areas not visited, any uncertainty, and optional chapter links. Keep detailed omission inventories out of the main reading path. Do not silently classify uninspected code as mechanical or safe.

## Validate

Run the bundled validator for every generated tour from the target repository root. Replace `<code-tour-directory>` with the absolute directory containing this `SKILL.md`; do not assume a particular agent's installation path. Use the available Python 3 command, such as `python3` or `python`.

```bash
python3 "<code-tour-directory>/scripts/validate_tour.py" .tours/<name>.tour --repo-root .
```

The validator checks working-tree paths, line bounds, patterns, and nearby `nextTour` titles. It does not validate against Git refs, enforce the full JSON schema, prove coverage, or assess factual accuracy. Its suggestions about content-only orientation steps are advisory; keep the opening anchored to a real file or directory.

Also verify every anchor by reading the referenced file at the pinned revision, check optional tour links, and confirm descriptions match the actual code. Bounds checks are not enough: the selected location must show the behavior discussed, not merely share its file. Check selection coordinates if used. If the working tree differs from the pinned revision, validate against an isolated copy of that revision rather than claiming the working-tree validator checked historical content.

Finish with a separate prose pass over every generated title and description, applying Unslop. Read the stops in playback order. Remove repeated labels, unnecessary instructions, obvious questions, and terminology introduced before its meaning. Split or relocate a stop if it still contains several ideas. Check that the overview explains the behavior before the machinery, questions are occasional and optional, and the reader does not need an unmentioned search to follow the explanation. Preserve factual precision during this pass.

Report the start tour's title and path, what it covers, and material omissions. In VS Code or VSCodium, open the repository root and run `CodeTour: Start Tour` from the Command Palette. The inline arrows advance or go back; `CodeTour: Resume Tour` returns after exploring other files.
