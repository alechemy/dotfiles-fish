#!/usr/bin/env bash
# UserPromptSubmit / SessionStart hook: re-inject the unslop rules so they apply to
# every reply rather than waiting to be invoked (the skill's "must always apply" is a
# description hint, which is not a mechanism). Body is read from the skill itself so
# the rules have one source. Arg 1 is the hook event name.

event=${1:-UserPromptSubmit}
skill_file="$HOME/.claude/skills/unslop/SKILL.md"

[ -f "$skill_file" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

body=$(awk 'BEGIN{fm=0} /^---$/{fm++; next} fm>=2' "$skill_file")
[ -n "$body" ] || exit 0

printf '%s' "$body" | jq -Rs --arg e "$event" \
  '{hookSpecificOutput: {hookEventName: $e, additionalContext: ("Standing prose policy — apply to every reply:\n" + .)}}'
exit 0
