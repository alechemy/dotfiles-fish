# slop-review

A `/slop-review` skill: reviews a codebase's **prose surface** — READMEs, docs, ADRs, comments, public strings, package metadata — for the residue of agent-assisted authoring.

It catches what `/code-review` (correctness) and `/prose-check` (deep prose-trope rewrite) don't:

- **Audience leaks** — agent-process or model-meta text ("training data", "the maintainer publishes", "session scratchpad") that escaped into human-facing docs. Audience is judged by the file's purpose: the same line is fine in `AGENTS.md`, slop in `README.md`.
- **Preemptive over-signal** — sentences that defend an unchallenged boundary or manufacture closure ("Two packages, nothing else.", "no Turborepo, no web").
- **Internal/private leakage** — names, ticket IDs, roadmap items, org politics. Flagged regardless of audience, including agent-facing files.
- **Inaccuracy** — versions, paths, commands, and claims that drift from the lockfile / `package.json` / filesystem. Verified, not guessed.
- **Redundancy/drift**, **comment noise** (source *and* config dotfiles / CI YAML), **scaffold leftovers**, **marketing filler**, and **LLM-generation detritus** (stray wrapper tags, tool-call preambles).

Default mode reports findings (Confirmed / Optional / Judgment call) with `file:line` and exact recommended changes. `--fix` applies the Confirmed ones; `--diff` scopes to the working tree.

The full lens and method live in `SKILL.md`.
