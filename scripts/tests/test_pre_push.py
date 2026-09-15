#!/usr/bin/env python3
"""Pre-push revision selection with two remotes and a fictional detector."""

import json
import shutil
import sys
import unittest

from test_bootstrap_safety import Fixture, REPO


class PrePushTests(Fixture):
    def setUp(self):
        super().setUp()
        self.init_repo()
        self.git("commit", "--allow-empty", "-qm", "clean base")
        self.base = self.git("rev-parse", "HEAD")
        self.zero = "0" * len(self.base)
        self.script = REPO / "scripts/git-hooks/pre-push"
        detector = self.bin / "betterleaks"
        detector.write_text(f"#!{sys.executable}\n" + """import json, os, pathlib, shlex, subprocess, sys
args = sys.argv[1:]
revision = args[args.index('--log-opts') + 1]
with open(os.environ['CALL_LOG'], 'a') as stream:
    stream.write(json.dumps(revision) + '\\n')
result = subprocess.run(['git', 'log', '--format=', '-p', *shlex.split(revision)],
                        capture_output=True, text=True)
sys.exit(1 if result.returncode or 'FICTIONAL_SCAN_SENTINEL' in result.stdout else 0)
""")
        detector.chmod(0o755)

    def commit_file(self, text):
        (self.repo / "fiction.txt").write_text(text)
        self.git("add", "fiction.txt")
        self.git("commit", "-qm", "fictional content")
        return self.git("rev-parse", "HEAD")

    def run_hook(self, rows):
        payload = "".join(f"{local_ref} {local} {remote_ref} {remote}\n"
                          for local_ref, local, remote_ref, remote in rows)
        return self.run_command("/bin/bash", str(self.script), "public", "https://example.invalid/repo",
                                input=payload)

    def test_new_branch_scans_commit_known_only_to_private_remote(self):
        secret_commit = self.commit_file("FICTIONAL_SCAN_SENTINEL\n")
        tip = self.commit_file("clean current tree\n")
        self.git("update-ref", "refs/remotes/private/main", tip)
        self.git("update-ref", "refs/remotes/public/main", self.base)
        # The old exclusion demonstrably loses the fictional finding.
        self.assertEqual(self.git("rev-list", tip, "--not", "--remotes"), "")
        self.assertIn(secret_commit, self.git("rev-list", tip))
        result = self.run_hook([("refs/heads/topic", tip, "refs/heads/topic", self.zero)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([json.loads(row) for row in self.calls()], [tip])

    @unittest.skipUnless(shutil.which("betterleaks"), "betterleaks is required")
    def test_real_detector_blocks_private_remote_history(self):
        detector = self.bin / "betterleaks"
        detector.unlink()
        detector.symlink_to(shutil.which("betterleaks"))
        (self.repo / ".betterleaks.toml").write_text(
            'title = "Fictional regression detector"\n'
            '[[rules]]\nid = "fictional-regression-marker"\n'
            'regex = "FICTIONAL_SCAN_SENTINEL"\n')
        self.commit_file("FICTIONAL_SCAN_SENTINEL\n")
        tip = self.commit_file("clean current tree\n")
        self.git("update-ref", "refs/remotes/private/main", tip)
        self.git("update-ref", "refs/remotes/public/main", self.base)
        result = self.run_hook([("refs/heads/topic", tip, "refs/heads/topic", self.zero)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("fictional-regression-marker", result.stdout + result.stderr)

    def test_existing_ref_scans_only_outgoing_range_and_deletion_skips(self):
        tip = self.commit_file("clean addition\n")
        result = self.run_hook([("refs/heads/main", tip, "refs/heads/main", self.base),
                                ("(delete)", self.zero, "refs/heads/old", self.base)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([json.loads(row) for row in self.calls()], [f"{self.base}..{tip}"])

    def test_failed_ref_does_not_hide_later_refs(self):
        tip = self.commit_file("FICTIONAL_SCAN_SENTINEL\n")
        result = self.run_hook([("refs/heads/bad", tip, "refs/heads/bad", self.zero),
                                ("refs/heads/clean", self.base, "refs/heads/clean", self.zero)])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual([json.loads(row) for row in self.calls()], [tip, self.base])


if __name__ == "__main__":
    unittest.main()
