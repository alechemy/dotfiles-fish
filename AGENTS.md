# Dotfiles agent instructions

Personal macOS dotfiles managed with GNU Stow. Packages under `stow/` mirror `$HOME`; `scripts/setup.sh` reconstructs the machine. Key tools are Fish, Homebrew, Mise, Pi, Ghostty, and Zed.

## Read before changing

`docs/dotfiles-reference.md` holds the detailed architecture and invariants moved out of always-loaded context. Read the relevant section before changing:

- Stow, setup, generated/seeded/merged config, git hooks, or secrets scanning.
- LaunchAgents, LaunchDaemons, AppleEvents, TCC-protected folders, or Python shebangs.
- DEVONthink ingestion, AppleScript line endings, pipeline logging, or SingleFile settings.
- Background jobs, display-dependent behavior, music organization, AeroSpace, or agent hooks.

Also read:

- `devonthink/AGENTS.md` for work under `devonthink/` or `stow/devonthink/`.
- The subsystem README or design document linked from the reference before changing a documented workflow.

## Repository-wide constraints

- Preserve the Stow mirror. A file destined for `~/.config/tool/config` belongs at `stow/<package>/.config/tool/config`. Restow only when files are added or removed.
- Never treat display count as docking state. Gate ultrawide behavior on the `DELL U4025QW` name. Gate power behavior independently through `~/.local/bin/should-run-background-job`.
- Do not stow app-owned files that are atomically rewritten. Use the repository's generated, seeded, fragment-merge, or copy-if-absent pattern.
- Before modifying launchd-driven code, read the AppleEvents and TCC sections in `docs/dotfiles-reference.md`. Interpreter identity and protected-folder staging are load-bearing.
- Keep secrets, credentials, machine identity, runtime state, and real third-party personal data out of git.
- Multiple agent sessions may modify this repo concurrently. Before amending, verify `HEAD` is the intended commit; otherwise create a new commit.
- Test tmux changes on an isolated socket. Never kill the default server.

## Validation

- Run `scripts/lint-launchd-plists.sh` after launchd or interpreter changes.
- Run `/usr/bin/python3 -m unittest discover -s devonthink/tests -t devonthink/tests` after DEVONthink pipeline changes.
- Run syntax checks and focused tests appropriate to every changed file.
- Run `git diff --check` and the staged betterleaks scan before committing.
