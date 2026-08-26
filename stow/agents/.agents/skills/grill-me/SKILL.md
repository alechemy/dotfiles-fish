---
name: grill-me
description: A relentless interview to sharpen a plan or design.
disable-model-invocation: true
license: MIT; see LICENSE
compatibility: Agent Skills-compliant; adapted for Pi's explicit skill commands and built-in tools.
metadata:
  source: https://github.com/mattpocock/skills/tree/6654f6b60cd9d5be8b54c6fafe44346dabeb3b76/skills/productivity/grill-me
  revision: 6654f6b60cd9d5be8b54c6fafe44346dabeb3b76
  upstream-dependency: https://github.com/mattpocock/skills/tree/6654f6b60cd9d5be8b54c6fafe44346dabeb3b76/skills/productivity/grilling
---

# Grill me

Interview the user until you reach a shared understanding. If the invocation includes arguments, use them as the subject. Otherwise infer the subject from the current conversation.

Map the subject as a **design tree**. Every decision branches into the decisions that depend on it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled, meaning the questions you can ask now without guessing at answers you have not heard yet. Ask the whole frontier in one round. Number each question and give your recommended answer. Then wait for the user's answers before the next round.

Format a round like this:

```markdown
**Q1 - <question title>:** <question body, which may include several choices>

Recommendation: <your recommended answer>

---

**Q2 - <question title>:** <question body, which may include several choices>

Recommendation: <your recommended answer>
```

Each round reshapes the tree. Settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier after every answer. A question whose answer depends on another question still open in the current round belongs to a later round.

Finding facts is your job, never the user's. Use the available tools to inspect the environment instead of asking for facts you can find. Run independent lookups in parallel when practical. Research any prerequisite facts before asking the questions that depend on them, but ask the rest of the frontier without delay. Do not mutate the environment while researching.

The decisions belong to the user. Put each decision to them and wait. Do not answer on their behalf.

The session is done when the frontier is empty. Ask the user to confirm that you have reached a shared understanding. Do not act on the result until they confirm.
