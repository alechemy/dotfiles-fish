if status is-interactive; and command -q wt
    command wt config shell init fish | source
end
