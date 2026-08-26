#!/usr/bin/env python3
"""Fail-closed tests for the agent-reader overlay installer."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "install-agent-reader.sh"
OVERLAY = Path(__file__).parents[2] / "patches" / "agent-reader"


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.marker = self.root / "unexpected-command"
        self.probe_log = self.root / "agent-read-probes"
        self.write_compatible_agent_reader()
        for command in ("git", "uv"):
            path = self.fake_bin / command
            path.write_text(
                f"#!/bin/sh\nprintf '%s\\n' {command} >> {self.marker}\nexit 99\n"
            )
            path.chmod(0o755)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_compatible_agent_reader(self) -> None:
        path = self.fake_bin / "agent-read"
        path.write_text(
            """#!/bin/sh
printf '%s\n' "$*" >> "$AGENT_READ_PROBE_LOG"
case "$*" in
  "list --help")
    printf '%s\n' 'usage: --cli {claude,copilot,pi} --json --limit LIMIT'
    printf '%s\n' 'Maximum sessions (0 = all; default: 20 for table, all for JSON).'
    ;;
  "transcript --help") printf '%s\n' 'usage: transcript --session SESSION' ;;
  *) exit 1 ;;
esac
"""
        )
        path.chmod(0o755)

    def run_script(
        self, script: Path = SCRIPT, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{self.fake_bin}:{env['PATH']}"
        env["AGENT_READ_PROBE_LOG"] = str(self.probe_log)
        return subprocess.run(
            [str(script), *arguments],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=False,
        )

    def test_compatible_install_skips_network_and_uv(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)
        self.assertEqual(
            self.probe_log.read_text().splitlines(),
            ["list --help", "transcript --help"],
        )
        self.assertFalse(self.marker.exists())

    def test_unknown_argument_is_rejected_without_side_effects(self) -> None:
        result = self.run_script(SCRIPT, "--unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        self.assertFalse(self.probe_log.exists())
        self.assertFalse(self.marker.exists())

    def test_overlay_checksum_mismatch_fails_before_capability_probe(self) -> None:
        checkout = self.root / "checkout"
        (checkout / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, checkout / "scripts" / SCRIPT.name)
        shutil.copytree(OVERLAY, checkout / "patches" / "agent-reader")
        with (checkout / "patches" / "agent-reader" / "pi-json.patch").open("ab") as handle:
            handle.write(b"\n")
        result = self.run_script(checkout / "scripts" / SCRIPT.name)
        self.assertEqual(result.returncode, 1)
        self.assertIn("overlay checksum mismatch", result.stderr)
        self.assertFalse(self.probe_log.exists())
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
