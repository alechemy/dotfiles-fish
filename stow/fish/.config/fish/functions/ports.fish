function ports -d "manage processes by the ports they are using"
  switch $argv[1]
    case ls
      lsof -i -n -P
    case show
      lsof -nP -iTCP:"$argv[2]" -sTCP:LISTEN
    case pid
      lsof -t -nP -iTCP:"$argv[2]" -sTCP:LISTEN
    case kill
      # `kill` doesn't read PIDs from stdin — capture into a variable so an
      # empty result (no listener) fails with a clear message instead of
      # falling through to bare `kill -9` and dumping kill's usage text.
      set -l pid (ports pid "$argv[2]")
      if test -z "$pid"
        echo "ports: no process listening on port $argv[2]" >&2
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
  show <port>       shows the process(es) listening on a given port
  pid <port>        same as show, but prints only the PIDs
  kill <port>       kill -9 the process(es) listening on the given port
GLOBAL OPTIONS:
  --help,-h         show help"
  end
end
