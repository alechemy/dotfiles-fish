#!/usr/bin/env bash
set -euo pipefail

direction="${1:-next}"

case "$direction" in
  next|prev) ;;
  *)
    echo "Usage: $0 [next|prev]" >&2
    exit 2
    ;;
esac

AEROSPACE_BIN="${AEROSPACE_BIN:-aerospace}"
STATE_FILE="${STATE_FILE:-$HOME/.hammerspoon-hidden-scratchpads.json}"

DIRECTION="$direction" AEROSPACE_BIN="$AEROSPACE_BIN" STATE_FILE="$STATE_FILE" python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

aerospace = os.environ.get("AEROSPACE_BIN", "aerospace")
direction = os.environ["DIRECTION"]  # next | prev
state_file = Path(os.environ["STATE_FILE"])


def run(*args: str) -> str:
    try:
        return subprocess.check_output(
            [aerospace, *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def parse_window_ids(raw: str):
    ids = []
    if not raw:
        return ids
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ids.append(int(line))
        except Exception:
            continue
    return ids


def load_hidden_ids(path: Path):
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return set()

    raw_ids = payload.get("hidden_window_ids", [])
    if not isinstance(raw_ids, list):
        return set()

    hidden = set()
    for item in raw_ids:
        try:
            hidden.add(int(item))
        except Exception:
            continue
    return hidden


ws = "".join(run("list-workspaces", "--focused").split())
if not ws:
    sys.exit(0)

all_ids = parse_window_ids(
    run("list-windows", "--workspace", ws, "--format", "%{window-id}")
)
if len(all_ids) <= 1:
    sys.exit(0)

hidden_ids = load_hidden_ids(state_file)
visible_ids = [wid for wid in all_ids if wid not in hidden_ids]

if len(visible_ids) <= 1:
    sys.exit(0)

focused_raw = run("list-windows", "--focused", "--format", "%{window-id}")
try:
    focused_id = int(focused_raw.strip())
except Exception:
    focused_id = None

if focused_id in visible_ids:
    i = visible_ids.index(focused_id)
    target = (
        visible_ids[(i + 1) % len(visible_ids)]
        if direction == "next"
        else visible_ids[(i - 1) % len(visible_ids)]
    )
else:
    target = visible_ids[0] if direction == "next" else visible_ids[-1]

subprocess.call(
    [aerospace, "focus", "--window-id", str(target)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
PY
