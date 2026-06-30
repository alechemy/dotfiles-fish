#!/usr/bin/env python3
# Re-apply the workspace assignments from `[[on-window-detected]]` rules in
# .aerospace.toml to every currently open window. AeroSpace only fires those
# rules at window-creation time, so windows that were open before a rule was
# added (or that started on the wrong workspace for any other reason) stay
# stuck. This script reconciles them on demand.
#
# Triggered from service mode (`s` for sort).

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

CONFIG = Path.home() / ".dotfiles" / "stow" / "aerospace" / ".aerospace.toml"
MOVE_RE = re.compile(r"^move-node-to-workspace\s+(\S+)$")


def load_rules() -> list[tuple[dict, str]]:
    data = tomllib.loads(CONFIG.read_text())
    rules: list[tuple[dict, str]] = []
    for entry in data.get("on-window-detected", []):
        run = entry.get("run")
        runs = run if isinstance(run, list) else [run]
        target: str | None = None
        for cmd in runs:
            if not isinstance(cmd, str):
                continue
            m = MOVE_RE.match(cmd.strip())
            if m:
                target = m.group(1)
                break
        if target is None:
            continue
        matcher = entry.get("if", {})
        if not matcher:
            continue
        rules.append((matcher, target))
    return rules


def match(matcher: dict, app_id: str, app_name: str) -> bool:
    if "app-id" in matcher and matcher["app-id"] != app_id:
        return False
    if "app-name-regex-substring" in matcher:
        if not re.search(matcher["app-name-regex-substring"], app_name):
            return False
    return True


def list_windows() -> list[dict]:
    out = subprocess.run(
        [
            "aerospace", "list-windows", "--all", "--json",
            "--format", "%{window-id} %{app-bundle-id} %{app-name} %{workspace}",
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def main() -> int:
    rules = load_rules()
    if not rules:
        print("no workspace-assignment rules found", file=sys.stderr)
        return 1

    moves: list[str] = []
    for w in list_windows():
        app_id = w.get("app-bundle-id") or ""
        app_name = w.get("app-name") or ""
        current = str(w.get("workspace") or "")
        wid = w.get("window-id")
        for matcher, target in rules:
            if match(matcher, app_id, app_name):
                if current != target:
                    moves.append(f"move-node-to-workspace --window-id {wid} {target}")
                break

    if moves:
        # One eval batches every move into a single AeroSpace round-trip,
        # avoiding the per-window relayout flicker of moving them one at a time.
        subprocess.run(["aerospace", "eval", "--", " ; ".join(moves)], check=False)

    print(f"sorted {len(moves)} window(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
