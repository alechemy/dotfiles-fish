if command -q eza
    alias ls "eza --icons --group-directories-first"
    alias ll "eza -l --icons --group-directories-first"
    alias la "eza -la --icons --group-directories-first"
    alias tree "eza --tree --icons"
end

if command -q bat
    alias cat bat
end

if command -q zoxide
    zoxide init fish | source
    alias cd z
end

if command -q fzf
    fzf --fish | source
end
