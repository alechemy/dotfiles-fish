---
name: unslop
description: Cut AI tells from any writing. Must always apply.
license: MIT; see LICENSE
compatibility: Agent Skills-compliant; designed for Pi and usable by compatible harnesses.
metadata:
  source: https://github.com/cursor/plugins/tree/main/pstack/skills/unslop
  revision: bdf7aa355337897f167153e05069aca505dae17c
  upstream-version: "0.14.3"
  supplemental-source: https://github.com/razakiau/skills/tree/main/skills/no-slop
  supplemental-adopted-revision: 82016fbe775447997f3df6501bf43d71723188bd
---

<!-- markdownlint-disable MD013 MD029 -->

# Unslop

Edit text to remove AI patterns while preserving voice and meaning.

## Process

1. Scan for the patterns below at sentence, paragraph, and section level. Check for contrasts split across sentences, redundant negative clarifications, repeated claim-example-lesson sequences, and repeated closers. Preserve purposeful repetition and structures required by the format.
2. Rewrite. Preserve meaning, match intended tone.
3. Preserve voice and meaning (see next section).
4. Self-audit: "What makes this obviously AI generated?" Fix remaining tells. Before sending, scan for formulaic contrast, especially at paragraph and response endings. Default to deleting the sentence.
5. Check the final sentences for unrequested caveats and status bookkeeping. Delete them unless they materially qualify a result, identify an unmet requirement, or affect the reader's next decision. Stop when the useful information is complete.

Keep the self-audit silent unless the user asks for a writing review. For review feedback, quote the affected text or give its location, name the problem, and suggest a concrete revision. When asked only to detect or audit patterns, report findings without rewriting the whole text. Describe observable patterns without assigning an AI score or claiming who wrote the text.

When editing an existing document, focus style corrections on the passages being changed. Check surrounding text for coherence, but leave unrelated wording alone unless a broader rewrite is requested.

## Preserve voice and meaning

Preserve the source's intended tone and point of view. Give recommendations when useful and explain their reasoning. Use concrete details and acknowledge uncertainty where the evidence requires it. Vary sentence length when it improves readability. Do not manufacture emotion, opinions, informality, or imperfections to sound human. Preserve genuine first-person statements, but never invent lived experience.

When editing for style, preserve negation, conditions, scope, uncertainty, attribution, and obligation strength. Keep quantities and units attached to the same subjects and claims. Preserve code, commands, paths, identifiers, citations, and verbatim quotations exactly unless the user explicitly requests changes to them. These preservation requirements take precedence over the style patterns below.

Never invent a number, example, mechanism, causal explanation, or personal experience to make prose more concrete. Use only supplied or verified details. If a necessary detail is missing, keep the uncertainty or ask for that detail when it blocks the task.

Repair only passages with an identifiable clarity or style problem. Leave acceptable passages unchanged, and return an already-good text unchanged. Do not replace filler with clipped fragments or decorative plain-sounding prose.

## Patterns to detect and fix

### Content

1. **Puffery.** "pivotal moment", "testament to", "evolving landscape", "setting the stage for", "indelible mark", "deeply rooted". Cut puffery, state what happened.
2. **Name-dropping.** Listing media outlets without context. Pick one, say what was said.
3. **Superficial -ing phrases.** "highlighting...", "ensuring...", "reflecting...", "showcasing...", "fostering...". Delete or expand with real sources.
4. **Promotional language.** "nestled", "vibrant", "breathtaking", "groundbreaking", "renowned", "stunning", "must-visit". Use neutral descriptions.
5. **Vague attributions.** "Experts believe", "Industry reports suggest", "Some critics argue". Name the source or delete.
6. **Formulaic challenges.** "Despite challenges... continues to thrive." Replace with specific facts.

### Language

7. **AI vocabulary.** Additionally, crucial, delve, enduring, enhance, fostering, garner, interplay, intricate, landscape (abstract), pivotal, showcase, tapestry (abstract), testament, underscore, vibrant. Replace with plain words.
8. **Fancy ways to say "is".** "serves as", "stands as", "boasts", "features". Just say "is" or "has".
9. **Formulaic contrast.** Do not use "Not just X, but Y", "This is X, not Y", "These remain X, not Y", "It's not X, it's Y", or fragments such as "Instructions, not enforcement." Delete the sentence by default. These sentences often repeat a point or add an unnecessary closing caveat. Rewrite only if deletion would lose necessary information, placing that information directly where it belongs. An accurate distinction does not justify the construction. Changing the verb or substituting "rather than" does not fix the same rhetorical pattern.
10. **Rule of three.** Forcing ideas into groups of three. Use the natural number.
11. **Synonym cycling.** Protagonist, main character, central figure, hero all in one paragraph. Pick one, repeat it.
12. **False ranges.** "from X to Y" where X and Y aren't on a meaningful scale. List topics directly.

### Style

13. **Em dash and semicolon overuse.** Avoid em dashes entirely. Avoid semicolons in prose except to separate list items that contain internal commas. Never use a semicolon as an em dash replacement or to join independent clauses. Split those clauses into separate sentences. For other sentence breaks, use periods or grammatically appropriate commas, not parentheses, en dashes, or hyphens as dash substitutes.
14. **Colon overuse.** Colons are fine before a list or example. Not as mid-sentence connectors. "If you're coming from traditional automation: instead of registering event handlers, you describe conditions" adds nothing with the colon. Rewrite to let the point stand on its own without comparison framing. "Describing when the scheduler should fire works best as plain English." Same meaning, no crutch punctuation.
15. **Boldface overuse.** Don't bold every proper noun or acronym.
16. **Inline-header lists.** The tell is a bold label and colon that restates the line: "**Performance:** Performance improved...". Convert those to prose. A bold lead-in that ends in a period, names the item, and is followed by genuinely new detail ("**Schema in TypeScript.** Tables live in one file.") is fine, not a tell.
17. **Decorative and redundant headings.** Use sentence case and name what the section contains. Cut a heading that merely repeats the document title and an opening sentence that merely repeats its heading. Replace headings written for effect with descriptive ones. Keep short sections when their headings help readers find information.
18. **Decorative emojis.** Remove from headings and bullets.
19. **Curly quotes.** Replace with straight quotes.

### Communication artifacts

20. **Chatbot phrases.** "I hope this helps!", "Let me know if...", "Of course!", "Certainly!", "Found the smoking gun!" Remove.
21. **Cutoff disclaimers.** "While specific details are limited..." Find sources or remove.
22. **Sycophantic tone.** "Great question! You're absolutely right!" Respond directly.

### Filler

23. **Filler phrases.** "In order to" becomes "To". "Due to the fact that" becomes "Because". "It is important to note that" gets deleted.
24. **Excessive hedging.** "could potentially possibly be argued that it might" becomes "may".
25. **Redundant endings.** Delete a recap, lesson, or emphatic closer that adds nothing: "That is the real win", "That distinction matters", or a paragraph restating the preceding example. Do not replace it with a better metaphor or a new slogan. End on the last useful point or next action. Keep summaries the user requests or readers need to navigate a long document.

### Jargon

26. **Abstract metaphor nouns.** Substrate, wedge, vector, locus, vantage, nexus, primitive (as noun), harness (as metaphor), surface (as in "API surface"), bedrock, scaffolding (as metaphor), modality, paradigm, gold-plating, ratchet (as metaphor), evacuate (for moving code), endgame, north star, flywheel. These read as technical but usually have a plainer concrete word. "Substrate" becomes "base". "Wedge in" becomes "add". "Vector" becomes "way" or "method". "Gold-plating" becomes "more than the job needs". "Ratchet" becomes the mechanism's real name or "a limit that only tightens". "Evacuate" becomes "move out". "Endgame" becomes "the last phase". Pick the concrete word.

### Plain speech

27. **Say what it does, not how it feels.** "the database stays close at hand", "SQL you can read", "types that follow your schema" name a feeling. The fix names the mechanism or a number: "`.toSQL()` returns the exact string sent to the database", "a column rename fails the build". Ask what the sentence tells the reader to do or know, then write that. If you can't restate it as a concrete instruction, fact, or number, cut it. One more check: if the sentence could appear unchanged in another project's docs, it says nothing about this one. Cut it.
28. **Shorten or split dense sentences.** If the reader has to backtrack to parse a sentence, break it in two or drop unnecessary clauses. Use sentence length as a readability cue. Avoid fixed word limits. Preserve the actor, sequence, and necessary reason even when that takes more words.
29. **Name the actor and action.** Prefer active voice. "Queries are validated" becomes "the compiler validates queries". Passive is fine when the actor is unknown or genuinely doesn't matter. Don't disguise instructions as facts. "The webhook URL stays out of chat" becomes "The webhook URL is sensitive, so don't paste it into our chat." When explaining a prohibition, put the reason first to avoid an ambiguous "don't ... because ..." construction.

    In PR descriptions, commit messages, changelogs, and progress updates, attribute edits to their author, explicitly or implicitly. Don't describe components as moving code, refactoring themselves, or redesigning their own APIs. Conventional change summaries may omit "I", as in "Refactored the test suite to share fixture setup." Preserve the intended point of view and the project's commit style; don't insert "I" into every item or invent who made a change.

    | Counter-example | Better |
    | --- | --- |
    | "The screen moves `pt-safe` out of `ResultList`." | "Moved `pt-safe` from `ResultList` to the screen container." |
    | "The test suite refactors its fixtures to share setup." | "Refactored the test suite to share fixture setup." |
    | "`ApiClient` extracts its retry loop into a helper." | "I extracted `ApiClient`'s retry loop into a helper." |

    Code can still be the actor when describing execution. Keep literal statements such as "The parser rejects malformed input" and "The list forwards `onPress` to each row". Even "The codemod renames deprecated method calls" is accurate if that is what the tool does when run. Distinguish runtime or tool behavior from authorship of the changes under discussion.

30. **Cut adverbs, or use a more precise verb.** "runs quickly" becomes "is fast" or the number. "significantly improves" becomes the measured delta. An adverb propping up a weak verb means the verb is wrong.
31. **Prefer plain, literal wording.** "utilize" becomes "use", "leverage" becomes "use", "facilitate" becomes "help". When the meaning is unchanged, prefer "start" to "spin up", "examine" to "dive into", and "return" to "circle back". Avoid brisk shorthand that obscures the action. "Once the card arrives" becomes "Once the script creates the card" when creation is what matters. Preserve the distinction between evidence and interpretation. "Logcat shows Android killing apps" becomes "In logcat, there are lines indicating that Android is killing apps to free up memory." Choose clarity over the shortest or most vivid verb.
32. **Mannered prose.** Avoid decorative metaphors, rhetorical fragments, personified code, and stock framing phrases when direct wording is clearer. Name the actor and action. Keep an analogy only when it explains the mechanism more clearly than a literal description.
33. **Over-compression.** In prose, avoid dropped articles, verbless fragments, symbol-speak, and abbreviations that make the reader decode instead of read. "Parser rejects bad date → exit 2, no write" becomes "The parser rejects a bad date, exits with code 2, and writes nothing." Write whole sentences with their articles and verbs in ordinary prose, and spell out arrows and abbreviations. Commit messages and change summaries may omit an implied author, as described in rule 29.
34. **Closing caveat inventories.** Do not append lists of what remains unverified, was not tested, was untouched, or needs clarification merely to sound thorough. "Saves were untouched; gameplay and save compatibility were not tested" adds nothing when the task did not involve saves or gameplay. Delete it. Keep a limitation when omitting it would mislead the reader about the requested outcome, including failed checks or incomplete requested verification. Put it beside the affected claim and state the consequence. If a missing decision blocks the task, ask the specific question where it arises instead of ending with "requirements need clarification". Do not hide a real blocker, imply unperformed verification, or add a generic disclaimer after an otherwise complete answer.
35. **Staged introductions and imaginary objections.** Cut introductions such as "Here's the thing", "What most people miss", and "Let me be clear" when they only announce the point. Remove self-answered rhetorical questions and defenses against objections that the reader has not raised and has no reason to consider. State any necessary claim directly. Preserve questions, concerns, and alternatives that help the reader learn or decide.

    **Redundant negative clarification.** After giving a sufficient instruction or explanation, stop. Delete follow-up sentences that negate an unstated alternative, repeat an already-clear condition, or disclaim an unrequested requirement. "Install the new version, then open it. Reopening the old version alone does not install an update" becomes "Install the new version, then open it." Keep a qualification when it answers an explicit question, resolves genuine ambiguity, or prevents a specific consequential mistake established by the task context. Place essential conditions in the instruction itself. A technically accurate caveat does not automatically deserve inclusion.

36. **Re-explaining known context.** In a reply, lead with the answer or decision. Omit background that the visible conversation shows the reader already has. Keep necessary rationale, requested evidence, and consequential limitations. Do not assume shared context when it is unavailable. Standalone documents and PR descriptions need enough context for their intended readers.
