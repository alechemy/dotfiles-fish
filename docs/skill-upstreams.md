# Shared skill sources and upstream audit

The repository maintains eight adaptations of published skills under [`stow/agents/.agents/skills/`](../stow/agents/.agents/skills/). Each carries source metadata and the upstream MIT license. Local instructions and helpers can differ substantially from upstream.

## Sources and attribution

The baseline links identify the upstream content used for comparison, not the current local contents.

| Local skill | Upstream author and source at baseline | Baseline evidence |
| --- | --- | --- |
| [`code-review`](../stow/agents/.agents/skills/code-review/SKILL.md) | Matt Pocock, [`code-review` at `3cca18b`](https://github.com/mattpocock/skills/tree/3cca18b368ae95cdbdebbff572ccafa662551015/skills/engineering/code-review). | The skill records this revision. |
| [`wizard`](../stow/agents/.agents/skills/wizard/SKILL.md) | Matt Pocock, [`wizard` at `3cca18b`](https://github.com/mattpocock/skills/tree/3cca18b368ae95cdbdebbff572ccafa662551015/skills/engineering/wizard). | The skill records this revision. |
| [`grill-me`](../stow/agents/.agents/skills/grill-me/SKILL.md) | Matt Pocock, [`grill-me`](https://github.com/mattpocock/skills/tree/6654f6b60cd9d5be8b54c6fafe44346dabeb3b76/skills/productivity/grill-me) and [`grilling`](https://github.com/mattpocock/skills/tree/6654f6b60cd9d5be8b54c6fafe44346dabeb3b76/skills/productivity/grilling) at `6654f6b`. | The skill records both sources and their revision. |
| [`handoff`](../stow/agents/.agents/skills/handoff/SKILL.md) | Matt Pocock, [`handoff` at `d28dfdc`](https://github.com/mattpocock/skills/tree/d28dfdc39beadc3142a33359b5cfa4765dcbd0bc/skills/productivity/handoff). | The baseline is reconstructed before local import `63d0dd8`. |
| [`unslop`](../stow/agents/.agents/skills/unslop/SKILL.md) | Lauren Tan, pstack [`unslop` at `bdf7aa3`](https://github.com/cursor/plugins/tree/bdf7aa355337897f167153e05069aca505dae17c/pstack/skills/unslop). | The skill records this revision and pstack version `0.14.3`. |
| [`teach`](../stow/agents/.agents/skills/teach/SKILL.md) | Lauren Tan, pstack [`teach` at `8f008c4`](https://github.com/cursor/plugins/tree/8f008c41981dca484d5beaf688d3aa2ea4ecf89b/pstack/skills/teach). | The baseline is reconstructed before local import `204226e`. This is not Matt Pocock's unrelated `teach` skill. |
| [`recall`](../stow/agents/.agents/skills/recall/SKILL.md) | Lauren Tan, pstack [`recall` at `e46364b`](https://github.com/cursor/plugins/tree/e46364b8be46000b7df0f260550cd712afbb8d36/pstack/skills/recall). | The baseline is reconstructed before local import `63d0dd8`. |
| [`code-tour`](../stow/agents/.agents/skills/code-tour/SKILL.md) | GitHub, Inc., [`code-tour` at `9ce8148`](https://github.com/github/awesome-copilot/tree/9ce814859eaa473178a1463ee3aa0c54a8860b86/skills/code-tour). | The skill records this revision. |

`handoff`, `teach`, and `recall` originally omitted attribution and import revisions. Their sources are supported by the original imported text and local commit history. Their reconstructed baselines are the last upstream commits touching the respective `SKILL.md` before the local import timestamp. They are useful comparison points, not proof of the exact upstream checkout originally used. Their metadata therefore uses `upstream-baseline` and `baseline-kind: reconstructed-before-import`, rather than claiming a known `revision`.

The upstream copyright notices are preserved in each skill's `LICENSE`: Matt Pocock for his four adaptations, Lauren Tan for the three pstack adaptations, and GitHub, Inc. for CodeTour. The [`simplify-review`](../stow/agents/.agents/skills/simplify-review/SKILL.md) workflow also uses `code-review/references/maintainability.md`, whose Pocock/Fowler attribution is covered by the code-review source and license.

## Audit on 2026-09-27

Fetched each repository's default branch, `main`, and compared immutable revisions. The reviewed heads were:

| Repository | Reviewed head |
| --- | --- |
| `mattpocock/skills` | [`c55ee46073ed923f86ce59a5eb3b6d895095d1b7`](https://github.com/mattpocock/skills/commit/c55ee46073ed923f86ce59a5eb3b6d895095d1b7). |
| `cursor/plugins` | [`ecc249f1e306fc64ddf83c7bed16cacf7c2239db`](https://github.com/cursor/plugins/commit/ecc249f1e306fc64ddf83c7bed16cacf7c2239db). |
| `github/awesome-copilot` | [`6c4d33b9cfca967a28bb2962ef4d55e4a384c88c`](https://github.com/github/awesome-copilot/commit/6c4d33b9cfca967a28bb2962ef4d55e4a384c88c). |

Comparisons covered the complete upstream skill directories, `grilling` as well as `grill-me`, and the relevant licenses. Local instructions were then checked to distinguish new upstream behavior from intentional local differences. The wizard template, CodeTour validator and schema, and all existing bundled licenses match the reviewed upstream bytes.

| Skill | Upstream changes since baseline | Recommendation |
| --- | --- | --- |
| `code-review` | The directory and license are unchanged. | Retain the local Correctness axis, exact comparison rules, evidence requirements, read-only boundary, and Hunk integration. |
| `wizard` | The directory, template, and license are unchanged. | Retain the local credential, approval, and template-compatibility safeguards. |
| `grill-me` | Both upstream directories and the license are unchanged. | Retain the combined Pi-compatible workflow. |
| `handoff` | The directory and license are unchanged from the reconstructed baseline. | Retain durable `.context/` storage, workspace headers, privacy rules, and review receipts. Upstream still uses OS temporary storage. |
| `unslop` | Upstream adds two rules, removes several older instructions, and changes invocation metadata. | Scoped versions of rules 32 and 33 are adopted, and "Adding soul" is replaced with "Preserve voice and meaning". Other differences and the decisions to retain local behavior are recorded below. |
| `teach` | Upstream trims repetition and changes punctuation. The investigation and teaching workflow is unchanged. | The user chose to retain the local version. It already condenses this guidance and replaces dependencies on pstack's `how`, `why`, and image-generation tools. |
| `recall` | Upstream trims repetition and changes punctuation. Transcript discovery, delegation, shared-record lookup, and output behavior are unchanged. | The user chose to retain the local version, including its bounded parser/filter, handoff-first lookup, explicit content scope, and metadata-only audit rules. |
| `code-tour` | The entire directory and license are unchanged, including files not vendored locally. | Retain the local passive-tour restrictions, pinned anchors, and behavior-oriented writing. |

### Unslop decisions

The substantive edits landed in [`e8d856f`](https://github.com/cursor/plugins/commit/e8d856f0273b42ebafe0ec3546bd645709e7c1b0) and [`70b2dc8`](https://github.com/cursor/plugins/commit/70b2dc8b4b85c8d5648624ca40d692c421fff32f). The current pstack manifest is `0.15.5`; that is the plugin version, not a separate Unslop release.

1. **Adopted rule 32, mannered prose, with narrower scope.** The local version rejects decorative metaphors, rhetorical fragments, personified code, and stock framing when direct wording is clearer. It explicitly permits an analogy when it explains the mechanism more clearly than a literal description. This extends rule 26 beyond metaphor nouns without banning useful teaching analogies.
2. **Adopted rule 33, over-compression, scoped to prose.** It rejects dropped articles, verbless fragments, arrows, and abbreviations that make readers decode prose. The prose scope preserves their use in diagrams, command examples, and reference tables. This supports the local preference for complete sentences and complements rules 28 through 31.
3. **Replaced "Adding soul" with "Preserve voice and meaning".** The local revision removes the invitations to "have opinions" and "let some mess in". It retains reasoned recommendations, concrete details, evidence-based uncertainty, readable sentence variation, and genuine first-person statements. It explicitly rejects manufactured emotion, opinions, informality, imperfections, and lived experience. The introduction and process step now refer to preserving voice and meaning. The self-audit remains.
4. **Rejected stable rule IDs by user decision.** No other repository skill currently references Unslop's rule numbers. Keep numbering sequential and organize rules for clarity; renumber when useful. Reconsider stable IDs only if actual cross-skill references create that requirement. The rule numbers used in this audit describe the versions reviewed here.

Other upstream edits and local decisions:

- **Retained rules 1, 2, 4, 6, and 21 by user decision.** Upstream removes these checks for puffery, name-dropping, promotional language, formulaic challenges, and cutoff disclaimers. The local fork keeps all five, with its existing safeguards for genuine uncertainty.
- **Retained rule 13's explanation by user decision.** Upstream removes the sentence explaining why substituting parentheses for em dashes defeats the intended purpose. The user sees little benefit in deleting that rationale and reports no known problems from retaining it.
- **Retained automatic invocation by user decision.** [`73f8be4`](https://github.com/cursor/plugins/commit/73f8be4873ea4ba2b7378243a036d3360c69e04d) adds `disable-model-invocation: true`; the local fork rejects this change. Global instructions continue to require loading Unslop before writing, and the skill remains advertised for automatic selection.
- Preserve the local first-person safeguard and revisions to rules 28 through 31. They explicitly prohibit invented lived experience, preserve necessary context and uncertainty, and prefer literal wording. An upstream file replacement would lose them.

The user approved the scoped versions of rules 32 and 33 on 2026-09-27, followed by the replacement for "Adding soul". These selectively adapt the reviewed `cursor/plugins` head recorded above. The user also chose to retain all five deleted rules and automatic invocation. The user chose clarity and sequential numbering over upstream's stable-ID policy. The user also chose to retain rule 13's explanation. All identified upstream differences have a recorded disposition; future audits should revisit retained differences only when new evidence or changed requirements warrants it. Unslop's `revision` and `upstream-version` still identify its original import baseline, not a full adoption of the reviewed upstream version.

## Unslop additions on 2026-10-14

The user approved small instruction improvements after comparison with JIA YI's MIT-licensed `agent-simple-english` 0.8.0 at [`ecd7b58d20f52d4f3f90aa632594478e68f3957d`](https://github.com/jyooi/agent-simple-english/tree/ecd7b58d20f52d4f3f90aa632594478e68f3957d). The relevant sources are its [phrasal-verb examples](https://github.com/jyooi/agent-simple-english/blob/ecd7b58d20f52d4f3f90aa632594478e68f3957d/src/dictionary/data/phrasal-verbs.json), [rule and feedback documentation](https://github.com/jyooi/agent-simple-english/blob/ecd7b58d20f52d4f3f90aa632594478e68f3957d/README.md), and [Pi adapter](https://github.com/jyooi/agent-simple-english/blob/ecd7b58d20f52d4f3f90aa632594478e68f3957d/src/extension/index.ts).

Applied to [`unslop/SKILL.md`](../stow/agents/.agents/skills/unslop/SKILL.md):

- Added plain-wording examples for "spin up", "dive into", and "circle back", conditional on preserving meaning.
- Made sentence length a readability cue.
- Scoped style corrections to the passages being changed, with surrounding context checked for coherence. Broader rewrites remain available when requested.
- Kept self-audits silent and made requested writing-review feedback specific to the text, problem, and proposed revision.

Existing safeguards for meaning, uncertainty, voice, and complete sentences remain. Automated gates remain deferred pending representative false-positive testing and Pi compatibility checks. The pstack import and reviewed revisions recorded above are unchanged.

The skill was clean at local commit `184bfdf097af5f815729e10295103782016cfb9b`, with SHA-256 `4f29f0421c69c6474d37ff5b8e1d3b96de55c18b28f79b9d53fe92c00b6d107d`. The first verified result has SHA-256 `692d8964e41c047ac6cd1c3b2edf971b604e455c46c4226b564aef85374a090f`. Verification covered the scoped diff, unchanged surrounding contents and frontmatter, sequential rule numbering, the installed skill symlink, and `git diff --check`. Model compliance remains unmeasured.

### Formulaic contrast follow-up

The user requested deletion by default because these sentences rarely add information. Expanded the rule to cover changed verbs and sentence fragments, added a final scan of paragraph and response endings, and limited rewriting to preserving necessary information. Accurate distinctions and substitutions such as "rather than" still require removing the stock construction. Deleted redundant contrast sentences from this audit record and removed the same framing from the sentence-length guidance.

The follow-up skill has SHA-256 `24f3777d3e137ad8384bfdc7632420490a2d5c3d8e594b0084716d1942f8e67c`. Verification covered the scoped diff, preserved frontmatter and rule numbering, the installed skill symlink, and `git diff --check`.

## Other skills and scope limits

No public-skill origin was identified in the inspected source metadata and repository history for `agent-browser`, `devonthink`, `jira`, `md-to-pdf`, `playlist-import`, `things`, or `worktrunk`. This is a provenance limit, not proof of independent authorship. `simplify-review` is a local workflow with the shared attribution noted above.

`agent-browser` intentionally reads version-matched instructions from its installed CLI rather than vendoring that guide. External CLI/package implementations, package-provided skills such as Context7 and pi-subagents, and the separately documented `agent-reader` overlay are outside this fork audit. Their maintenance is covered by [agent tooling maintenance](agent-tooling-maintenance.md).

## Repeating the comparison

Use the shared [upstream-maintenance skill](../stow/agents/.agents/skills/upstream-maintenance/SKILL.md). It reads this record, compares new upstream revisions with the local forks, shows proposed changes in context, and preserves these decisions. In Pi, run `/reload` after first installing the skill, then invoke it explicitly or request an upstream skill audit in ordinary language:

```text
/skill:upstream-maintenance Check all adapted skills in this repository for upstream changes.
```

The skill's [audit record reference](../stow/agents/.agents/skills/upstream-maintenance/references/audit-record.md) defines the evidence to preserve on subsequent audits. The audit above predates that format and has no exact local-content manifest; its immutable upstream comparisons and recorded decisions remain useful, but its historical local state must not be invented. The next audit should capture its own local state and carry these decisions forward.

Fetch upstream into a scratch checkout and pin the target to a full SHA. Use the last fully reviewed revision for new upstream changes, and the original import baseline to understand local adaptations. Include bundled helpers, referenced upstream skill dependencies, and licenses. To reproduce this audit's original Unslop comparison in a scratch clone of `cursor/plugins`:

```bash
git fetch origin bdf7aa355337897f167153e05069aca505dae17c ecc249f1e306fc64ddf83c7bed16cacf7c2239db
git diff bdf7aa355337897f167153e05069aca505dae17c ecc249f1e306fc64ddf83c7bed16cacf7c2239db -- \
  pstack/skills/unslop pstack/LICENSE pstack/.cursor-plugin/plugin.json
```

Compare the result with the local fork before recommending changes. Record the reviewed head separately from the imported baseline. Reviewing a newer revision alone is not a reason to advance an import pin. Keep adaptations selective so upstream changes do not remove local privacy, permission, or platform constraints.
