function pi-local -d "pi against the local oMLX server"
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

    omlx launch pi $model_args $argv
end
