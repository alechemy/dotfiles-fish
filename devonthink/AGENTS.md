# DEVONthink agent instructions

This directory documents and tests a DEVONthink 4 document-processing pipeline. Stowable smart rules, launchd plists, and helper binaries live under `../stow/devonthink/`.

## Read before changing

- Read `README.md` for the canonical pipeline sequence, smart-rule criteria, metadata fields, setup, and test inventory.
- Read `docs/agent-reference.md` for detailed AppleScript conventions, pipeline invariants, privacy rules, manual-run behavior, MCP boundaries, and external dependencies.
- Read `docs/entities.md` before changing the entity layer or `entity-dt-bridge.js`.
- Read `docs/boox-local.md` before changing Boox or journal ingestion.
- Read the integration document under `docs/` before changing its workflow.
- Read `../docs/dotfiles-reference.md` before changing launchd interpreters, AppleEvents, TCC-sensitive folder access, AppleScript line endings, or shared pipeline logging.

## Hard constraints

- Use `application id "DNtp"` and `performSmartRule(theRecords)` in smart-rule AppleScript.
- Launchd automation talks to DEVONthink through `/usr/bin/osascript`, never through an interactive MCP server.
- Set `PIPELINE_MANUAL=1` before an agent or CI manually drives pipeline scripts, so watchdog notifications remain reserved for unattended failures.
- Never put a real third party's name, contact details, calendar data, or other identifier in tracked files. Use fictional reserved test data. Machine-local identifiers belong in `~/.config/dt-pipeline/entities.conf`.
- Preserve the smart-rule state machine and pre-flagging contract for programmatically created records. Read the detailed reference before changing any creator or metadata flag.
- A filesystem watcher that delays backlog handling must subscribe before starting the sweep, so changes during the delay cannot land in a startup blind spot.
- Record-body writers must preserve LF line endings; readers must tolerate CR, LF, and CRLF.

## Tests

```bash
/usr/bin/python3 -m unittest discover -s devonthink/tests -t devonthink/tests
```

For agent validation, use `PIPELINE_MANUAL=1 /usr/bin/python3 devonthink/tests/run_synthetic.py`. It isolates HOME and credential/provider environment before importing tests and excludes live Calendar/Contacts canaries. Focused tests need the same disposable HOME and fictional config/transport stubs. HOME does not isolate macOS account APIs.

The suite is stdlib-only. It stubs pipeline logging. Test new bridge handlers through the actual JXA harness as well as Python mocks, including recursive groups and missing date values for inventory handlers. Passive-envelope integration tests must exercise the production dispatcher and complete retry, promotion and application sequence; extracted handlers and retry-only tests do not cover those contracts.
