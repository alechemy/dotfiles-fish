set -g fish_greeting

# Catppuccin Macchiato is persisted as universal `fish_color_*` variables in
# fish_variables (set once via `fish_config theme save` during bootstrap).
# We deliberately don't call `fish_config theme choose` here: it would set
# colors with -g (per-shell) on every startup, adding ~120 ms of redundant
# work since the persisted universals already provide the same colors.

if test -f "$HOME/.cargo/env.fish"
    source "$HOME/.cargo/env.fish"
end

# Added by OrbStack: command-line tools and integration
# This won't be added again if you remove it.
source ~/.orbstack/shell/init2.fish 2>/dev/null || :

# LM Studio CLI (lms) — installed via `lms bootstrap`. Gate on directory
# existence so a machine without LM Studio doesn't carry a dead PATH entry.
if test -d ~/.lmstudio/bin
    fish_add_path ~/.lmstudio/bin
end

