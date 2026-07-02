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
# filter (right after `|`) is left alone once ITS OWN pipeline segment disables color
# (--colou?r=never among that grep's args, or NO_COLOR= immediately before the grep
# word) — filtering another command's stdout is what grep is for, and color-off output
# renders fine. Color-off tokens elsewhere in the command don't count: the corruption
# comes from the grep that renders last, so the check must be per-segment. A grep used
# for a content search (start of command, or after ; & ( newline) is redirected to
# ripgrep even with color off, since ripgrep is the right tool there regardless.

input=$(cat)

cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
[ -z "$cmd" ] && exit 0

# Walk the command segment by segment (split on | & ; ( and newlines — quote-blind,
# consistent with the rest of this guard), tracking each segment's leading separator.
# grep must open its segment to count: a bare space is deliberately NOT a boundary,
# so the word "grep" inside a quoted argument (echo, commit messages) doesn't trip it.
# This means grep behind a wrapper word (git/sudo/xargs grep) slips through — accepted,
# since those are rare and the no-false-positive property matters more.
split_re=$'^([^|&;(\n]*)([|&;(\n])(.*)$'
blocked=0
rest="$cmd"
lead="^"
while [ -n "$rest" ] || [ "$lead" != "done" ]; do
  if [[ "$rest" =~ $split_re ]]; then
    seg="${BASH_REMATCH[1]}"
    nextlead="${BASH_REMATCH[2]}"
    rest="${BASH_REMATCH[3]}"
  else
    seg="$rest"
    nextlead="done"
    rest=""
  fi

  s="${seg#"${seg%%[![:space:]]*}"}"
  nocolor=0
  if [[ "$s" =~ ^NO_COLOR=[^[:space:]]*[[:space:]]+(.*)$ ]]; then
    nocolor=1
    s="${BASH_REMATCH[1]}"
  fi
  if [[ "$s" =~ ^(grep|egrep|fgrep)([[:space:]]|$) ]]; then
    if [[ "$lead" != "|" ]] || { [[ $nocolor -eq 0 ]] && ! [[ "$s" =~ --colou?r=never ]]; }; then
      blocked=1
    fi
  fi

  lead="$nextlead"
done

[ "$blocked" -eq 1 ] || exit 0

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
