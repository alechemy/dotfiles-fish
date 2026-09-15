function __ports_pids
    set -l rows (command lsof -t +w -nP -iTCP:$argv[1] -sTCP:LISTEN 2>&1)
    set -l result $status
    if test $result -eq 1; and test (count $rows) -eq 0
        return 1
    end
    if test $result -ne 0; or test (count $rows) -eq 0
        echo "ports: cannot verify listeners" >&2
        return 2
    end
    for row in $rows
        if not string match -qr '^[1-9][0-9]*$' -- $row
            echo "ports: cannot verify listeners" >&2
            return 2
        end
    end
    printf '%s\n' $rows | command sort -nu
end

function __ports_identity
    # Include owner, start time and executable, but never command arguments.
    set -l row (LC_ALL=C command ps -p $argv[1] -o uid= -o lstart= -o stat= -o comm= 2>&1)
    set -l result $status
    if test $result -eq 1; and test (count $row) -eq 0
        return 1
    end
    if test $result -ne 0; or test (count $row) -ne 1
        return 2
    end
    set -l fields (string match -r '^\s*([0-9]+)\s+([A-Za-z]{3} [A-Za-z]{3} +[0-9]{1,2} [0-9:]{8} [0-9]{4})\s+(\S+)\s+(.+)$' -- $row)
    if test (count $fields) -ne 5
        return 2
    end
    if string match -q 'Z*' -- $fields[4]
        return 1
    end
    printf '%s\n' "$fields[2] $fields[3] $fields[5]"
end

function ports -d "Inspect TCP listeners; stop one verified owner with explicit force"
    set -l usage 'Usage: ports ls | show <port> | pid <port> | kill <port> [--force]'
    set -l action $argv[1]
    switch "$action"
        case -h --help
            test (count $argv) -eq 1; or begin
                echo $usage >&2
                return 2
            end
            echo $usage
            echo 'kill sends TERM and checks exit 30 times at 0.1s intervals; --force permits KILL afterward.'
            return 0
        case ls
            test (count $argv) -eq 1; or begin
                echo $usage >&2
                return 2
            end
            command lsof -i -n -P
            return $status
        case show pid kill
            if test (count $argv) -ne 2
                if test "$action" != kill; or test (count $argv) -ne 3; or test "$argv[3]" != --force
                    echo $usage >&2
                    return 2
                end
            end
        case '*'
            echo $usage >&2
            return 2
    end
    if not string match -qr '^[0-9]{1,5}$' -- "$argv[2]"; or test "$argv[2]" -lt 1; or test "$argv[2]" -gt 65535
        echo 'ports: port must be an integer from 1 to 65535' >&2
        return 2
    end
    set -l port (math "$argv[2] + 0")
    if test "$action" = show
        command lsof -nP -iTCP:$port -sTCP:LISTEN
        return $status
    end
    set -l pids (__ports_pids $port)
    set -l result $status
    if test $result -ne 0
        test $result -ne 1; or echo "ports: no process listening on port $port" >&2
        return $result
    end
    if test "$action" = pid
        printf '%s\n' $pids
        return 0
    end
    if test (count $pids) -ne 1
        echo 'ports: multiple listener owners; refusing to signal any process' >&2
        return 1
    end
    set -l pid $pids[1]
    set -l identity (__ports_identity $pid)
    if test $status -ne 0; or test "$pid" -le 1; or test "$pid" = "$fish_pid"
        echo 'ports: cannot verify process identity' >&2
        return 1
    end
    set -l uid (command id -u)
    if test $status -ne 0; or not string match -qr '^[0-9]+$' -- "$uid"; or not string match -q "$uid *" -- "$identity"
        echo 'ports: refusing a listener not owned by the current user' >&2
        return 1
    end
    set -l owner (string replace -ar '[[:cntrl:]]' '?' -- "$identity" | string sub -l 160)
    echo "ports: port $port, PID $pid, UID/start/executable: $owner" >&2
    for signal in TERM KILL
        # Never discover a new kill target after the initial snapshot.
        set -l current_pids (__ports_pids $port)
        set -l listeners_status $status
        set -l current_identity (__ports_identity $pid)
        if test $status -ne 0; or test "$current_identity" != "$identity"; or test $listeners_status -ne 0; or test (count $current_pids) -ne 1; or test "$current_pids[1]" != "$pid"
            echo 'ports: listener or process identity changed; refusing to signal' >&2
            return 1
        end
        command kill -$signal $pid; or return 1
        for attempt in (command seq 30)
            set current_identity (__ports_identity $pid)
            set -l identity_status $status
            if test $identity_status -eq 1
                return 0
            end
            if test $identity_status -ne 0; or test "$current_identity" != "$identity"
                echo 'ports: process identity changed or became unverifiable; stopping' >&2
                return 1
            end
            command sleep 0.1
        end
        if test "$signal" = KILL
            echo 'ports: process has not exited after KILL' >&2
            return 1
        end
        if not contains -- --force $argv
            echo 'ports: process survived TERM; no escalation without --force' >&2
            return 1
        end
    end
end
