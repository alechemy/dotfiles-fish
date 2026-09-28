#!/usr/bin/env python3
"""Opt-in tuicr/cmux integration check in one disposable synthetic workspace."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "stow/agents/.agents/skills/code-review/tuicr_review.py"
spec = importlib.util.spec_from_file_location("tuicr_review", HELPER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-ui", action="store_true", required=True,
                        help="Allow creation and cleanup of one unfocused synthetic cmux workspace")
    parser.parse_args()
    os.umask(0o077)
    os.environ["PI_SESSION_ID"] = "tuicr-smoke-" + uuid.uuid4().hex
    directories = []
    workspace = None
    with tempfile.TemporaryDirectory(prefix="tuicr-review-smoke-") as temporary:
        repo = Path(temporary).resolve()

        def git(*args):
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                           env=env, stderr=subprocess.PIPE).strip()

        git("init", "-q", "-b", "main")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "core.hooksPath", "/dev/null")
        (repo / "sample.txt").write_text("original\nsecond\n")
        (repo / "old name.txt").write_text("rename\n")
        (repo / "removed.txt").write_text("removed\n")
        (repo / "binary.dat").write_bytes(b"\x00\x01")
        (repo / "CRLF.txt").write_bytes(b"original\r\n")
        (repo / "café.txt").write_text("original\n")
        git("add", "--all")
        git("commit", "-qm", "Root fixture")
        base = git("rev-parse", "HEAD")
        (repo / "sample.txt").write_text("committed\nsecond\n")
        (repo / "old name.txt").rename(repo / "new name.txt")
        (repo / "removed.txt").unlink()
        (repo / "binary.dat").write_bytes(b"\x00\x02")
        (repo / "CRLF.txt").write_bytes(b"changed\r\n")
        (repo / "café.txt").write_text("changed\n")
        git("add", "--all")
        git("commit", "-qm", "Changed fixture")
        head = git("rev-parse", "HEAD")
        (repo / "sample.txt").write_text("staged\nsecond\n")
        git("add", "--all")
        (repo / "sample.txt").write_text("working\nsecond\n")
        (repo / "untracked.txt").write_text("untracked\n")
        (repo / "untracked-binary.dat").write_bytes(b"\x00\x03")
        try:
            created = review.cmux("workspace", "create", "--cwd", str(repo), "--name", "tuicr integration smoke", "--focus", "false")
            workspace = created["workspace_id"]
            tree = review.cmux("tree", "--workspace", workspace)
            surfaces = [surface["id"] for window in tree.get("windows", [])
                        for item in window.get("workspaces", []) if item.get("id") == workspace
                        for pane in item.get("panes", []) for surface in pane.get("surfaces", [])
                        if surface.get("type") == "terminal"]
            assert len(surfaces) == 1
            os.environ.update(CMUX_WORKSPACE_ID=workspace, CMUX_SURFACE_ID=surfaces[0])
            for case in (*review.MODES, "textconv", "unborn"):
                if case == "textconv":
                    git("config", "diff.fixture.textconv", "/usr/bin/false")
                    (repo / ".gitattributes").write_text("*.txt diff=fixture\n")
                elif case == "unborn":
                    git("config", "--unset", "diff.fixture.textconv")
                    (repo / ".gitattributes").unlink()
                    git("checkout", "--orphan", "unborn")
                mode = case if case in review.MODES else "worktree"
                args = argparse.Namespace(repo=str(repo), mode=mode, path=[],
                                          base=base if mode in ("endpoints", "since") else None,
                                          head=(base if mode == "root" else head) if mode in ("root", "endpoints") else None,
                                          include_untracked=mode in ("worktree", "since"))
                captured = review.prepare(args)
                directory = review.review_directory(captured["review"])
                directories.append(directory)
                state = review.load_state(directory)
                with review.locked(directory):
                    opened = review.open_viewer(directory, state)
                    finding = {"id": "R1", "axis": "Correctness", "priority": "P2", "scope": "line",
                               "path": "sample.txt", "side": "new", "start_line": 1, "end_line": 2,
                               "title": "Synthetic integration check.", "body": "This is synthetic review feedback. \n"}
                    values = {"findings": [finding, {"id": "R2", "axis": "Spec", "priority": "optional", "scope": "review",
                                                     "title": "Synthetic general finding.", "body": "Keep this as a review comment."},
                                          {"id": "R3", "axis": "Standards", "priority": "P2", "scope": "file",
                                           "path": "sample.txt", "title": "Synthetic file finding.", "body": "Keep this on the file."},
                                          {**finding, "id": "R4", "start_line": 999, "end_line": 999}]}
                    for _ in range(2):
                        imported = review.import_findings(directory, state, values)
                        assert imported["inline"] == ["R1"] and imported["attached"] == ["R1", "R2", "R3"]
                        assert imported["report_only"] == ["R4"]
                    assert len(review.saved_comments(directory, state)) == 3
                    review.show_finding(directory, state, "R1")
                    assert review.open_viewer(directory, state)["reused"]
                    human = review.tuicr(directory, state, "add", "--session", opened["session"], "--input", "-",
                                         data={"username": "user", "content": "Synthetic human feedback."})
                    assert [item["id"] for item in review.human_comments(directory, state)["comments"]] == [human["id"]]
                review.cmux("close-surface", "--workspace", workspace, "--surface", state["launch"]["surface"])
                pid = review.read_json(directory / "process.json")["pid"]
                deadline = time.monotonic() + 10
                while review.run(["ps", "-p", str(pid), "-o", "lstart="], allowed=(0, 1)).strip():
                    if time.monotonic() >= deadline:
                        raise review.ReviewError("Closed smoke process did not exit.")
                    time.sleep(0.1)
                if case == "endpoints":
                    reopened = review.open_viewer(directory, state, reopen=True)
                    assert reopened["session"] == opened["session"]
                    assert len(review.saved_comments(directory, state)) == 4
                    review.cmux("close-surface", "--workspace", workspace, "--surface", state["launch"]["surface"])
                print(json.dumps({"mode": case, "result": "passed"}))
        finally:
            if workspace:
                review.cmux("workspace", "close", workspace)
            for directory in directories:
                if review.load_state(directory)["owner"] == os.environ["PI_SESSION_ID"]:
                    shutil.rmtree(directory)


if __name__ == "__main__":
    main()
