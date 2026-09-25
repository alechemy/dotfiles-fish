#!/usr/bin/env python3
"""cmux setup contracts."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/setup-cmux.sh"
MIGRATE_LINK = ROOT / "scripts/migrate-cmux-config-link.py"


class CmuxSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cmux-setup-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "Library/Application Support/com.cmuxterm.app/config.ghostty"
        self.config.parent.mkdir(parents=True)
        self.extension = self.root / ".config/hunk/extensions/worktrunk-feedback.ts"
        self.env = {**os.environ, "HOME": str(self.root), "CMUX_GHOSTTY_CONFIG": str(self.config)}

    def run_setup(self, *args, check=True, env=None):
        return subprocess.run(["bash", str(SCRIPT), *args], env=env or self.env,
                              capture_output=True, text=True, check=check, timeout=15)

    def test_removes_legacy_override_and_is_idempotent(self):
        self.config.write_text("# retired override\ncommand = /opt/homebrew/bin/fish -l -c 'exec herdr'\n")
        self.run_setup()
        self.assertFalse(self.config.exists())
        self.assertIn("worktrunk-send-feedback", self.extension.read_text())
        self.assertEqual(self.extension.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.config.parent.glob("config.ghostty.backup-*"))), 1)
        self.run_setup()
        self.assertEqual(len(list(self.config.parent.glob("config.ghostty.backup-*"))), 1)

    def test_removes_override_file_when_it_has_no_other_settings(self):
        self.config.write_text("command = /usr/bin/env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_TAB_ID -u HERDR_WORKSPACE_ID -u HERDR_SOCKET_PATH -u HERDR_BIN_PATH /opt/homebrew/bin/fish -l -c 'exec /opt/homebrew/bin/herdr'\n")
        self.run_setup()
        self.assertFalse(self.config.exists())
        self.assertEqual(len(list(self.config.parent.glob("config.ghostty.backup-*"))), 1)

    def test_refuses_unrelated_settings(self):
        contents = "font-size = 14\ncommand = /opt/homebrew/bin/fish -l -c 'exec herdr'\n"
        self.config.write_text(contents)
        result = self.run_setup(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), contents)
        self.assertEqual(list(self.config.parent.glob("config.ghostty.backup-*")), [])

    def test_hunk_only_preserves_app_settings_and_updates_only_extension(self):
        contents = "font-size = 14\n"
        self.config.write_text(contents)
        self.run_setup("--hunk-only")
        self.assertEqual(self.config.read_text(), contents)
        self.assertFalse((self.root / ".cmuxterm/automations.json").exists())
        self.assertEqual(list(self.config.parent.glob("config.ghostty.backup-*")), [])
        self.assertEqual(self.extension.read_bytes(), (ROOT / "scripts/cmux/worktrunk-feedback.ts").read_bytes())
        inode = self.extension.stat().st_ino
        self.run_setup("--hunk-only")
        self.assertEqual(self.extension.stat().st_ino, inode)
        self.assertNotEqual(self.run_setup("--hunk-only", "--install-pi-hook", check=False).returncode, 0)

    def test_refuses_symlinks_and_unknown_commands(self):
        target = self.root / "target"
        self.config.symlink_to(target)
        result = self.run_setup(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        self.config.unlink()
        self.config.write_text("command = /bin/zsh -l\n")
        result = self.run_setup(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), "command = /bin/zsh -l\n")

    def test_legacy_stow_link_migration_is_exact(self):
        dotfiles = self.root / "dotfiles"
        self.config = self.root / ".config/ghostty/config"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        old = dotfiles / "stow/ghostty/.config/ghostty/config"
        current = dotfiles / "stow/cmux/.config/ghostty/config"
        self.config.symlink_to(old)
        subprocess.run(["python3", str(MIGRATE_LINK), str(dotfiles)], env=self.env,
                       capture_output=True, text=True, check=True)
        self.assertFalse(self.config.is_symlink())
        self.config.symlink_to(current)
        subprocess.run(["python3", str(MIGRATE_LINK), str(dotfiles)], env=self.env,
                       capture_output=True, text=True, check=True)
        self.assertEqual(self.config.resolve(strict=False), current.resolve(strict=False))
        self.config.unlink()
        unrelated = self.root / "other/config"
        self.config.symlink_to(unrelated)
        result = subprocess.run(["python3", str(MIGRATE_LINK), str(dotfiles)], env=self.env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.resolve(strict=False), unrelated.resolve(strict=False))

    def test_cmux_terminal_config_uses_fish_without_app_window_bindings(self):
        config = (ROOT / "stow/cmux/.config/ghostty/config").read_text()
        for name in (
            "HERDR_AGENT", "HERDR_ENV", "HERDR_PANE_ID", "HERDR_PROCESS_DETECTION",
            "HERDR_TAB_ID", "HERDR_WORKSPACE_ID", "HERDR_SOCKET_PATH", "HERDR_BIN_PATH",
        ):
            self.assertIn(f"-u {name}", config)
        self.assertIn("/opt/homebrew/bin/fish -l\n", config)
        self.assertNotIn("exec herdr", config.lower())
        self.assertNotIn("new_split", config)
        self.assertNotIn("new_window", config)

    def test_pi_hook_install_is_explicit(self):
        binary = self.root / "bin/cmux"
        binary.parent.mkdir()
        log = self.root / "cmux.log"
        binary.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\n")
        binary.chmod(0o755)
        env = {**self.env, "PATH": f"{binary.parent}:{self.env['PATH']}"}
        self.run_setup(env=env)
        self.assertFalse(log.exists())
        self.run_setup("--install-pi-hook", env=env)
        self.assertEqual(log.read_text(), "hooks setup pi --yes\nautomation reload\n")

    def test_setup_succeeds_when_cmux_is_not_running_for_reload(self):
        binary = self.root / "bin/cmux"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\n[ "$1" != automation ]\n')
        binary.chmod(0o755)
        env = {**self.env, "PATH": f"{binary.parent}:{self.env['PATH']}"}
        result = self.run_setup("--install-pi-hook", env=env)
        self.assertIn("next app launch", result.stderr)
        self.assertTrue((self.root / ".cmuxterm/automations.json").exists())

    def test_gap_automation_merge_preserves_other_rules_and_is_idempotent(self):
        config = self.root / ".cmuxterm/automations.json"
        config.parent.mkdir()
        unrelated = {"id": "mine", "when": {"event": "workspace.created"}, "then": []}
        config.write_text(json.dumps({"version": 1, "rules": [unrelated]}))
        self.run_setup()
        rules = json.loads(config.read_text())["rules"]
        self.assertEqual(rules[0], unrelated)
        self.assertEqual(rules[1]["id"], "dotfiles.aerospace-gaps")
        self.assertIn("pane.created", rules[1]["where"]["name"])
        self.assertIn("workspace.selected", rules[1]["where"]["name"])
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        inode = config.stat().st_ino
        self.run_setup()
        self.assertEqual(config.stat().st_ino, inode)
        self.assertEqual(len(list(config.parent.glob("*.backup-*"))), 1)
        rules[1]["enabled"] = False
        config.write_text(json.dumps({"version": 1, "rules": rules}))
        self.run_setup()
        self.assertFalse(json.loads(config.read_text())["rules"][1]["enabled"])

    def test_gap_automation_refuses_invalid_config_and_symlinks(self):
        config = self.root / ".cmuxterm/automations.json"
        config.parent.mkdir()
        config.write_text("invalid json")
        self.assertNotEqual(self.run_setup(check=False).returncode, 0)
        self.assertEqual(config.read_text(), "invalid json")
        config.unlink()
        target = self.root / "elsewhere.json"
        config.symlink_to(target)
        self.assertNotEqual(self.run_setup(check=False).returncode, 0)
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
