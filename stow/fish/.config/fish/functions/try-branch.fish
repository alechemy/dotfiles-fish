function try-branch --argument-names branch
    if test (count $argv) -ne 1
        echo 'usage: try-branch <local-or-remote-branch>' >&2
        return 1
    end
    functions -q wt; or begin
        echo 'Worktrunk shell integration must be loaded before try-branch.' >&2
        return 1
    end

    if not command git show-ref --verify --quiet "refs/heads/$branch"
        for remote in (command git remote)
            if string match --quiet -- "$remote/*" "$branch"
                set -l remote_branch (string sub --start (math (string length -- "$remote") + 2) -- "$branch")
                command git check-ref-format "refs/heads/$remote_branch"; or return
                set -l refspec "+refs/heads/$remote_branch:refs/remotes/$branch"
                printf '+ git fetch -- %s %s\n' (string escape -- "$remote") (string escape -- "$refspec")
                command git fetch -- "$remote" "$refspec"; or return
                break
            end
        end
    end

    printf '+ wt switch --create %s --base %s\n' (string escape -- "try/$branch") (string escape -- "$branch")
    wt switch --create "try/$branch" --base "$branch"; or return
    printf 'Ready in %s\n' "$PWD"
end
