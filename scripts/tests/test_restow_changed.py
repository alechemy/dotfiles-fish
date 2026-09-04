#!/usr/bin/env python3
"""Restow dispatch in disposable repositories, with every rebuild and Stow stubbed."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "restow-changed.sh"


class RestowChangedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        for directory in (self.repo / "scripts", self.home, self.bin):
            directory.mkdir(parents=True)
        self.log = self.root / "calls"
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=f"{self.bin}:{os.environ['PATH']}", CALL_LOG=str(self.log),
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        shutil.copy2(SCRIPT, self.repo / "scripts/restow-changed.sh")
        self.stub(self.bin / "stow", 'printf "stow %s\\n" "$*" >> "$CALL_LOG"')
        for name in ("merge-pi-settings.sh", "build-dtnote-handler.sh", "build-launchd-plists.sh",
                     "build-vscode-config.sh", "build-zed-config.sh", "build-streamrip-config.sh"):
            self.stub(self.repo / "scripts" / name, f'echo {name} >> "$CALL_LOG"')
        self.write("stow/pi/.pi/agent/settings.fragment.json", "{}")
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("config", "commit.gpgsign", "false")
        self.old = self.commit()

    def stub(self, path, body):
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def write(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_hook(self, new=None):
        result = subprocess.run(["/bin/bash", str(self.repo / "scripts/restow-changed.sh"),
                                 self.old, new or self.commit()], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_script_only_change_rebuilds_without_stow(self):
        with (self.repo / "scripts/merge-pi-settings.sh").open("a") as stream:
            stream.write("# changed\n")
        self.assertEqual(self.run_hook(), ["merge-pi-settings.sh"])

    def test_fragment_change_rebuilds_before_stow(self):
        self.write("stow/pi/.pi/agent/settings.fragment.json", '{"theme":"dark"}')
        calls = self.run_hook()
        self.assertEqual(calls[0], "merge-pi-settings.sh")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].startswith("stow --restow --no-folding "))
        self.assertIn(f"--target={self.home} pi", calls[1])

    def test_noop_and_unrelated_diff_do_nothing(self):
        self.assertEqual(self.run_hook(self.old), [])
        self.write("notes.md", "fixture")
        self.assertEqual(self.run_hook(), [])

    def test_dtnote_script_only_rebuild_retains_app_gate(self):
        with (self.repo / "scripts/build-dtnote-handler.sh").open("a") as stream:
            stream.write("# changed\n")
        new = self.commit()
        self.assertEqual(self.run_hook(new), [])
        (self.home / "Applications/DTNote.app").mkdir(parents=True)
        self.assertEqual(self.run_hook(new), ["build-dtnote-handler.sh"])

    def test_rebuild_failure_remains_nonfatal(self):
        self.stub(self.repo / "scripts/merge-pi-settings.sh", 'echo failed >> "$CALL_LOG"; exit 1')
        self.assertEqual(self.run_hook(), ["failed"])

    def test_opt_in_packages_require_existing_link(self):
        for package in ("stow/devonthink", "stow/streamrip", "stow-work/work", "stow-local/local"):
            self.write(f"{package}/.fixture-{package.split('/')[-1]}", "old")
        self.old = self.commit()
        for package in ("stow/devonthink", "stow/streamrip", "stow-work/work", "stow-local/local"):
            self.write(f"{package}/.fixture-{package.split('/')[-1]}", "new")
        new = self.commit()
        self.assertEqual(self.run_hook(new), [])
        for package in ("stow/devonthink", "stow/streamrip", "stow-work/work", "stow-local/local"):
            name = f".fixture-{package.split('/')[-1]}"
            (self.home / name).symlink_to(self.repo / package / name)
        calls = self.run_hook(new)
        self.assertEqual(len(calls), 4)
        self.assertEqual({call.split()[-1] for call in calls}, {"devonthink", "streamrip", "work", "local"})

    def test_deleted_package_prunes_only_its_links(self):
        deleted = self.write("stow/example/.removed", "old")
        other = self.write("stow/example/.keep-real", "old")
        foreign = self.write("stow/example/.keep-foreign", "old")
        self.old = self.commit()
        (self.home / ".removed").symlink_to(deleted)
        (self.home / ".keep-real").write_text("local")
        (self.home / ".keep-foreign").symlink_to(self.root / "elsewhere")
        for path in (deleted, other, foreign):
            path.unlink()
        deleted.parent.rmdir()
        self.assertEqual(self.run_hook(), [])
        self.assertFalse((self.home / ".removed").is_symlink())
        self.assertEqual((self.home / ".keep-real").read_text(), "local")
        self.assertTrue((self.home / ".keep-foreign").is_symlink())


if __name__ == "__main__":
    unittest.main()
