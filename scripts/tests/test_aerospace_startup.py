#!/usr/bin/env python3
"""Run the configured restart cleanup against disposable gap state."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class AeroSpaceStartupTests(unittest.TestCase):
    def test_restart_clears_only_the_current_users_manual_override(self):
        config = (REPO / "stow/aerospace/.aerospace.toml").read_text()
        commands = re.findall(r"'exec-and-forget (rm -f [^']+)'", config)
        self.assertEqual(len(commands), 1)
        with tempfile.TemporaryDirectory(prefix="gap startup ") as directory:
            home = Path(directory) / "home with spaces"
            state = home / ".cache/aerospace-gaps"
            state.mkdir(parents=True)
            suppression = state / "suppressed-workspace"
            suppression.write_text("7\n")
            unrelated = state / "ws-counts"
            unrelated.write_text("preserve\n")
            other = Path(directory) / "other/.cache/aerospace-gaps/suppressed-workspace"
            other.parent.mkdir(parents=True)
            other.write_text("8\n")
            env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
            for _ in range(2):
                result = subprocess.run(["/bin/bash", "-c", commands[0]], env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(suppression.exists())
                self.assertEqual(unrelated.read_text(), "preserve\n")
                self.assertEqual(other.read_text(), "8\n")


if __name__ == "__main__":
    unittest.main()
