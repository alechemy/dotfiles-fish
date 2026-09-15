# Maintainability criteria

These criteria are shared by the full code review and the focused simplify-review skill. They define judgments, not review scope, delegation, or edit authority.

Look for duplicated capabilities, speculative configuration, dependencies with suitable existing replacements, and abstractions that add no useful boundary. Recommend a change only when it makes maintenance easier while preserving required behavior, validation, error handling, security, accessibility, and operational constraints. Follow the project's testing conventions.

Do not treat fewer lines, one caller, or one implementation as sufficient evidence. Do not recommend reducing scope or deleting safeguards to make a change smaller. Documented repository standards override the heuristics below. Skip anything already enforced by tooling.

## Smell baseline

Pocock's baseline draws on Fowler's code smells in *Refactoring*, chapter 3. Use each as a question to investigate, not an automatic instruction to introduce an abstraction or move code.

- Mysterious Name asks whether a name obscures the actual behavior or value.
- Duplicated Code asks whether repeated logic is genuinely the same responsibility and should change together.
- Feature Envy asks whether behavior repeatedly accesses another object's internals and belongs with that data.
- Data Clumps asks whether repeated groups of fields represent a coherent concept rather than coincidental parameters.
- Primitive Obsession asks whether a domain type would enforce a concrete invariant missing from a primitive.
- Repeated Switches asks whether repeated dispatch logic can share one representation without hiding useful differences.
- Shotgun Surgery asks whether one responsibility forces unrelated modules to change together.
- Divergent Change asks whether a module combines responsibilities that evolve independently.
- Speculative Generality asks whether configuration, hooks, or abstractions serve a current requirement.
- Message Chains asks whether callers depend on internal navigation they should not need to know.
- Middle Man asks whether delegation adds a useful boundary, policy, or contract.
- Refused Bequest asks whether inheritance forces implementations to ignore or contradict their inherited contract.

## Evidence threshold

For each worthwhile suggestion, give the reviewed location, simpler alternative, evidence that it preserves requirements, and verification needed. State uncertainty when equivalence depends on an unverified assumption. Label a smell as a possible smell and explain its concrete maintenance cost. A smell alone is not a blocker. Do not impose a finding quota or line-deletion target.
