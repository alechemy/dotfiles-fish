function ports -d "manage processes by the ports they are using"
  switch $argv[1]
    case ls
      lsof -i -n -P
    case show
      lsof -i :"$argv[2]" | tail -n 1
    case pid
      ports show "$argv[2]" | awk '{ print $2; }'
    case kill
      # `kill` doesn't read PIDs from stdin — the previous `… | kill -9` form
      # silently never killed anything. Use command substitution so the PID
      # arrives as a real argv element. Capture into a variable first so an
      # empty PID (no process listening) fails with a clear message instead
      # of falling through to bare `kill -9` and dumping kill's usage text.
      set -l pid (ports pid "$argv[2]")
      if test -z "$pid"
        echo "ports: no process found on port $argv[2]" >&2
        return 1
      end
      kill -9 $pid
    case '*'
      echo "NAME:
  ports - a tool to easily see what's happening on your computer's ports
USAGE:
  ports [global options] command [command options] [arguments...]
COMMANDS:
  ls                list all open ports and the processes running in them
  show <port>       shows which process is running on a given port
  pid <port>        same as show, but prints only the PID
  kill <port>       kill the process is running in the given port with kill -9
GLOBAL OPTIONS:
  --help,-h         show help"
  end
end
