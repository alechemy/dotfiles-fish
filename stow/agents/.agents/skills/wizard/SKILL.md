---
name: wizard
description: Generate an interactive bash wizard that walks a human through steps only they can perform. Use when provisioning infrastructure, setting up credentials or CI secrets, walking an unfamiliar third-party dashboard, or running a one-off migration or cutover. Don't invoke this for steps the agent can perform itself.
license: MIT; see LICENSE.
compatibility: Requires Bash. Generated stages may need a browser or GitHub CLI.
metadata:
  source: https://github.com/mattpocock/skills/tree/3cca18b368ae95cdbdebbff572ccafa662551015/skills/engineering/wizard
  revision: 3cca18b368ae95cdbdebbff572ccafa662551015
---

# Wizard

Adapted from Matt Pocock's wizard skill. Generate a Bash script that walks a human through a manual procedure, opens the relevant URLs, explains what to click, captures values, and writes them to agreed destinations.

[template.sh](template.sh) supplies stage progress, confirmation gates, cross-platform URL opening, hidden secret entry, `.env` upserts, GitHub secret and variable writes, and a closing summary. Resolve this path relative to this skill directory. Read the template before copying it. Author stages below its `STAGES` marker; preserve the bundled library above it.

A wizard is temporary by default. Save it to an agreed scratch or `scripts/` path. Commit it only when the user wants a repeatable setup path in the repository.

## Permissions and privacy

Follow the active agent instructions for permissions, privacy, and verification. This skill grants no permission to publish, provision services, change remote secrets or variables, or perform destructive operations. Confirm the exact target and proposed actions with the user. Scope approval is not blanket permission for later remote writes. Put confirmation gates before remote writes and irreversible actions in the generated script.

Never read or print live `.env` files or generated configuration that may contain resolved secrets. Inspect tracked templates and variable names instead. If current configuration must be checked, use a redacting parser that reports only structural fields or whether a property exists. The human enters credentials in the local terminal, never in chat. Keep credentials out of script source, logs, command-line arguments, Git, and the closing summary. Do not enable shell tracing.

Agree on a bounded manual procedure. Automate steps the agent can safely perform; reserve the wizard for private account interaction and other steps requiring a human.

## Process

### 1. Scope the procedure

Read relevant repository documentation, safe configuration templates such as `.env.example`, `docker-compose*`, and `.github/workflows/*`. Identify the `secrets.*` and `vars.*` references relevant to the requested procedure. For a migration, establish the current state, target state, and irreversible actions.

Show the ordered stages and the values each produces. Ask the user to confirm, add, drop, or reorder them. For each value, establish where the human gets it, whether it is secret, and where it belongs: a local environment file, a GitHub secret or variable, both, or nowhere. Some stages capture no values.

### 2. Map each stage's journey

For each stage, specify the URL, precise UI path, action, and destination variable. Check current documentation where the UI or command is uncertain. Never invent dashboard steps. Ask one focused question if documentation cannot resolve the uncertainty.

### 3. Author the wizard

Copy `template.sh` to the agreed path. Replace its example with one focused `stage` per step, in dependency order. Set `TOTAL_STAGES` to the number of stages. Use `stage`, `say`/`step`, `open_url`, `ask`/`ask_secret`, `write_env`, `set_secret`/`set_var`, `pause`/`confirm`, and `finish`.

Open each URL before asking for its value. Use `ask_secret` for credentials. Persist only to the destinations agreed during scoping; some values must never enter a local `.env` file. Use `set_secret` only for values CI needs. Bind GitHub operations to the confirmed repository rather than relying on an ambiguous working directory.

Treat the template as a starting point, not a guarantee for every value or platform. Verify Bash compatibility and destination file permissions. Its `write_env` helper writes literal, single-line `KEY=VALUE` entries and replaces the destination file. Do not use it for multiline values, files needing preserved formatting or symlinks, or values requiring escaping without a reviewed adjustment to the generated copy. Keep generated credential files private and outside Git.

### 4. Verify and hand off

Run `bash -n <script>` and the project's applicable shell checks. Make the generated script executable.

Do not run or source the complete wizard yourself. It opens browsers, blocks on input, and can change local or remote state. Trace the stages statically. Use isolated tests with synthetic values and stubbed external commands when testing helper behavior.

Check that every scoped value is captured and reaches only its agreed destinations, every `set_secret` name matches the relevant CI reference, and every privileged action has the required confirmation. The summary must distinguish completed and skipped work without printing values.

Tell the user how to run the script and report any verification gaps. For a requested repeatable setup path, link it from the repository documentation. Do not delete the wizard until the user confirms the procedure is complete.
