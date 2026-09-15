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
        self.stub(self.bin / "op", 'exit 1')
        self.stub(self.bin / "stow", 'printf "stow %s\\n" "$*" >> "$CALL_LOG"')
        for name in ("merge-pi-settings.sh", "build-dtnote-handler.sh", "build-launchd-plists.sh",
                     "build-vscode-config.sh", "build-zed-config.sh", "build-streamrip-config.sh",
                     "build-context7-config.sh", "build-things-config.sh",
                     "build-git-allowed-signers.sh", "setup-herdr.sh", "setup-worktrunk.sh"):
            self.stub(self.repo / "scripts" / name, f'echo "{name}${{1:+ $*}}" >> "$CALL_LOG"')
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
        self.stderr = result.stderr
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_script_only_change_rebuilds_without_stow(self):
        with (self.repo / "scripts/merge-pi-settings.sh").open("a") as stream:
            stream.write("# changed\n")
        self.assertEqual(self.run_hook(), ["merge-pi-settings.sh", "merge-pi-settings.sh --models"])

    def test_generated_builder_only_changes(self):
        for script in ("build-launchd-plists.sh", "build-vscode-config.sh",
                       "build-git-allowed-signers.sh"):
            with self.subTest(script=script):
                with (self.repo / "scripts" / script).open("a") as stream:
                    stream.write("# changed\n")
                self.assertEqual(self.run_hook(), [script])
                self.log.unlink()
                self.old = self.git("rev-parse", "HEAD")

    def test_secret_builders_keep_auth_gate(self):
        for script in ("build-zed-config.sh", "build-context7-config.sh", "build-things-config.sh"):
            with self.subTest(script=script):
                with (self.repo / "scripts" / script).open("a") as stream:
                    stream.write("# changed\n")
                new = self.commit()
                self.assertEqual(self.run_hook(new), [])
                self.assertIn("1Password CLI is unavailable", self.stderr)
                self.stub(self.bin / "op", 'test "$*" = "vault list"')
                self.assertEqual(self.run_hook(new), [script])
                self.log.unlink()
                self.stub(self.bin / "op", 'exit 1')
                self.old = new

    def test_builder_only_additions_queue_package_but_rewrites_do_not(self):
        outputs = {
            "build-context7-config.sh": "stow/fish/.config/fish/conf.d/context7.fish",
            "build-zed-config.sh": "stow/zed/.config/zed/settings.json",
            "build-vscode-config.sh": "stow/vscode/Library/Application Support/VSCodium/User/settings.json",
            "build-launchd-plists.sh": "stow/example/Library/LaunchAgents/com.example.plist",
        }
        self.write("stow/example/Library/LaunchAgents/com.example.plist.template", "fixture")
        self.old = self.commit()
        self.stub(self.bin / "op", 'test "$*" = "vault list"')
        for script, output in outputs.items():
            with self.subTest(script=script):
                path = self.repo / output
                self.stub(self.repo / "scripts" / script,
                          f'echo "{script}" >> "$CALL_LOG"\n'
                          f'mkdir -p "{path.parent}"\nprintf fictional > "{path}"')
                new = self.commit()
                calls = self.run_hook(new)
                self.assertEqual(calls[0], script)
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[1].endswith(" " + output.split("/")[1]))
                self.log.unlink()
                self.assertEqual(self.run_hook(new), [script])
                self.log.unlink()
                # Include the fictional generated output in the next baseline
                # so only the next builder changes in this disposable fixture.
                self.old = self.commit()

    def test_failed_builder_never_queues_new_output(self):
        path = self.repo / "stow/fish/.config/fish/conf.d/context7.fish"
        self.stub(self.bin / "op", 'test "$*" = "vault list"')
        self.stub(self.repo / "scripts/build-context7-config.sh",
                  f'mkdir -p "{path.parent}"; echo fictional > "{path}"; exit 1')
        self.assertEqual(self.run_hook(), [])
        self.assertIn("failed; re-run it by hand", self.stderr)

    def test_streamrip_builder_requires_active_package_and_auth(self):
        output = self.write("stow/streamrip/.config/streamrip/config.toml", "fictional")
        self.old = self.commit()
        with (self.repo / "scripts/build-streamrip-config.sh").open("a") as stream:
            stream.write("# changed\n")
        new = self.commit()
        self.stub(self.bin / "op", 'echo op >> "$CALL_LOG"')
        self.assertEqual(self.run_hook(new), [])
        target = self.home / ".config/streamrip/config.toml"
        target.parent.mkdir(parents=True)
        target.symlink_to(output)
        self.stub(self.bin / "op", 'exit 1')
        self.assertEqual(self.run_hook(new), [])
        self.assertIn("1Password CLI is unavailable", self.stderr)
        self.stub(self.bin / "op", 'test "$*" = "vault list"')
        self.assertEqual(self.run_hook(new), ["build-streamrip-config.sh"])

    def test_root_daemon_changes_only_print_manual_installer(self):
        for path in ("launchd/com.user.iogpu-wired-limit.plist.template",
                     "scripts/install-iogpu-limit.sh"):
            with self.subTest(path=path):
                self.write(path, "fixture")
                self.assertEqual(self.run_hook(), [])
                self.assertIn("run scripts/install-iogpu-limit.sh by hand", self.stderr)
                self.assertNotIn("old agent definition", self.stderr)
                self.old = self.git("rev-parse", "HEAD")

    def test_user_agent_template_rebuilds_and_reminds_without_reload(self):
        self.write("stow/example/Library/LaunchAgents/com.example.plist.template", "fixture")
        calls = self.run_hook()
        self.assertEqual(calls[0], "build-launchd-plists.sh")
        self.assertTrue(calls[1].endswith(" example"))
        self.assertIn("old agent definition", self.stderr)

    def test_fragment_change_rebuilds_before_stow(self):
        self.write("stow/pi/.pi/agent/settings.fragment.json", '{"theme":"dark"}')
        calls = self.run_hook()
        self.assertEqual(calls[0], "merge-pi-settings.sh")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].startswith("stow --restow --no-folding "))
        self.assertIn(f"--target={self.home} pi", calls[1])

    def test_models_fragment_change_rebuilds_before_stow(self):
        self.write("stow/pi/.pi/agent/models.fragment.json", '{"providers":{}}')
        calls = self.run_hook()
        self.assertEqual(calls[0], "merge-pi-settings.sh --models")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].startswith("stow --restow --no-folding "))

    def test_worktrunk_seed_change_runs_setup(self):
        self.write("stow/worktrunk/_seed/.config/worktrunk/config.toml", '[merge]\nremove = false\n')
        calls = self.run_hook()
        self.assertEqual(calls[0], "setup-worktrunk.sh")
        self.assertTrue(calls[1].endswith(" worktrunk"))

    def test_worktrunk_setup_change_runs_without_stow(self):
        with (self.repo / "scripts/setup-worktrunk.sh").open("a") as stream:
            stream.write("\n")
        self.assertEqual(self.run_hook(), ["setup-worktrunk.sh"])

    def test_linked_worktree_never_restows_or_rebuilds(self):
        self.write("stow/pi/.pi/agent/settings.fragment.json", '{"theme":"dark"}')
        new = self.commit()
        target = self.root / "task"
        self.git("worktree", "add", "-qb", "task", str(target))
        result = subprocess.run(["bash", str(target / "scripts/restow-changed.sh"), self.old, new],
                                cwd=target, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assertIn("linked worktree", result.stderr)

    def test_herdr_seed_change_runs_setup(self):
        self.write("stow/herdr/_seed/.config/herdr/config.toml", 'onboarding = false\n')
        calls = self.run_hook()
        self.assertEqual(calls[0], "setup-herdr.sh")
        self.assertTrue(calls[1].endswith(" herdr"))

    def test_herdr_setup_script_change_runs_without_stow(self):
        with (self.repo / "scripts/setup-herdr.sh").open("a") as stream:
            stream.write("\n")
        self.assertEqual(self.run_hook(), ["setup-herdr.sh"])

    def test_hunk_plugin_changes_run_setup_without_stow(self):
        for name in ("install-herdr-hunk-diff.sh", "configure-herdr-hunk.mjs",
                     "patches/herdr-hunk-diff.patch"):
            with self.subTest(name=name):
                self.write(f"scripts/{name}", "fixture")
                self.assertEqual(self.run_hook(), ["setup-herdr.sh"])
                self.log.unlink()
                self.old = self.git("rev-parse", "HEAD")

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
        self.assertEqual(self.run_hook(), ["failed", "failed"])

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
