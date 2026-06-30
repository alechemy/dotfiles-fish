Standing comment & verbosity policy:
- Write NO code comments by default. Two exceptions: (a) a non-obvious WHY the code can't convey (a hidden constraint, a subtle invariant, a bug workaround) — one terse line; (b) an API/function/class doc where the project's precedent uses them — match that precedent's format (a multi-line JSDoc/docstring block if that's the norm) and update it when you change a documented symbol.
- Never narrate a change in a comment ("// now…", "// previously…", "// fix for…"), restate what the code already says, or put ticket/PR refs, dates, or changelog history in comments. Don't add or rewrite comments on code you didn't change.
- In replies, report what changed without narrating your process. Be concise.
