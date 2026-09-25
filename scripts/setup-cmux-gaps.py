#!/usr/bin/python3
"""Merge the AeroSpace layout automation into cmux's app-owned configuration."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

RULE_ID = "dotfiles.aerospace-gaps"
EVENTS = [
    "workspace.created", "workspace.selected", "workspace.closed", "workspace.moved",
    "window.created", "window.closed", "window.focused",
    "pane.created", "pane.closed", "pane.focused", "pane.resized",
    "pane.swapped", "pane.broken", "pane.joined", "surface.moved", "config.reloaded",
]


def install(path):
    if path.is_symlink():
        raise ValueError("Refusing a symlinked cmux automation config")
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original) if original is not None else {"version": 1, "rules": []}
    if config.get("version") != 1 or not isinstance(config.get("rules"), list):
        raise ValueError("Unsupported cmux automation config")
    if any(not isinstance(rule, dict) or not isinstance(rule.get("id"), str) for rule in config["rules"]):
        raise ValueError("Invalid cmux automation rules")
    rule = {
        "id": RULE_ID,
        "when": {"event": "*"},
        "where": {"name": EVENTS},
        "then": [{"action": "run", "command": '/usr/bin/python3 "$HOME/.local/bin/aerospace-cmux-gaps.py" --refresh'}],
        "rate_limit": {"interval_seconds": 1, "maximum": 32},
    }
    existing = [item for item in config["rules"] if item["id"] == RULE_ID]
    if len(existing) > 1:
        raise ValueError("Duplicate AeroSpace automation rules")
    if existing and "enabled" in existing[0]:
        rule["enabled"] = existing[0]["enabled"]
    updated = dict(config, rules=[item for item in config["rules"] if item["id"] != RULE_ID] + [rule])
    if updated == config:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if original is not None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = path.with_name(f"{path.name}.backup-{stamp}")
        with backup.open("xb") as output:
            os.chmod(backup, 0o600)
            output.write(original)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        try:
            json.dump(updated, temporary, indent=2)
            temporary.write("\n")
            temporary.close()
            os.replace(temporary.name, path)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        install(Path.home() / ".cmuxterm/automations.json")
    except (OSError, ValueError, TypeError, AttributeError):
        raise SystemExit("Could not install the cmux AeroSpace automation; existing configuration was preserved.")
