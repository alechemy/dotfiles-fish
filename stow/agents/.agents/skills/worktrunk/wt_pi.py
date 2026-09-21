#!/usr/bin/env python3
"""Worktrunk task terminals, Pi activity, and local review feedback."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import uuid


class WorkflowError(Exception):
    pass


def run(args, cwd=None, *, check=True, timeout=15, capture=True, input_text=None):
    env = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")}
    if args[0] == "ps":
        env["LC_ALL"] = "C"
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=capture,
                            input=input_text,
                            stdin=subprocess.DEVNULL if capture and input_text is None else None,
                            timeout=timeout)
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


def cmux(*args, timeout=40):
    result = run(["cmux", "--json", "--id-format", "uuids", *args], timeout=timeout)
    response = json.loads(result.stdout)
    if not isinstance(response, dict):
        raise WorkflowError("cmux returned an unexpected response.")
    return response


def require_cmux():
    if not os.environ.get("CMUX_WORKSPACE_ID") or not os.environ.get("CMUX_SURFACE_ID"):
        raise WorkflowError("Run this command from a native cmux terminal.")


def human_backend():
    if os.environ.get("PI_CODING_AGENT") or os.environ.get("PI_SESSION_ID") or os.environ.get("AI_AGENT"):
        raise WorkflowError("This launcher is for human shell use. Agents must use Subagents project.open or managed delegation.")
    if os.environ.get("CMUX_WORKSPACE_ID") and os.environ.get("CMUX_SURFACE_ID"):
        return "cmux"
    raise WorkflowError("Run this command from a native cmux terminal.")


def human_launcher():
    return human_backend()


def inside(path, root):
    if not path:
        return False
    try:
        return Path(path).resolve().is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def task_state(root):
    root, common, gitdir = repository(root)
    state = read_json(gitdir / "wt-pi/task.json")
    if not isinstance(state, dict) or state.get("version") != 2 or state.get("path") != str(root):
        raise WorkflowError("This worktree is not owned by the cmux task launcher.")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if (state.get("backend") != "cmux" or state.get("repository") != str(common)
            or state.get("branch") != branch):
        raise WorkflowError("The task binding no longer matches this worktree. Inspect it before continuing.")
    return state


def write_task_state(root, state):
    _, _, gitdir = repository(root)
    with locked(gitdir / "wt-pi/task.lock"):
        write_json(gitdir / "wt-pi/task.json", state)


def cmux_tree(workspace_id=None):
    args = ["tree"]
    if workspace_id:
        args += ["--workspace", workspace_id]
    else:
        args.append("--all")
    return cmux(*args)


def cmux_surfaces(tree):
    for window in tree.get("windows", []):
        for workspace in window.get("workspaces", []):
            for pane in workspace.get("panes", []):
                for surface in pane.get("surfaces", []):
                    yield window, workspace, pane, surface


def cmux_bound_surface(state):
    workspace_id, surface_id = state.get("workspace_id"), state.get("surface_id")
    if not workspace_id and not surface_id:
        return None
    if not workspace_id or not surface_id:
        raise WorkflowError("The cmux task binding is incomplete. Inspect the recorded workspace before retrying.")
    matches = [(window, workspace, pane, surface)
               for window, workspace, pane, surface in cmux_surfaces(cmux_tree(workspace_id))
               if workspace.get("id") == workspace_id and surface.get("id") == surface_id]
    if len(matches) != 1:
        raise WorkflowError("The recorded cmux surface is stale or ambiguous. Inspect the task before retrying.")
    surface = matches[0][3]
    if surface.get("type") not in (None, "terminal"):
        raise WorkflowError("The recorded cmux surface is not a terminal.")
    if surface.get("cwd") and not inside(surface["cwd"], Path(state["path"])):
        raise WorkflowError("The recorded cmux surface no longer belongs to this task worktree.")
    return matches[0]


def cmux_task_surfaces(root, state):
    entries = list(cmux_surfaces(cmux_tree()))
    workspace_entries = [entry for entry in entries if entry[1].get("id") == state.get("workspace_id")]
    exact = [entry for entry in workspace_entries if entry[3].get("id") == state.get("surface_id")]
    if exact:
        return exact
    if workspace_entries:
        raise WorkflowError("The recorded cmux workspace exists but its task surface is stale. Inspect it before removal.")
    return [entry for entry in entries if entry[3].get("cwd") and inside(entry[3]["cwd"], root)]


def cmux_saved_session_active(state):
    session_id = state.get("session_id")
    if not session_id:
        return False
    response = cmux("sessions", "list", "--agent", "pi", "--session", session_id,
                    "--workspace", state["workspace_id"], "--surface", state["surface_id"])
    sessions = response.get("sessions")
    if not isinstance(sessions, list):
        raise WorkflowError("cmux returned an unexpected session inventory.")
    exact = [item for item in sessions if item.get("session_id") == session_id
             and item.get("workspace_id") == state["workspace_id"]
             and item.get("surface_id") == state["surface_id"]]
    if len(exact) > 1:
        raise WorkflowError("cmux reported ambiguous Pi session ownership for this task.")
    if not exact:
        return False
    item = exact[0]
    return bool(item.get("active_for_surface") and item.get("agent_lifecycle") in ("running", "idle", "needsInput"))


def process_started(pid):
    result = run(["ps", "-p", str(pid), "-o", "lstart="], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def alive(record):
    return bool(record.get("started")) and process_started(record["pid"]) == record["started"]


def marker(root, branch):
    result = run(["wt", "-C", str(root), "config", "state", "marker", "get",
                  "--branch", branch, "--format=json"])
    return json.loads(result.stdout)


def owner_matches(record, owner):
    if not owner:
        return False
    return (record.get("backend") == "cmux"
            and record.get("workspace_id") == owner.get("workspace_id")
            and record.get("surface_id") == owner.get("surface_id")
            and (not owner.get("session_id") or record.get("session_id") == owner.get("session_id")))


def activity(root, *, token=None, pid=None, status=None, owner=None, session_id=None,
             backend=None, workspace_id=None, surface_id=None, feedback_socket=None,
             feedback_token=None):
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
            detected_backend = backend or ("cmux" if surface_id else "standalone")
            record = {"pid": pid, "started": started, "updated": time.time(), "status": status,
                      "backend": detected_backend, "session_id": session_id,
                      "workspace_id": workspace_id, "surface_id": surface_id,
                      "feedback_socket": feedback_socket, "feedback_token": feedback_token}
            sessions[token] = {key: value for key, value in record.items() if value is not None}
            if detected_backend == "cmux" and session_id and workspace_id and surface_id:
                task_path = gitdir / "wt-pi/task.json"
                task = read_json(task_path)
                if isinstance(task, dict) and task.get("version") == 2 and task.get("backend") == "cmux":
                    with locked(gitdir / "wt-pi/task.lock"):
                        latest = read_json(task_path)
                        if not isinstance(latest, dict):
                            raise WorkflowError("The task binding disappeared during activity reporting.")
                        intended = latest.get("session_id")
                        changed = False
                        if intended and intended != session_id:
                            raise WorkflowError("The Pi session does not match the task's recorded conversation.")
                        ids_match = (latest.get("workspace_id") == workspace_id
                                     and latest.get("surface_id") == surface_id)
                        if not ids_match:
                            if intended != session_id:
                                raise WorkflowError("The Pi session does not match the recorded cmux task surface.")
                            entries = list(cmux_surfaces(cmux_tree()))
                            old = [entry for entry in entries
                                   if entry[1].get("id") == latest.get("workspace_id")
                                   and entry[3].get("id") == latest.get("surface_id")]
                            replacement = [entry for entry in entries
                                           if entry[1].get("id") == workspace_id
                                           and entry[3].get("id") == surface_id]
                            if old or len(replacement) != 1:
                                raise WorkflowError("The restored Pi session has ambiguous cmux ownership.")
                            surface = replacement[0][3]
                            if surface.get("cwd") and not inside(surface["cwd"], root):
                                raise WorkflowError("The restored cmux surface does not belong to this task.")
                            latest["workspace_id"] = workspace_id
                            latest["surface_id"] = surface_id
                            changed = True
                        if not intended:
                            latest["session_id"] = session_id
                            changed = True
                        if changed:
                            write_json(task_path, latest)
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
        owned = sum(1 for record in sessions.values() if owner_matches(record, owner))
        return len(sessions) - min(1, owned)


def active_records(root):
    activity(root)
    _, _, gitdir = repository(root)
    state = read_json(gitdir / "wt-pi/activity.json", {"sessions": {}})
    return [record for record in state.get("sessions", {}).values() if alive(record)]


def cmux_owner(state):
    return {"backend": "cmux", "workspace_id": state.get("workspace_id"),
            "surface_id": state.get("surface_id"), "session_id": state.get("session_id")}


def matching_activity(root, state):
    owner = cmux_owner(state)
    return [record for record in active_records(root) if owner_matches(record, owner)]


def prepare_cmux_launch(root, state):
    previous = state.get("launch")
    if isinstance(previous, dict) and previous.get("status") == "creating":
        raise WorkflowError("A previous cmux workspace launch has an unknown outcome. Inspect it before retrying.")
    launch_token = uuid.uuid4().hex
    state["launch"] = {"token": launch_token, "status": "creating", "requested_at": time.time()}
    write_task_state(root, state)
    return launch_token


def wait_for_cmux_binding(root, workspace_id, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = task_state(root)
        if state.get("workspace_id") == workspace_id and state.get("surface_id"):
            return state
        time.sleep(0.05)
    raise WorkflowError("cmux created the task workspace but its terminal did not bind. Inspect the workspace before retrying.")


def create_cmux_task(root, branch, state, *, focus, resume):
    launch_token = prepare_cmux_launch(root, state)
    command = shlex.join(["wt-pi", "_start", launch_token])
    response = cmux("new-workspace", "--name", branch, "--cwd", str(root), "--command", command,
                    "--focus", "true" if focus else "false")
    workspace_id = response.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise WorkflowError("cmux returned an unexpected workspace identity. The checkout was retained for inspection.")
    _, _, gitdir = repository(root)
    task_path = gitdir / "wt-pi/task.json"
    with locked(gitdir / "wt-pi/task.lock"):
        latest = read_json(task_path)
        launch = latest.get("launch") if isinstance(latest, dict) else None
        if (not isinstance(latest, dict) or latest.get("version") != 2
                or latest.get("backend") != "cmux" or latest.get("path") != str(root)
                or not isinstance(launch, dict) or launch.get("token") != launch_token
                or latest.get("workspace_id") not in (None, workspace_id)):
            raise WorkflowError("cmux created a workspace that conflicts with the recorded task binding.")
        latest["workspace_id"] = workspace_id
        write_json(task_path, latest)
    bound = wait_for_cmux_binding(root, workspace_id)
    return {"action": "resumed" if resume else "opened", "path": str(root),
            "workspace_id": workspace_id, "surface_id": bound["surface_id"]}


def open_cmux_task(root, branch, *, focus=True, resume=True):
    state = task_state(root)
    bound = cmux_bound_surface(state)
    if bound is None:
        return create_cmux_task(root, branch, state, focus=focus, resume=resume)
    foreign = activity(root, owner=cmux_owner(state))
    if foreign:
        raise WorkflowError("Another agent is active in this worktree. Quit it before resuming the task's Pi surface.")
    owned = matching_activity(root, state)
    if len(owned) > 1:
        raise WorkflowError("Multiple Pi sessions claim this task surface. Resolve them before reopening.")
    if owned or cmux_saved_session_active(state):
        if focus:
            cmux("select-workspace", "--workspace", state["workspace_id"])
        return {"action": "reused", "path": str(root), "workspace_id": state["workspace_id"],
                "surface_id": state["surface_id"]}
    launch = state.get("launch")
    if isinstance(launch, dict) and launch.get("pid") and process_started(launch["pid"]) == launch.get("started"):
        if focus:
            cmux("select-workspace", "--workspace", state["workspace_id"])
        return {"action": "reused", "path": str(root), "workspace_id": state["workspace_id"],
                "surface_id": state["surface_id"]}
    launch_token = prepare_cmux_launch(root, state)
    command = shlex.join(["wt-pi", "_start", launch_token])
    cmux("respawn-pane", "--workspace", state["workspace_id"], "--surface", state["surface_id"],
         "--command", command)
    if focus:
        cmux("select-workspace", "--workspace", state["workspace_id"])
    return {"action": "resumed", "path": str(root), "workspace_id": state["workspace_id"],
            "surface_id": state["surface_id"]}


def start_cmux_pi(launch_token):
    require_cmux()
    root, common, gitdir = repository(Path.cwd())
    task_path = gitdir / "wt-pi/task.json"
    workspace_id = os.environ["CMUX_WORKSPACE_ID"]
    surface_id = os.environ["CMUX_SURFACE_ID"]
    with locked(gitdir / "wt-pi/task.lock"):
        state = read_json(task_path)
        launch = state.get("launch") if isinstance(state, dict) else None
        if (not isinstance(state, dict) or state.get("version") != 2 or state.get("backend") != "cmux"
                or state.get("path") != str(root) or state.get("branch") != git(root, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
                or not isinstance(launch, dict) or launch.get("token") != launch_token
                or launch.get("status") != "creating"):
            raise WorkflowError("The cmux launch token does not match this task.")
        if state.get("repository") != str(common):
            raise WorkflowError("The cmux task repository identity changed.")
        if state.get("workspace_id") not in (None, workspace_id) or state.get("surface_id") not in (None, surface_id):
            raise WorkflowError("The cmux launch surface does not match the task binding.")
        state["workspace_id"] = workspace_id
        state["surface_id"] = surface_id
        state["launch"] = {**launch, "status": "bound", "pid": os.getpid(),
                           "started": process_started(os.getpid())}
        write_json(task_path, state)
    for key in list(os.environ):
        if key == "HERDR_ENV" or key.startswith("HERDR_"):
            os.environ.pop(key, None)
    args = ["pi", "--session", state["session_id"]] if state.get("session_id") else ["pi", "--name", state["branch"]]
    os.execvp(args[0], args)


def launch(args):
    human_launcher()
    root, common, _ = repository(Path.cwd())
    validate_branch(root, args.branch)
    require_cmux()
    current = [entry for entry in cmux_surfaces(cmux_tree(os.environ["CMUX_WORKSPACE_ID"]))
               if entry[3].get("id") == os.environ["CMUX_SURFACE_ID"]]
    if len(current) != 1 or (current[0][3].get("cwd") and not inside(current[0][3]["cwd"], root)):
        raise WorkflowError("Run the task launcher from this repository's native cmux terminal.")
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
                    or response.get("branch") != args.branch or Path(response.get("path", "")).resolve() != target):
                raise WorkflowError("Worktrunk returned an unexpected allocation. The checkout was retained for inspection.")
            _, target_common, _ = repository(target)
            if target_common != common or target == root:
                raise WorkflowError("Worktrunk returned an unexpected repository. The checkout was retained for inspection.")
            state = {"version": 2, "backend": "cmux", "path": str(target),
                     "repository": str(common), "branch": args.branch}
            write_task_state(target, state)
            require_approved(target)
        else:
            target = find_worktree(root, args.branch)
            task_state(target)
        return open_cmux_task(target, args.branch, focus=not args.no_focus, resume=args.action == "open")


def feedback_paths(root):
    _, _, gitdir = repository(root)
    directory = gitdir / "wt-pi"
    return directory / "feedback.json", directory / "feedback.lock", directory / "reviews.json", directory / "reviews.lock"


def validated_comment(value):
    if not isinstance(value, dict) or value.get("source") != "user":
        raise WorkflowError("Feedback contains an invalid comment source.")
    comment_id = value.get("id")
    summary = value.get("summary")
    path = value.get("path")
    if not all(isinstance(item, str) and item for item in (comment_id, summary, path)):
        raise WorkflowError("Feedback contains an invalid comment.")
    if len(comment_id) > 256 or len(summary) > 8000 or len(path) > 2000:
        raise WorkflowError("Feedback exceeds the supported size.")
    comment = {key: value[key] for key in ("id", "source", "path", "summary", "rationale", "resolution", "side", "line")
               if key in value and value[key] is not None}
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        fingerprint = hashlib.sha256(json.dumps(comment, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    comment["fingerprint"] = fingerprint
    return comment


def review_event(root, action, pid, payload, tracking_token):
    if not re.fullmatch(r"[a-f0-9]{32}", tracking_token):
        raise WorkflowError("Invalid review tracking token.")
    _, _, reviews_path, reviews_lock = feedback_paths(root)
    pending_path = reviews_path.parent / f"review-pending-{tracking_token}.json"
    comment_id = payload.get("id") if isinstance(payload, dict) else None
    write_json(pending_path, {"version": 1, "comment_id": comment_id,
                              "hunk_pid": pid, "hunk_started": process_started(pid)})
    with locked(reviews_lock):
        state = read_json(reviews_path, {"version": 1, "comments": {}})
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("comments"), dict):
            raise WorkflowError("Invalid saved Hunk review state.")
        if action == "upsert":
            if (not isinstance(payload, dict) or payload.get("source") != "user"
                    or not isinstance(payload.get("id"), str) or not payload["id"]
                    or not isinstance(payload.get("fingerprint"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", payload["fingerprint"])):
                raise WorkflowError("Invalid Hunk comment identity.")
            comment = {"id": payload["id"], "source": "user", "fingerprint": payload["fingerprint"],
                       "hunk_pid": pid, "hunk_started": process_started(pid)}
            state["comments"][comment["id"]] = comment
        else:
            comment_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(comment_id, str) or not comment_id:
                raise WorkflowError("Invalid Hunk comment identity.")
            state["comments"].pop(comment_id, None)
        write_json(reviews_path, state)
        for candidate in reviews_path.parent.glob("review-pending-*.json"):
            pending = read_json(candidate)
            if isinstance(pending, dict) and pending.get("comment_id") == comment_id:
                candidate.unlink(missing_ok=True)


def feedback_records(root):
    feedback_path, feedback_lock, reviews_path, reviews_lock = feedback_paths(root)
    with locked(feedback_lock):
        feedback = read_json(feedback_path, {"version": 1, "attempts": {}})
    with locked(reviews_lock):
        reviews = read_json(reviews_path, {"version": 1, "comments": {}})
    if not isinstance(feedback, dict) or feedback.get("version") != 1 or not isinstance(feedback.get("attempts"), dict):
        raise WorkflowError("Invalid feedback delivery state.")
    if not isinstance(reviews, dict) or reviews.get("version") != 1 or not isinstance(reviews.get("comments"), dict):
        raise WorkflowError("Invalid saved Hunk review state.")
    return feedback, reviews


def comment_delivery_status(feedback, comment_id, fingerprint=None):
    attempts = [attempt for attempt in feedback["attempts"].values()
                if comment_id in attempt.get("comment_ids", [])]
    if any(attempt.get("status") in ("uncertain", "pending") for attempt in attempts):
        return "uncertain"
    if any(attempt.get("status") == "delivered"
           and (fingerprint is None or attempt.get("comment_fingerprints", {}).get(comment_id) == fingerprint)
           for attempt in attempts):
        return "delivered"
    return None


def format_feedback(comments):
    lines = ["Review feedback from Hunk:"]
    for comment in comments:
        location = comment["path"]
        if comment.get("line"):
            location += f":{comment['line']}"
        if comment.get("side"):
            location += f" ({comment['side']})"
        lines.extend(["", f"- {location}", f"  {comment['summary']}"])
        if comment.get("rationale"):
            lines.append(f"  Rationale: {comment['rationale']}")
        if comment.get("resolution") not in (None, "active"):
            lines.append(f"  Anchor: {comment['resolution']}")
    return "\n".join(lines)


def deliver_feedback(root, payload):
    root, _, _ = repository(root)
    state = task_state(root)
    if state.get("backend") != "cmux" or not state.get("session_id"):
        raise WorkflowError("This task has no bound cmux Pi session for feedback.")
    comments_raw = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(comments_raw, list) or not comments_raw or len(comments_raw) > 100:
        raise WorkflowError("Feedback must contain between 1 and 100 saved human comments.")
    comments = [validated_comment(comment) for comment in comments_raw]
    if len({comment["id"] for comment in comments}) != len(comments):
        raise WorkflowError("Feedback contains duplicate comment identities.")
    records = active_records(root)
    matching = [record for record in records if owner_matches(record, cmux_owner(state))]
    if len(matching) != 1 or len(records) != 1:
        raise WorkflowError("Feedback requires exactly one verified Pi recipient in this worktree.")
    recipient = matching[0]
    if recipient.get("status") != "idle":
        raise WorkflowError("The selected Pi recipient is busy, blocked, or unavailable.")
    socket_path, socket_token = recipient.get("feedback_socket"), recipient.get("feedback_token")
    if not isinstance(socket_path, str) or not isinstance(socket_token, str):
        raise WorkflowError("The selected Pi recipient cannot accept verified feedback.")
    feedback_path, feedback_lock, _, _ = feedback_paths(root)
    with locked(feedback_lock):
        feedback = read_json(feedback_path, {"version": 1, "attempts": {}})
        if not isinstance(feedback, dict) or feedback.get("version") != 1 or not isinstance(feedback.get("attempts"), dict):
            raise WorkflowError("Invalid feedback delivery state.")
        uncertain = [comment["id"] for comment in comments
                     if comment_delivery_status(feedback, comment["id"], comment["fingerprint"]) == "uncertain"]
        if uncertain:
            raise WorkflowError("A previous delivery is uncertain. Inspect the Pi session before choosing any manual resend.")
        pending = [comment for comment in comments
                   if comment_delivery_status(feedback, comment["id"], comment["fingerprint"]) != "delivered"]
        if not pending:
            return {"status": "already-delivered", "delivered_ids": [comment["id"] for comment in comments]}
        request_id = uuid.uuid4().hex
        request = {"request_id": request_id, "token": socket_token, "session_id": state["session_id"],
                   "workspace_id": state["workspace_id"], "surface_id": state["surface_id"],
                   "text": format_feedback(pending)}
        encoded = (json.dumps(request) + "\n").encode()
        if len(encoded) > 65536:
            raise WorkflowError("Feedback exceeds the delivery limit.")
        feedback["attempts"][request_id] = {
            "comment_ids": [comment["id"] for comment in pending],
            "comment_fingerprints": {comment["id"]: comment["fingerprint"] for comment in pending},
            "session_id": state["session_id"], "status": "pending", "created_at": time.time()}
        write_json(feedback_path, feedback)
    connected = False
    response = None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(socket_path)
            connected = True
            with locked(feedback_lock):
                feedback = read_json(feedback_path)
                feedback["attempts"][request_id]["status"] = "uncertain"
                write_json(feedback_path, feedback)
            client.sendall(encoded)
            received = b""
            while not received.endswith(b"\n") and len(received) <= 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                received += chunk
            response = json.loads(received.decode())
    except (OSError, ValueError, socket.timeout):
        if not connected:
            with locked(feedback_lock):
                feedback = read_json(feedback_path)
                feedback["attempts"][request_id]["status"] = "failed"
                write_json(feedback_path, feedback)
            raise WorkflowError("The selected Pi feedback receiver is unavailable.")
        raise WorkflowError("Feedback delivery is uncertain. Inspect the Pi session before retrying.")
    if not isinstance(response, dict) or response.get("request_id") != request_id:
        raise WorkflowError("Feedback delivery is uncertain. Inspect the Pi session before retrying.")
    if response.get("status") != "delivered":
        with locked(feedback_lock):
            feedback = read_json(feedback_path)
            feedback["attempts"][request_id]["status"] = "failed"
            write_json(feedback_path, feedback)
        raise WorkflowError("The selected Pi recipient refused feedback because its identity or readiness changed.")
    with locked(feedback_lock):
        feedback = read_json(feedback_path)
        feedback["attempts"][request_id]["status"] = "delivered"
        feedback["attempts"][request_id]["delivered_at"] = time.time()
        write_json(feedback_path, feedback)
    delivered = [comment["id"] for comment in comments
                 if comment_delivery_status(feedback, comment["id"], comment["fingerprint"]) == "delivered"]
    return {"status": "delivered", "delivered_ids": delivered}


def unsent_feedback(root):
    feedback_path, _, _, _ = feedback_paths(root)
    feedback, reviews = feedback_records(root)
    comments = reviews["comments"]
    unsent = [comment_id for comment_id, comment in comments.items()
              if comment_delivery_status(feedback, comment_id, comment.get("fingerprint")) != "delivered"]
    uncertain = [attempt for attempt in feedback["attempts"].values() if attempt.get("status") in ("pending", "uncertain")]
    pending_reviews = list(feedback_path.parent.glob("review-pending-*.json"))
    return len(unsent), len(uncertain) + len(pending_reviews)


def check_remove(root, state):
    root, _, _ = repository(root)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
    if branch.startswith("pi-subagents/"):
        raise WorkflowError("Subagents owns this worktree. Use its lifecycle tools.")
    if activity(root):
        raise WorkflowError("Pi sessions are still active in this worktree. Quit them before removal.")
    require_cmux()
    surfaces = cmux_task_surfaces(root, state)
    if surfaces:
        raise WorkflowError("cmux surfaces still reference this worktree. Send Hunk comments and close its task workspace before removal.")
    unsent, uncertain = unsent_feedback(root)
    if unsent or uncertain:
        raise WorkflowError("Unsent or uncertain Hunk feedback remains for this task. Preserve or resolve it before removal.")
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
    if os.environ.get("PI_CODING_AGENT") or os.environ.get("PI_SESSION_ID") or os.environ.get("AI_AGENT"):
        raise WorkflowError("This launcher is for human shell use. Agents must use Subagents project.open or managed delegation.")
    root, common, _ = repository(Path.cwd())
    validate_branch(root, args.branch)
    with locked(common / "wt-pi-launch.lock"):
        target = find_worktree(root, args.branch)
        state = task_state(target)
        if inside(Path.cwd(), target):
            raise WorkflowError("Run removal from another worktree after closing the task workspace.")
        check_remove(target, state)
        require_approved(target)
        if git(target, "status", "--porcelain", "--untracked-files=all").stdout:
            raise WorkflowError("The worktree has uncommitted files. Commit or preserve them before removal.")
        ignored = git(target, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout
        if ignored and not args.discard_ignored:
            raise WorkflowError("The worktree contains ignored files. Preserve anything needed, then use --discard-ignored deliberately.")
        run(["wt", "remove", args.branch, "--foreground", "--no-delete-branch"], cwd=root, timeout=300,
            capture=False)
        return {"action": "removed", "branch_retained": True}


def read_stdin_json(limit=131072):
    value = sys.stdin.read(limit + 1)
    if len(value) > limit:
        raise WorkflowError("Input exceeds the supported size.")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise WorkflowError("Expected one JSON object on stdin.")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__, prog="wt pi")
    if sys.argv[1:2] == ["_activity"]:
        parser.add_argument("action", choices=("_activity",))
        parser.add_argument("token")
        parser.add_argument("pid", type=int)
        parser.add_argument("status", choices=("working", "idle", "blocked", "clear"))
        parser.add_argument("--session-id")
        parser.add_argument("--backend", choices=("standalone", "cmux"))
        parser.add_argument("--workspace-id")
        parser.add_argument("--surface-id")
        parser.add_argument("--feedback-socket")
        parser.add_argument("--feedback-token")
    elif sys.argv[1:2] == ["_start"]:
        parser.add_argument("action", choices=("_start",))
        parser.add_argument("launch_token")
    elif sys.argv[1:2] == ["_review"]:
        parser.add_argument("action", choices=("_review",))
        parser.add_argument("operation", choices=("upsert", "remove"))
        parser.add_argument("--repo", required=True)
        parser.add_argument("--pid", required=True, type=int)
        parser.add_argument("--tracking-token", required=True)
    else:
        commands = parser.add_subparsers(dest="action", required=True)
        for name in ("new", "open"):
            command = commands.add_parser(name, help="Create a task and launch Pi." if name == "new" else "Focus a task workspace or resume Pi.")
            command.add_argument("branch")
            command.add_argument("--no-focus", action="store_true")
            if name == "new":
                command.add_argument("--base")
        command = commands.add_parser("remove", help="Remove a stopped, clean task worktree; retain its branch.")
        command.add_argument("branch")
        command.add_argument("--discard-ignored", action="store_true")
        commands.add_parser("list", help="Refresh Pi activity markers and list worktrees.")
        command = commands.add_parser("feedback", help="Deliver saved Hunk comments to the bound idle Pi session.")
        command.add_argument("--repo", required=True)
    args = parser.parse_args()
    try:
        if args.action in ("new", "open"):
            result = launch(args)
        elif args.action == "remove":
            result = remove(args)
        elif args.action == "_start":
            start_cmux_pi(args.launch_token)
            return
        elif args.action == "_activity":
            if git(Path.cwd(), "rev-parse", "--show-toplevel", check=False).returncode:
                return
            if not re.fullmatch(r"[a-f0-9]{32}", args.token) or args.pid <= 0:
                raise WorkflowError("Invalid activity identity.")
            activity(Path.cwd(), token=args.token, pid=args.pid, status=args.status,
                     session_id=args.session_id, backend=args.backend,
                     workspace_id=args.workspace_id, surface_id=args.surface_id,
                     feedback_socket=args.feedback_socket, feedback_token=args.feedback_token)
            return
        elif args.action == "_review":
            review_event(Path(args.repo), args.operation, args.pid, read_stdin_json(), args.tracking_token)
            return
        elif args.action == "feedback":
            result = deliver_feedback(Path(args.repo), read_stdin_json())
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
