#!/usr/bin/python3
"""Cache cmux column counts from native automation for AeroSpace auto-gaps."""

import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

HOME = Path.home()
AUTO_GAPS = HOME / ".dotfiles/scripts/aerospace-auto-gaps.sh"
STATE = HOME / ".cache/aerospace-gaps"
SHRINK_DELAY = 2
ERRORS = (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError)


def environment():
    env = dict(os.environ)
    for key in ("CMUX_WORKSPACE_ID", "CMUX_SURFACE_ID", "CMUX_TAB_ID"):
        env.pop(key, None)
    env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def query(*args):
    result = subprocess.run(
        ["cmux", "--json", "--id-format", "uuids", *args],
        env=environment(), capture_output=True, text=True, check=True, timeout=2,
    )
    return json.loads(result.stdout)


def column_count(panes):
    frames = []
    for pane in panes:
        frame = pane.get("pixel_frame")
        if not isinstance(frame, dict) or not pane.get("surface_count"):
            continue
        values = [frame.get(key) for key in ("x", "y", "width", "height")]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            continue
        x, y, width, height = values
        if width > 0 and height > 0:
            frames.append((x, y, x + width, y + height))

    edges = sorted({edge for frame in frames for edge in (frame[1], frame[3])})
    columns = 0
    for top, bottom in zip(edges, edges[1:]):
        row = (top + bottom) / 2
        intervals = sorted((right, left) for left, y, right, end in frames if y <= row < end)
        count, previous = 0, -math.inf
        for right, left in intervals:
            if left >= previous - 0.5:
                count += 1
                previous = right
        columns = max(columns, count)
    return columns


def selected_workspaces(snapshot):
    return {window["id"]: window["selected_workspace_id"] for window in snapshot["windows"]}


def window_columns():
    windows = selected_workspaces(query("rpc", "window.list"))
    identities = {}
    for terminal in query("debug-terminals")["terminals"]:
        number, window = terminal.get("window_number"), terminal.get("window_id")
        if isinstance(number, int) and number > 0 and window in windows:
            identities.setdefault(number, set()).add(window)
    columns = {}
    for number, ids in identities.items():
        if len(ids) != 1:
            continue
        window = next(iter(ids))
        workspace = windows[window]
        if not workspace:
            continue
        params = json.dumps({"window_id": window, "workspace_id": workspace})
        columns[str(number)] = max(1, column_count(query("rpc", "pane.list", params)["panes"]))
    if selected_workspaces(query("rpc", "window.list")) != windows:
        raise ValueError("Workspace selection changed during sampling")
    return columns


def boot_id():
    return subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                                   text=True, timeout=2).strip()


def needs_full_width(native_ids, tiled_count):
    snapshot = read_snapshot(STATE / "cmux-columns.json")
    if snapshot.get("boot_id") != boot_id():
        return False
    columns = snapshot["window_columns"]
    extra = sum(columns.get(str(number), 1) - 1 for number in native_ids)
    return extra > 0 and tiled_count + extra >= 3


def aerospace(*args):
    return subprocess.run(
        ["aerospace", *args], env=environment(), capture_output=True,
        text=True, check=True, timeout=2,
    ).stdout.strip()


def tiled_windows(workspace):
    rows = json.loads(aerospace(
        "list-windows", "--workspace", workspace, "--json", "--format",
        "%{window-id} %{app-name} %{window-layout} "
        "%{workspace-root-container-layout} %{window-is-fullscreen}",
    ))
    return sorted((row for row in rows if row["window-layout"] != "floating"),
                  key=lambda row: row["window-id"])


def sizing_state(cache, current_boot):
    try:
        snapshot = json.loads(cache.read_text())
        if snapshot.get("boot_id") == current_boot and isinstance(snapshot.get("workspaces"), dict):
            return snapshot["workspaces"]
    except ERRORS:
        pass
    return {}


def resize_pair(workspace, screen_width, gap):
    """Apply column proportions to a horizontal pair under the gap worker's lock."""
    if screen_width <= 2 * gap or gap < 0:
        return
    if aerospace("list-workspaces", "--focused") != workspace:
        return
    windows = tiled_windows(workspace)
    if len(windows) != 2 or any(
        row["window-layout"] != "h_tiles"
        or row["workspace-root-container-layout"] != "h_tiles"
        or row["window-is-fullscreen"] for row in windows
    ):
        return
    current_boot = boot_id()
    snapshot = read_snapshot(STATE / "cmux-columns.json")
    columns = snapshot.get("window_columns", {}) if snapshot.get("boot_id") == current_boot else {}
    weights = [columns.get(str(row["window-id"]), 1) if row["app-name"].lower() == "cmux" else 1
               for row in windows]
    total = sum(weights)
    if (total > 2) != (gap == 0):
        return
    cache = STATE / "cmux-sizing.json"
    states = sizing_state(cache, current_boot)
    previous = states.get(workspace)
    signature = {"windows": [row["window-id"] for row in windows], "columns": weights,
                 "screen_width": screen_width, "gap": gap}
    if previous == signature:
        return
    if total == 2 and (not isinstance(previous, dict) or previous.get("windows") != signature["windows"]):
        return
    index = min(range(2), key=lambda i: (weights[i], windows[i]["app-name"].lower() == "cmux", i))
    width = round((screen_width - 2 * gap) * weights[index] / total)
    if windows != tiled_windows(workspace) or aerospace("list-workspaces", "--focused") != workspace:
        return
    aerospace("resize", "--window-id", str(windows[index]["window-id"]), "width", str(width))
    if total > 2:
        states[workspace] = signature
    else:
        states.pop(workspace, None)
    write_snapshot(cache, {"boot_id": current_boot, "workspaces": states})


def recompute():
    try:
        if (STATE / "display-mode").read_text().strip() != "docked":
            return
        subprocess.run(["/bin/bash", str(AUTO_GAPS), "cmux-layout"], env=environment(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def sample_snapshot():
    current_boot = boot_id()
    try:
        columns = window_columns()
    except ERRORS:
        columns = {}
    return {"boot_id": current_boot, "window_columns": columns, "revision": uuid.uuid4().hex}


def read_snapshot(cache):
    try:
        snapshot = json.loads(cache.read_text())
        if isinstance(snapshot, dict) and isinstance(snapshot.get("window_columns"), dict):
            if all(number.isdecimal() and int(number) > 0 and type(count) is int and count >= 1
                   for number, count in snapshot["window_columns"].items()):
                return snapshot
    except ERRORS:
        pass
    return {}


def write_snapshot(cache, snapshot):
    with tempfile.NamedTemporaryFile(mode="w", dir=STATE, delete=False) as temporary:
        try:
            json.dump(snapshot, temporary)
            temporary.close()
            os.replace(temporary.name, cache)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


def refresh():
    STATE.mkdir(parents=True, exist_ok=True)
    cache = STATE / "cmux-columns.json"
    with (STATE / "cmux-lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        time.sleep(0.2)
        snapshot = sample_snapshot()
        previous = read_snapshot(cache)
        held = previous.get("window_columns", {}) if previous.get("boot_id") == snapshot["boot_id"] else {}
        columns = snapshot["window_columns"]
        shrinking = any(columns.get(number, 1) < count for number, count in held.items())
        snapshot["window_columns"] = {
            number: max(held.get(number, 1), columns.get(number, 1))
            for number in held.keys() | columns.keys()
            if number in columns or held[number] > 1
        }
        write_snapshot(cache, snapshot)
        deadline = time.monotonic() + SHRINK_DELAY
    recompute()
    if not shrinking:
        return
    time.sleep(max(0, deadline - time.monotonic()))
    with (STATE / "cmux-lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if read_snapshot(cache).get("revision") != snapshot["revision"]:
            return
        write_snapshot(cache, sample_snapshot())
    recompute()


def main(args):
    """Refresh, resize with --resize WORKSPACE SCREEN_WIDTH GAP, or test TILED_COUNT ID [...]."""
    try:
        if args == ["--refresh"]:
            refresh()
            return 0
        if len(args) == 4 and args[0] == "--resize":
            resize_pair(args[1], int(args[2]), int(args[3]))
            return 0
        if len(args) < 2:
            return 1
        tiled_count = int(args[0])
        native_ids = {int(value) for value in args[1:]}
        if tiled_count < len(native_ids) or any(number <= 0 for number in native_ids):
            return 1
        return 0 if needs_full_width(native_ids, tiled_count) else 1
    except ERRORS:
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
