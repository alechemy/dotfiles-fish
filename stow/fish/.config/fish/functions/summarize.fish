function summarize --description "Summarize a URL or file via Claude Code"
    if test (count $argv) -eq 0
        echo "Usage: summarize <url-or-file-path>"
        return 1
    end

    set -l input $argv[1]
    set -l rest $argv[2..]

    # Pre-convert binary formats that Claude Code can't read directly
    if test -f "$input"
        switch (string lower -- "$input")
            case '*.epub' '*.docx'
                echo "Converting $(basename "$input") with pandoc..."
                mkdir -p /tmp/summarize
                set -l converted /tmp/summarize/source.md
                if not pandoc "$input" -t markdown --wrap=none -o $converted
                    echo "Failed to convert with pandoc. Install with: brew install pandoc"
                    return 1
                end
                set -l size (math (wc -c < $converted) / 1024)
                echo "Converted ($size KB)."
                claude "/summarize $converted --original-path \"$input\" $rest"
                return
        end
    end

    claude "/summarize $input $rest"
end
