---
name: teach
description: Explain a body of work plainly so a person understands what it is, how it works, and why it was built that way. Use for "teach me this", "help me understand X", or "explain this change or subsystem to me".
---

# Teach

Help the person understand the subject; do not change it.

1. Infer the few things they should leave understanding from their question, current task, and demonstrated knowledge. Do not quiz them for context already present in the conversation.
2. Investigate both how and why, sized to the question. Trace the code and runtime flow for how. Use focused `git log`, `git blame`, pull requests, linked tickets, and relevant comments for why. If the environment offers independent workers and the subsystem is too broad to inspect coherently in one pass, parallelize distinct slices; otherwise investigate directly. Never make parallel workers a prerequisite.
3. Separate evidence from inference. Cite the commit, pull request, ticket, or comment behind claims of intent. Hedge conclusions that the record does not establish.
4. Start with the smallest complete explanation: define the subject in plain terms and connect it to the case in front of the person. Then explain the mechanism, rationale, and relevant edge cases. Explain the flow rather than listing symbols.
5. Keep it conversational. Stop after a useful layer and follow the person's lead unless the invocation is one-shot. Use a diagram only when it explains the mechanism faster than prose; introduce complex systems through a short sequence of diagrams rather than one crowded figure.

Reply with the explanation itself, not a report about producing it.
