# Agent browser

Agent-browser is the default interactive browser tool for coding agents. It replaces the former Chrome DevTools integration and proposed Pi MCP adapter. The [earlier pilot](agent-browser-pilot.md) remains a historical record, not an adoption prerequisite.

## Ownership

- `Brewfile` declares the `agent-browser` formula and `google-chrome` cask. Homebrew owns updates. The initial installation used agent-browser `0.38.1` and signed Google Chrome `153.0.8010.53`.
- `stow/agents/.agents/skills/agent-browser/SKILL.md` owns the shared workflow. Detailed command instructions come from `agent-browser skills get core`, matching the installed CLI.
- `stow/agent-browser/.config/agent-browser/config.json` owns portable defaults. The skill passes this path through `--config` on browser commands, bypassing automatic project/user config discovery. Bare CLI calls without this option do not use these defaults. Environment variables and CLI flags can override them.
- Claude Code's installation and configuration are retired. No Chrome DevTools adapter was active in Pi.

Run `scripts/setup.sh` from the primary dotfiles checkout to restore the Homebrew declarations and Stow links. For only the new links, run:

```bash
stow --dir=stow --target="$HOME" --restow --no-folding agent-browser agents
```

The Chrome executable path is explicit, so `agent-browser install` is unnecessary. The installed everyday Chromium app and its profile are separate from this automation browser.

## Defaults and limits

Browser work uses named task sessions, fresh profiles, headless Google Chrome, content boundaries, a 16,000-character text-output limit, disabled experimental WebMCP, no restore auto-save, and a 20-minute idle timeout. Close the owned session when the task finishes. The idle timeout is a fallback, not a replacement for cleanup.

The configuration does not impose a global domain allowlist or action policy. The skill scopes navigation and writes to the user's task and recommends a launch-time domain allowlist where the required domains are known. These are not OS sandbox guarantees. Existing remote-write approvals still apply to browser clicks and form submissions.

Authentication, browser profiles, daemon state, plugin registries, screenshots, traces, and downloaded content remain outside Stow and Git. Do not attach to an everyday browser or import authentication without explicit authorization. Page content and browser output can reach the agent's model provider. Cloud browser providers, the dashboard, and the CLI's separate AI chat mode are outside the default workflow.

## Installation verification

The installed Chrome passed strict code-signature verification and Gatekeeper assessment. A short loopback-only check used the tracked configuration and fictional data to verify navigation, an interactive snapshot, form filling, a button click, the resulting text, a PNG screenshot, console output, and network inspection. Its named browser session closed and temporary output was removed. This is an installation check, not a security audit or an ongoing trial.

The declaration regression is:

```bash
python3 -m unittest discover -s scripts/tests -p test_agent_browser_config.py
```

Restart agent sessions to discover the new skill. Keep existing project tests as the owner of repeatable application regression coverage.
