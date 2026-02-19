function rip -d "download an album"
    set -l compilation_flag ""
    set -l compilation_explicit 0
    set -l url_or_query ""
    set -l genre ""

    # Parse arguments
    for arg in $argv
        if test "$arg" = --compilation
            set compilation_flag --compilation
            set compilation_explicit 1
        else if test "$arg" = "--compilation=false"
            set compilation_flag ""
            set compilation_explicit 1
        else if test -z "$url_or_query"
            set url_or_query "$arg"
        else if test -z "$genre"
            set genre "$arg"
        else
            echo "Usage: rip [--compilation|--compilation=false] <url|search_query> [genre]"
            echo ""
            echo "Examples (URL mode):"
            echo "  rip 'https://play.qobuz.com/album/xyz123' 'Rock'"
            echo "  rip --compilation 'https://play.qobuz.com/album/xyz123' 'Rock'"
            echo ""
            echo "Examples (search mode):"
            echo "  rip 'Lonerism' 'Rock'"
            echo "  rip 'Tame Impala Currents' 'Electronic'"
            echo "  rip --compilation 'Various Artists Compilation' 'Soundtrack'"
            echo ""
            echo "Interactive genre selection:"
            echo "  rip 'Lonerism'"
            echo "  rip --compilation 'Various Artists Compilation'"
            return 1
        end
    end

    # Validate required arguments
    # Genre is optional here; if omitted, the remote script can prompt interactively.
    if test -z "$url_or_query"
        echo "Usage: rip [--compilation|--compilation=false] <url|search_query> [genre]"
        echo ""
        echo "Examples (URL mode):"
        echo "  rip 'https://play.qobuz.com/album/xyz123' 'Rock'"
        echo "  rip --compilation 'https://play.qobuz.com/album/xyz123' 'Rock'"
        echo ""
        echo "Examples (search mode):"
        echo "  rip 'Lonerism' 'Rock'"
        echo "  rip 'Tame Impala Currents' 'Electronic'"
        echo "  rip --compilation 'Various Artists Compilation' 'Soundtrack'"
        echo ""
        echo "Interactive genre selection:"
        echo "  rip 'Lonerism'"
        echo "  rip --compilation 'Various Artists Compilation'"
        return 1
    end

    # Auto-set compilation for Soundtrack genre if not explicitly set
    # Only do this when genre is provided (if omitted, remote script will prompt for it later).
    if test -n "$genre" -a "$genre" = Soundtrack -a $compilation_explicit -eq 0
        set compilation_flag --compilation
        echo "ℹ️  Auto-setting compilation flag for Soundtrack genre"
    end

    # Detect if this is a URL or a search query
    set -l genre_label "$genre"
    if test -z "$genre_label"
        set genre_label "(prompt)"
    end

    if string match -q 'http*' "$url_or_query"
        echo "🎵 Starting download: $genre_label album from $url_or_query"
    else
        echo "🔍 Searching for: \"$url_or_query\" (genre: $genre_label)"
    end

    if test -n "$compilation_flag"
        echo "   (Marking as compilation)"
    end

    # Replace with actual user+ip
    # Use -t flag to allocate pseudo-terminal for interactive search selection
    set DEST 'admin@192.168.50.54'

    set -l remote_cmd "source ~/.profile && /share/CACHEDEV1_DATA/python-apps/rip-and-tag.sh $compilation_flag '$url_or_query'"
    if test -n "$genre"
        set remote_cmd "$remote_cmd '$genre'"
    end

    ssh -t $DEST "$remote_cmd"

    if test $status -eq 0
        echo "✅ Download completed successfully!"
    else
        echo "❌ Download failed!"
    end
end
