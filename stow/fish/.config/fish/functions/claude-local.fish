function claude-local -d "Claude Code against the local oMLX server"
    set -l conf $HOME/.config/dt-pipeline/entities.conf
    set -l model_args
    set -l env_args

    if test -r $conf
        set -l entry (string match -r '^OMLX_MODEL=\S+' <$conf)
        if test -n "$entry"
            # Track the DEVONthink pipeline's model: a second resident set of
            # weights exceeds oMLX's memory ceiling, so any other choice makes
            # the two workloads evict each other on every alternation.
            set -l model (string replace 'OMLX_MODEL=' '' -- $entry)
            set model_args --model $model
            # The launcher remaps only the opus/sonnet/haiku tier aliases, so
            # a concrete model id in settings.json would boot unmapped.
            set env_args ANTHROPIC_MODEL=$model
        end
    end

    # MCP servers and plugins cost ~79k tokens of tool schemas per request here,
    # which a 3B-active model spends more on than it can use.
    env $env_args omlx launch claude $model_args --strict-mcp-config --settings '{"enabledPlugins":{}}' $argv
end
