complete -c ports -f
complete -c ports -n __fish_use_subcommand -a ls -d "List all open ports"
complete -c ports -n __fish_use_subcommand -a show -d "Show process on a port"
complete -c ports -n __fish_use_subcommand -a pid -d "Print PID of process on a port"
complete -c ports -n __fish_use_subcommand -a kill -d "Send TERM to one verified listener"
complete -c ports -n "__fish_seen_subcommand_from kill" -l force -d "Permit KILL if TERM does not stop the original listener"
