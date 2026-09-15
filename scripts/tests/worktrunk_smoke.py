"""Two real task worktrees, native Pi markers, and isolated Hunk recipients."""

import json
from pathlib import Path
import shlex
import subprocess


def exercise_worktrunk(root, env, workspace, main_pane, api, wait, key, drain):
    control = api("tab", "create", "--workspace", workspace, "--cwd", str(root), "--no-focus")
    control_pane = control["root_pane"]["pane_id"]

    def command(name, arguments, expected=0):
        output = root / f"{name}.json"
        status = root / f"{name}.status"
        error = root / f"{name}.err"
        script = (f"wt pi {shlex.join(arguments)} > {shlex.quote(str(output))} "
                  f"2> {shlex.quote(str(error))}; echo $status > {shlex.quote(str(status))}")
        api("pane", "run", control_pane, script)
        wait(status.exists, name, seconds=40)
        actual = int(status.read_text().strip())
        assert actual == expected, f"{name}: exit {actual}: {error.read_text()}"
        return json.loads(output.read_text()) if actual == 0 else None

    def marker(path):
        result = subprocess.run(["wt", "-C", str(path), "config", "state", "marker", "get", "--format=json"],
                                env=env, capture_output=True, text=True, check=True, timeout=10)
        value = json.loads(result.stdout)
        return value.get("marker") if value else None

    tasks = [command(f"new-{name}", ["new", name]) for name in ("task-a", "task-b")]
    assert tasks[0]["path"] != tasks[1]["path"]
    for task in tasks:
        wait(lambda: marker(task["path"]) == "💬", "native idle marker")
    initial_tabs = len(api("tab", "list", "--workspace", workspace)["tabs"])
    reused = command("reuse-a", ["open", "task-a"])
    assert reused["tab_id"] == tasks[0]["tab_id"]
    assert len(api("tab", "list", "--workspace", workspace)["tabs"]) == initial_tabs
    command("refuse-live-remove", ["remove", "task-a"], expected=1)
    for index, task in enumerate(tasks):
        path = Path(task["path"])
        (path / "fixture.py").write_text(f"value = {index + 3}\n")
        api("tab", "focus", task["tab_id"])
        api("agent", "focus", task["pane_id"])
        drain(1)
        key(b"\x02f")

        def hunk(*args):
            result = subprocess.run(["hunk", "session", *args, "--repo", str(path), "--json"],
                                    env=env, capture_output=True, text=True, timeout=10)
            return result

        wait(lambda: hunk("get").returncode == 0, f"Hunk session for task {index}")
        assert hunk("navigate", "--file", "fixture.py", "--new-line", "1").returncode == 0
        drain(0.5)
        note = f"Synthetic feedback only for task {index}."
        key(b"c")
        key(note.encode() + b"\x13")

        def comments():
            result = hunk("comment", "list", "--type", "user")
            assert result.returncode == 0
            return json.loads(result.stdout)["comments"]

        wait(lambda: len(comments()) == 1, "task human comment")
        api("tab", "focus", task["tab_id"])
        api("agent", "focus", task["pane_id"])
        drain(1)
        if index == 0:
            (root / "waiting.json").unlink(missing_ok=True)
            (root / "confirm.json").unlink(missing_ok=True)
            key(b"/smoke-confirm\x13")
            wait(lambda: (root / "waiting.json").exists(), "task confirmation opened")
            wait(lambda: marker(path) == "❗", "native blocked marker")
            key(b"\x02F")
            drain(0.5)
            assert len(comments()) == 1
            assert not (root / "confirm.json").exists()
            key(b"\x1b")
            wait(lambda: marker(path) == "💬", "native idle after dialog")
        key(b"\x02F")
        receipt = root / f"input-{path.name}.json"
        wait(receipt.exists, "task-specific feedback receipt")
        assert note in json.loads(receipt.read_text())["text"]
        other = root / f"input-{Path(tasks[1 - index]['path']).name}.json"
        if other.exists():
            assert note not in other.read_text()
        wait(lambda: not comments(), "task comment cleanup")
        receipt.unlink()
        key(b"\x02F")
        drain(0.5)
        assert not receipt.exists(), "Repeated send delivered duplicate task feedback"
    task = tasks[1]
    api("pane", "send-keys", task["pane_id"], "ctrl+d")
    wait(lambda: marker(task["path"]) is None, "Pi quit in retained task tab")
    wait(lambda: not any(agent["pane_id"] == task["pane_id"] for agent in api("agent", "list")["agents"]),
         "task pane returned to its shell")
    resumed = command("resume-shell", ["open", "task-b"])
    assert resumed["action"] == "resumed"
    assert resumed["pane_id"] == task["pane_id"]
    wait(lambda: marker(task["path"]) == "💬", "native idle after shell resume")
    for index, task in enumerate(tasks):
        path = Path(task["path"])
        api("tab", "close", task["tab_id"])
        wait(lambda: marker(path) is None, "native marker cleared at shutdown")
        branch = f"task-{'a' if index == 0 else 'b'}"
        command(f"dirty-{branch}", ["remove", branch], expected=1)
        subprocess.run(["git", "-C", str(path), "add", "fixture.py"], env=env, check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                        "commit", "-qm", "fixture change"], env=env, check=True)
        removed = command(f"remove-{branch}", ["remove", branch])
        assert removed["branch_retained"] is True
        assert not path.exists()
        subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", f"refs/heads/{branch}"],
                       env=env, check=True, capture_output=True)
    api("tab", "close", control["tab"]["tab_id"])
    api("agent", "focus", main_pane)
    print("Worktrunk smoke passed: two tasks, idempotent tabs, native markers, isolated Hunk feedback and safe removal.")
