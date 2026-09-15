#!/usr/bin/env python3
"""Bootstrap safety with fictional disposable state; never run a live setup."""

import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.repo = self.root / "repo"
        for path in (self.home, self.bin, self.repo / "scripts"):
            path.mkdir(parents=True)
        self.log = self.root / "calls"
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/opt/homebrew/bin:/usr/bin:/bin",
                    "CALL_LOG": str(self.log), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull}

    def run_command(self, *args, **kwargs):
        return subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True,
                              text=True, timeout=20, **kwargs)

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def copy_script(self, name):
        target = self.repo / "scripts" / name
        shutil.copy2(REPO / "scripts" / name, target)
        return target

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def git(self, *args):
        result = self.run_command("git", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def init_repo(self):
        self.git("init", "-q", "--template=")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("config", "commit.gpgsign", "false")


class SetupPrerequisiteTests(Fixture):
    def setUp(self):
        super().setUp()
        self.script = self.copy_script("setup.sh")
        self.stub("sudo", 'echo sudo >> "$CALL_LOG"; exit 91')

    def test_missing_clt_never_calls_git_or_installer(self):
        self.stub("xcode-select", 'echo "xcode-select $*" >> "$CALL_LOG"; exit 1')
        self.stub("git", 'echo git >> "$CALL_LOG"; exit 92')
        result = self.run_command("/bin/bash", str(self.script))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), ["xcode-select -p"])
        self.assertIn("xcode-select --install", result.stderr)

    def test_linked_worktree_refused_before_sudo(self):
        self.stub("xcode-select", 'echo "xcode-select $*" >> "$CALL_LOG"')
        self.stub("git", """echo "git $4" >> "$CALL_LOG"
case "$4" in
  --git-dir) echo .git/worktrees/task ;;
  --git-common-dir) echo .git ;;
  *) exit 92 ;;
esac""")
        result = self.run_command("/bin/bash", str(self.script))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), ["xcode-select -p", "git --git-dir", "git --git-common-dir"])
        self.assertIn("primary dotfiles checkout", result.stderr)

    def test_scheduled_template_has_setup_loader(self):
        source = (REPO / "scripts/setup.sh").read_text()
        template = REPO / "stow/runnability/Library/LaunchAgents/com.user.runnability-sync.plist.template"
        definition = plistlib.loads(template.read_bytes())
        label = definition["Label"]
        self.assertFalse(definition["RunAtLoad"])
        self.assertIn("StartCalendarInterval", definition)
        self.assertIn(f'load_launch_agent "$HOME/Library/LaunchAgents/{label}.plist"', source)
        # Every tracked personal user-agent template has an explicit loader or
        # the separate DEVONthink opt-in dispatch. Use Git's inventory only.
        paths = subprocess.run(["git", "-C", str(REPO), "ls-files", "stow"],
                               env=self.env, capture_output=True, text=True, check=True).stdout.splitlines()
        for name in paths:
            if "/Library/LaunchAgents/" in name and name.endswith(".plist.template"):
                if name.startswith("stow/devonthink/"):
                    continue
                label = plistlib.loads((REPO / name).read_bytes())["Label"]
                self.assertIn(f'{label}.plist', source, name)

    def test_manual_hooks_recipe_from_unrelated_directory(self):
        self.init_repo()
        (self.home / ".dotfiles").symlink_to(self.repo, target_is_directory=True)
        reference = (REPO / "docs/dotfiles-reference.md").read_text()
        command = re.search(r'`(git -C ~/.dotfiles config --local core.hooksPath [^`]+)`', reference).group(1)
        result = subprocess.run(["/bin/bash", "-c", command], cwd=self.home, env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.git("config", "--local", "core.hooksPath")).resolve(),
                         (self.repo / "scripts/git-hooks").resolve())


@unittest.skipUnless(shutil.which("fish"), "Fish is required")
class BrewCleanupTests(Fixture):
    def setUp(self):
        super().setUp()
        self.script = REPO / "stow/bin/.local/bin/brew-cleanup"
        self.cache = self.root / "brew cache"
        self.cache.mkdir()
        (self.cache / "keep").write_text("fictional")
        self.env.update(BREW_REPO=str(self.repo), BREW_CACHE=str(self.cache), FAIL="")
        self.stub("brew", """echo "brew $*" >> "$CALL_LOG"
[ "$FAIL" != "brew $*" ] || exit 3
case "$*" in
  --repo) printf '%s\\n' "$BREW_REPO" ;;
  --cache) printf '%s\\n' "$BREW_CACHE" ;;
esac""")
        for name in ("git", "rm"):
            self.stub(name, f'echo "{name} $*" >> "$CALL_LOG"; [ "$FAIL" != "{name} $*" ]')

    def run_cleanup(self):
        return self.run_command(shutil.which("fish"), "--no-config", str(self.script))

    def test_every_failure_stops_downstream_cleanup(self):
        expected = ["brew --repo", "git prune", "git gc", "brew cleanup", "brew autoremove",
                    "brew --cache", f"rm -rf -- {self.cache}"]
        for index, call in enumerate(expected):
            with self.subTest(call=call):
                self.env["FAIL"] = call
                result = self.run_cleanup()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Done!", result.stdout)
                self.assertEqual(self.calls(), expected[:index + 1])
                self.log.unlink()
        self.env["FAIL"] = ""
        result = self.run_cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Done!", result.stdout)
        self.assertEqual(self.calls(), expected)
        self.assertEqual((self.cache / "keep").read_text(), "fictional")

    def test_invalid_cache_path_is_not_removed(self):
        for cache in ("", "/", "////", "relative"):
            with self.subTest(cache=cache):
                self.env["BREW_CACHE"] = cache
                result = self.run_cleanup()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call.startswith("rm ") for call in self.calls()))
                self.log.unlink()

    def test_missing_brew_repository_stops_before_git(self):
        for repo in (str(self.root / "absent"), ""):
            with self.subTest(repo=repo):
                self.env["BREW_REPO"] = repo
                self.assertNotEqual(self.run_cleanup().returncode, 0)
                self.assertEqual(self.calls(), ["brew --repo"])
                self.log.unlink()


class GitSigningTests(Fixture):
    def setUp(self):
        super().setUp()
        self.script = self.copy_script("build-git-allowed-signers.sh")
        self.historical = self.repo / "stow/git/.config/git/allowed_signers"
        self.historical.parent.mkdir(parents=True)
        config = self.repo / "stow/git/.gitconfig"
        config.write_text('[user]\n email = fixture@example.invalid\n')
        self.old_key = self.root / "historical"
        self.local_key = self.home / ".ssh/id_signing"
        self.local_key.parent.mkdir()
        for key in (self.old_key, self.local_key):
            result = self.run_command("ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(key))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.historical.write_text("fixture@example.invalid " + self.old_key.with_suffix(".pub").read_text())
        self.out = self.home / ".config/git/allowed_signers.local"

    def build(self):
        return self.run_command("/bin/bash", str(self.script))

    def test_fresh_machine_and_historical_commits_verify(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        original_source = self.historical.read_bytes()
        original_stat = self.out.stat()
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.out.stat().st_ino, original_stat.st_ino)
        self.assertEqual(self.out.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.init_repo()
        self.git("config", "gpg.format", "ssh")
        configured = self.git("config", "--file", str(REPO / "stow/git/.gitconfig"),
                              "gpg.ssh.allowedSignersFile")
        self.assertEqual(configured, "~/.config/git/allowed_signers.local")
        self.git("config", "gpg.ssh.allowedSignersFile", str(self.out))
        self.git("config", "commit.gpgsign", "true")
        for key in (self.old_key, self.local_key):
            self.git("config", "user.signingkey", str(key))
            self.git("commit", "--allow-empty", "-qm", "fictional signed commit")
            self.git("verify-commit", "HEAD")
        self.assertEqual(self.historical.read_bytes(), original_source)
        self.assertIn('namespaces="git"', self.out.read_text())

    def test_missing_or_invalid_public_key_keeps_existing_output(self):
        self.assertEqual(self.build().returncode, 0)
        original = self.out.read_bytes()
        public = self.local_key.with_suffix(".pub")
        public.unlink()
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(self.out.read_bytes(), original)
        public.write_text("invalid fictional public key\n")
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(self.out.read_bytes(), original)

    def test_symlink_output_never_writes_tracked_trust(self):
        self.out.parent.mkdir(parents=True)
        self.out.symlink_to(self.historical)
        original = self.historical.read_bytes()
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(self.historical.read_bytes(), original)
        self.assertTrue(self.out.is_symlink())


if __name__ == "__main__":
    unittest.main()
