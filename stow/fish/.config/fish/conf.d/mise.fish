if command -q mise
    # Defer `mise activate` until the first prompt fires. The activation
    # adds ~100-300 ms per shell startup; for interactive sessions we don't
    # need it until the user is about to type a command. Non-interactive
    # `fish -c '...'` invocations skip this entirely (no prompt event), so
    # mise shims must continue to work via `$PATH` for scripts that don't
    # show a prompt.
    function __mise_lazy_activate --on-event fish_prompt
        functions -e __mise_lazy_activate
        mise activate fish --quiet | source
    end
end
