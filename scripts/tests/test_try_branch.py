#!/usr/bin/env python3
"""Exercise the Fish try-branch helper against disposable Git remotes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "stow/fish/.config/fish/functions/try-branch.fish"


class TryBranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="try-branch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.remote = self.root / "remote.git"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / ".config"),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_TERMINAL_PROMPT="0")
        self.git("init", "-qb", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "hooks"))
        self.git("commit", "--allow-empty", "-qm", "fixture")
        self.original = self.git("rev-parse", "HEAD")
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-q", "origin", "HEAD:refs/heads/feature/account")
        self.git("update-ref", "-d", "refs/remotes/origin/feature/account")
        self.calls = self.root / "wt-args"

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, check=True,
                              timeout=10).stdout.strip()

    def run_helper(self, *args, wt_status=0, integration=True):
        env = {**self.env, "TRY_BRANCH_SOURCE": str(SOURCE), "WT_CALLS": str(self.calls),
               "WT_STATUS": str(wt_status)}
        wrapper = """
            function wt
                printf '%s\\n' $argv > "$WT_CALLS"
                command git rev-parse --verify "$argv[-1]^{commit}"; or return
                return $WT_STATUS
            end
        """ if integration else ""
        return subprocess.run(["fish", "--no-config", "-c",
                               'source "$TRY_BRANCH_SOURCE"; ' + wrapper +
                               '; try-branch $argv', *args],
                              cwd=self.repo, env=env, capture_output=True,
                              text=True, timeout=15)

    def test_fetches_missing_remote_branch(self):
        result = self.run_helper("origin/feature/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/feature/account"), self.original)
        self.assertEqual(self.calls.read_text().splitlines(),
                         ["switch", "--create", "try/origin/feature/account", "--base",
                          "origin/feature/account"])
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_fetch_is_limited_to_requested_branch(self):
        self.git("push", "-q", "origin", "HEAD:refs/heads/unrelated")
        self.git("update-ref", "-d", "refs/remotes/origin/unrelated")
        self.git("config", "--unset-all", "remote.origin.fetch")
        result = self.run_helper("origin/feature/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/remotes/"),
                         "refs/remotes/origin/feature/account")

    def test_refreshes_stale_remote_branch(self):
        self.git("fetch", "-q", "origin")
        self.git("commit", "--allow-empty", "-qm", "remote update")
        updated = self.git("rev-parse", "HEAD")
        self.git("push", "-q", "origin", "HEAD:refs/heads/feature/account")
        self.git("update-ref", "refs/remotes/origin/feature/account", self.original)
        result = self.run_helper("origin/feature/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/feature/account"), updated)

    def test_refreshes_rewritten_remote_branch(self):
        self.git("commit", "--allow-empty", "-qm", "temporary tip")
        self.git("update-ref", "refs/remotes/origin/feature/account", self.git("rev-parse", "HEAD"))
        result = self.run_helper("origin/feature/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/feature/account"), self.original)

    def test_fetch_failure_does_not_use_cached_ref(self):
        self.git("fetch", "-q", "origin")
        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        result = self.run_helper("origin/feature/account")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertNotIn("Ready in", result.stdout)

    def test_local_branches_do_not_fetch(self):
        self.git("remote", "set-url", "origin", str(self.root / "missing.git"))
        self.git("branch", "origin/local")
        for branch in ("main", "origin/local"):
            with self.subTest(branch=branch):
                result = self.run_helper(branch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls.read_text().splitlines()[-1], branch)

    def test_other_named_remote(self):
        self.git("remote", "rename", "origin", "upstream")
        result = self.run_helper("upstream/feature/account")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "refs/remotes/upstream/feature/account"), self.original)

    def test_missing_remote_branch_does_not_switch(self):
        result = self.run_helper("origin/missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_deleted_remote_branch_does_not_use_cached_ref(self):
        self.git("fetch", "-q", "origin")
        self.git("push", "-q", "origin", ":refs/heads/feature/account")
        self.git("update-ref", "refs/remotes/origin/feature/account", self.original)
        result = self.run_helper("origin/feature/account")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())

    def test_invalid_remote_branch_does_not_fetch(self):
        result = self.run_helper("origin/feature:refs/heads/main")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("+ git fetch", result.stdout)
        self.assertFalse(self.calls.exists())

    def test_preserves_switch_failure(self):
        result = self.run_helper("main", wt_status=7)
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("Ready in", result.stdout)

    def test_argument_count_and_integration_checked_before_fetch(self):
        for args, integration in (((), True), (("main", "extra"), True),
                                  (("origin/feature/account",), False)):
            with self.subTest(args=args, integration=integration):
                result = self.run_helper(*args, integration=integration)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.calls.exists())
                self.assertNotIn("+ git fetch", result.stdout)


if __name__ == "__main__":
    unittest.main()
