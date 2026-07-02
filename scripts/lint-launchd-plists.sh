#!/usr/bin/env bash
# scripts/lint-launchd-plists.sh
#
# Verify that launch agents which drive AppleEvents to TCC-protected apps stay
# under Apple-signed parent processes.
#
# macOS attributes Apple Events to the calling binary's code signature.
# Adhoc-signed binaries at versioned paths (mise's Python, Homebrew's Python,
# anything launched by `uv run`) get a fresh TCC identity on every upgrade,
# which invalidates the prior Automation grant. The system then re-prompts
# "X wants to control data in other apps," and because launch agents run
# headless, the prompt blocks the pipeline silently when the user is AFK.
#
# This linter enforces:
#
#   1. The plist's ProgramArguments[0] must be an Apple-signed binary at a
#      stable path: /usr/bin/python3, /bin/bash, /bin/sh, or /usr/bin/osascript.
#      /usr/bin/env is intentionally excluded because it resolves through
#      launchd's PATH and would let mise's shimmed Python win.
#
#   2. The entry script's own shebang must be #!<ProgramArguments[0]>, so a
#      manual test run uses the same interpreter as production.
#
#   3. Scripts invoked via shebang resolution from a shell entry — either a
#      "$VAR" holding a $HOME/.local/bin path or a literal
#      "$HOME/.local/bin/<name>" at command position — must themselves use an
#      allowlisted shebang. Shell helpers are scanned recursively; helpers are
#      resolved across all packages (stow/*, stow-work/*, stow-local/*), since
#      shared gates like should-run-background-job live in stow/bin.
#
#   4. Inline `-c` command strings are rejected outright: launchd agents must
#      point at a script file the linter can follow.
#
# Scans *.plist.template plus any plain *.plist without a .template sibling
# (generated outputs are skipped) under stow/, stow-work/, and stow-local/.
#
# See CLAUDE.md → "Launch Agents and AppleEvents" for the full rationale and
# the canonical split-architecture pattern when third-party Python deps are
# needed (entry under /usr/bin/python3, parser under uv run).
#
# Run from the repo root or via setup.sh. Exits non-zero on any violation.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ALLOWED_PROGRAM_ARG0=(/usr/bin/python3 /bin/bash /bin/sh /usr/bin/osascript)
ALLOWED_SHEBANG=("#!/bin/bash" "#!/bin/sh" "#!/usr/bin/python3" "#!/usr/bin/osascript")

# Command-position prefix: line start, after ; & | { (, or after a control
# keyword. Filters out paths passed as arguments to an explicit interpreter.
CMD_POS='(^[[:space:]]*|[;&|{(][[:space:]]*|(if|then|else|elif|do|while|until)[[:space:]]+)'

errors=0

err() {
    printf '  ERROR: %s\n' "$1" >&2
    errors=$((errors + 1))
}

contains() {
    local needle=$1
    shift
    local x
    for x in "$@"; do
        [[ "$x" == "$needle" ]] && return 0
    done
    return 1
}

# Real plist parse (templates parse fine: __HOME__ is an ordinary string).
# Prints one arg per line, or a single PARSE-ERROR:/NO-ARGS line.
extract_program_args() {
    /usr/bin/python3 - "$1" <<'PY'
import plistlib, sys
try:
    with open(sys.argv[1], "rb") as f:
        data = plistlib.load(f)
except Exception as e:
    print(f"PARSE-ERROR: {e}")
    sys.exit(0)
args = data.get("ProgramArguments")
if not isinstance(args, list) or not args:
    print("NO-ARGS")
    sys.exit(0)
for a in args:
    print(a)
PY
}

shebang_of() {
    [[ -f "$1" ]] || return 1
    local first
    IFS= read -r first < "$1" || return 1
    [[ "$first" == "#!"* ]] && printf '%s\n' "$first"
}

check_shebang() {
    local script=$1 context=$2 sb
    if [[ ! -f "$script" ]]; then
        err "$context: $script does not exist"
        return
    fi
    sb=$(shebang_of "$script") || true
    if [[ -z "$sb" ]]; then
        err "$context: $script has no shebang"
        return
    fi
    if ! contains "$sb" "${ALLOWED_SHEBANG[@]}"; then
        err "$context: $script shebang '$sb' must be one of: ${ALLOWED_SHEBANG[*]}"
    fi
}

# Helpers are shared across packages (should-run-background-job lives in
# stow/bin while its callers live in stow/devonthink etc.), so resolve the
# name against every package's .local/bin.
resolve_helper() {
    local name=$1 cand
    for cand in stow/*/.local/bin/"$name" stow-work/*/.local/bin/"$name" stow-local/*/.local/bin/"$name"; do
        if [[ -f "$cand" ]]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

# Emit the .local/bin-relative names of helpers a shell script invokes via
# shebang resolution: "$VAR" (where VAR was assigned a $HOME/.local/bin path)
# or a literal "$HOME/.local/bin/<name>", either one at command position.
find_invoked_helper_names() {
    local script=$1

    local mapping
    mapping=$(grep -oE '^[[:space:]]*((local|readonly|export)[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*="?\$HOME/\.local/bin/[A-Za-z0-9_./-]+"?' "$script" 2>/dev/null \
              | sed -E 's@^[[:space:]]*((local|readonly|export)[[:space:]]+)?@@; s@="?\$HOME/\.local/bin/@=@; s@"$@@') || true
    local var path
    while IFS='=' read -r var path; do
        [[ -z "$var" ]] && continue
        if grep -qE "${CMD_POS}\"\\\$${var}\"" "$script"; then
            printf '%s\n' "$path"
        fi
    done <<< "$mapping"

    grep -oE "${CMD_POS}\"\\\$HOME/\\.local/bin/[A-Za-z0-9_./-]+\"" "$script" 2>/dev/null \
        | sed -E 's|.*\$HOME/\.local/bin/||; s|"$||' || true
}

# Breadth-first over shell scripts reachable from the entry: check every
# invoked helper's shebang, and keep following helpers that are themselves
# shell scripts (same TCC attribution chain). Visited-set guards cycles.
scan_shell_scripts() {
    local plist=$1 entry=$2
    local visited=" " queue=("$entry") cur name helper sb

    while (( ${#queue[@]} > 0 )); do
        cur=${queue[0]}
        queue=("${queue[@]:1}")
        [[ "$visited" == *" $cur "* ]] && continue
        visited+="$cur "

        while IFS= read -r name; do
            [[ -z "$name" ]] && continue
            if ! helper=$(resolve_helper "$name"); then
                err "$plist via $(basename "$cur"): helper '$name' not found in any package's .local/bin"
                continue
            fi
            check_shebang "$helper" "$plist via $(basename "$cur")"
            sb=$(shebang_of "$helper") || true
            if [[ "$sb" == "#!/bin/bash" || "$sb" == "#!/bin/sh" ]]; then
                queue+=("$helper")
            fi
        done < <(find_invoked_helper_names "$cur" | sort -u)
    done
}

plists=()
while IFS= read -r line; do
    # Generated outputs have a .template sibling; lint the template only.
    if [[ "$line" == *.plist && -f "$line.template" ]]; then
        continue
    fi
    plists+=("$line")
done < <(find stow stow-work stow-local -path '*/Library/LaunchAgents/*' \
              \( -name '*.plist.template' -o -name '*.plist' \) 2>/dev/null | sort)

if (( ${#plists[@]} == 0 )); then
    echo "No launch-agent plists found under stow/, stow-work/, or stow-local/."
    exit 0
fi

for plist in "${plists[@]}"; do
    pkgroot="${plist%%/Library/*}"

    args=()
    while IFS= read -r line; do
        args+=("$line")
    done < <(extract_program_args "$plist")

    if [[ "${args[0]:-}" == PARSE-ERROR:* ]]; then
        err "$plist: not a parseable plist (${args[0]#PARSE-ERROR: })"
        continue
    fi
    if [[ "${args[0]:-NO-ARGS}" == "NO-ARGS" ]]; then
        err "$plist: <key>ProgramArguments</key> missing or empty"
        continue
    fi

    if ! contains "${args[0]}" "${ALLOWED_PROGRAM_ARG0[@]}"; then
        err "$plist: ProgramArguments[0]='${args[0]}' must be one of: ${ALLOWED_PROGRAM_ARG0[*]}"
    fi

    for a in "${args[@]}"; do
        if [[ "$a" == "-c" ]]; then
            err "$plist: inline -c command string; use a script file so the linter can follow it"
        fi
    done

    # Entry = first arg under __HOME__/ (or a literal $HOME path in a plain
    # plist) that resolves to a file in this package. Prefer one with a
    # shebang so option-value files (--init-file foo.rc) aren't mistaken for
    # the entry; fall back to the first resolving file so a shebang-less
    # entry is flagged rather than skipped.
    entry_path=""
    fallback_path=""
    candidates=0
    for ((i = 1; i < ${#args[@]}; i++)); do
        a=${args[i]}
        rel=""
        if [[ "$a" == __HOME__/* ]]; then
            rel=${a#__HOME__/}
        elif [[ "$a" == "$HOME"/* ]]; then
            rel=${a#"$HOME"/}
        fi
        [[ -z "$rel" ]] && continue
        candidates=$((candidates + 1))
        f="$pkgroot/$rel"
        [[ -f "$f" ]] || continue
        if shebang_of "$f" >/dev/null; then
            entry_path=$f
            break
        fi
        [[ -z "$fallback_path" ]] && fallback_path=$f
    done
    [[ -z "$entry_path" ]] && entry_path=$fallback_path
    if [[ -z "$entry_path" ]]; then
        if (( candidates > 0 )); then
            err "$plist: no __HOME__ argument resolves to a file under $pkgroot"
        fi
        continue
    fi

    check_shebang "$entry_path" "$plist (entry)"
    sb=$(shebang_of "$entry_path") || true
    if [[ -n "$sb" ]] && contains "$sb" "${ALLOWED_SHEBANG[@]}" && [[ "$sb" != "#!${args[0]}" ]]; then
        err "$plist: entry shebang '$sb' must match ProgramArguments[0] ('#!${args[0]}') so manual runs use the production interpreter"
    fi

    if [[ "${args[0]}" == /bin/bash || "${args[0]}" == /bin/sh ]]; then
        scan_shell_scripts "$plist" "$entry_path"
    fi
    # Python entries are invoked by the plist directly via /usr/bin/python3;
    # helpers they spawn run as separate processes whose AppleEvent
    # attribution doesn't propagate back, so we don't follow into them.
done

if (( errors > 0 )); then
    printf '\n%d violation(s). See CLAUDE.md → "Launch Agents and AppleEvents" for the rule.\n' "$errors" >&2
    exit 1
fi

printf 'OK: %d plist(s) checked.\n' "${#plists[@]}"
