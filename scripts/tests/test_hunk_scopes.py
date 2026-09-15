#!/usr/bin/env python3
"""Offline Git scope contracts used by the Hunk review guidance.

This runs real Git against synthetic files. It does not load Hunk or prove its
base resolver, fallback UI, status dispatcher, or comment-send lifecycle.
See test_hunk_patch.mjs for the exact tracked patch's executable coverage.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class HunkGitScopeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        # Ignore user signing, hooks, aliases, attributes, and Git environment.
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                        GIT_TERMINAL_PROMPT="0")
        self.git("init", "-q", "-b", "release", "--template=")
        for name in ("staged.txt", "unstaged.txt"):
            self.write(name, "baseline\n")
        self.git("add", "--", ".")
        self.git("commit", "-qm", "integration baseline")
        self.merge_base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-qb", "task")
        self.write("first.txt", "first task commit\n")
        self.git("add", "--", "first.txt")
        self.git("commit", "-qm", "first task change")
        self.write("second.txt", "second task commit\n")
        self.git("add", "--", "second.txt")
        self.git("commit", "-qm", "second task change")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        # The integration target diverges after the task's fork point.
        self.git("checkout", "-q", "release")
        self.write("integration-only.txt", "independent integration change\n")
        self.git("add", "--", "integration-only.txt")
        self.git("commit", "-qm", "integration advanced")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "task")
        self.write("staged.txt", "staged change\n")
        self.git("add", "--", "staged.txt")
        self.write("unstaged.txt", "unstaged change\n")
        self.write("untracked.txt", "untracked change\n")

    def git(self, *args, check=True, cwd=None):
        result = subprocess.run(["git", *args], cwd=cwd or self.repo,
                                env=self.env, capture_output=True, text=True, timeout=10)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def write(self, path, text):
        (self.repo / path).write_text(text)

    def names(self, *args):
        result = self.git("diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", *args, "--")
        return set(result.stdout.split("\0")) - {""}

    def test_two_commits_staged_unstaged_and_untracked_have_distinct_scopes(self):
        self.assertEqual(self.names("release...HEAD"), {"first.txt", "second.txt"})
        self.assertEqual(self.names("HEAD^", self.head), {"second.txt"})
        self.assertEqual(self.names("--cached", self.head), {"staged.txt"})
        self.assertEqual(self.names(), {"unstaged.txt"})
        self.assertEqual(self.names(self.head), {"staged.txt", "unstaged.txt"})
        untracked = self.git("ls-files", "--others", "--exclude-standard", "-z", "--").stdout
        self.assertEqual(untracked.split("\0"), ["untracked.txt", ""])
        self.assertEqual(self.git("rev-list", "--count", "release..HEAD").stdout.strip(), "2")
        # A whole-task WIP review needs an explicit dirty/untracked inventory too.
        self.assertEqual(self.names(self.merge_base), {
            "first.txt", "second.txt", "staged.txt", "unstaged.txt",
        })

    def test_non_main_target_uses_merge_base_not_target_tip(self):
        self.assertNotEqual(self.base, self.merge_base)
        self.assertEqual(self.git("merge-base", "--all", self.base, self.head).stdout.strip(),
                         self.merge_base)
        self.assertEqual(self.names("release...HEAD"), self.names(self.merge_base, self.head))
        self.assertEqual(self.names(self.base, self.head), {
            "first.txt", "second.txt", "integration-only.txt",
        })
        self.assertNotEqual(self.git("rev-parse", "--verify", "main", check=False).returncode, 0)

    def test_invalid_or_missing_base_cannot_establish_branch_review(self):
        for ref in ("missing-base", "--help", "HEAD:staged.txt", ""):
            with self.subTest(ref=ref):
                result = self.git("rev-parse", "--verify", "--end-of-options",
                                  f"{ref}^{{commit}}", check=False)
                self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(self.git("diff", "missing-base...HEAD", "--", check=False).returncode, 0)
        # A UI fallback to working-tree mode is NOT evidence for these two commits.
        self.assertTrue({"first.txt", "second.txt"}.isdisjoint(self.names(self.head)))

    def test_unrelated_history_has_no_merge_base(self):
        unrelated = self.git("commit-tree", "HEAD^{tree}", "-m", "unrelated root").stdout.strip()
        result = self.git("merge-base", "--all", unrelated, self.head, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(self.git("diff", f"{unrelated}...{self.head}", "--",
                                     check=False).returncode, 0)

    def test_dirty_edits_change_wip_evidence_without_changing_reviewed_head(self):
        before = self.git("diff", "--no-ext-diff", "--no-textconv", self.head, "--").stdout
        self.write("unstaged.txt", "edited after review\n")
        after = self.git("diff", "--no-ext-diff", "--no-textconv", self.head, "--").stdout
        self.assertNotEqual(before, after)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.head)
        self.assertEqual(self.names("release...HEAD"), {"first.txt", "second.txt"})


if __name__ == "__main__":
    unittest.main()
