complete -c ports -f
complete -c ports -n __fish_use_subcommand -a ls -d "List all open ports"
complete -c ports -n __fish_use_subcommand -a show -d "Show process on a port"
complete -c ports -n __fish_use_subcommand -a pid -d "Print PID of process on a port"
complete -c ports -n __fish_use_subcommand -a kill -d "Kill process on a port"
