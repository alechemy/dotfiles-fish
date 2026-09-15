"""Publisher boundary tests. Build/render and every push are disposable stubs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PUBLISH = ROOT / "trmnl/learn/bin/publish.sh"
ORIGIN = "https://github.com/alechemy/trmnl-learn.git"


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "public"
        self.repo.mkdir()
        self.plugin = self.root / "plugin"
        (self.plugin / "bin").mkdir(parents=True)
        (self.plugin / "dist").mkdir()
        shutil.copy2(PUBLISH, self.plugin / "bin/publish.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {"HOME": str(self.root / "home"),
                    "PATH": str(self.bin) + ":/opt/homebrew/bin:/usr/bin:/bin",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "TRMNL_LEARN_REPO": str(self.repo), "FIXTURE_ROOT": str(self.root)}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture Publisher")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("remote", "add", "origin", ORIGIN)
        (self.repo / "corpus.json").write_text('{"facts": []}')
        (self.repo / "corpus-0.json").write_text('{"facts": []}')
        (self.repo / "corpus-9.json").write_text('{"facts": []}')
        (self.repo / "corpus-notes.json").write_text('fictional unrelated notes\n')
        (self.repo / "README.md").write_text('fictional unrelated readme\n')
        (self.repo / ".gitignore").write_text('local-only.txt\n')
        self.commit_and_sync()
        self.initial_head = self.git("rev-parse", "HEAD").stdout.strip()
        builder = """#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
(root / "build-called").touch()
if os.environ.get("BUILD_FAIL"):
    sys.exit(1)
for name in ("corpus.json", "corpus-0.json", "corpus-1.json"):
    (root / "plugin/dist" / name).write_text(json.dumps({"facts": [{"id": "fictional-01"}]}))
"""
        overflow = """#!/usr/bin/python3
import os, pathlib, subprocess, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
repo = pathlib.Path(os.environ["TRMNL_LEARN_REPO"])
(root / "overflow-called").touch()
action = os.environ.get("BUILD_ACTION")
if action == "dirty":
    (repo / "new-work.txt").write_text("fictional concurrent work")
elif action == "branch":
    subprocess.run(["/usr/bin/git", "checkout", "-qb", "other"], cwd=repo, check=True)
elif action == "remote":
    subprocess.run(["/usr/bin/git", "remote", "set-url", "origin", "https://fixture.invalid/wrong"], cwd=repo, check=True)
elif action == "head":
    subprocess.run(["/usr/bin/git", "commit", "--allow-empty", "-qm", "Concurrent fictional commit"], cwd=repo, check=True)
if os.environ.get("OVERFLOW_FAIL"):
    sys.exit(1)
"""
        wrapper = """#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
if sys.argv[1] == "status" and os.environ.get("STATUS_FAIL"):
    sys.exit(1)
if sys.argv[1] == "push":
    (root / "push.json").write_text(json.dumps(sys.argv[2:]))
    if os.environ.get("PUSH_FAIL"):
        sys.exit(1)
    # Simulate Git refreshing origin/main after a successful push. No network.
    import subprocess
    subprocess.run(["/usr/bin/git", "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    sys.exit(0)
if sys.argv[1] in ("fetch", "pull", "clone", "ls-remote"):
    sys.exit(91)
os.execv("/usr/bin/git", ["git", *sys.argv[1:]])
"""
        for path, text in ((self.plugin / "bin/build.js", builder),
                           (self.plugin / "bin/overflow-check.rb", overflow),
                           (self.bin / "git", wrapper)):
            path.write_text(text)
            path.chmod(0o755)

    def git(self, *args):
        result = subprocess.run(["/usr/bin/git", "-C", str(self.repo), *args],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def commit_and_sync(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "Fictional fixture")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def publish(self, **env):
        return subprocess.run(["/bin/bash", str(self.plugin / "bin/publish.sh")],
                              env=dict(self.env, **env), text=True, capture_output=True, timeout=20)

    def assert_refused_before_mutation(self, **env):
        status = self.git("status", "--porcelain", "--untracked-files=all").stdout
        head = self.git("rev-parse", "HEAD").stdout
        corpus = (self.repo / "corpus.json").read_bytes()
        result = self.publish(**env)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.root / "push.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, head)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all").stdout, status)
        self.assertEqual((self.repo / "corpus.json").read_bytes(), corpus)
        return result

    def test_only_managed_files_are_committed_and_old_shards_removed(self):
        (self.repo / "local-only.txt").write_text("ignored fictional work")
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pushed 1 facts", result.stdout)
        changed = set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.splitlines())
        self.assertEqual(changed, {"corpus.json", "corpus-0.json", "corpus-1.json", "corpus-9.json"})
        self.assertFalse((self.repo / "corpus-9.json").exists())
        self.assertEqual((self.repo / "corpus-notes.json").read_text(), "fictional unrelated notes\n")
        self.assertEqual((self.repo / "local-only.txt").read_text(), "ignored fictional work")
        commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(json.loads((self.root / "push.json").read_text()),
                         ["-q", "origin", commit + ":refs/heads/main"])
        (self.root / "push.json").unlink()
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("corpus unchanged", result.stdout)
        self.assertFalse((self.root / "push.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), commit)

    def test_dirty_index_worktree_and_untracked_files_are_preserved(self):
        for state in ("staged", "unstaged", "untracked"):
            with self.subTest(state=state):
                path = self.repo / ("untracked.txt" if state == "untracked" else "README.md")
                path.write_text("fictional work to preserve\n")
                if state == "staged":
                    self.git("add", "README.md")
                self.assert_refused_before_mutation()
                self.assertEqual(path.read_text(), "fictional work to preserve\n")
                self.assertFalse((self.root / "build-called").exists())
                if state == "untracked":
                    path.unlink()
                else:
                    self.git("reset", "--hard", "HEAD")

    def test_wrong_branch_detached_head_and_unpublished_commits_are_refused(self):
        self.git("checkout", "-qb", "topic")
        self.assert_refused_before_mutation()
        self.git("checkout", "--detach", "main")
        self.assert_refused_before_mutation()
        self.git("checkout", "main")
        (self.repo / "README.md").write_text("unpublished fictional work")
        self.git("commit", "-qam", "Unrelated pending commit")
        self.assert_refused_before_mutation()
        self.assertFalse((self.root / "build-called").exists())

    def test_wrong_or_multiple_push_urls_and_missing_tracking_ref_are_refused(self):
        self.git("remote", "set-url", "origin", "https://fixture.invalid/wrong")
        self.assert_refused_before_mutation()
        self.git("remote", "set-url", "origin", ORIGIN)
        self.git("remote", "set-url", "--add", "--push", "origin", ORIGIN)
        self.git("remote", "set-url", "--add", "--push", "origin", "https://fixture.invalid/extra")
        self.assert_refused_before_mutation()
        self.git("config", "--unset-all", "remote.origin.pushurl")
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assert_refused_before_mutation()

    def test_failed_status_check_is_not_treated_as_clean(self):
        result = self.assert_refused_before_mutation(STATUS_FAIL="1")
        self.assertIn("cannot inspect destination status", result.stderr)
        self.assertFalse((self.root / "build-called").exists())

    def test_nested_directory_is_not_a_destination_checkout_root(self):
        sub = self.repo / "nested"
        sub.mkdir()
        self.assert_refused_before_mutation(TRMNL_LEARN_REPO=str(sub))

    def test_linked_worktree_is_supported(self):
        self.git("checkout", "--detach")
        worktree = self.root / "linked"
        self.git("worktree", "add", str(worktree), "main")
        result = self.publish(TRMNL_LEARN_REPO=str(worktree))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "push.json").exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.initial_head)

    def test_build_and_overflow_failures_leave_destination_untouched(self):
        self.assert_refused_before_mutation(BUILD_FAIL="1")
        self.assert_refused_before_mutation(OVERFLOW_FAIL="1")

    def test_checks_repeat_after_rendering(self):
        for action in ("dirty", "branch", "remote", "head"):
            with self.subTest(action=action):
                result = self.publish(BUILD_ACTION=action)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse((self.root / "push.json").exists())
                self.assertEqual((self.repo / "corpus.json").read_text(), '{"facts": []}')
                if action == "dirty":
                    self.assertEqual((self.repo / "new-work.txt").read_text(), "fictional concurrent work")
                    (self.repo / "new-work.txt").unlink()
                elif action == "branch":
                    self.git("checkout", "main")
                elif action == "head":
                    self.assertIn("main differs from origin/main", result.stderr)
                    self.git("reset", "--hard", self.initial_head)
                else:
                    self.git("remote", "set-url", "origin", ORIGIN)

    def test_ignored_managed_files_and_symlinks_are_not_overwritten(self):
        (self.repo / ".gitignore").write_text("corpus-1.json\n")
        self.commit_and_sync()
        (self.repo / "corpus-1.json").write_text("ignored fictional work")
        self.assert_refused_before_mutation()
        self.assertEqual((self.repo / "corpus-1.json").read_text(), "ignored fictional work")
        (self.repo / "corpus-1.json").unlink()
        (self.repo / ".gitignore").write_text("")
        outside = self.root / "outside.json"
        outside.write_text("outside fictional work")
        (self.repo / "corpus.json").unlink()
        (self.repo / "corpus.json").symlink_to(outside)
        self.commit_and_sync()
        self.assert_refused_before_mutation()
        self.assertEqual(outside.read_text(), "outside fictional work")

    def test_failed_push_retains_commit_and_requires_manual_review(self):
        result = self.publish(PUSH_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commit retained", result.stderr)
        self.assertNotEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.initial_head)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        (self.root / "push.json").unlink()
        retry = self.assert_refused_before_mutation()
        self.assertIn("review pending commits or a failed push manually", retry.stderr)


if __name__ == "__main__":
    unittest.main()
