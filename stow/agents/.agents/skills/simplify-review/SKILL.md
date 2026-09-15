---
name: simplify-review
description: Review a change for unnecessary complexity while preserving required behavior and operational constraints.
disable-model-invocation: true
---

# Simplify review

Review the named change without editing files. If no scope is supplied, use the change discussed in the current conversation. Ask when the scope is ambiguous; do not include unrelated working-tree changes.

Work in the current session. Delegate to an existing reviewer only when the user requests an independent opinion. Pass the review scope, requirements, and constraints explicitly.

Read the relevant implementation, callers, tests, and project constraints. Apply the shared [maintainability criteria](../code-review/references/maintainability.md). This focused command owns its scope and delegation policy; it does not run the full code-review workflow.

Use the shared criteria's evidence threshold for each finding. Report only worthwhile simplifications.

If none qualify, say "No worthwhile simplifications found." This is a focused complexity review, not a correctness sign-off.
