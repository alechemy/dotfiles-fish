# Audit record

Keep provenance, reviewed revisions, and decisions in the owning repository so later sessions can continue without private transcripts or temporary clones. Reuse an existing document rather than introducing a registry or changing every skill's metadata. Preserve historical decisions when updating it.

An existing prose report is sufficient if it contains the information below. Record missing evidence as unknown. Do not reconstruct a local snapshot after the fact and label it as the state originally reviewed.

## Per-skill evidence

Record:

- The local skill path and owning repository.
- The upstream author, repository, tracked branch or release channel, skill paths, required upstream skill dependencies, and license path.
- The original import revision or reconstructed baseline, with its evidence and uncertainty. Record the local import commit when known.
- The previous reviewed revision, fetched target, audit date, exact reviewed paths, and exclusions. Use full SHAs and immutable source links. A repository-wide head may be shared by several skills, but review coverage is per skill and path.
- The local state compared. Use the local commit plus clean/dirty status and a content manifest for dirty or untracked in-scope files. Identify deletions and symlink targets as well as file bytes. If approved edits follow, distinguish the reviewed state from the verified resulting state.
- The comparison result, validation performed, and coverage gaps. Failure to retrieve a source or an incomplete comparison must remain visible on the next run.

The original import baseline, latest reviewed upstream revision, and sources of selectively adopted changes are separate facts. Do not overload a single `revision` field to represent all three. An unchanged upstream skill directory can be reviewed at a newer repository head without changing its import baseline.

For downloads without a Git revision, record the exact source URL, version if available, retrieval date, and content digest. State whether the URL is mutable.

## Decisions

Use a table or short sections. The following is a template, not required literal headings:

```markdown
### <Skill name>: <change subject>

- The upstream evidence is <immutable file/diff link at full revision>.
- The affected local section or helper is <path and meaningful heading/function>.
- The proposal is <exact local change, or link to its before/after comparison>.
- The disposition is <proposed, approved, applied, retained, deferred, or superseded>.
- The reason is <user's decision and relevant local requirement>.
- Approval was <date and approved scope, or not yet given>.
- Verification is <checks and exact resulting state, or not applicable/pending>.
- Reconsider when <concrete change in evidence or requirements, if known>.
```

Use `applied` only after editing and checking the result. `Approved` does not mean implemented. `Retained` means the local behavior was deliberately kept. `Deferred` stays on the pending list. When a later decision replaces an earlier one, mark the earlier entry `superseded` and link the replacement rather than erasing its rationale.

Reference decisions by meaningful subjects, such as "retain automatic invocation", rather than mutable line or rule numbers. Numbered references in an old audit describe that historical version; they do not impose permanent IDs on the skill.

On the next audit, compare new evidence with each relevant decision. An unrelated upstream commit does not invalidate a rejection. Changed candidate text, an affected helper, new compatibility requirements, or a demonstrated safety issue may justify reconsideration. Show the new reason before asking again.

## Completion

Give every scoped skill a result, including unchanged and already-covered cases. Preserve exclusions, incomplete coverage, and unresolved decisions beside the latest reviewed marker. Report adopted changes separately from attribution-only repairs and deliberate local differences.

Before closing, check that the record answers what was fetched, what was compared, which local state was examined, what the user decided, what was actually applied, and what remains. Temporary checkout paths and this session's memory are not durable evidence.
