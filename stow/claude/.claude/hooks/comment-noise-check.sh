#!/usr/bin/env bash
# PostToolUse (Edit|Write) backstop against comment noise.
# Flags newly-written code comments that look like changelog narration, ticket
# references, or jargon, and feeds them back to the model for review.
# Non-blocking: emits additionalContext only when it finds something; silent otherwise.

input=$(cat)

file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$file" in
  *.ts | *.tsx | *.js | *.jsx | *.mts | *.cts) ;;
  *) exit 0 ;;
esac

# new_string (Edit), content (Write), or every edits[].new_string (MultiEdit, which the
# unanchored Edit|Write matcher also fires on) — joined so a MultiEdit isn't a blind spot.
content=$(printf '%s' "$input" | jq -r '[.tool_input.new_string, .tool_input.content, (.tool_input.edits[]?.new_string)] | map(select(type == "string")) | join("\n")' 2>/dev/null)
[ -z "$content" ] && exit 0

# Comment-ish lines (//, /*, or a JSDoc continuation "* ...") that also carry a
# high-signal narration / ticket-ref / jargon marker. Ticket pattern is
# case-sensitive (DD-1324, not utf-8); phrases match either case on the first letter.
hits=$(printf '%s\n' "$content" \
  | grep -nE '(//|/\*|^[[:space:]]*\*)' \
  | grep -E '[A-Z]{2,}-[0-9]{2,}|[Aa]dded in|[Pp]reviously|[Uu]sed to|[Ww]e now|[Nn]o longer|[Rr]enamed|[Cc]hangelog|[Pp]osterior' \
  2>/dev/null)

[ -z "$hits" ] && exit 0

reason="Comment(s) just written to ${file} look like changelog narration, ticket references, or jargon. Re-check each against the no-comment-noise rule — don't narrate the change, no ticket refs in comments, don't restate the code; reserve comments for API docs or genuinely non-obvious rationale — and delete or rewrite any that don't qualify:
${hits}"

jq -n --arg r "$reason" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $r}}' 2>/dev/null

exit 0
