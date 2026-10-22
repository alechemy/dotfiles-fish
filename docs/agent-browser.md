# Agent browser

Pi declares the unversioned `npm:pi-agent-browser-native` package; the initial verified release is 0.9.6. Upstream owns the native tools, operating instructions, reference guards, code worker, and browser lifecycle. Our former shared `agent-browser` skill is retired. The [earlier pilot](agent-browser-pilot.md) remains a historical record.

## Installation

- Homebrew owns the `agent-browser` formula and `google-chrome` cask. Installed versions are CLI 0.39.0 and Chrome 155.0.8059.40.
- Do not run `agent-browser install` or npm-global installs.
- After installing or updating the package, fully restart Pi. `/reload` can retain previously compiled code.

## Configuration

- `stow/agent-browser/.config/agent-browser/config.json` owns the seven portable browser defaults.
- Fish's `env.fish` exports `AGENT_BROWSER_CONFIG=$HOME/.config/agent-browser/config.json`, avoiding implicit project config discovery. Outside Fish, pass the same path to the native `agent_browser` args via `--config`.
- `restoreSave: "never"` disables automatic authentication-state saving. The other defaults select headless Google Chrome, content boundaries, a 16,000-character output limit, disabled WebMCP, and a 20-minute idle timeout.

## Sessions

- Parent and descendants share one root browser when the extension loads in children; independent roots stay isolated.
- Cloud children do not load the extension by default, so browser tasks stay on the parent unless a child is explicitly configured.
- Close the task-owned browser when work finishes. Never use `close --all`. Root browsers otherwise survive Pi exit; the idle timeout is a fallback, not a replacement.

## Boundaries

- Privacy, auth approval, artifact hygiene, sandbox constraints, domain scoping, and cleanup live in the global AGENTS.md, not a separate skill.
- Keep `pi-web-access` for research and `cmux` for user-requested browser panes.

## Verification

The installation trial used the published package with the Pi 1.1.0 SDK and no model calls. It exercised a loopback-only fictional form, verified accessible refs, filling and clicking, visible text, browser-code execution, QA with console, network, and page-error checks, an inline PNG screenshot, stale-ref rejection, and exact session close. This is a compatibility check, not a security audit.

Config regression:

```bash
python3 -m unittest discover -s scripts/tests -p test_agent_browser_config.py
```
