#!/usr/bin/env bash
# PreToolUse (Bash) guard: block grep run through the Bash tool.
#
# The Bash tool runs commands under a PTY, so grep auto-enables --color and emits
# ANSI match-highlight codes. The harness's stdout renderer corrupts those highlighted
# tokens (e.g. "^Notebook-[0-9]+" rendered as "^n+"), so grep results are silently
# lossy. The first-class Grep tool and Read render correctly because they're structured.
#
# Blocks bare grep/egrep/fgrep (exit 2, message to stderr) and redirects to ripgrep —
# the first-class Grep tool when it's wired, otherwise `rg` via Bash (the Grep tool is
# absent in some sessions, returning "No such tool available"). A grep used as a pipe
# filter (right after `|`) is left alone once color is disabled (--color=never or a
# leading NO_COLOR=) — filtering another command's stdout is what grep is for, and
# color-off output renders fine. A grep used for a content search (start of command, or
# after ; & ( newline) is redirected to ripgrep even with color off, since ripgrep is
# the right tool there regardless.

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
lead="${BASH_REMATCH[1]}"

# Pipe filter (grep right after `|`): filtering another command's stdout is grep's job,
# and color-off output renders fine — allow it. A content search (any other command
# position) is redirected to ripgrep regardless of color, so it falls through.
if [[ "$lead" == "|" ]]; then
  [[ "$cmd" =~ (--colou?r=never|NO_COLOR=) ]] && exit 0
fi

cat >&2 <<'EOF'
Blocked: grep via the Bash tool corrupts its own output here. The Bash tool runs under
a PTY, so grep auto-enables --color, and the harness's stdout renderer eats the
highlighted match tokens (e.g. "^Notebook-[0-9]+" renders as "^n+") — results are
silently unreliable.

To search file contents, use ripgrep — never grep, and never fall back to reading
files one by one:
- If the first-class Grep tool is available, use it (it's ripgrep, rendered structurally).
- If it returns "No such tool available" this session, run ripgrep directly via Bash:
  `rg --color=never PATTERN [PATH]`. `rg` is on PATH and is NOT blocked by this guard;
  it is the sanctioned content-search fallback, not `grep --color=never`.

To filter a command's output in a pipe (not a file search), grep is fine with color off:
`… | grep --color=never PATTERN` (or prefix NO_COLOR=1, which disables the highlighting
that triggers the corruption).
EOF
exit 2
