#!/usr/bin/env python3
"""Isolated Fish workflow shortcuts with disposable Git history and a Hunk stub."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
ABBRS = ROOT / "stow/fish/.config/fish/conf.d/abbrs.fish"
HB = ROOT / "stow/fish/.config/fish/functions/hb.fish"
FISH = shutil.which("fish")
SHORTCUTS = {
    "pn": "wt pi new",
    "po": "wt pi open",
    "pl": "wt pi list",
    "ws": "wt switch",
    "wb": "wt switch -",
    "hd": "hunk diff",
    "hs": "hunk diff --staged",
    "hc": "hunk show HEAD",
    "hwatch": "hunk diff --watch",
    "prm": "wt pi remove",
    "wmerge": "wt merge --no-commit --no-rebase --no-remove",
}


@unittest.skipUnless(FISH, "Fish is required")
class DevShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dev shortcuts ")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.env = {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"), "XDG_CACHE_HOME": str(self.home / "cache"),
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "LC_ALL": "C", "TERM": "dumb", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
        hunk = self.bin / "hunk"
        hunk.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/hunk-args"\nexit "${HUNK_EXIT:-0}"\n')
        hunk.chmod(0o755)
        self.git("init", "-b", "main")
        self.git("commit", "--allow-empty", "-m", "base")
        self.git("checkout", "-b", "feature/test")
        self.git("commit", "--allow-empty", "-m", "task")

    def git(self, *args):
        return subprocess.run(
            ["/usr/bin/git", *args], cwd=self.repo, env=self.env,
            text=True, capture_output=True, check=True, timeout=10,
        )

    def fish(self, script, interactive=False):
        return subprocess.run(
            [FISH, "--no-config", *(["-i"] if interactive else []), "-c", script],
            cwd=self.repo, env=self.env, text=True, capture_output=True, timeout=10,
        )

    def hb(self, *args):
        return self.fish(f"source {shlex.quote(str(HB))}; hb " + shlex.join(args))

    def test_abbreviations_reload_without_duplicates(self):
        source = f"source {shlex.quote(str(ABBRS))}; "
        result = self.fish(source * 2 + "abbr --show", interactive=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = {}
        for line in result.stdout.splitlines():
            fields = shlex.split(line)
            name, expansion = fields[-2:]
            self.assertNotIn(name, actual)
            actual[name] = expansion
        for name, expansion in SHORTCUTS.items():
            self.assertEqual(actual[name], expansion)
        result = self.fish(source + "abbr --show")
        self.assertEqual(result.stdout, "")

    def test_branch_review_keeps_explicit_scope(self):
        for base in ("main", "HEAD~1", "refs/heads/main"):
            with self.subTest(base=base):
                result = self.hb(base)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.home / "hunk-args").read_text().splitlines(),
                                 ["diff", f"{base}...HEAD"])

    def test_invalid_arguments_do_not_launch_hunk(self):
        for args in ((), ("",), ("main", "extra"), ("--help",), ("--staged",)):
            with self.subTest(args=args):
                result = self.hb(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Usage: hb <base>", result.stderr)
        self.assertFalse((self.home / "hunk-args").exists())

    def test_invalid_or_noncommit_base_does_not_launch_hunk(self):
        for base in ("missing", "main^{tree}"):
            with self.subTest(base=base):
                self.assertNotEqual(self.hb(base).returncode, 0)
        self.assertFalse((self.home / "hunk-args").exists())

    def test_unrelated_history_does_not_launch_hunk(self):
        self.git("checkout", "--orphan", "unrelated")
        self.git("commit", "--allow-empty", "-m", "unrelated")
        result = self.hb("main")
        self.assertEqual(result.returncode, 1)
        self.assertIn("common ancestor", result.stderr)
        self.assertFalse((self.home / "hunk-args").exists())

    def test_hunk_failure_is_preserved(self):
        self.env["HUNK_EXIT"] = "7"
        self.assertEqual(self.hb("main").returncode, 7)


if __name__ == "__main__":
    unittest.main()
