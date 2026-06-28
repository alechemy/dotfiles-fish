#!/usr/bin/env bash
#
# Fetches the Things URL auth token from 1Password and writes it into ~/.zshenv
# as `export THINGS_AUTH_TOKEN=...`. zsh sources ~/.zshenv on EVERY invocation
# (interactive or not), and Claude Code's Bash tool runs non-interactive zsh, so
# this is what makes the token reach the `things` skill's Things URL writes
# (update / json / cancel — the `add` command does not need it).
#
# Why ~/.zshenv and not a fish conf.d like build-context7-config.sh: the context7
# key feeds an MCP server, which inherits the env of a fish-launched `claude`.
# The `things` skill instead runs `op`/`python3`/`open` through the Bash *tool*,
# whose zsh does NOT inherit fish's env — proven: with context7.fish built, fish
# has CONTEXT7_API_KEY but the Bash tool does not. ~/.zshenv is the injection
# point the Bash tool actually honors.
#
# The secret lands in ~/.zshenv, which is OUTSIDE the dotfiles repo ($HOME is not
# a git repo), so it is never committed — no gitignore entry is needed. zsh is
# not stowed here (~/.zshrc and ~/.zprofile are plain files), so a direct write
# matches the existing layout. Single value, so this uses `op read` (like
# build-context7-config.sh), not the `op inject` template flow.
#
set -e

# Item: create a 1Password "API Credential" item named "Things URL Token" in the
# Private vault with the token in its `credential` field, or change OP_REF below
# to your item's id/field (see Things → Settings → General → Enable Things URLs →
# Manage for the token itself).
OP_REF="${THINGS_OP_REF:-op://Private/Things URL Token/credential}"
ZSHENV="$HOME/.zshenv"
BEGIN="# >>> things-token (managed by build-things-config.sh) — DO NOT EDIT >>>"
END="# <<< things-token <<<"

TMP=""
cleanup() {
    if [[ -n "$TMP" && -f "$TMP" ]]; then
        rm -f "$TMP"
    fi
}
trap cleanup EXIT

if ! command -v op >/dev/null 2>&1; then
  echo "Error: 1Password CLI (op) is not installed." >&2
  echo "  Install with: brew install --cask 1password-cli" >&2
  exit 1
fi
# `op vault list` rather than `op whoami`: with 1Password app integration
# enabled, `op whoami` reports "not signed in" even when data commands work.
if ! op vault list >/dev/null 2>&1; then
  echo "Error: 1Password CLI can't read your vaults." >&2
  echo "  Enable 1Password > Settings > Developer > 'Integrate with 1Password CLI', then unlock the app." >&2
  echo "  Or, for a temporary session: eval \$(op signin)" >&2
  exit 1
fi

echo "Fetching Things URL token from 1Password..."
KEY="$(op read "$OP_REF")"
if [ -z "$KEY" ]; then
  echo "Error: Things token at $OP_REF resolved empty." >&2
  exit 1
fi

# Rebuild ~/.zshenv: strip any prior managed block, preserve everything else,
# then append the fresh block. Idempotent and non-destructive.
TMP="$(mktemp)"
if [ -f "$ZSHENV" ]; then
  awk -v b="$BEGIN" -v e="$END" '
    $0==b {skip=1; next}
    skip && $0==e {skip=0; next}
    !skip {print}
  ' "$ZSHENV" >"$TMP"
fi
{
  echo "$BEGIN"
  echo "# Auto-generated from 1Password ($OP_REF) by scripts/build-things-config.sh."
  echo "# Gives zsh (the shell Claude Code's Bash tool runs) THINGS_AUTH_TOKEN, used"
  echo "# by the 'things' skill for Things URL writes (update/json/cancel)."
  echo "export THINGS_AUTH_TOKEN='$KEY'"
  echo "$END"
} >>"$TMP"
chmod 600 "$TMP"
mv "$TMP" "$ZSHENV"
chmod 600 "$ZSHENV"
TMP=""

echo "Successfully wrote THINGS_AUTH_TOKEN into $ZSHENV"
