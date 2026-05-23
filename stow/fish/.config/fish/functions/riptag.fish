function riptag -d "download, tag, and organize an album into the music library"
    # --- Configuration ---
    set -l NAS admin@192.168.50.54
    set -l NAS_RIP /share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip
    set -l NAS_RIP_CONFIG /share/CACHEDEV1_DATA/streamrip/config.toml
    set -l LOCAL_PYTHON $HOME/Developer/streamrip/.venv/bin/python3
    set -l LOCAL_RIP $HOME/Developer/streamrip/.venv/bin/rip
    set -l TAGGER $HOME/.local/bin/tagger.py
    set -l ORGANIZER $HOME/.local/bin/music-organize.py
    set -l WORKER $HOME/.local/bin/riptag-worker.sh
    set -l ALLOWED_GENRES Ambient Bluegrass Classical Country Electronic Experimental Folk Hip-Hop Jazz Lo-Fi Mashup Pop R&B Reggae Rock Soundtrack Unknown

    # --- Argument parsing ---
    set -l compilation_flag
    set -l compilation_explicit 0
    set -l playlist_flag
    set -l year
    set -l year_explicit 0
    set -l local_mode 0
    set -l resume_id
    set -l url_or_query
    set -l genre
    set -l replaces

    for arg in $argv
        switch $arg
            case --compilation
                set compilation_flag --compilation
                set compilation_explicit 1
            case '--compilation=false'
                set compilation_flag --no-compilation
                set compilation_explicit 1
            case '--year=*'
                set year (string replace -- '--year=' '' $arg)
                set year_explicit 1
            case --local
                set local_mode 1
            case '--resume=*'
                set resume_id (string replace -- '--resume=' '' $arg)
                set local_mode 1
            case '--replaces=*'
                set replaces (string replace -- '--replaces=' '' $arg)
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
            if test (count $meta_lines) -ge 3 -a -n "$meta_lines[3]"
                set playlist_flag "$meta_lines[3]"
            end
            if test $year_explicit -eq 0 -a (count $meta_lines) -ge 4 -a -n "$meta_lines[4]"
                set year "$meta_lines[4]"
            end
            if test -z "$replaces" -a (count $meta_lines) -ge 5 -a -n "$meta_lines[5]"
                set replaces "$meta_lines[5]"
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

    # --- Auto-detect playlist URLs ---
    # (Soundtrack auto-compilation now lives in tagger.py, which can check the
    # actual downloaded tracks — a single-artist soundtrack like Bo Burnham's
    # INSIDE shouldn't be marked compilation just because the genre is Soundtrack.)
    if string match -q '*/playlist/*' "$url_or_query"
        set playlist_flag --playlist-mode
        if test $compilation_explicit -eq 0
            set compilation_flag --compilation
        end
        if test -z "$year"
            set year (date +%Y)
            echo "ℹ️  Playlist URL detected: forcing compilation + Various Artists + unified cover + year=$year"
        else
            echo "ℹ️  Playlist URL detected: forcing compilation + Various Artists + unified cover (year=$year)"
        end
    end

    # --- Build year args (passed to worker if set) ---
    set -l year_args
    if test -n "$year"
        set year_args --year $year
    end

    # --- Build replaces args (re-download guard; passed to worker if set) ---
    # $replaces_args is a list for direct worker calls; $replaces_remote is a
    # single shell-escaped string spliced into the NAS-mode ssh command.
    set -l replaces_args
    set -l replaces_remote
    if test -n "$replaces"
        set replaces_args --replaces "$replaces"
        set -l esc (string replace -a "'" "'\\''" "$replaces")
        set replaces_remote "--replaces '$esc'"
    end

    # --- Resume mode: skip URL resolution, go straight to worker ---
    if test -n "$resume_id"
        echo "🔄 Resuming session $resume_id"
        echo "   Genre: $genre"
        if test -n "$compilation_flag"
            echo "   Compilation: yes"
        end
        if test -n "$playlist_flag"
            echo "   Playlist mode: yes"
        end
        if test -n "$year"
            echo "   Year: $year"
        end
        echo "   Mode: local (VPN resume)"
        echo ""

        LOCAL_PYTHON="$LOCAL_PYTHON" LOCAL_RIP="$LOCAL_RIP" "$WORKER" --local --resume "$resume_id" $compilation_flag $playlist_flag $year_args $replaces_args "$genre"
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
        else if test $worker_status -eq 3
            echo ""
            echo "↩️  Kept the existing library copy — the new download wasn't an improvement."
            return 0
        else if test $worker_status -ne 0
            echo ""
            echo "❌ Something went wrong."
            return 1
        end

        # Success
        __riptag_done
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
            command "$LOCAL_RIP" search -o "$tmpfile" -n 5 qobuz album "$url_or_query" >/dev/null 2>&1
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
    if test -n "$playlist_flag"
        echo "   Playlist mode: yes (Various Artists + unified cover)"
    end
    if test -n "$year"
        echo "   Year: $year"
    end
    if test $local_mode -eq 1
        echo "   Mode: local (will upload to NAS after download)"
    else
        echo "   Mode: NAS (downloading directly on NAS)"
    end
    echo ""

    # --- Run the worker ---
    if test $local_mode -eq 1
        LOCAL_PYTHON="$LOCAL_PYTHON" LOCAL_RIP="$LOCAL_RIP" "$WORKER" --local $compilation_flag $playlist_flag $year_args $replaces_args "$url" "$genre"
    else
        # Deploy scripts to NAS /tmp, then run via SSH
        scp -q "$TAGGER" "$ORGANIZER" "$WORKER" "$NAS":/tmp/
        if test $status -ne 0
            echo "ERROR: Failed to deploy scripts to NAS."
            return 1
        end
        ssh -t "$NAS" ". ~/.profile 2>/dev/null; TAGGER_SCRIPT=/tmp/tagger.py ORGANIZER_SCRIPT=/tmp/music-organize.py bash /tmp/riptag-worker.sh $compilation_flag $playlist_flag $year_args $replaces_remote '$url' '$genre'"
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
    else if test $worker_status -eq 3
        echo ""
        echo "↩️  Kept the existing library copy — the new download wasn't an improvement."
        return 0
    else if test $worker_status -ne 0
        echo ""
        echo "❌ Something went wrong."
        return 1
    end

    # --- Done ---
    __riptag_done
end

function __riptag_done
    echo ""
    echo "✅ Done! Album organized into the library."
    echo "   Navidrome will pick it up on its next scan."
end

function __riptag_usage
    echo "Usage: riptag [--compilation|--compilation=false] [--year=YYYY] [--replaces=PATH] [--local] <url|search_query> [genre]"
    echo "       riptag --resume=<session-id> [--compilation|--compilation=false] [--year=YYYY] [--replaces=PATH] [genre]"
    echo ""
    echo "Options:"
    echo "  --compilation          Mark as compilation album"
    echo "  --compilation=false    Explicitly not a compilation"
    echo "  --year=YYYY            Set the same year on every track"
    echo "                         (auto-defaults to current year for playlist URLs)"
    echo "  --replaces=PATH        Re-download guard: PATH (relative to the library"
    echo "                         root) is the existing album folder to replace;"
    echo "                         the replacement happens only if the new download"
    echo "                         is no worse on track count and quality"
    echo "  --local                Download locally instead of on NAS"
    echo "  --resume=<id>          Resume a failed session (implies --local)"
    echo ""
    echo "Playlist URLs (containing /playlist/) are auto-detected and unified:"
    echo "  forces --compilation, sets albumartist=Various Artists, embeds the"
    echo "  first track's cover into every track, and sets year=current year."
    echo ""
    echo "Examples:"
    echo "  riptag 'https://play.qobuz.com/album/xyz123' Rock"
    echo "  riptag --compilation 'https://play.qobuz.com/album/xyz123' Soundtrack"
    echo "  riptag 'https://open.qobuz.com/playlist/12345' Soundtrack"
    echo "  riptag --year=2024 'https://open.qobuz.com/playlist/12345' Soundtrack"
    echo "  riptag 'Tame Impala Currents' Electronic"
    echo "  riptag 'Lonerism'  # interactive genre selection"
    echo "  riptag --resume=a3f1b02c  # retry failed tracks (genre remembered)"
end
