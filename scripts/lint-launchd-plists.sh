#!/bin/bash
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
# This linter enforces two rules:
#
#   1. The plist's ProgramArguments[0] must be an Apple-signed binary at a
#      stable path: /usr/bin/python3, /bin/bash, /bin/sh, or /usr/bin/osascript.
#      /usr/bin/env is intentionally excluded because it resolves through
#      launchd's PATH and would let mise's shimmed Python win.
#
#   2. Sub-scripts that the entry script invokes via shebang resolution (i.e.,
#      "$VAR" arg or ./script arg, not  /usr/bin/python3 script.py) must
#      themselves use a shebang in the same allowlist.
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

extract_program_args() {
    # Stream the <string>...</string> values that sit between <key>ProgramArguments</key>
    # and the matching </array>, in order.
    awk '
        /<key>ProgramArguments<\/key>/ { in_pa = 1; next }
        in_pa && /<\/array>/           { in_pa = 0 }
        in_pa && /<string>/ {
            sub(/.*<string>/, "")
            sub(/<\/string>.*/, "")
            print
        }
    ' "$1"
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

# For a bash entry script, identify any helper scripts in $HOME/.local/bin/
# that look like they are invoked via shebang resolution (i.e., the variable
# holding their path is used as a command, not as an argument to an explicit
# interpreter). Emits the corresponding source path under stow/<pkg>/.local/bin/.
find_invoked_subscripts() {
    local script=$1 pkg=$2

    # Pass 1: collect VAR=path mappings where path is $HOME/.local/bin/<name>.
    # Quotes around the RHS are optional. Extension is optional too, so
    # extensionless executables (e.g., pipeline-log) are caught.
    local mapping
    mapping=$(grep -oE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*="?\$HOME/\.local/bin/[A-Za-z0-9_./-]+"?' "$script" 2>/dev/null \
              | sed -E 's|^[[:space:]]*||; s|="?\$HOME/\.local/bin/|=|; s|"$||') || true
    [[ -z "$mapping" ]] && return 0

    # Pass 2: for each mapping, emit the script path only if "$VAR" is invoked
    # at the start of a command line. This filters out variables that hold a
    # path purely for use as an argument to an explicit interpreter.
    local var path
    while IFS='=' read -r var path; do
        [[ -z "$var" ]] && continue
        if grep -qE '^[[:space:]]*"\$'"$var"'"' "$script"; then
            printf 'stow/%s/.local/bin/%s\n' "$pkg" "$path"
        fi
    done <<< "$mapping"
}

plists=()
while IFS= read -r line; do
    plists+=("$line")
done < <(find stow -path '*/Library/LaunchAgents/*.plist.template' 2>/dev/null | sort)

if (( ${#plists[@]} == 0 )); then
    echo "No launch-agent plist templates found under stow/."
    exit 0
fi

for plist in "${plists[@]}"; do
    pkg=$(awk -F/ '{print $2}' <<< "$plist")

    args=()
    while IFS= read -r line; do
        args+=("$line")
    done < <(extract_program_args "$plist")

    if (( ${#args[@]} == 0 )); then
        err "$plist: <key>ProgramArguments</key> not found"
        continue
    fi

    if ! contains "${args[0]}" "${ALLOWED_PROGRAM_ARG0[@]}"; then
        err "$plist: ProgramArguments[0]='${args[0]}' must be one of: ${ALLOWED_PROGRAM_ARG0[*]}"
    fi

    # Find the entry script: first arg that looks like __HOME__/...
    entry=""
    for ((i = 1; i < ${#args[@]}; i++)); do
        if [[ "${args[i]}" == __HOME__/* ]]; then
            entry=${args[i]}
            break
        fi
    done
    [[ -z "$entry" ]] && continue

    entry_path="stow/$pkg/${entry#__HOME__/}"
    if [[ ! -f "$entry_path" ]]; then
        err "$plist: entry script $entry_path not found"
        continue
    fi

    # Recurse one level into bash entries. Python entries are invoked by the
    # plist directly via /usr/bin/python3; helpers they spawn run as separate
    # processes whose AppleEvent attribution doesn't propagate back, so we
    # don't follow into them.
    if [[ "$entry_path" == *.sh ]]; then
        while IFS= read -r sub; do
            [[ -z "$sub" ]] && continue
            check_shebang "$sub" "$plist via $(basename "$entry_path")"
        done < <(find_invoked_subscripts "$entry_path" "$pkg" | sort -u)
    fi
done

if (( errors > 0 )); then
    printf '\n%d violation(s). See CLAUDE.md → "Launch Agents and AppleEvents" for the rule.\n' "$errors" >&2
    exit 1
fi

printf 'OK: %d plist template(s) checked.\n' "${#plists[@]}"
