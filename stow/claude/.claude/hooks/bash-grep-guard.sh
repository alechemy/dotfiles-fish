#!/usr/bin/env bash
# PreToolUse (Bash) guard: block grep run through the Bash tool.
#
# The Bash tool runs commands under a PTY, so grep auto-enables --color and emits
# ANSI match-highlight codes. The harness's stdout renderer corrupts those highlighted
# tokens (e.g. "^Notebook-[0-9]+" rendered as "^n+"), so grep results are silently
# lossy. The first-class Grep tool and Read render correctly because they're structured.
#
# Blocks bare grep/egrep/fgrep (exit 2, message to stderr) and redirects to the Grep
# tool. Allows the invocation when color is explicitly disabled (--color=never or a
# leading NO_COLOR=), since that is the actual trigger and pipe-filtering still needs it.

input=$(cat)

cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

# grep family at a command position: start of string, or after a shell separator
# (| & ; ( newline) and optional spaces. A bare space is deliberately NOT a boundary,
# so the word "grep" inside a quoted argument (echo, commit messages) doesn't trip it.
# This means grep behind a wrapper word (git/sudo/xargs grep) slips through — accepted,
# since those are rare and the no-false-positive property matters more.
sep=$'(^|[|&;(\n])'
[[ "$cmd" =~ ${sep}[[:space:]]*(grep|egrep|fgrep)([[:space:]]|$) ]] || exit 0

# Color off → output renders fine → allow (covers legitimate pipe filtering).
[[ "$cmd" =~ (--colou?r=never|NO_COLOR=) ]] && exit 0

cat >&2 <<'EOF'
Blocked: grep via the Bash tool corrupts its own output here. The Bash tool runs under
a PTY, so grep auto-enables --color, and the harness's stdout renderer eats the
highlighted match tokens (e.g. "^Notebook-[0-9]+" renders as "^n+") — results are
silently unreliable.

- To search file contents: use the first-class Grep tool (ripgrep, rendered structurally).
- To filter a command's output in a pipe: append --color=never (e.g.
  `… | grep --color=never PATTERN`) or prefix NO_COLOR=1, which disables the
  highlighting that triggers the corruption.
EOF
exit 2
