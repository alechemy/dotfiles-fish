function riptag -d "download, tag, and import an album to Apple Music"
    # --- Configuration ---
    set -l NAS admin@192.168.50.54
    set -l NAS_RIP /share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip
    set -l NAS_RIP_CONFIG /share/CACHEDEV1_DATA/streamrip/config.toml
    set -l TAGGER $HOME/.local/bin/tagger.py
    set -l WORKER $HOME/.local/bin/riptag-worker.sh
    set -l ALLOWED_GENRES Ambient Bluegrass Classical Country Electronic Experimental Folk Hip-Hop Jazz Lo-Fi Mashup Pop R&B Reggae Rock Soundtrack Unknown

    # --- Argument parsing ---
    set -l compilation_flag
    set -l compilation_explicit 0
    set -l local_mode 0
    set -l resume_id
    set -l url_or_query
    set -l genre

    for arg in $argv
        switch $arg
            case --compilation
                set compilation_flag --compilation
                set compilation_explicit 1
            case '--compilation=false'
                set compilation_flag
                set compilation_explicit 1
            case --local
                set local_mode 1
            case '--resume=*'
                set resume_id (string replace -- '--resume=' '' $arg)
                set local_mode 1
            case '-h' '--help'
                __riptag_usage
                return 0
            case '-*'
                echo "Unknown option: $arg"
                __riptag_usage
                return 1
            case '*'
                if test -z "$url_or_query"
                    set url_or_query $arg
                else if test -z "$genre"
                    set genre $arg
                else
                    __riptag_usage
                    return 1
                end
        end
    end

    # --- Resume mode: load saved genre/compilation from metadata ---
    if test -n "$resume_id"
        set -l meta_file "/tmp/riptag-$resume_id.meta"
        if test -f "$meta_file"
            set -l meta_lines (cat "$meta_file")
            if test -z "$genre"
                # Use saved genre (first positional arg might be genre override)
                if test -n "$url_or_query" -a -z "$genre"
                    set genre "$url_or_query"
                    set url_or_query
                else
                    set genre "$meta_lines[1]"
                end
            end
            if test $compilation_explicit -eq 0 -a (count $meta_lines) -ge 2 -a -n "$meta_lines[2]"
                set compilation_flag "$meta_lines[2]"
            end
        else
            # No meta file — treat positional arg as genre
            if test -n "$url_or_query" -a -z "$genre"
                set genre "$url_or_query"
                set url_or_query
            end
        end
    end

    # --- Validate: need URL (normal) or session ID (resume) ---
    if test -z "$resume_id" -a -z "$url_or_query"
        __riptag_usage
        return 1
    end

    # --- Genre prompt (if omitted) ---
    if test -z "$genre"
        echo ""
        echo "Select a genre:"
        for i in (seq (count $ALLOWED_GENRES))
            printf "  [%2d] %s\n" $i $ALLOWED_GENRES[$i]
        end

        while true
            echo ""
            read -P "Enter selection (1-"(count $ALLOWED_GENRES)") or 0 to cancel: " choice
            if test "$choice" = 0
                echo "Cancelled."
                return 1
            end
            if string match -qr '^\d+$' "$choice"
                and test "$choice" -ge 1
                and test "$choice" -le (count $ALLOWED_GENRES)
                set genre $ALLOWED_GENRES[$choice]
                break
            end
            echo "Invalid selection. Enter a number."
        end
        echo ""
        echo "✅ Genre selected: $genre"
        echo ""
    end

    # --- Genre validation ---
    if not contains "$genre" $ALLOWED_GENRES
        echo "ERROR: Invalid genre '$genre'"
        echo ""
        echo "Allowed genres:"
        for g in $ALLOWED_GENRES
            echo "  $g"
        end
        return 1
    end

    # --- Auto-set compilation for Soundtrack ---
    if test "$genre" = Soundtrack -a $compilation_explicit -eq 0
        set compilation_flag --compilation
        echo "ℹ️  Auto-setting compilation flag for Soundtrack genre"
    end

    # --- Resume mode: skip URL resolution, go straight to worker ---
    if test -n "$resume_id"
        echo "🔄 Resuming session $resume_id"
        echo "   Genre: $genre"
        if test -n "$compilation_flag"
            echo "   Compilation: yes"
        end
        echo "   Mode: local (VPN resume)"
        echo ""

        "$WORKER" --local --resume "$resume_id" $compilation_flag "$genre"
        set -l worker_status $status

        if test $worker_status -eq 2
            # Partial failure — worker kept the download and wrote session ID
            set -l sid (cat /tmp/riptag-resume-id 2>/dev/null)
            if test -n "$sid"
                echo ""
                echo "⚠️  Some tracks still failing. Switch VPN and retry:"
                echo "  riptag --resume=$sid"
            end
            return 1
        else if test $worker_status -ne 0
            echo ""
            echo "❌ Something went wrong."
            return 1
        end

        # Success — nudge Music.app
        __riptag_nudge_music
        return 0
    end

    # --- Resolve URL (search if not already a URL) ---
    set -l url
    if string match -q 'http*' "$url_or_query"
        set url "$url_or_query"
    else
        echo "🔍 Searching for: \"$url_or_query\"..."
        echo ""

        # Search and parse results into "id\tdesc" lines
        set -l results
        if test $local_mode -eq 1
            set -l tmpfile (mktemp)
            command rip search -o "$tmpfile" -n 5 qobuz album "$url_or_query" >/dev/null 2>&1
            if test $status -ne 0
                echo "ERROR: Search failed."
                rm -f "$tmpfile"
                return 1
            end
            set results (python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    for r in json.load(f):
        print(str(r.get("id", "")) + "\t" + r.get("desc", "Unknown"))
' "$tmpfile")
            rm -f "$tmpfile"
        else
            set -l escaped_query (string replace -a "'" "'\\''" "$url_or_query")
            set results (ssh "$NAS" "$NAS_RIP --config-path $NAS_RIP_CONFIG search -o /tmp/rip-search.json -n 5 qobuz album '$escaped_query' >/dev/null 2>&1 && cat /tmp/rip-search.json && rm -f /tmp/rip-search.json" | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    print(str(r.get("id", "")) + "\t" + r.get("desc", "Unknown"))
')
        end

        if test (count $results) -eq 0
            echo "No results found."
            return 1
        end

        echo "Search Results:"
        echo "============================================================"
        for i in (seq (count $results))
            set -l parts (string split -m 1 \t $results[$i])
            set -l album_id $parts[1]
            set -l desc $parts[2]
            echo ""
            printf "  [%d] %s\n" $i "$desc"
            printf "      https://play.qobuz.com/album/%s\n" "$album_id"
        end
        echo ""
        echo "============================================================"

        while true
            echo ""
            read -P "Enter selection (1-"(count $results)") or 0 to cancel: " choice
            if test "$choice" = 0
                echo "Cancelled."
                return 1
            end
            if string match -qr '^\d+$' "$choice"
                and test "$choice" -ge 1
                and test "$choice" -le (count $results)
                set -l parts (string split -m 1 \t $results[$choice])
                set url "https://play.qobuz.com/album/$parts[1]"
                break
            end
            echo "Invalid selection."
        end
        echo ""
        echo "✅ Selected: $url"
        echo ""
    end

    # --- Summary ---
    echo "🎵 Downloading: $url"
    echo "   Genre: $genre"
    if test -n "$compilation_flag"
        echo "   Compilation: yes"
    end
    if test $local_mode -eq 1
        echo "   Mode: local (will upload to NAS after download)"
    else
        echo "   Mode: NAS (downloading directly on NAS)"
    end
    echo ""

    # --- Run the worker ---
    if test $local_mode -eq 1
        "$WORKER" --local $compilation_flag "$url" "$genre"
    else
        # Deploy scripts to NAS /tmp, then run via SSH
        scp -q "$TAGGER" "$WORKER" "$NAS":/tmp/
        if test $status -ne 0
            echo "ERROR: Failed to deploy scripts to NAS."
            return 1
        end
        ssh -t "$NAS" ". ~/.profile 2>/dev/null; TAGGER_SCRIPT=/tmp/tagger.py bash /tmp/riptag-worker.sh $compilation_flag '$url' '$genre'"
    end
    set -l worker_status $status

    if test $worker_status -eq 2
        # Partial failure — extract session ID and show resume command
        set -l sid (cat /tmp/riptag-resume-id 2>/dev/null)
        if test -n "$sid"
            echo ""
            echo "⚠️  Some tracks failed. Switch VPN and retry:"
                echo "  riptag --resume=$sid"
        end
        return 1
    else if test $worker_status -ne 0
        echo ""
        echo "❌ Something went wrong."
        return 1
    end

    # --- Nudge Music.app ---
    __riptag_nudge_music
end

function __riptag_nudge_music
    echo "--> Triggering Music.app import..."
    pgrep -xq Music; or open -j -a Music
    set -l auto_add_dir '/Volumes/Media/Music/Music/Media.localized/Automatically Add to Music.localized'
    open -g "$auto_add_dir"
    echo ""
    echo "✅ Done! Album should now be importing into Music.app."
end

function __riptag_usage
    echo "Usage: riptag [--compilation|--compilation=false] [--local] <url|search_query> [genre]"
    echo "       riptag --resume=<session-id> [--compilation|--compilation=false] [genre]"
    echo ""
    echo "Options:"
    echo "  --compilation          Mark as compilation album"
    echo "  --compilation=false    Explicitly not a compilation"
    echo "  --local                Download locally instead of on NAS"
    echo "  --resume=<id>         Resume a failed session (implies --local)"
    echo ""
    echo "Examples:"
    echo "  riptag 'https://play.qobuz.com/album/xyz123' Rock"
    echo "  riptag --compilation 'https://play.qobuz.com/album/xyz123' Soundtrack"
    echo "  riptag 'Tame Impala Currents' Electronic"
    echo "  riptag 'Lonerism'  # interactive genre selection"
    echo "  riptag --resume=a3f1b02c  # retry failed tracks (genre remembered)"
end
