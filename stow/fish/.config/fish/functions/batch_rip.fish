#!/usr/bin/env fish

function batch_rip -d "Batch music downloader with cooldown and logging"
    # --- Argument parsing ---
    argparse 'l/local' 'limit=' -- $argv
    or return 1

    set -l local_flag
    if set -q _flag_local
        set local_flag --local
    end

    set -l limit 0
    if set -q _flag_limit
        set limit $_flag_limit
    end

    # Helper functions (--no-scope-shadowing shares caller's local variables)
    function _log --no-scope-shadowing
        echo "["(date '+%Y-%m-%d %H:%M:%S')"] $argv[1]" >>$log_file
    end

    function _log_error --no-scope-shadowing
        echo "["(date '+%Y-%m-%d %H:%M:%S')"] ERROR: $argv[1]" >>$error_log
    end

    function _update_input_file --no-scope-shadowing
        python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
done = set(sys.argv[2:])
data = [e for e in data if e.get('url') not in done]
with open(sys.argv[1], 'w') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
" "$input_file" $successful_urls
    end

    function _move_to_retry --no-scope-shadowing -a url session_id
        python3 -c "
import json, sys, os
url = sys.argv[2]
sid = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
input_path, retry_path = sys.argv[1], sys.argv[4]

with open(input_path) as f:
    data = json.load(f)

entry = None
remaining = []
for e in data:
    if e.get('url') == url and entry is None:
        entry = e
    else:
        remaining.append(e)

if entry is None:
    sys.exit(0)

if sid:
    entry['sessionId'] = sid

with open(input_path, 'w') as f:
    json.dump(remaining, f, indent=2, ensure_ascii=False)
    f.write('\n')

retry = []
if os.path.exists(retry_path):
    with open(retry_path) as f:
        retry = json.load(f)
retry.append(entry)
with open(retry_path, 'w') as f:
    json.dump(retry, f, indent=2, ensure_ascii=False)
    f.write('\n')
" "$input_file" "$url" "$session_id" "$retry_file"
    end

    # Configuration
    set -l log_dir "$HOME/.local/share/batch_rip"
    mkdir -p "$log_dir"
    set -l cooldown 10
    set -l input_file (test -n "$argv[1]"; and echo $argv[1]; or echo "$log_dir/downloads.json")
    set -l retry_file (path dirname $input_file)/needs-retry.json
    set -l timestamp (date +%Y%m%d_%H%M%S)
    set -l log_file "$log_dir/rip_$timestamp.log"
    set -l error_log "$log_dir/rip_errors_$timestamp.log"
    # Colors for output
    set -l red '\033[0;31m'
    set -l green '\033[0;32m'
    set -l yellow '\033[1;33m'
    set -l nc '\033[0m'

    # Check if input file exists
    if not test -f $input_file
        echo -e $red"Error: Input file '$input_file' not found!"$nc
        echo "Expected JSON array: [{\"url\": \"...\", \"genre\": \"...\", ...}, ...]"
        return 1
    end

    # Check if riptag function/command exists
    if not functions -q riptag; and not command -q riptag
        echo -e $red"Error: 'riptag' function or command not found!"$nc
        return 1
    end

    # Parse JSON into tab-separated lines: url\tgenre\tcompilation\tartist\ttitle\tsessionId
    # Entries with "skip": true are excluded
    set -l entries (python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for e in data:
    if e.get('skip'):
        continue
    parts = [
        e.get('url', ''),
        e.get('genre', ''),
        'true' if e.get('compilation') else 'false',
        e.get('artist', ''),
        e.get('title', ''),
        e.get('sessionId', ''),
    ]
    print('\t'.join(parts))
" "$input_file")

    if test $status -ne 0
        echo -e $red"Error: Failed to parse '$input_file' as JSON."$nc
        return 1
    end

    set -l total (count $entries)
    if test $limit -gt 0 -a $limit -lt $total
        set total $limit
    end
    set -l current 0
    set -l successful_urls
    set -l failed_count 0

    _log "Starting batch download session"
    _log "Input file: $input_file"
    _log "Total downloads: $total"
    _log "Cooldown: $cooldown seconds"
    if test $limit -gt 0
        _log "Limit: $limit albums"
    end
    if test -n "$local_flag"
        _log "Mode: local (--local applied to all)"
    end
    _log ----------------------------------------

    for entry in $entries
        set current (math $current + 1)

        set -l fields (string split \t "$entry")
        set -l url $fields[1]
        set -l genre $fields[2]
        set -l compilation $fields[3]
        set -l artist $fields[4]
        set -l title $fields[5]
        set -l session_id $fields[6]

        if test -z "$url" -o -z "$genre"
            _log_error "Missing url or genre in entry $current"
            continue
        end

        # Build display string
        set -l display
        if test -n "$artist" -a -n "$title"
            set display "$artist - $title"
        else if test -n "$artist"
            set display "$artist"
        else
            set display "$url"
        end

        # Build riptag arguments
        set -l riptag_args
        if test -n "$session_id"
            # Resume a previously failed download
            set riptag_args "--resume=$session_id"
            if test "$compilation" = true
                set -a riptag_args --compilation
            end
            set -a riptag_args $genre
        else
            set riptag_args $local_flag
            if test "$compilation" = true
                set -a riptag_args --compilation
            end
            set -a riptag_args $url $genre
        end

        echo -en $yellow"[$current/$total]"$nc" $display ($genre) "
        if test -n "$session_id"
            _log "Resuming: $display ($genre) [session: $session_id]"
        else
            _log "Downloading: $display ($genre) [$url]"
        end

        if riptag $riptag_args >>$log_file 2>>$error_log
            echo -e $green"✓"$nc
            _log "OK: $url"
            set -a successful_urls $url
            _update_input_file
        else
            echo -e $red"✗"$nc
            _log_error "FAILED: $display ($genre) [$url]"
            set failed_count (math $failed_count + 1)
            # Move failed entry to retry file (with session ID if available)
            set -l sid (cat /tmp/riptag-resume-id 2>/dev/null)
            _move_to_retry "$url" "$sid"
            if test -n "$sid"
                _log "Moved to retry file with session ID: $sid ($url)"
            else
                _log "Moved to retry file: $url"
            end
        end

        if test $limit -gt 0 -a $current -ge $limit
            _log "Limit of $limit albums reached, stopping"
            echo -e $yellow"Limit of $limit albums reached, stopping."$nc
            break
        end

        # Cooldown (skip for last item)
        if test $current -lt $total
            sleep $cooldown
        end
    end

    _log ----------------------------------------
    _log "Batch download session completed"
    _log "Total processed: $current downloads"

    # Summary
    set -l successes (count $successful_urls)
    echo -e "\n"$green"Batch download completed!"$nc
    echo "Processed: $current downloads"
    echo "Successful: $successes"
    echo "Failed: $failed_count"
    echo "Logs: $log_file"

    if test $failed_count -gt 0
        echo -e $yellow"Failed albums written to $retry_file for retry."$nc
    end
end
