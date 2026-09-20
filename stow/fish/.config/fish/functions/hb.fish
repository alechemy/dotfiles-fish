function hb -d "Review committed branch changes against an explicit base"
    if test (count $argv) -ne 1; or test -z "$argv[1]"; or string match -q -- '-*' "$argv[1]"
        printf 'Usage: hb <base>\n' >&2
        return 2
    end

    set -l base (command git rev-parse --verify --end-of-options "$argv[1]^{commit}")
    or return

    if not command git merge-base "$base" HEAD >/dev/null
        printf 'hb: base and HEAD must have a common ancestor.\n' >&2
        return 1
    end

    command hunk diff "$argv[1]...HEAD"
end
