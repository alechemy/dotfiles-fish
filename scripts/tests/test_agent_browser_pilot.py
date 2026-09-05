#!/usr/bin/env python3
"""Offline fictional regressions; real adapter execution is separately opt-in."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HELPER = Path(__file__).resolve().parents[1] / "test-agent-browser-pilot.py"
SPEC = importlib.util.spec_from_file_location("agent_browser_pilot", HELPER)
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class AgentBrowserPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_environment_does_not_inherit_credentials_or_injection(self):
        with patch.dict(os.environ, {"FAKE_AUTH_TOKEN": "fictional", "NODE_OPTIONS": "fictional", "MCP_OUTPUT_GUARD": "0"}):
            env = pilot.environment(self.root, Path("/fictional/bin/node"), Path("/fictional/pi"))
        for name in ("FAKE_AUTH_TOKEN", "NODE_OPTIONS", "PI_PACKAGE_DIR", "PUPPETEER_DANGEROUS_NO_SANDBOX"):
            self.assertNotIn(name, env)
        self.assertEqual(env["MCP_OUTPUT_GUARD"], "1")
        self.assertEqual(env["PI_OFFLINE"], "1")
        self.assertEqual(env["PI_MCP_ADAPTER_TEST_AUTH_STORE"], "memory")
        for name in ("HOME", "PI_CODING_AGENT_DIR", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
            path = Path(env[name])
            self.assertEqual(path.parent, self.root)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual(Path(env["npm_config_userconfig"]).read_text(), "")
        self.assertEqual(Path(env["npm_config_globalconfig"]).read_text(), "")

    def test_configuration_keeps_approval_and_no_ambient_imports(self):
        config = pilot.configuration(self.root)
        pilot.validate_configuration(config)
        self.assertEqual(config["imports"], [])
        self.assertEqual(config["settings"]["hostConfigDiscovery"], "off")
        self.assertEqual(config["settings"]["agentPluginPaths"], [])
        for field in ("scriptMode", "sampling", "samplingAutoApprove", "elicitation", "autoAuth", "directTools"):
            self.assertIs(config["settings"][field], False)
        server = config["mcpServers"]["pilot"]
        self.assertIs(server["approveTools"], True)
        self.assertEqual(server["protocolVersion"], "legacy")
        self.assertEqual(server["lifecycle"], "lazy")
        self.assertIs(server["exposeResources"], False)
        self.assertIs(config["mcpServers"]["disabled"]["disabled"], True)

    def test_empty_wildcard_and_malformed_allowlists_fail(self):
        for value in ([], None, "allowed_echo", ["*"], [""], [False], ["echo?"]):
            with self.subTest(value=value):
                config = pilot.configuration(self.root)
                config["mcpServers"]["pilot"]["includeTools"] = value
                with self.assertRaises(ValueError):
                    pilot.validate_configuration(config)

    def test_tree_digest_detects_bytes_and_names(self):
        path = self.root / "source.ts"
        path.write_text("fictional one")
        first = pilot.tree_digest(self.root)
        path.write_text("fictional two")
        second = pilot.tree_digest(self.root)
        self.assertNotEqual(first, second)
        path.rename(self.root / "renamed.ts")
        self.assertNotEqual(second, pilot.tree_digest(self.root))

    def test_tree_digest_rejects_symlink(self):
        (self.root / "link").symlink_to("absent")
        with self.assertRaisesRegex(ValueError, "symlink"):
            pilot.tree_digest(self.root)

    def test_version_gate_rejects_wrong_package(self):
        pi = self.root / "pi"
        pi.mkdir()
        (pi / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent", "version": "99.0.0"}))
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            pilot.validate_inputs(Path(sys.executable), pi, pi)

    def test_fixture_fixed_exchange_and_journal(self):
        journal = self.root / "journal.jsonl"
        messages = [
            {"id": 1, "method": "initialize", "params": {"capabilities": {}}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
            {"id": 3, "method": "tools/call", "params": {"name": "allowed_echo", "arguments": {}}},
            {"id": 4, "method": "fictional/missing"},
        ]
        result = subprocess.run([sys.executable, str(pilot.RESOURCES / "synthetic-mcp.py"), str(journal)],
                                input="".join(json.dumps(message) + "\n" for message in messages), text=True,
                                capture_output=True, check=True, timeout=10, cwd=self.root,
                                env={"PATH": "/usr/bin:/bin", "HOME": str(self.root)})
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "fictional-pilot")
        tools = next(row for row in responses if row.get("id") == 2)["result"]["tools"]
        self.assertEqual(len(tools), 5)
        self.assertEqual(responses[-1]["error"]["code"], -32601)
        rows = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual([row["event"] for row in rows], ["start", "initialize", "call", "exit"])

    def test_process_group_exit_between_probe_and_signal(self):
        for calls, expected in (([None, ProcessLookupError()], False),
                                ([None, None, None, ProcessLookupError()], True)):
            with self.subTest(forced=expected):
                child = Mock(pid=12345)
                with patch.object(pilot.os, "killpg", side_effect=calls), patch.object(pilot.time, "sleep"):
                    self.assertIs(pilot.stop_group(child), expected)
                child.wait.assert_called_once_with(timeout=10)

    def test_process_group_cleanup_reaps_owned_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True,
                                 env={"PATH": "/usr/bin:/bin"}, cwd=self.root,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(pilot.stop_group(child))
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
