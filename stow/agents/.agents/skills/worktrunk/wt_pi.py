#!/usr/bin/env python3
"""Worktrunk task terminals and native Pi activity, with repository-local state."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


class WorkflowError(Exception):
    pass


def run(args, cwd=None, *, check=True, timeout=15, capture=True):
    env = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")}
    if args[0] == "ps":
        env["LC_ALL"] = "C"
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=capture,
                            stdin=subprocess.DEVNULL if capture else None, timeout=timeout)
    if check and result.returncode:
        raise WorkflowError(f"{args[0]} {args[1]} failed. Inspect that command locally before retrying.")
    return result


def git(cwd, *args, check=True):
    return run(["git", "-C", str(cwd), *args], check=check)


def repository(cwd):
    result = git(cwd, "rev-parse", "--show-toplevel", check=False)
    if result.returncode:
        raise WorkflowError("Run this command inside a Git worktree.")
    root = Path(result.stdout.strip()).resolve()
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    gitdir = Path(git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    return root, common.resolve(), gitdir.resolve()


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as error:
        raise WorkflowError("Invalid Worktrunk integration state; inspect the repository's Git directory.") from error


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        with temporary.open("x") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as stream:
        os.chmod(path, 0o600)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkflowError("Another Worktrunk integration command is active. Retry when it finishes.")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def worktrees(cwd):
    result = git(cwd, "worktree", "list", "--porcelain", "-z")
    rows, row = [], {}
    for field in result.stdout.split("\0"):
        if not field:
            if row:
                rows.append(row)
                row = {}
            continue
        key, _, value = field.partition(" ")
        row[key] = value
    if row:
        rows.append(row)
    return rows


def find_worktree(cwd, branch):
    matches = [row for row in worktrees(cwd) if row.get("branch") == f"refs/heads/{branch}"]
    if len(matches) != 1:
        raise WorkflowError("The branch must have exactly one existing worktree.")
    return Path(matches[0]["worktree"]).resolve()


def validate_branch(cwd, branch):
    if branch.startswith("pi-subagents/"):
        raise WorkflowError("Subagents owns the pi-subagents/ namespace. Use its lifecycle tools.")
    if branch.startswith("-") or git(cwd, "check-ref-format", f"refs/heads/{branch}", check=False).returncode:
        raise WorkflowError("Use a literal, valid local branch name.")


def require_approved(cwd):
    result = run(["wt", "-C", str(cwd), "config", "approvals", "list", "--format=json"])
    state = json.loads(result.stdout)
    if state.get("state") not in ("approved", "no_commands") or state.get("stale"):
        raise WorkflowError("Review project commands with 'wt config approvals list', then approve them deliberately with 'wt config approvals add'.")


def herdr(*args):
    result = run(["herdr", *args], timeout=40)
    response = json.loads(result.stdout)
    if not isinstance(response.get("result"), dict):
        raise WorkflowError("Herdr returned an unexpected response.")
    return response["result"]


def require_herdr():
    if os.environ.get("HERDR_ENV") != "1" or not os.environ.get("HERDR_PANE_ID"):
        raise WorkflowError("Run this command from a Herdr shell pane.")


def human_launcher():
    if os.environ.get("PI_CODING_AGENT") or os.environ.get("PI_SESSION_ID") or os.environ.get("AI_AGENT"):
        raise WorkflowError("This launcher is for human shell use. Agents must use Subagents project.open or managed delegation.")
    require_herdr()


def all_panes():
    panes = []
    for workspace in herdr("workspace", "list")["workspaces"]:
        panes.extend(herdr("pane", "list", "--workspace", workspace["workspace_id"])["panes"])
    return panes


def inside(path, root):
    if not path:
        return False
    return Path(path).resolve().is_relative_to(root)


def task_panes(root):
    return [pane for pane in all_panes()
            if any(inside(pane.get(key), root) for key in ("cwd", "foreground_cwd"))]


def task_state(root):
    _, _, gitdir = repository(root)
    state = read_json(gitdir / "wt-pi/task.json")
    if not isinstance(state, dict) or state.get("version") != 1 or state.get("path") != str(root):
        raise WorkflowError("This worktree is not owned by 'wt pi new'. Use its original owner's lifecycle tools.")
    return state


def open_task(root, branch, *, focus=True, resume=True):
    state = task_state(root)
    panes = task_panes(root)
    if panes:
        matching = [pane for pane in panes if pane["tab_id"] == state.get("tab_id")]
        if not matching:
            raise WorkflowError("This worktree already has terminals outside its recorded task tab. Resolve them before reopening.")
        if focus:
            herdr("tab", "focus", matching[0]["tab_id"])
        agents = herdr("agent", "list")["agents"]
        if any(agent["pane_id"] == state.get("pane_id") for agent in agents):
            return {"action": "reused", "path": str(root), "tab_id": matching[0]["tab_id"]}
        if not any(pane["pane_id"] == state.get("pane_id") for pane in matching):
            raise WorkflowError("The task's Pi pane was closed. Close the remaining task tab before reopening.")
        pane_ids = {pane["pane_id"] for pane in panes}
        if any(agent["pane_id"] in pane_ids for agent in agents) or activity(root):
            raise WorkflowError("Another agent is active in this worktree. Quit it before resuming the task's Pi pane.")
        herdr("agent", "start", f"pi-{uuid.uuid4().hex[:16]}", "--kind", "pi", "--pane", state["pane_id"],
              "--", "--continue")
        return {"action": "resumed", "path": str(root), "tab_id": state["tab_id"], "pane_id": state["pane_id"]}
    if activity(root):
        raise WorkflowError("Pi is already running outside this task tab. Quit that session before reopening here.")
    current = herdr("pane", "current", "--current")["pane"]
    _, common, _ = repository(root)
    try:
        _, caller_common, _ = repository(current.get("foreground_cwd") or current["cwd"])
    except WorkflowError:
        caller_common = None
    if caller_common != common:
        raise WorkflowError("Open this repository in a Herdr workspace first, then run the task launcher there.")
    created = herdr("tab", "create", "--workspace", current["workspace_id"],
                    "--cwd", str(root), "--label", branch, "--no-focus")
    pane = created["root_pane"]["pane_id"]
    state.update(tab_id=created["tab"]["tab_id"], pane_id=pane)
    _, _, gitdir = repository(root)
    write_json(gitdir / "wt-pi/task.json", state)
    if focus:
        herdr("tab", "focus", state["tab_id"])
    args = ["--continue"] if resume else ["--name", branch]
    herdr("agent", "start", f"pi-{uuid.uuid4().hex[:16]}", "--kind", "pi", "--pane", pane, "--", *args)
    return {"action": "opened", "path": str(root), "tab_id": state["tab_id"], "pane_id": pane}


def launch(args):
    human_launcher()
    root, common, _ = repository(Path.cwd())
    validate_branch(root, args.branch)
    current = herdr("pane", "current", "--current")["pane"]
    _, caller_common, _ = repository(current.get("foreground_cwd") or current["cwd"])
    if caller_common != common:
        raise WorkflowError("Run the task launcher from this repository's Herdr workspace.")
    with locked(common / "wt-pi-launch.lock"):
        if args.action == "new":
            require_approved(root)
            command = ["wt", "switch", "--create", args.branch, "--no-cd", "--format=json"]
            if args.base:
                command += ["--base", args.base]
            result = run(command, cwd=root, timeout=300)
            response = json.loads(result.stdout)
            target = find_worktree(root, args.branch)
            if (response.get("action") != "created" or response.get("created_branch") is not True
                    or response.get("branch") != args.branch
                    or Path(response.get("path", "")).resolve() != target):
                raise WorkflowError("Worktrunk returned an unexpected allocation. The checkout was retained for inspection.")
            _, target_common, gitdir = repository(target)
            if target_common != common or target == root:
                raise WorkflowError("Worktrunk returned an unexpected repository. The checkout was retained for inspection.")
            write_json(gitdir / "wt-pi/task.json", {"version": 1, "path": str(target)})
            require_approved(target)
        else:
            target = find_worktree(root, args.branch)
        return open_task(target, args.branch, focus=not args.no_focus, resume=args.action == "open")


def process_started(pid):
    result = run(["ps", "-p", str(pid), "-o", "lstart="], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def alive(record):
    return bool(record.get("started")) and process_started(record["pid"]) == record["started"]


def marker(root, branch):
    result = run(["wt", "-C", str(root), "config", "state", "marker", "get",
                  "--branch", branch, "--format=json"])
    return json.loads(result.stdout)


def activity(root, *, token=None, pid=None, status=None):
    root, _, gitdir = repository(root)
    result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = result.stdout.strip() or None
    directory = gitdir / "wt-pi"
    with locked(directory / "activity.lock"):
        state = read_json(directory / "activity.json")
        if state is None:
            if branch is None:
                return 0
            state = {"sessions": {}, "marker": None, "branch": branch}
        sessions = {key: value for key, value in state["sessions"].items() if alive(value)}
        if status == "clear":
            sessions.pop(token, None)
        if state["branch"] != branch:
            if sessions:
                if status != "clear":
                    raise WorkflowError("The branch changed beneath an active Pi session. Quit Pi before restarting it.")
                write_json(directory / "activity.json", {**state, "sessions": sessions})
                return len(sessions)
            previous_branch = state["branch"]
            if previous_branch and not git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{previous_branch}", check=False).returncode:
                previous = marker(root, previous_branch)
                if previous and previous.get("marker") == state["marker"]:
                    run(["wt", "-C", str(root), "config", "state", "marker", "clear", "--branch", previous_branch])
            state["marker"] = None
        if branch is None:
            write_json(directory / "activity.json", {"sessions": {}, "marker": None, "branch": None})
            return 0
        if token and status != "clear":
            started = process_started(pid)
            if started is None:
                raise WorkflowError("The reporting Pi process is no longer running.")
            sessions[token] = {"pid": pid, "started": started, "updated": time.time(), "status": status}
        current = marker(root, branch)
        current_value = current.get("marker") if isinstance(current, dict) else None
        previous = state["marker"]
        desired = next((symbol for name, symbol in (("blocked", "❗"), ("working", "🤖"), ("idle", "💬"))
                        if any(item["status"] == name for item in sessions.values())), None)
        if current_value in (None, previous):
            if desired:
                run(["wt", "-C", str(root), "config", "state", "marker", "set", desired, "--branch", branch])
            elif previous is not None:
                run(["wt", "-C", str(root), "config", "state", "marker", "clear", "--branch", branch])
            previous = desired
        write_json(directory / "activity.json", {"sessions": sessions, "marker": previous, "branch": branch})
        return len(sessions)


def check_remove(root):
    root, _, _ = repository(root)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if branch.startswith("pi-subagents/"):
        raise WorkflowError("Subagents owns this worktree. Use its lifecycle tools.")
    if activity(root):
        raise WorkflowError("Pi sessions are still active in this worktree. Quit them before removal.")
    require_herdr()
    if task_panes(root):
        raise WorkflowError("Herdr panes still reference this worktree. Send Hunk comments and close its task tab before removal.")
    result = run(["lsof", "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpn"], check=False)
    if result.returncode != 0 or not result.stdout:
        raise WorkflowError("Could not verify worktree process ownership.")
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid != os.getpid() and inside(line[1:], root):
            raise WorkflowError("A process still has its working directory in this worktree. Stop it before removal.")


def remove(args):
    require_herdr()
    root, common, _ = repository(Path.cwd())
    validate_branch(root, args.branch)
    with locked(common / "wt-pi-launch.lock"):
        target = find_worktree(root, args.branch)
        task_state(target)
        if inside(Path.cwd(), target):
            raise WorkflowError("Run removal from another worktree after closing the task tab.")
        check_remove(target)
        require_approved(target)
        if git(target, "status", "--porcelain", "--untracked-files=all").stdout:
            raise WorkflowError("The worktree has uncommitted files. Commit or preserve them before removal.")
        ignored = git(target, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
        if ignored and not args.discard_ignored:
            raise WorkflowError("The worktree contains ignored files. Preserve anything needed, then use --discard-ignored deliberately.")
        run(["wt", "remove", args.branch, "--foreground", "--no-delete-branch"], cwd=root, timeout=300,
            capture=False)
        return {"action": "removed", "branch_retained": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__, prog="wt pi")
    if sys.argv[1:2] == ["_activity"]:
        parser.add_argument("action", choices=("_activity",))
        parser.add_argument("token")
        parser.add_argument("pid", type=int)
        parser.add_argument("status", choices=("working", "idle", "blocked", "clear"))
    else:
        commands = parser.add_subparsers(dest="action", required=True)
        for name in ("new", "open"):
            command = commands.add_parser(name, help="Create a task and launch Pi." if name == "new" else "Focus a task tab or resume Pi.")
            command.add_argument("branch")
            command.add_argument("--no-focus", action="store_true")
            if name == "new":
                command.add_argument("--base")
        command = commands.add_parser("remove", help="Remove a stopped, clean task worktree; retain its branch.")
        command.add_argument("branch")
        command.add_argument("--discard-ignored", action="store_true")
        commands.add_parser("list", help="Refresh Pi activity markers and list worktrees.")
    args = parser.parse_args()
    try:
        if args.action in ("new", "open"):
            result = launch(args)
        elif args.action == "remove":
            result = remove(args)
        elif args.action == "_activity":
            if git(Path.cwd(), "rev-parse", "--show-toplevel", check=False).returncode:
                return
            if not re.fullmatch(r"[a-f0-9]{32}", args.token) or args.pid <= 0:
                raise WorkflowError("Invalid activity identity.")
            activity(Path.cwd(), token=args.token, pid=args.pid, status=args.status)
            return
        else:
            for row in worktrees(Path.cwd()):
                root = Path(row["worktree"])
                if root.exists():
                    activity(root)
            run(["wt", "list"], capture=False)
            return
        print(json.dumps(result))
    except (WorkflowError, OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, WorkflowError) else "Integration command failed. Inspect the task before retrying."
        print(f"wt pi: {message}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
