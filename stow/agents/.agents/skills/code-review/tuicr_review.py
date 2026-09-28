#!/usr/bin/env python3
"""Bind Pi findings to an exact tuicr snapshot or GitHub PR session."""

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
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


class ReviewError(Exception):
    pass


LIMIT = 32 * 1024 * 1024
MODES = ("endpoints", "root", "staged", "unstaged", "worktree", "since")


def run(args, cwd=None, data=None, allowed=(0,), timeout=20, extra_env=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    env.update(extra_env or {})
    if args[0] == "ps":
        env["LC_ALL"] = "C"
    output = bytearray()
    try:
        with tempfile.TemporaryFile() as source:
            if data is not None:
                source.write(data)
                source.seek(0)
            process = subprocess.Popen(args, cwd=cwd, env=env, stdin=source if data is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise subprocess.TimeoutExpired(args, timeout)
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        if len(output) + len(chunk) > LIMIT:
                            raise ReviewError("The review exceeds the 32 MiB helper limit; use the text report.")
                        output.extend(chunk)
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            finally:
                process.stdout.close()
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReviewError(f"{Path(args[0]).name} could not complete; inspect it locally before retrying.") from error
    if process.returncode not in allowed:
        raise ReviewError(f"{Path(args[0]).name} failed with exit code {process.returncode}; inspect it locally.")
    return bytes(output)


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
    if state.get("version") != 2 or state.get("viewer") != "tuicr" or state.get("id") != directory.name or state.get("owner") != owner():
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
    if getattr(args, "pr_url", None) and (mode != "endpoints" or args.path or args.include_untracked):
        raise ReviewError("Native PR sessions require a complete endpoints comparison without path or untracked selections.")
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
               "--find-renames", "--src-prefix=a/", "--dst-prefix=b/", "--unified=3",
               "--diff-algorithm=myers", "--indent-heuristic", "--inter-hunk-context=0"]
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
    state = {"version": 2, "viewer": "tuicr", "id": review_id, "owner": session_owner, "repo": repo,
             "comparison": target, "patch_sha256": digest(patch), "findings": {}, "imports": {}}
    save(directory / "review.patch", patch)
    if getattr(args, "pr_url", None):
        configure_viewer(directory)
        private_directory(directory / "viewer")
        state["pr"] = prepare_pr(directory, state, args.pr_url, patch)
    else:
        state["snapshot"] = build_snapshot(directory, state)
    fresh(directory, state)
    save_state(directory, state)
    return {"review": review_id, "repo": repo, "comparison": target,
            "patch": str(directory / "review.patch"), "patch_sha256": state["patch_sha256"],
            "viewer": "tuicr", "snapshot": state.get("snapshot"), "pr": state.get("pr")}


def github_target(url):
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)/?", url)
    if not match or any(part in (".", "..") for part in match.groups()[:2]):
        raise ReviewError("Native PR review currently requires a canonical https://github.com/owner/repo/pull/number URL.")
    owner, name, number = match.groups()
    return {"url": f"https://github.com/{owner}/{name}/pull/{number}", "number": int(number),
            "repository": {"kind": "git_hub", "host": "github.com", "owner": owner, "name": name}}


def gh_environment(config):
    return {"GH_CONFIG_DIR": config, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1", "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
            "GH_PAGER": "cat", "GH_DEBUG": "", "DEBUG": "", "GH_TELEMETRY": "0"}


def pr_identity(pr):
    repository = pr["repository"]
    endpoint = f'repos/{repository["owner"]}/{repository["name"]}/pulls/{pr["number"]}'
    value = json.loads(run(["gh", "api", "--hostname", "github.com", endpoint],
                           extra_env=gh_environment(pr["gh_config_dir"])))
    if (value.get("number") != pr["number"] or value.get("html_url", "").lower() != pr["url"].lower()
            or value["base"]["repo"]["full_name"].lower() != f'{repository["owner"]}/{repository["name"]}'.lower()):
        raise ReviewError("GitHub returned a different PR identity.")
    base, head = value["base"]["sha"], value["head"]["sha"]
    if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base, head)):
        raise ReviewError("GitHub returned invalid PR revisions.")
    return {"base_head": base, "head": head}


def fresh_pr(state):
    pr = state.get("pr")
    if pr and pr_identity(pr) != {key: pr[key] for key in ("base_head", "head")}:
        raise ReviewError("The PR base or head changed; prepare a new review before attaching or publishing findings.")


def tuicr_content_hashes(patch):
    result = {}
    for block in re.split(r"(?m)(?=^diff --git )", patch.replace("\r\n", "\n")):
        if not block.startswith("diff --git "):
            continue
        files = patch_files(block)
        if len(files) != 1:
            raise ReviewError("Unsupported forge diff block.")
        value, in_hunk = 0xcbf29ce484222325, False
        for line in block.removesuffix("\n").split("\n"):
            if line.startswith("@@ "):
                in_hunk = True
            elif in_hunk and line.startswith((" ", "+", "-")):
                for byte in (line + "\n").encode():
                    value = ((value ^ byte) * 0x100000001b3) & 0xffffffffffffffff
        result[next(iter(files))] = value
    return result


def prepare_pr(directory, state, url, patch):
    pr = github_target(url)
    config = os.environ.get("GH_CONFIG_DIR") or str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gh")
    pr["gh_config_dir"] = str(Path(config).expanduser().resolve())
    pr["gh_home"] = str(Path.home())
    pr.update(pr_identity(pr))
    target = state["comparison"]
    if pr["head"] != target["head"]:
        raise ReviewError("The pinned head does not match the PR head.")
    bases = git(state["repo"], "merge-base", "--all", pr["base_head"], pr["head"]).decode().splitlines()
    if bases != [target["base"]]:
        raise ReviewError("The pinned base must be the PR's single merge base; fetch missing history before preparing.")
    forge_patch = run(["gh", "pr", "diff", pr["url"], "--color", "never"],
                      extra_env=gh_environment(pr["gh_config_dir"]))
    if patch_files(forge_patch.decode()) != patch_files(patch.decode()):
        raise ReviewError("The GitHub diff differs from the pinned comparison; retain the text report.")
    pr["content_hashes"] = tuicr_content_hashes(forge_patch.decode())
    pr["patch_sha256"] = digest(forge_patch)
    save(directory / "forge.patch", forge_patch)
    fresh_pr({"pr": pr})
    return pr


def fresh(directory, state):
    patch = directory / "review.patch"
    if patch.is_symlink() or digest(patch.read_bytes()) != state["patch_sha256"]:
        raise ReviewError("The saved patch changed; do not attach findings to it.")
    if digest(snapshot(state["repo"], state["comparison"])) != state["patch_sha256"]:
        raise ReviewError("The reviewed content changed; prepare a new review before importing or discussing findings.")
    if "pr" in state:
        forge_patch = directory / "forge.patch"
        if forge_patch.is_symlink() or digest(forge_patch.read_bytes()) != state["pr"]["patch_sha256"]:
            raise ReviewError("The saved forge patch changed; prepare a new review.")
    if "snapshot" in state:
        if private_git(directory, "rev-parse", "HEAD").decode().strip() != state["snapshot"]["head"]:
            raise ReviewError("The isolated snapshot HEAD changed; prepare a new review.")


def cli_json(args, data=None):
    value = json.loads(run(args, data=encoded(data) if data is not None else None))
    if not isinstance(value, (dict, list)):
        raise ReviewError("The review tool returned an unexpected response.")
    return value


def cmux(*args):
    return cli_json(["cmux", *args, "--json", "--id-format", "uuids"])


def unquote_path(value):
    if value.startswith('"') and value.endswith('"'):
        return codecs.escape_decode(value[1:-1].encode())[0].decode()
    return value


def patch_files(patch):
    patch = patch.replace("\r\n", "\n")
    files = {}
    for block in re.split(r"(?m)(?=^diff --git )", patch):
        if not block.startswith("diff --git "):
            continue
        lines = block.removesuffix("\n").split("\n")
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
            elif line.startswith("new file mode "):
                old = None
            elif line.startswith("deleted file mode "):
                new = None
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


def viewer_environment(directory, state=None):
    home = directory / "tuicr-home"
    env = {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
           "XDG_DATA_HOME": str(home / ".local/share"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
           "TUICR_PROFILE": "0", "TUICR_PROFILE_FILE": ""}
    if state and state.get("pr"):
        env.update(gh_environment(state["pr"]["gh_config_dir"]))
        env["PATH"] = str(directory / "bin") + os.pathsep + os.environ.get("PATH", os.defpath)
    return env


def configure_gh(directory, state):
    binary = shutil.which("gh")
    if not binary:
        raise ReviewError("GitHub CLI is unavailable; native PR sessions require gh.")
    home = state["pr"].get("gh_home", str(Path.home()))
    wrapper = private_directory(directory / "bin") / "gh"
    command = f'#!/bin/sh\nHOME={shlex.quote(home)} exec {shlex.quote(str(Path(binary).resolve()))} "$@"\n'
    save(wrapper, command.encode())
    wrapper.chmod(0o700)


def private_git(directory, *args, data=None):
    return run(["git", "--no-pager", "--literal-pathspecs", "-C", str(directory / "snapshot"),
                "-c", "core.hooksPath=/dev/null", "-c", "core.quotePath=false",
                "-c", "user.name=Pi review snapshot", "-c", "user.email=review@example.invalid",
                *args], data=data, extra_env=viewer_environment(directory))


def configure_viewer(directory):
    home = private_directory(directory / "tuicr-home")
    config = private_directory(home / ".config/tuicr")
    save(config / "config.toml", b'appearance = "dark"\nignore_whitespace = false\n'
         b'backend = "cli"\nno_update_check = true\nreview_watch_interval_ms = 1000\n'
         b'diff_watch_interval_ms = 0\nshow_commits = false\nusername = "user"\n')


def build_snapshot(directory, state):
    private_directory(directory / "snapshot")
    configure_viewer(directory)
    object_format = git(state["repo"], "rev-parse", "--show-object-format").decode().strip()
    private_git(directory, "init", "-q", "--template=", "-b", "review", "--object-format=" + object_format)
    target = state["comparison"]
    baseline = target["base"] if target["mode"] in ("endpoints", "since") else target["head"]
    files = patch_files((directory / "review.patch").read_text())
    entries = []
    for file in files.values():
        path = file["old_path"]
        if path is None or target["mode"] == "root":
            continue
        if target["mode"] == "unstaged":
            value = git(state["repo"], "ls-files", "--stage", "-z", "--", path)
        elif baseline:
            value = git(state["repo"], "ls-tree", "-z", baseline, "--", path)
        else:
            continue
        if not value:
            continue
        records = value.rstrip(b"\0").split(b"\0")
        if len(records) != 1:
            raise ReviewError("Ambiguous baseline path; keep this review in text.")
        metadata, name = records[0].split(b"\t", 1)
        mode, second, third = metadata.split()
        oid = second if target["mode"] == "unstaged" else third
        if set(oid) == {48}:
            continue
        if mode != b"160000":
            blob = git(state["repo"], "cat-file", "blob", oid.decode())
            oid = private_git(directory, "hash-object", "-w", "--stdin", data=blob).strip()
        entries.append(mode + b" " + oid + b"\t" + name + b"\0")
    private_git(directory, "read-tree", "--empty")
    if entries:
        private_git(directory, "update-index", "-z", "--index-info", data=b"".join(entries))
    tree = private_git(directory, "write-tree").decode().strip()
    base = private_git(directory, "commit-tree", tree, data=b"Review baseline\n").decode().strip()
    private_git(directory, "apply", "--cached", "--binary", "--whitespace=nowarn", "--",
                str(directory / "review.patch"))
    tree = private_git(directory, "write-tree").decode().strip()
    head = private_git(directory, "commit-tree", tree, "-p", base,
                       data=b"Selected review changes\n").decode().strip()
    private_git(directory, "update-ref", "refs/heads/review", head)
    result = {"base": base, "head": head}
    actual = snapshot(str(directory / "snapshot"), {"mode": "endpoints", "base": base, "head": head,
                                                    "paths": [], "include_untracked": False})
    if patch_files(actual.decode()) != files:
        raise ReviewError("The isolated snapshot differs from the selected patch; retain the text report.")
    return result


def viewer_directory(directory, state):
    return directory / ("viewer" if state.get("pr") else "snapshot")


def viewer_command(directory, state):
    if state.get("pr"):
        return ["--no-update-check", "pr", state["pr"]["url"]]
    snapshot = state["snapshot"]
    return ["--no-update-check", "--revisions", snapshot["base"] + ".." + snapshot["head"]]


def tuicr(directory, state, *args, data=None):
    return json.loads(run([state["tuicr_binary"], "review", *args],
                          cwd=viewer_directory(directory, state), data=encoded(data) if data is not None else None,
                          extra_env=viewer_environment(directory, state)))


def launch_command(directory):
    args = [sys.executable, str(Path(__file__).resolve()), "_run", "--review", directory.name]
    payload = base64.b64encode(encoded(args)).decode()
    return ('exec /usr/bin/python3 -c \'import base64,json,os; '
            f'a=json.loads(base64.b64decode("{payload}")); os.execv(a[0],a)\'')


def process_started(pid):
    value = run(["ps", "-p", str(pid), "-o", "lstart="]).decode().strip()
    if not value:
        raise ReviewError("The tuicr process is no longer available.")
    return value


def run_viewer(directory):
    state = json.loads((directory / "review.json").read_bytes())
    launch = state.get("launch", {})
    workspace, surface = os.environ.get("CMUX_WORKSPACE_ID"), os.environ.get("CMUX_SURFACE_ID")
    if not workspace or not surface or workspace != launch.get("workspace") or surface == launch.get("caller"):
        raise ReviewError("The new review terminal does not match the launch request.")
    os.umask(0o077)
    fresh(directory, state)
    save(directory / "process.json", encoded({"pid": os.getpid(), "started": process_started(os.getpid()),
                                               "workspace": workspace, "surface": surface}))
    os.chdir(viewer_directory(directory, state))
    for key in list(os.environ):
        if key.startswith("GIT_"):
            del os.environ[key]
    os.environ.update(viewer_environment(directory, state))
    args = [state["tuicr_binary"], *viewer_command(directory, state)]
    os.execv(args[0], args)


def read_json(path):
    if path.is_symlink() or path.stat().st_size > LIMIT:
        raise ReviewError("Invalid or oversized review state.")
    return json.loads(path.read_bytes())


def session_record(directory, state, path):
    path = Path(path)
    home = (directory / "tuicr-home").resolve()
    if not path.is_absolute() or home not in path.resolve().parents:
        raise ReviewError("tuicr returned a session outside this review's private storage.")
    session = read_json(path)
    if session.get("version") != "1.3":
        raise ReviewError("The tuicr session format is unsupported.")
    if state.get("pr"):
        pr = state["pr"]
        key = {"repository": pr["repository"], "number": pr["number"], "head_sha": pr["head"]}
        if (session.get("diff_source") != "pull_request" or session.get("pr_session_key") != key
                or session.get("base_commit") != pr["head"] or session.get("commit_range") is not None):
            raise ReviewError("The tuicr PR identity or head changed; prepare a new review.")
        if session.get("commit_selection_range") is not None:
            raise ReviewError("tuicr has a narrowed PR commit selection; select all commits before importing findings.")
    else:
        head = state["snapshot"]["head"]
        if (Path(session.get("repo_path", "")).resolve() != (directory / "snapshot").resolve()
                or session.get("diff_source") != "commit_range" or session.get("commit_range") != [head]
                or session.get("base_commit") != head or session.get("pr_session_key") is not None):
            raise ReviewError("The tuicr comparison changed.")
    binding = {"path": str(path), "id": session["id"]}
    if state.get("session") and binding != state["session"]:
        raise ReviewError("The tuicr session was replaced; prepare a new review.")
    files = patch_files((directory / "review.patch").read_bytes().decode())
    expected_paths = set(files) if state.get("pr") else set(files) | {f"Commit Message ({head[:7]})"}
    if set(session["files"]) != expected_paths:
        raise ReviewError("tuicr's saved file inventory differs from the reviewed comparison.")
    if state.get("pr"):
        hashes = {path: value.get("content_hash") for path, value in session["files"].items()}
        if hashes != state["pr"]["content_hashes"]:
            raise ReviewError("tuicr's saved PR diff content differs from the reviewed comparison.")
    return binding, files


def viewer_sessions(directory, state):
    selector = ["--all"] if state.get("pr") else ["--repo", str(directory / "snapshot")]
    return tuicr(directory, state, "list", *selector)


def active_binding(directory, state):
    marker = read_json(directory / "process.json")
    if (process_started(marker["pid"]) != marker.get("started")
            or marker.get("surface") != state["launch"]["surface"]
            or marker.get("workspace") != state["launch"]["workspace"]):
        raise ReviewError("The tuicr process no longer matches this review's terminal.")
    sessions = viewer_sessions(directory, state)
    kind = "pr" if state.get("pr") else "local"
    candidates = [item for item in sessions if item.get("active") and item.get("kind") == kind]
    if len(candidates) != 1:
        raise ReviewError("Expected exactly one active tuicr session for this review.")
    binding, files = session_record(directory, state, candidates[0]["path"])
    active = read_json(Path(binding["path"]).parent.parent / "active_sessions.json")
    if active.get("version") != "1.0" or [item["pid"] for item in active["sessions"]
            if item.get("path") == binding["path"]] != [marker["pid"]]:
        raise ReviewError("The active tuicr session does not belong to the launched process.")
    return binding, files


def live_session(directory, state):
    fresh(directory, state)
    launch = state.get("launch", {})
    if not launch.get("surface") or not surface_exists(launch["workspace"], launch["surface"]):
        raise ReviewError("The review's cmux terminal is unavailable; use open --reopen after closing its old pane.")
    if not state.get("session"):
        raise ReviewError("The tuicr launch is incomplete; run open with this review ID to inspect it.")
    return active_binding(directory, state)


def saved_comments(directory, state):
    fresh(directory, state)
    session_record(directory, state, state["session"]["path"])
    comments = tuicr(directory, state, "comments", "--session", state["session"]["path"])
    session_record(directory, state, state["session"]["path"])
    return comments


def human_comments(directory, state):
    fresh_pr(state)
    author = "Pi review " + state["id"]
    receipts = set(state["imports"].values())
    result = {"review": state["id"], "comments": [item for item in saved_comments(directory, state)
              if item.get("author") != author and item.get("id") not in receipts]}
    fresh_pr(state)
    return result


def open_viewer(directory, state, reopen=False):
    fresh_pr(state)
    fresh(directory, state)
    workspace, caller = os.environ.get("CMUX_WORKSPACE_ID"), os.environ.get("CMUX_SURFACE_ID")
    if not workspace or not caller or not surface_exists(workspace, caller):
        raise ReviewError("Automatic review opening requires the owning Pi terminal in native cmux.")
    if reopen:
        launch = state.get("launch", {})
        if not launch.get("surface") or not state.get("session"):
            raise ReviewError("An incomplete launch cannot be reopened automatically; inspect it manually.")
        marker = read_json(directory / "process.json")
        alive = bool(run(["ps", "-p", str(marker["pid"]), "-o", "lstart="], allowed=(0, 1)).strip())
        if surface_exists(launch["workspace"], launch["surface"]) or alive:
            raise ReviewError("Close the old review pane and process before reopening; saved comments are retained.")
        session_record(directory, state, state["session"]["path"])
        state.pop("launch")
        (directory / "process.json").unlink(missing_ok=True)
        save_state(directory, state)
    if state.get("session") and state.get("launch"):
        session, _ = live_session(directory, state)
        return {"review": state["id"], "session": session["path"], "reused": True}
    if "launch" not in state:
        binary = shutil.which("tuicr")
        if not binary:
            raise ReviewError("tuicr is unavailable; install the Homebrew-managed binary before opening a review.")
        if run([binary, "--version"]).decode().strip() != "tuicr 0.27.0":
            raise ReviewError("This helper's persisted-session checks require tuicr 0.27.0; verify a newer format before updating the guard.")
        state["tuicr_binary"] = str(Path(binary).resolve())
        if state.get("pr"):
            configure_gh(directory, state)
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
    wait_seconds = 60 if state.get("pr") else 15
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        marker_path = directory / "process.json"
        if marker_path.exists():
            marker = read_json(marker_path)
            try:
                started = process_started(marker["pid"])
            except ReviewError as error:
                raise ReviewError("The tuicr launch process is unavailable; inspect its pane before preparing a new review.") from error
            if started != marker.get("started"):
                raise ReviewError("The tuicr launch process was replaced; inspect its pane before preparing a new review.")
            sessions = viewer_sessions(directory, state)
            if any(item.get("active") for item in sessions):
                binding, _ = active_binding(directory, state)
                fresh(directory, state)
                fresh_pr(state)
                state["session"] = binding
                launch["status"] = "ready"
                save_state(directory, state)
                return {"review": state["id"], "session": binding["path"], "surface": launch["surface"]}
        time.sleep(0.15)
    raise ReviewError(f"tuicr did not register within {wait_seconds} seconds. Inspect its pane, then run open with the same review ID; do not launch another.")


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
    payload = {"username": "Pi review " + state["id"],
               "type": "suggestion" if finding["priority"] == "optional" else "issue",
               "content": f'[{finding["id"]}][{finding["priority"]}][{finding["axis"]}] {finding["title"]}\n\n{finding["body"]}'}
    if finding.get("reference"):
        payload["content"] += "\n\nReference: " + finding["reference"]
    payload["content"] = payload["content"].strip()
    if finding["scope"] == "review":
        return payload
    path = finding["path"]
    matches = [(name, file) for name, file in files.items()
               if path == name or path == file.get("old_path")]
    if len(matches) != 1:
        return None
    path, file = matches[0]
    payload["file"] = path
    if finding["scope"] == "line":
        lines = file["lines"][finding["side"]]
        if any(line not in lines for line in range(finding["start_line"], finding["end_line"] + 1)):
            return None
        payload.update(side=finding["side"], start_line=finding["start_line"], end_line=finding["end_line"])
    return payload


def same_comment(comment, payload):
    return (comment.get("author") == payload["username"] and comment.get("path") == payload.get("file")
            and comment.get("content") == payload["content"] and comment.get("comment_type") == payload["type"]
            and comment.get("side") == payload.get("side") and comment.get("start_line") == payload.get("start_line")
            and comment.get("end_line") == payload.get("end_line") and comment.get("lifecycle_state") == "local_draft")


def import_findings(directory, state, values):
    findings = validate_findings(values)
    fresh_pr(state)
    for finding_id, value in findings.items():
        if finding_id in state["findings"] and state["findings"][finding_id] != value:
            raise ReviewError("An existing finding ID changed. Preserve its evidence; use a new ID for a revised finding.")
    session, files = live_session(directory, state)
    session_path = session["path"]
    comments = saved_comments(directory, state)
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
            reconciled[finding_id] = matches[0]["id"]
        elif finding_id in state["imports"]:
            raise ReviewError("An imported note was removed or edited, or is no longer a local draft; it will not be recreated automatically.")
        elif finding_id in state.get("pending", []):
            raise ReviewError("A previous import may still complete. Its notes are not visible yet; inspect the pane rather than retrying the write.")
        else:
            pending[finding_id] = payload
    state["findings"].update(findings)
    state["imports"].update(reconciled)
    state["pending"] = [key for key in state.get("pending", []) if key not in reconciled]
    save_state(directory, state)
    for finding_id, payload in pending.items():
        live_session(directory, state)
        state["pending"].append(finding_id)
        save_state(directory, state)
        receipt = tuicr(directory, state, "add", "--session", session_path, "--input", "-", data=payload)
        if not receipt.get("id") or not same_comment(receipt, payload):
            raise ReviewError("tuicr returned an unexpected receipt. Reconcile with the same import; do not create new IDs.")
        state["imports"][finding_id] = receipt["id"]
        state["pending"].remove(finding_id)
        save_state(directory, state)
        live_session(directory, state)
    fresh_pr(state)
    delivered = list(reconciled) + list(pending)
    return {"review": state["id"], "inline": [key for key in delivered if findings[key]["scope"] == "line"],
            "attached": delivered, "report_only": report_only, "artifact": str(directory / "review.json")}


def show_finding(directory, state, finding_id):
    fresh_pr(state)
    session, files = live_session(directory, state)
    finding = state["findings"].get(finding_id)
    if not finding:
        raise ReviewError("That finding is not in this review.")
    comment_id = state["imports"].get(finding_id)
    payload = comment_payload(state, finding, files)
    comments = saved_comments(directory, state)
    if not comment_id or not payload or not any(item.get("id") == comment_id and same_comment(item, payload) for item in comments):
        raise ReviewError("This finding has no unchanged local-draft tuicr note; discuss its saved report entry.")
    fresh_pr(state)
    return {"review": state["id"], "finding": finding, "session": session["path"], "comment": comment_id,
            "navigation": "In this review pane, open :summary, select " + finding_id + ", and press Enter.",
            "surface": state["launch"]["surface"]}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture = commands.add_parser("prepare", help="Capture an exact comparison before starting reviewers")
    capture.add_argument("--repo", required=True)
    capture.add_argument("--mode", choices=MODES, required=True)
    capture.add_argument("--base")
    capture.add_argument("--head")
    capture.add_argument("--path", action="append", default=[])
    capture.add_argument("--include-untracked", action="store_true")
    capture.add_argument("--pr-url", help="Bind a complete endpoints comparison to a native GitHub PR session")
    for name in ("open", "import", "show", "comments", "status", "_run"):
        command = commands.add_parser(name)
        command.add_argument("--review", required=True)
        if name == "open":
            command.add_argument("--reopen", action="store_true", help="Reopen a review whose old pane and tuicr process have closed")
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
                elif args.action == "import":
                    data = sys.stdin.buffer.read(LIMIT + 1) if args.input == "-" else Path(args.input).read_bytes()
                    if len(data) > LIMIT:
                        raise ReviewError("Findings input exceeds 32 MiB.")
                    result = import_findings(directory, state, json.loads(data))
                elif args.action == "show":
                    result = show_finding(directory, state, args.finding)
                elif args.action == "comments":
                    result = human_comments(directory, state)
                else:
                    fresh_pr(state)
                    session, _ = live_session(directory, state)
                    fresh_pr(state)
                    result = {"review": state["id"], "session": session["path"], "comparison": state["comparison"], "pr": state.get("pr"),
                              "findings": list(state["findings"]), "attached": list(state["imports"])}
        print(json.dumps(result, ensure_ascii=True))
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as error:
        message = str(error) if isinstance(error, ReviewError) else "Invalid review state or tool response; inspect locally before retrying."
        print(json.dumps({"error": message}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
