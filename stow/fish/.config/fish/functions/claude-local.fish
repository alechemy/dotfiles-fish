function claude-local -d "Claude Code against the local oMLX server"
    set -l conf $HOME/.config/dt-pipeline/entities.conf
    set -l model_args

    if test -r $conf
        set -l entry (string match -r '^OMLX_MODEL=\S+' <$conf)
        if test -n "$entry"
            # Track the DEVONthink pipeline's model: a second resident set of
            # weights exceeds oMLX's memory ceiling, so any other choice makes
            # the two workloads evict each other on every alternation.
            set model_args --model (string replace 'OMLX_MODEL=' '' -- $entry)
        end
    end

    # MCP servers and plugins cost ~79k tokens of tool schemas per request here,
    # which a 3B-active model spends more on than it can use.
    omlx launch claude $model_args --strict-mcp-config --settings '{"enabledPlugins":{}}' $argv
end
