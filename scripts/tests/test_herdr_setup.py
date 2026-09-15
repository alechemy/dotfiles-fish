#!/usr/bin/env python3
"""Herdr bootstrap and Ghostty shortcut contracts in a disposable home."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class HerdrSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        for directory in (self.repo / "scripts", self.home, self.bin):
            directory.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/setup-herdr.sh", self.repo / "scripts/setup-herdr.sh")
        shutil.copytree(ROOT / "stow/herdr/_seed", self.repo / "stow/herdr/_seed")
        self.log = self.root / "calls"
        installer = self.repo / "scripts/install-herdr-hunk-diff.sh"
        installer.write_text('#!/bin/sh\necho "install hunk plugin" >> "$CALL_LOG"\n')
        installer.chmod(0o755)
        stub = self.bin / "herdr"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
                        'if [ "$1 $2" = "config check" ]; then test -f "$HERDR_CONFIG_PATH"; fi\n')
        stub.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("PI_", "HERDR_"))}
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}",
                        CALL_LOG=str(self.log))
        self.config = self.home / ".config/herdr/config.toml"

    def run_setup(self, expected=0):
        result = subprocess.run(["bash", str(self.repo / "scripts/setup-herdr.sh")],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_seeds_config_and_installs_integration(self):
        self.run_setup()
        self.assertEqual(self.config.read_bytes(),
                         (ROOT / "stow/herdr/_seed/.config/herdr/config.toml").read_bytes())
        self.assertTrue((self.home / ".pi/agent").is_dir())
        self.assertEqual(self.log.read_text().splitlines(),
                         ["config check", "integration install pi", "install hunk plugin"])

    def test_preserves_app_owned_preferences(self):
        self.run_setup()
        self.config.write_text('onboarding = false\n[theme]\nname = "nord"\n')
        before = self.config.stat().st_mtime_ns
        self.run_setup()
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assertIn('name = "nord"', self.config.read_text())

    def test_dangling_config_is_not_overwritten(self):
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(self.root / "absent")
        self.run_setup(expected=1)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse((self.root / "absent").exists())
        self.assertNotIn("integration install pi", self.log.read_text())

    def test_respects_explicit_pi_directory(self):
        target = self.home / "another-agent"
        self.env["PI_CODING_AGENT_DIR"] = str(target)
        self.run_setup()
        self.assertTrue(target.is_dir())
        self.assertFalse((self.home / ".pi/agent").exists())


class GhosttyHerdrKeysTests(unittest.TestCase):
    def test_every_herdr_forward_has_a_plain_shell_override(self):
        config = (ROOT / "stow/ghostty/.config/ghostty/config").read_text()
        shell = (ROOT / "stow/ghostty/.config/ghostty/shell.conf").read_text()
        forwarded = dict(re.findall(r"^keybind = ([^=]+)=(text:.*)$", config, re.M))
        overrides = dict(re.findall(r"^keybind = ([^=]+)=(.*)$", shell, re.M))
        for key in forwarded.keys() - {"cmd+enter", "shift+enter"}:
            self.assertIn(key, overrides)
            self.assertFalse(overrides[key].startswith("text:"))
        self.assertIn("command = /opt/homebrew/bin/fish\n", shell)

    def test_forwarded_keys_match_herdr_bindings(self):
        config = (ROOT / "stow/ghostty/.config/ghostty/config").read_text()
        herdr = (ROOT / "stow/herdr/_seed/.config/herdr/config.toml").read_text()
        aliases = {"enter": "\r", "left_bracket": "[", "right_bracket": "]"}
        for key, codepoint, modifier in re.findall(
                r"^keybind = ([^=]+)=text:\\x1b\[(\d+);(\d+)u$", config, re.M):
            if key == "cmd+enter":
                continue
            parts = key.split("+")
            name = parts[-1]
            char = aliases.get(name, name.removeprefix("digit_"))
            expected_modifier = 1 + sum({"shift": 1, "alt": 2, "ctrl": 4, "cmd": 8}[p]
                                        for p in parts[:-1])
            self.assertEqual(int(codepoint), ord(char), key)
            self.assertEqual(int(modifier), expected_modifier, key)
            binding = "+".join(parts[:-1] + ["enter" if char == "\r" else char])
            if char.isdigit():
                self.assertIn('"cmd+1..9"', herdr)
            else:
                self.assertIn(f'"{binding}"', herdr)
        for number in range(1, 10):
            self.assertIn(f"keybind = cmd+digit_{number}=text:", config)

    def test_submit_bindings_remain_owned_by_pi(self):
        config = (ROOT / "stow/ghostty/.config/ghostty/config").read_text()
        herdr = (ROOT / "stow/herdr/_seed/.config/herdr/config.toml").read_text()
        self.assertIn(r"keybind = cmd+enter=text:\x1b[13;9u", config)
        self.assertNotIn('"cmd+enter"', herdr)
        self.assertNotIn('"ctrl+s"', herdr)


if __name__ == "__main__":
    unittest.main()
