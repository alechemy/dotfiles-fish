#!/usr/bin/env fish

function batch_rip -d "Batch music downloader with cooldown and logging"
    # Helper functions
    function _log
        echo "["(date '+%Y-%m-%d %H:%M:%S')"] $argv[1]" | tee -a $log_file
    end

    function _log_error
        echo "["(date '+%Y-%m-%d %H:%M:%S')"] ERROR: $argv[1]" | tee -a $error_log
    end

    # Configuration
    set -l log_dir "$HOME/.local/share/batch_rip"
    mkdir -p "$log_dir"
    set cooldown (test -n "$argv[1]"; and echo $argv[1]; or echo 30) # Default 30 seconds
    set input_file (test -n "$argv[2]"; and echo $argv[2]; or echo "$log_dir/downloads.txt") # Default input file
    set log_file "$log_dir/rip_"(date +%Y%m%d_%H%M%S)".log"
    set error_log "$log_dir/rip_errors_"(date +%Y%m%d_%H%M%S)".log"

    # Colors for output
    set red '\033[0;31m'
    set green '\033[0;32m'
    set yellow '\033[1;33m'
    set nc '\033[0m' # No Color

    # Check if input file exists
    if not test -f $input_file
        echo -e $red"Error: Input file '$input_file' not found!"$nc
        echo "Create a file with format: URL GENRE (one per line)"
        echo "Example:"
        echo "https://example.com/song1 rock"
        echo "https://example.com/song2 jazz"
        return 1
    end

    # Check if rip function/command exists
    if not functions -q rip; and not command -q rip
        echo -e $red"Error: 'rip' function or command not found!"$nc
        return 1
    end

    # Count total downloads and store all lines
    set all_lines (cat $input_file)
    set total_lines (count $all_lines)
    set current 0
    set successful_lines

    _log "Starting batch download session"
    _log "Input file: $input_file"
    _log "Total downloads: $total_lines"
    _log "Cooldown: $cooldown seconds"
    _log ----------------------------------------

    # Read the input file line by line
    for line in $all_lines
        # Skip empty lines and comments
        if test -z "$line"; or string match -q '#*' $line
            continue
        end

        set current (math $current + 1)

        # Parse URL and genre (split on first space)
        set parts (string split -m 1 ' ' $line)
        set url $parts[1]
        set genre $parts[2]

        if test -z "$url"; or test -z "$genre"
            _log_error "Invalid line format: $line"
            continue
        end

        echo -e $yellow"[$current/$total_lines] Processing: $url ($genre)"$nc
        _log "Starting download: $url ($genre)"

        # Run the rip command and capture output
        if rip $url $genre >>$log_file 2>>$error_log
            echo -e $green"✓ Success: $url"$nc
            _log "Download completed: $url"
            # Track successful line for removal
            set successful_lines $successful_lines $line
        else
            echo -e $red"✗ Failed: $url"$nc
            _log_error "Download failed: $url ($genre)"
        end

        # Cooldown (skip for last item)
        if test $current -lt $total_lines
            echo -e $yellow"Cooling down for $cooldown seconds..."$nc
            _log "Cooldown: $cooldown seconds"
            sleep $cooldown
        end
    end

    _log ----------------------------------------
    _log "Batch download session completed"
    _log "Total processed: $current downloads"

    # Remove successful downloads from input file
    if test (count $successful_lines) -gt 0
        set remaining_lines
        for line in $all_lines
            set found_success false
            for success_line in $successful_lines
                if test "$line" = "$success_line"
                    set found_success true
                    break
                end
            end
            if not $found_success
                set remaining_lines $remaining_lines $line
            end
        end

        # Rewrite the input file with remaining downloads
        if test (count $remaining_lines) -gt 0
            printf '%s\n' $remaining_lines >$input_file
            _log "Removed "(count $successful_lines)" successful downloads from $input_file"
        else
            # All downloads completed, create empty file
            echo -n >$input_file
            _log "All downloads completed! Cleared $input_file"
        end
    end

    # Summary
    set errors (if test -f $error_log; wc -l < $error_log; else; echo "0"; end)
    set successes (count $successful_lines)
    echo -e "\n"$green"Batch download completed!"$nc
    echo "Processed: $current downloads"
    echo "Successful: $successes"
    echo "Errors: $errors"
    echo "Logs: $log_file"
    echo "Error log: $error_log"

    if test $successes -gt 0
        echo -e $green"Removed $successes successful downloads from $input_file"$nc
    end
end
