#!/usr/bin/env python3
"""Exercise real tuicr PR sessions with a synthetic, network-free gh executable."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "stow/agents/.agents/skills/code-review/tuicr_review.py"
spec = importlib.util.spec_from_file_location("tuicr_review", HELPER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)

GH = '''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
root=Path(__file__).parent
fixture=json.loads((root/'fixture.json').read_text())
if os.environ.get('HOME') != fixture['original_home']:
    sys.exit(1)
a=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f:
    f.write(json.dumps(a)+'\\n')
if a[:2]==['pr','view']:
    result=fixture['view']
elif a[:2]==['pr','diff']:
    print((root/'review.patch').read_text(),end='')
    sys.exit(0)
elif a and a[0]=='api' and any('/contents/sample.txt?ref=' in s for s in a):
    ref=next(s.split('?ref=')[1] for s in a if '/contents/sample.txt?ref=' in s)
    print(fixture['contents'][ref],end='')
    sys.exit(0)
elif a and a[0]=='api' and 'graphql' in a and not any('mutation' in s for s in a):
    result={'data':{'viewer':{'login':'fixture-user'},'repository':{'pullRequest':{
        'reviews':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}},
        'reviewThreads':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}
    }}}}
elif a and a[0]=='api' and any('/files?' in s for s in a):
    result=fixture['files']
elif a and a[0]=='api' and any('/commits?' in s for s in a):
    result=fixture['commits']
elif a and a[0]=='api' and any(s=='repos/fixture/project/pulls/88' for s in a):
    result=fixture['identity']
else:
    (root/'unexpected').write_text(json.dumps(a))
    sys.exit(2)
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-ui", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    os.environ["PI_SESSION_ID"] = "tuicr-pr-smoke-" + uuid.uuid4().hex
    workspace = None
    directory = None
    with tempfile.TemporaryDirectory(prefix="tuicr-pr-smoke-") as temporary:
        root = Path(temporary).resolve()
        repo = root / "repo"
        repo.mkdir()
        binary = root / "bin"
        binary.mkdir()
        (binary / "gh").write_text(GH)
        (binary / "gh").chmod(0o700)
        tool_paths = [str(Path(shutil.which(tool)).parent) for tool in ("cmux", "tuicr", "git")]
        path = os.pathsep.join(dict.fromkeys([str(binary), *tool_paths, "/usr/bin", "/bin"]))
        env = {"PATH": path, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

        def git(*args):
            return review.run(["git", "-C", str(repo), *args], extra_env=env).decode().strip()

        git("init", "-q", "-b", "main")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "core.hooksPath", "/dev/null")
        (repo / "sample.txt").write_text("before\nsecond\n")
        git("add", "--all")
        git("commit", "-qm", "Baseline")
        base = git("rev-parse", "HEAD")
        (repo / "sample.txt").write_text("after\nsecond\n")
        git("add", "--all")
        git("commit", "-qm", "Change")
        head = git("rev-parse", "HEAD")
        url = "https://github.com/fixture/project/pull/88"
        fixture = {"original_home": str(Path.home()), "view": {"number": 88, "url": url, "title": "Synthetic PR", "state": "OPEN", "body": "Synthetic review.",
                            "headRefOid": head, "baseRefOid": base, "headRefName": "feature", "baseRefName": "main"},
                   "identity": {"number": 88, "html_url": url, "head": {"sha": head},
                                "base": {"sha": base, "repo": {"full_name": "fixture/project"}}},
                   "contents": {base: "before\nsecond\n", head: "after\nsecond\n"},
                   "files": [{"filename": "sample.txt", "status": "modified"}],
                   "commits": [{"sha": head, "commit": {"message": "Change"}}]}
        (binary / "fixture.json").write_text(json.dumps(fixture))
        target = {"mode": "endpoints", "base": base, "head": head, "paths": [], "include_untracked": False}
        (binary / "review.patch").write_bytes(review.snapshot(str(repo), target))
        launch = review.launch_command

        def launch_command(directory):
            return "exec env PATH=" + shlex.quote(path) + " " + launch(directory).removeprefix("exec ")

        try:
            created = review.cmux("workspace", "create", "--cwd", str(repo), "--name", "tuicr PR smoke", "--focus", "false")
            workspace = created["workspace_id"]
            tree = review.cmux("tree", "--workspace", workspace)
            surfaces = [surface["id"] for window in tree.get("windows", [])
                        for item in window.get("workspaces", []) if item.get("id") == workspace
                        for pane in item.get("panes", []) for surface in pane.get("surfaces", [])
                        if surface.get("type") == "terminal"]
            assert len(surfaces) == 1
            with patch.dict(os.environ, {"PATH": path, "CMUX_WORKSPACE_ID": workspace, "CMUX_SURFACE_ID": surfaces[0]}), \
                    patch.object(review, "launch_command", side_effect=launch_command):
                args = argparse.Namespace(repo=str(repo), mode="endpoints", base=base, head=head,
                                          path=[], include_untracked=False, pr_url=url)
                captured = review.prepare(args)
                directory = review.review_directory(captured["review"])
                state = review.load_state(directory)
                try:
                    opened = review.open_viewer(directory, state)
                except review.ReviewError:
                    if (binary / "unexpected").exists():
                        print("Unsupported synthetic gh call:", (binary / "unexpected").read_text())
                    raise
                assert review.open_viewer(directory, state)["reused"]
                finding = {"id": "R1", "axis": "Correctness", "priority": "P2", "scope": "line", "path": "sample.txt",
                           "side": "new", "start_line": 1, "end_line": 2, "title": "Synthetic finding.", "body": "Synthetic evidence. \n"}
                for _ in range(2):
                    assert review.import_findings(directory, state, {"findings": [finding]})["inline"] == ["R1"]
                assert len(review.saved_comments(directory, state)) == 1
                assert review.show_finding(directory, state, "R1")["comment"] == state["imports"]["R1"]
                human = review.tuicr(directory, state, "add", "--session", opened["session"], "--input", "-",
                                     data={"username": "user", "content": "Synthetic human feedback."})
                assert [item["id"] for item in review.human_comments(directory, state)["comments"]] == [human["id"]]
                fixture["identity"]["head"]["sha"] = "f" * 40
                (binary / "fixture.json").write_text(json.dumps(fixture))
                try:
                    review.import_findings(directory, state, {"findings": [{**finding, "id": "R2"}]})
                except review.ReviewError as error:
                    assert "PR base or head changed" in str(error)
                else:
                    raise AssertionError("Changed PR head was accepted")
                assert len(review.saved_comments(directory, state)) == 2
                assert not (binary / "unexpected").exists(), (binary / "unexpected").read_text()
                calls = [json.loads(line) for line in (binary / "calls.jsonl").read_text().splitlines()]
                assert any(call[:2] == ["pr", "view"] for call in calls)
                assert any(any("/files?" in value for value in call) for call in calls)
                print("Native PR smoke passed: exact PR binding, local drafts, idempotent imports, human feedback, and stale-head refusal.")
        finally:
            if workspace:
                review.cmux("workspace", "close", workspace)
            if directory and (directory / "process.json").exists():
                pid = review.read_json(directory / "process.json")["pid"]
                deadline = time.monotonic() + 10
                while review.run(["ps", "-p", str(pid), "-o", "lstart="], allowed=(0, 1)).strip():
                    if time.monotonic() >= deadline:
                        raise review.ReviewError("Synthetic PR viewer did not exit; artifacts retained.")
                    time.sleep(0.1)
            if directory:
                shutil.rmtree(directory)


if __name__ == "__main__":
    main()
