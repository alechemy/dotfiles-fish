#!/usr/bin/env python3
"""Fail-closed tests for the Claude work-MCP merge."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "merge-claude-mcp.sh"


class MergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.work = self.root / "repo" / "stow-work" / "work" / "mcp-servers.json"
        self.target = self.home / ".claude.json"
        self.home.mkdir()
        self.work.parent.mkdir(parents=True)
        (self.root / "tmp").mkdir()

    def tearDown(self) -> None:
        if self.target.exists() and self.target.is_file():
            self.target.chmod(0o600)
        self.tempdir.cleanup()

    def run_merge(self, fake_command: str | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "DOTFILES": str(self.root / "repo"),
                "HOME": str(self.home),
                "TMPDIR": str(self.root / "tmp"),
            }
        )
        if fake_command:
            fake_bin = self.root / f"fake-{fake_command}"
            fake_bin.mkdir()
            command = fake_bin / fake_command
            command.write_text("#!/bin/sh\nexit 1\n")
            command.chmod(0o755)
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
        return subprocess.run(
            [str(SCRIPT)],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
            check=False,
        )

    def write_target(self, value: object) -> bytes:
        content = json.dumps(value, separators=(",", ":")).encode()
        self.target.write_bytes(content)
        return content

    def test_missing_inputs_create_minimal_mode_600_state(self) -> None:
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.target.read_text()), {"mcpServers": {}})
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def test_retires_managed_personal_entries_and_preserves_other_state(self) -> None:
        self.write_target(
            {
                "runtimeOwned": {"keep": True},
                "mcpServers": {
                    "devonthink": {"type": "stdio", "command": "/safe/old"},
                    "ankimcp": {"type": "sse", "url": "http://127.0.0.1:4473"},
                    "filesystem": {"type": "stdio", "command": "/safe/old"},
                    "adhoc": {"type": "stdio", "command": "/safe/adhoc"},
                },
            }
        )
        self.work.write_text(
            json.dumps(
                {"work-only-test": {"type": "stdio", "command": "/safe/work"}}
            )
        )
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        merged = json.loads(self.target.read_text())
        self.assertEqual(merged["runtimeOwned"], {"keep": True})
        self.assertEqual(
            sorted(merged["mcpServers"]), ["adhoc", "work-only-test"]
        )

    def test_invalid_live_or_work_json_never_replaces_live_state(self) -> None:
        for invalid_path in ("live", "work"):
            with self.subTest(invalid_path=invalid_path):
                original = self.write_target({"runtimeOwned": True})
                if invalid_path == "live":
                    self.target.write_text("not JSON")
                    original = b"not JSON"
                else:
                    self.work.write_text("not JSON")
                result = self.run_merge()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.target.read_bytes(), original)
                self.assertNotIn("merged MCP servers", result.stdout)
                self.work.unlink(missing_ok=True)

    def test_unreadable_inputs_fail_closed(self) -> None:
        original = self.write_target({"runtimeOwned": True})
        self.target.chmod(0)
        result = self.run_merge()
        self.target.chmod(0o600)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertNotIn("merged MCP servers", result.stdout)

        self.work.write_text("{}")
        self.work.chmod(0)
        result = self.run_merge()
        self.work.chmod(0o600)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertNotIn("merged MCP servers", result.stdout)

    def test_non_file_inputs_fail_closed(self) -> None:
        self.target.mkdir()
        result = self.run_merge()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.target.is_dir())
        self.assertNotIn("merged MCP servers", result.stdout)
        self.target.rmdir()

        original = self.write_target({"runtimeOwned": True})
        self.work.mkdir()
        result = self.run_merge()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertNotIn("merged MCP servers", result.stdout)

    def test_chmod_or_move_failure_is_reported_without_false_success(self) -> None:
        for command in ("chmod", "mv"):
            with self.subTest(command=command):
                original = self.write_target({"runtimeOwned": True})
                result = self.run_merge(fake_command=command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.target.read_bytes(), original)
                self.assertNotIn("merged MCP servers", result.stdout)


if __name__ == "__main__":
    unittest.main()
