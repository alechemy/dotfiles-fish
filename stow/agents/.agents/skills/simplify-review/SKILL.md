---
name: simplify-review
description: Review a change for unnecessary complexity while preserving required behavior and operational constraints.
disable-model-invocation: true
---

# Simplify review

Review the named change without editing files. If no scope is supplied, use the change discussed in the current conversation. Ask when the scope is ambiguous; do not include unrelated working-tree changes.

Work in the current session. Delegate to an existing reviewer only when the user requests an independent opinion. Pass the review scope, requirements, and constraints explicitly.

Read the relevant implementation, callers, tests, and project constraints. Look for duplicated capabilities, speculative configuration, dependencies with suitable existing replacements, and abstractions that add no useful boundary.

Recommend a change only when it makes maintenance easier while preserving required behavior, validation, error handling, security, accessibility, and operational constraints. Follow the project's testing conventions. Do not treat fewer lines, one caller, or one implementation as sufficient evidence. Do not recommend reducing scope or deleting safeguards to make the change smaller.

For each finding, give the location, simpler alternative, evidence that it meets the requirements, and verification needed. State uncertainty when equivalence depends on an unverified assumption. Report only worthwhile simplifications, without a finding quota or line-deletion target.

If none qualify, say "No worthwhile simplifications found." This is a focused complexity review, not a correctness sign-off.
