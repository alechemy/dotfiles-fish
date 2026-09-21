#!/usr/bin/env python3
"""Test AeroSpace gap behavior without touching live state."""

from pathlib import Path
import re
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
GAPS_LIB = REPO / "scripts/aerospace-gaps-lib.sh"


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


class AeroSpaceGapPresetTests(unittest.TestCase):
    def gap_for(self, count, app=""):
        command = f"""
            . {str(GAPS_LIB)!r}
            gap_full=8
            gap_split=120
            gap_centered=240
            gap_for_tiled_count "$1" "$2"
        """
        result = subprocess.run(["/bin/bash", "-c", command, "--", str(count), app],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout.strip())

    def test_lone_cmux_window_uses_two_window_gap(self):
        self.assertEqual(self.gap_for(1, "cmux"), 120)

    def test_other_counts_and_apps_keep_existing_presets(self):
        self.assertEqual(self.gap_for(0), 240)
        self.assertEqual(self.gap_for(1, "Mail"), 240)
        self.assertEqual(self.gap_for(2, "cmux"), 120)
        self.assertEqual(self.gap_for(3, "cmux"), 8)


if __name__ == "__main__":
    unittest.main()
