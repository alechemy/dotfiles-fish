#!/usr/bin/env bash
# UserPromptSubmit / SessionStart hook: re-inject the standing comment policy so it
# survives long sessions and compaction (the durability gap CLAUDE.md alone leaves).
# Emits the additionalContext JSON form; arg 1 is the hook event name.

event=${1:-UserPromptSubmit}
policy_file="$HOME/.claude/comment-policy.md"

[ -f "$policy_file" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

jq -n --arg e "$event" --rawfile c "$policy_file" \
  '{hookSpecificOutput: {hookEventName: $e, additionalContext: $c}}'
exit 0
