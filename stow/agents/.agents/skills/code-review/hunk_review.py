#!/usr/bin/env python3
"""Keep Pi review findings bound to an exact local Hunk comparison."""

import argparse
import base64
import codecs
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid


class ReviewError(Exception):
    pass


LIMIT = 32 * 1024 * 1024
MODES = ("endpoints", "root", "staged", "unstaged", "worktree", "since")


def run(args, cwd=None, data=None, allowed=(0,), timeout=20):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    if args[0] == "ps":
        env["LC_ALL"] = "C"
    try:
        result = subprocess.run(args, cwd=cwd, env=env, input=data,
                                stdin=subprocess.DEVNULL if data is None else None,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReviewError(f"{Path(args[0]).name} could not complete; inspect it locally before retrying.") from error
    if result.returncode not in allowed:
        raise ReviewError(f"{Path(args[0]).name} failed with exit code {result.returncode}; inspect it locally.")
    if len(result.stdout) > LIMIT:
        raise ReviewError("The review exceeds the 32 MiB helper limit; use the text report.")
    return result.stdout


def git(repo, *args, allowed=(0,)):
    return run(["git", "--no-pager", "--literal-pathspecs", "-c", "core.quotePath=false", "-C", str(repo), *args], allowed=allowed)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode()


def private_directory(path):
    if path.is_symlink():
        raise ReviewError("Review storage must not be symlinked.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def storage():
    return private_directory(Path.home() / ".local/state/pi-code-review")


def review_directory(review_id):
    if not re.fullmatch(r"[0-9a-f]{32}", review_id):
        raise ReviewError("Use the review ID returned by prepare.")
    path = storage() / review_id
    if path.is_symlink() or not path.is_dir():
        raise ReviewError("The saved review is unavailable.")
    return path


def save(path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_state(directory, state):
    save(directory / "review.json", encoded(state) + b"\n")


@contextmanager
def locked(directory):
    path = directory / "lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReviewError("Another command owns this review; retry after it finishes.") from error
        yield


def owner():
    value = os.environ.get("PI_SESSION_ID")
    if not value:
        raise ReviewError("Run the helper from the owning Pi session.")
    return value


def load_state(directory):
    path = directory / "review.json"
    if path.is_symlink() or path.stat().st_size > LIMIT:
        raise ReviewError("Invalid review state.")
    state = json.loads(path.read_bytes())
    if state.get("version") != 1 or state.get("id") != directory.name or state.get("owner") != owner():
        raise ReviewError("This review belongs to another Pi session or uses an unsupported format.")
    return state


def commit(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def current_head(repo):
    result = git(repo, "rev-parse", "--verify", "HEAD", allowed=(0, 128))
    return result.decode().strip() or None


def relative_path(value):
    if not isinstance(value, str):
        raise ReviewError("Paths must be strings.")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
        raise ReviewError("Paths must be relative to the repository and cannot traverse parents.")
    return value


def comparison(args):
    repo = Path(git(args.repo, "rev-parse", "--show-toplevel").decode().removesuffix("\n")).resolve()
    mode = args.mode
    if mode in ("endpoints", "since") and not args.base:
        raise ReviewError("This comparison requires --base.")
    if mode in ("endpoints", "root") and not args.head:
        raise ReviewError("This comparison requires --head.")
    if mode not in ("endpoints", "since") and args.base:
        raise ReviewError("--base is only valid for endpoints or since.")
    if mode not in ("endpoints", "root") and args.head:
        raise ReviewError("Mutable comparisons pin the current HEAD automatically.")
    if args.include_untracked and mode not in ("worktree", "since"):
        raise ReviewError("Untracked files belong only to worktree or since comparisons.")
    head = commit(repo, args.head) if args.head else current_head(repo)
    if mode == "root" and len(git(repo, "rev-list", "--parents", "-n", "1", head).split()) != 1:
        raise ReviewError("Root mode requires a root commit; select explicit endpoints otherwise.")
    return str(repo), {"mode": mode, "base": commit(repo, args.base) if args.base else None,
                       "head": head, "paths": [relative_path(path) for path in args.path],
                       "include_untracked": args.include_untracked}


def snapshot(repo, target):
    if git(repo, "ls-files", "--unmerged", "-z"):
        raise ReviewError("Resolve or explicitly review the merge conflict before opening a normal diff.")
    mode, head = target["mode"], target["head"]
    if mode not in ("endpoints", "root") and current_head(repo) != head:
        raise ReviewError("HEAD changed during this mutable review; prepare a new review.")
    options = ["--no-ext-diff", "--no-textconv", "--no-color", "--binary", "--full-index",
               "--find-renames", "--src-prefix=a/", "--dst-prefix=b/", "--unified=3"]
    if mode == "root":
        args = ["diff-tree", "--root", "--no-commit-id", "-p", *options, head]
    else:
        args = ["diff", *options]
        if mode == "endpoints":
            args += [target["base"], head]
        elif mode == "staged":
            args += ["--cached", *([head] if head else [])]
        elif mode == "worktree":
            args += [head] if head else ["--cached"]
        elif mode == "since":
            args += [target["base"]]
    patch = git(repo, *args, "--", *target["paths"])
    if mode == "worktree" and head is None:
        staged = git(repo, "diff", "--cached", "--name-only", "-z", "--", *target["paths"])
        patch = b""
        for name in staged.split(b"\0"):
            if name:
                path = os.fsdecode(name)
                if os.path.lexists(Path(repo) / path):
                    patch += new_file_patch(repo, path, options)
    if target["include_untracked"]:
        names = git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", *target["paths"])
        for name in names.split(b"\0"):
            if name:
                patch += new_file_patch(repo, os.fsdecode(name), options)
    if len(patch) > LIMIT:
        raise ReviewError("The review exceeds the 32 MiB helper limit; use the text report.")
    return patch


def new_file_patch(repo, path, options):
    return git(repo, "diff", "--no-index", *options, "--", "/dev/null", path, allowed=(0, 1))


def has_textconv(repo):
    return bool(git(repo, "config", "--name-only", "--get-regexp", r"^diff\..*\.textconv$", allowed=(0, 1)))


def prepare(args):
    session_owner = owner()
    repo, target = comparison(args)
    patch = snapshot(repo, target)
    if not patch:
        raise ReviewError("The selected comparison is empty; no review was opened.")
    if snapshot(repo, target) != patch:
        raise ReviewError("The comparison changed while capturing it; retry after edits settle.")
    review_id = uuid.uuid4().hex
    directory = private_directory(storage() / review_id)
    state = {"version": 1, "id": review_id, "owner": session_owner, "repo": repo,
             "comparison": target, "patch_sha256": digest(patch), "patch_view": has_textconv(repo),
             "findings": {}, "imports": {}}
    save(directory / "review.patch", patch)
    save_state(directory, state)
    return {"review": review_id, "repo": repo, "comparison": target,
            "patch": str(directory / "review.patch"), "patch_sha256": state["patch_sha256"],
            "viewer": "patch" if viewer_command(directory, state)[0] == "patch" else "native"}


def fresh(directory, state):
    patch = directory / "review.patch"
    if patch.is_symlink() or digest(patch.read_bytes()) != state["patch_sha256"]:
        raise ReviewError("The saved patch changed; do not attach findings to it.")
    if digest(snapshot(state["repo"], state["comparison"])) != state["patch_sha256"]:
        raise ReviewError("The reviewed content changed; prepare a new review before importing or navigating.")


def cli_json(args, data=None):
    value = json.loads(run(args, data=encoded(data) if data is not None else None))
    if not isinstance(value, (dict, list)):
        raise ReviewError("The review tool returned an unexpected response.")
    return value


def cmux(*args):
    return cli_json(["cmux", *args, "--json", "--id-format", "uuids"])


def hunk(*args, data=None):
    return cli_json(["hunk", "session", *args, "--json"], data=data)


def unquote_path(value):
    if value.startswith('"') and value.endswith('"'):
        return codecs.escape_decode(value[1:-1].encode())[0].decode()
    return value


def patch_files(patch, *, trim_trailing_context=False):
    # Hunk exports patch resources with LF line endings.
    patch = patch.replace("\r\n", "\n")
    files = {}
    for block in re.split(r"(?m)(?=^diff --git )", patch):
        if not block.startswith("diff --git "):
            continue
        lines = block.removesuffix("\n").split("\n")
        if trim_trailing_context:
            while lines[-1] == " ":
                lines.pop()
        header = lines[0][11:]
        if header.startswith('"'):
            match = re.match(r'("(?:[^"\\]|\\.)*") (.*)', header)
            if not match:
                raise ReviewError("Unsupported quoted diff path; retain this finding in the report.")
            old, new = map(unquote_path, match.groups())
        else:
            old, separator, new = header.rpartition(" b/")
            if separator:
                new = "b/" + new
            else:
                old, separator, new = header.partition(' "b/')
                new = unquote_path('"b/' + new) if separator else ""
        old, new = old[2:], new[2:]
        numbers = None
        content = {"old": {}, "new": {}}
        for line in lines[1:]:
            if line.startswith("@@ "):
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if not match:
                    raise ReviewError("Unsupported diff hunk.")
                numbers = [int(value) for value in match.groups()]
            elif numbers is not None:
                if not line or line[0] not in " +-\\":
                    raise ReviewError("Malformed diff content.")
                if line.startswith((" ", "-")):
                    content["old"][numbers[0]] = line[1:]
                    numbers[0] += 1
                if line.startswith((" ", "+")):
                    content["new"][numbers[1]] = line[1:]
                    numbers[1] += 1
            elif line.startswith(("--- ", "+++ ")):
                path = unquote_path(line[4:].split("\t", 1)[0])
                path = None if path == "/dev/null" else path[2:]
                if line.startswith("--- "):
                    old = path
                else:
                    new = path
            elif line.startswith("rename from "):
                old = unquote_path(line[12:])
            elif line.startswith("rename to "):
                new = unquote_path(line[10:])
        path = new or old
        if not path or path in files:
            raise ReviewError("Ambiguous diff paths; retain this review in the report.")
        files[path] = {"old_path": old, "new_path": new, "lines": content}
    return files


def surface_exists(workspace, surface):
    tree = cmux("tree", "--workspace", workspace)
    matches = [item for window in tree.get("windows", [])
               for ws in window.get("workspaces", []) if ws.get("id") == workspace
               for pane in ws.get("panes", []) for item in pane.get("surfaces", [])
               if item.get("id") == surface and item.get("type") == "terminal"]
    return len(matches) == 1


def viewer_command(directory, state):
    target = state["comparison"]
    mode = target["mode"]
    if state.get("patch_view") or (mode == "worktree" and target["head"] is None):
        return ["patch", str(directory / "review.patch"), "--agent-notes"]
    if mode == "root":
        command = ["show", target["head"]]
    elif mode == "endpoints":
        command = ["diff", target["base"], target["head"]]
    elif mode == "staged":
        command = ["diff", "--staged"]
    elif mode == "since":
        command = ["diff", target["base"]]
    elif mode == "worktree":
        command = ["diff", target["head"]]
    else:
        command = ["diff"]
    command += ["--agent-notes"]
    if command[0] == "diff":
        command += ["--no-exclude-untracked" if target["include_untracked"] else "--exclude-untracked"]
    return command + ["--", *target["paths"]]


def launch_command(directory):
    args = [sys.executable, str(Path(__file__).resolve()), "_run", "--review", directory.name]
    payload = base64.b64encode(encoded(args)).decode()
    return ('exec /usr/bin/python3 -c \'import base64,json,os; '
            f'a=json.loads(base64.b64decode("{payload}")); os.execv(a[0],a)\'')


def process_started(pid):
    value = run(["ps", "-p", str(pid), "-o", "lstart="]).decode().strip()
    if not value:
        raise ReviewError("The Hunk process is no longer available.")
    return value


def run_viewer(directory):
    state = json.loads((directory / "review.json").read_bytes())
    launch = state.get("launch", {})
    workspace, surface = os.environ.get("CMUX_WORKSPACE_ID"), os.environ.get("CMUX_SURFACE_ID")
    if not workspace or not surface or workspace != launch.get("workspace") or surface == launch.get("caller"):
        raise ReviewError("The new review terminal does not match the launch request.")
    if viewer_command(directory, state)[0] != "patch" and has_textconv(state["repo"]):
        raise ReviewError("Git textconv configuration changed; prepare a new snapshot-backed review.")
    save(directory / "process.json", encoded({"pid": os.getpid(), "started": process_started(os.getpid()),
                                               "workspace": workspace, "surface": surface}))
    os.chdir(state["repo"])
    for key in list(os.environ):
        if key.startswith("GIT_"):
            del os.environ[key]
    os.environ.update(GIT_LITERAL_PATHSPECS="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    args = [state["hunk_binary"], *viewer_command(directory, state)]
    os.execv(args[0], args)


def publication(session):
    result = session.get("snapshot", {}).get("state", {}).get("reviewPublication", {})
    if not isinstance(result.get("generation"), str):
        raise ReviewError("Hunk did not provide a review generation; Hunk 0.22.0 or later is required.")
    return result


def verify_view(directory, state, session):
    marker = json.loads((directory / "process.json").read_bytes())
    if (session.get("pid") != marker["pid"] or process_started(marker["pid"]) != marker.get("started")
            or Path(session.get("cwd", "")).resolve() != Path(state["repo"])
            or marker.get("surface") != state["launch"]["surface"]
            or marker.get("workspace") != state["launch"]["workspace"]):
        raise ReviewError("The Hunk process no longer matches this review's terminal.")
    if viewer_command(directory, state)[0] == "patch":
        if session.get("inputKind") != "patch" or session.get("sourceLabel") != str(directory / "review.patch"):
            raise ReviewError("The Hunk comparison changed.")
    elif not session.get("repoRoot") or Path(session["repoRoot"]).resolve() != Path(state["repo"]):
        raise ReviewError("The Hunk repository changed.")
    generation = publication(session)["generation"]
    if state.get("session") and state["session"] != {"id": session["sessionId"], "generation": generation}:
        raise ReviewError("Hunk reloaded or switched comparisons; prepare a new review.")
    view = hunk("review", session["sessionId"], "--include-patch")["review"]
    saved_patch = (directory / "review.patch").read_bytes().decode()
    expected = patch_files(saved_patch)
    trimmed = patch_files(saved_patch, trim_trailing_context=True)
    actual = {}
    for item in view["files"]:
        path = item["path"]
        if path in actual:
            raise ReviewError("Hunk exposes multiple files at the same path; retain findings in the report.")
        parsed = patch_files(item.get("patch", ""))
        source = expected.get(path)
        if not parsed and source and not any(source["lines"].values()) and not item.get("hunks"):
            actual[path] = source
        elif len(parsed) == 1 and path in parsed:
            actual.update(parsed)
        else:
            raise ReviewError("Hunk could not export a complete text patch; retain findings in the report.")
    if actual.keys() != expected.keys() or any(
            actual[path] not in (expected[path], trimmed[path]) for path in expected):
        raise ReviewError("Hunk's displayed diff differs from the reviewed snapshot; no findings were attached.")
    after = hunk("get", session["sessionId"])["session"]
    if publication(after)["generation"] != generation:
        raise ReviewError("Hunk changed while inspecting it; no findings were attached.")
    return actual


def live_session(directory, state):
    fresh(directory, state)
    launch = state.get("launch", {})
    if not launch.get("surface") or not surface_exists(launch["workspace"], launch["surface"]):
        raise ReviewError("The review's cmux terminal is unavailable; use open --reopen after closing its old pane.")
    if not state.get("session"):
        raise ReviewError("The Hunk launch is incomplete; run open with this review ID to inspect it.")
    session = hunk("get", state["session"]["id"])["session"]
    return session, verify_view(directory, state, session)


def open_viewer(directory, state, reopen=False):
    fresh(directory, state)
    workspace, caller = os.environ.get("CMUX_WORKSPACE_ID"), os.environ.get("CMUX_SURFACE_ID")
    if not workspace or not caller or not surface_exists(workspace, caller):
        raise ReviewError("Automatic review opening requires the owning Pi terminal in native cmux.")
    if reopen:
        launch = state.get("launch", {})
        session_id = state.get("session", {}).get("id")
        if not launch.get("surface") or not session_id:
            raise ReviewError("An incomplete launch cannot be reopened automatically; inspect it manually.")
        if surface_exists(launch["workspace"], launch["surface"]) or any(
                item.get("sessionId") == session_id for item in hunk("list")["sessions"]):
            raise ReviewError("Close the old review pane before reopening; its notes will remain in the local artifact.")
        state.pop("session")
        state.pop("launch")
        state["imports"] = {}
        state.pop("pending", None)
        (directory / "process.json").unlink(missing_ok=True)
        save_state(directory, state)
    if state.get("session"):
        session, _ = live_session(directory, state)
        return {"review": state["id"], "session": session["sessionId"], "reused": True}
    if "launch" not in state:
        binary = shutil.which("hunk")
        if not binary:
            raise ReviewError("Hunk is unavailable; install the Homebrew-managed binary before opening a review.")
        state["hunk_binary"] = str(Path(binary).resolve())
        state["launch"] = {"workspace": workspace, "caller": caller, "status": "requested"}
        save_state(directory, state)
        response = cmux("new-split", "right", "--workspace", workspace, "--surface", caller,
                        "--command", launch_command(directory), "--focus", "false")
        if (response.get("workspace_id") != workspace or not response.get("surface_id")
                or response.get("type") != "terminal" or response["surface_id"] == caller):
            raise ReviewError("cmux returned an incomplete launch identity; inspect the pane before retrying.")
        state["launch"].update(surface=response["surface_id"], status="created")
        save_state(directory, state)
    launch = state["launch"]
    if launch["workspace"] != workspace or launch["caller"] != caller or not launch.get("surface"):
        raise ReviewError("The launch identity is incomplete or belongs to another terminal; inspect it manually.")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        marker_path = directory / "process.json"
        if marker_path.exists():
            marker = json.loads(marker_path.read_bytes())
            matches = [item for item in hunk("list")["sessions"] if item.get("pid") == marker["pid"]]
            if len(matches) > 1:
                raise ReviewError("Multiple Hunk sessions claim this process; inspect the review pane.")
            if len(matches) == 1:
                session = matches[0]
                verify_view(directory, state, session)
                fresh(directory, state)
                state["session"] = {"id": session["sessionId"], "generation": publication(session)["generation"]}
                launch["status"] = "ready"
                save_state(directory, state)
                return {"review": state["id"], "session": session["sessionId"], "surface": launch["surface"]}
        time.sleep(0.15)
    raise ReviewError("Hunk did not register within 15 seconds. Inspect its pane, then run open with the same review ID; do not launch another.")


def validate_findings(values):
    if not isinstance(values, dict) or set(values) != {"findings"} or not isinstance(values["findings"], list):
        raise ReviewError("Input must be an object containing a findings array.")
    result = {}
    for value in values["findings"]:
        if not isinstance(value, dict):
            raise ReviewError("Each finding must be an object.")
        required = {"id", "axis", "priority", "scope", "title", "body"}
        allowed = required | {"path", "side", "start_line", "end_line", "reference"}
        if not required <= value.keys() or value.keys() - allowed:
            raise ReviewError("A finding has missing or unknown fields.")
        if not isinstance(value["id"], str) or not re.fullmatch(r"R[1-9][0-9]*", value["id"]) or value["id"] in result:
            raise ReviewError("Finding IDs must be distinct R1, R2, etc.")
        if value["axis"] not in ("Correctness", "Spec", "Standards") or value["priority"] not in ("P0", "P1", "P2", "optional"):
            raise ReviewError("Use the review skill's axes and P0/P1/P2 or optional priority.")
        for field in ("title", "body", "reference"):
            if field in value and (not isinstance(value[field], str) or not value[field].strip() or len(value[field]) > 16000):
                raise ReviewError("Finding text must be nonempty and at most 16,000 characters per field.")
        scope = value["scope"]
        if scope not in ("line", "file", "review"):
            raise ReviewError("Finding scope must be line, file, or review.")
        if scope in ("line", "file"):
            relative_path(value.get("path", ""))
        elif "path" in value:
            raise ReviewError("Review-level findings do not have a file path.")
        if scope == "line":
            if value.get("side") not in ("old", "new"):
                raise ReviewError("Line findings require an old/new side.")
            start, end = value.get("start_line"), value.get("end_line")
            if type(start) is not int or type(end) is not int or start < 1 or end < start or end - start > 1000:
                raise ReviewError("Line findings require a positive, ordered range of at most 1,001 lines.")
        elif any(key in value for key in ("side", "start_line", "end_line")):
            raise ReviewError("Only line findings have line anchors.")
        result[value["id"]] = value
    return result


def comment_payload(state, finding, files):
    if finding["scope"] != "line":
        return None
    path = finding["path"]
    matches = [(name, file) for name, file in files.items()
               if path == name or path == file.get("old_path")]
    if len(matches) != 1:
        return None
    path, file = matches[0]
    lines = file["lines"][finding["side"]]
    if any(line not in lines for line in range(finding["start_line"], finding["end_line"] + 1)):
        return None
    rationale = finding["body"]
    if finding["end_line"] != finding["start_line"]:
        rationale += f'\n\nReviewed range: {finding["side"]} lines {finding["start_line"]}-{finding["end_line"]}.'
    if finding.get("reference"):
        rationale += "\n\nReference: " + finding["reference"]
    return {"filePath": path, finding["side"] + "Line": finding["start_line"],
            "summary": f'[{finding["id"]}][{finding["priority"]}][{finding["axis"]}] {finding["title"]}',
            "rationale": rationale, "author": "Pi review " + state["id"]}


def same_comment(comment, payload):
    side = "old" if "oldLine" in payload else "new"
    return (comment.get("author") == payload["author"] and comment.get("filePath") == payload["filePath"]
            and comment.get("summary") == payload["summary"] and comment.get("rationale") == payload["rationale"]
            and comment.get("side") == side and comment.get("line") == payload[side + "Line"])


def import_findings(directory, state, values):
    findings = validate_findings(values)
    for finding_id, value in findings.items():
        if finding_id in state["findings"] and state["findings"][finding_id] != value:
            raise ReviewError("An existing finding ID changed. Preserve its evidence; use a new ID for a revised finding.")
    session, files = live_session(directory, state)
    session_id = session["sessionId"]
    comments = hunk("comment", "list", session_id)["comments"]
    pending, report_only, reconciled = {}, [], {}
    for finding_id, value in findings.items():
        payload = comment_payload(state, value, files)
        if payload is None:
            report_only.append(finding_id)
            continue
        matches = [comment for comment in comments if same_comment(comment, payload)]
        if len(matches) > 1:
            raise ReviewError("Duplicate imported notes exist; inspect them instead of importing again.")
        if matches:
            reconciled[finding_id] = matches[0]["commentId"]
        elif finding_id in state["imports"]:
            raise ReviewError("An imported note was removed or edited; it will not be recreated automatically.")
        elif finding_id in state.get("pending", []):
            raise ReviewError("A previous import may still complete. Its notes are not visible yet; inspect the pane rather than retrying the write.")
        else:
            pending[finding_id] = payload
    state["findings"].update(findings)
    state["imports"].update(reconciled)
    state["pending"] = [key for key in state.get("pending", []) if key not in reconciled]
    save_state(directory, state)
    if pending:
        fresh(directory, state)
        if publication(hunk("get", session_id)["session"])["generation"] != state["session"]["generation"]:
            raise ReviewError("Hunk changed before importing; findings remain in the local artifact.")
        state["pending"] += list(pending)
        save_state(directory, state)
        response = hunk("comment", "apply", session_id, "--stdin", data={"comments": list(pending.values())})
        applied = response["result"]["applied"]
        if len(applied) != len(pending):
            raise ReviewError("Hunk returned an incomplete receipt. Reconcile with the same import; do not create new IDs.")
        for finding_id, receipt in zip(pending, applied):
            payload = pending[finding_id]
            side = "old" if "oldLine" in payload else "new"
            if (receipt.get("filePath") != payload["filePath"] or receipt.get("side") != side
                    or receipt.get("line") != payload[side + "Line"] or not receipt.get("commentId")):
                raise ReviewError("Hunk returned a different anchor; inspect the pane before continuing.")
            state["imports"][finding_id] = receipt["commentId"]
        state["pending"] = [key for key in state["pending"] if key not in pending]
        save_state(directory, state)
        live_session(directory, state)
    return {"review": state["id"], "inline": list(reconciled) + list(pending), "report_only": report_only,
            "artifact": str(directory / "review.json")}


def show_finding(directory, state, finding_id):
    session, files = live_session(directory, state)
    finding = state["findings"].get(finding_id)
    if not finding:
        raise ReviewError("That finding is not in this review.")
    comment_id = state["imports"].get(finding_id)
    payload = comment_payload(state, finding, files)
    comments = hunk("comment", "list", session["sessionId"])["comments"]
    if not comment_id or not payload or not any(item.get("commentId") == comment_id and same_comment(item, payload) for item in comments):
        raise ReviewError("This finding has no unchanged inline note; discuss its local report entry.")
    hunk("navigate", session["sessionId"], "--comment", comment_id)
    return {"review": state["id"], "finding": finding, "session": session["sessionId"], "comment": comment_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture = commands.add_parser("prepare", help="Capture an exact comparison before starting reviewers")
    capture.add_argument("--repo", required=True)
    capture.add_argument("--mode", choices=MODES, required=True)
    capture.add_argument("--base")
    capture.add_argument("--head")
    capture.add_argument("--path", action="append", default=[])
    capture.add_argument("--include-untracked", action="store_true")
    for name in ("open", "import", "show", "comments", "status", "_run"):
        command = commands.add_parser(name)
        command.add_argument("--review", required=True)
        if name == "open":
            command.add_argument("--reopen", action="store_true", help="Reopen a review whose old pane and Hunk process have closed")
        if name == "import":
            command.add_argument("--input", required=True, help="Findings JSON file, or - for stdin")
        if name == "show":
            command.add_argument("--finding", required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args)
        else:
            directory = review_directory(args.review)
            if args.action == "_run":
                run_viewer(directory)
                return
            with locked(directory):
                state = load_state(directory)
                if args.action == "open":
                    result = open_viewer(directory, state, reopen=args.reopen)
                    if args.reopen and state["findings"]:
                        result["restored"] = import_findings(directory, state, {"findings": list(state["findings"].values())})
                elif args.action == "import":
                    data = sys.stdin.buffer.read(LIMIT + 1) if args.input == "-" else Path(args.input).read_bytes()
                    if len(data) > LIMIT:
                        raise ReviewError("Findings input exceeds 32 MiB.")
                    result = import_findings(directory, state, json.loads(data))
                elif args.action == "show":
                    result = show_finding(directory, state, args.finding)
                else:
                    session, _ = live_session(directory, state)
                    result = (hunk("comment", "list", session["sessionId"], "--type", "user") if args.action == "comments"
                              else {"review": state["id"], "session": session["sessionId"], "comparison": state["comparison"],
                                    "findings": list(state["findings"]), "inline": list(state["imports"])})
        print(json.dumps(result, ensure_ascii=True))
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, ReviewError) else "Invalid review state or tool response; inspect locally before retrying."
        print(json.dumps({"error": message}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
