#!/usr/bin/env python3
"""Offline fictional regressions; real adapter execution is separately opt-in."""

import hashlib
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

    def test_synthetic_environment_is_literal_and_separate_from_browser_configuration(self):
        config = pilot.synthetic_configuration(self.root)
        pilot.validate_configuration(config)
        server = config["mcpServers"]["pilot"]
        self.assertTrue(server["literalEnv"])
        self.assertEqual(server["env"], {
            "PILOT_LITERAL": "!fictional-${PILOT_PARENT_VALUE}", "PILOT_VALUE": "fictional-override",
        })
        self.assertNotIn("env", pilot.configuration(self.root)["mcpServers"]["pilot"])
        spec = importlib.util.spec_from_file_location("synthetic_mcp", pilot.RESOURCES / "synthetic-mcp.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        with patch.dict(os.environ, {**server["env"], "PILOT_PARENT_VALUE": "fictional-parent"}, clear=True):
            self.assertTrue(all(fixture.environment_checks().values()))
            for key, field in (("PILOT_LITERAL", "literalPreserved"), ("PILOT_VALUE", "configuredValueDelivered"),
                               ("PILOT_PARENT_VALUE", "adapterParentInherited")):
                with patch.dict(os.environ, {key: "fictional-wrong"}):
                    self.assertFalse(fixture.environment_checks()[field])
            with patch.dict(os.environ, {"FAKE_AUTH_TOKEN": "fictional-outside"}):
                self.assertFalse(fixture.environment_checks()["outerCredentialAbsent"])
        self.assertTrue(all(type(value) is bool for value in fixture.environment_checks().values()))

    def test_empty_wildcard_and_malformed_allowlists_fail(self):
        for value in ([], None, "allowed_echo", ["*"], [""], [False], ["echo?"]):
            with self.subTest(value=value):
                config = pilot.configuration(self.root)
                config["mcpServers"]["pilot"]["includeTools"] = value
                with self.assertRaises(ValueError):
                    pilot.validate_configuration(config)

    def test_browser_configuration_is_finite_and_does_not_change_refusal_fixture(self):
        config = pilot.browser_configuration(self.root, Path("/fictional/node"), Path("/fictional/server"),
                                             Path("/fictional/Google Chrome"), 1234)
        server = config["mcpServers"]["pilot"]
        self.assertEqual(set(config["mcpServers"]), {"pilot"})
        self.assertEqual(server["includeTools"], pilot.BROWSER_TOOLS)
        self.assertEqual(len(server["includeTools"]), 12)
        self.assertFalse(server["approveTools"])
        self.assertTrue(pilot.configuration(self.root)["mcpServers"]["pilot"]["approveTools"])
        self.assertEqual(server["args"], [
            "/fictional/server/build/src/bin/chrome-devtools-mcp.js", "--headless", "--isolated",
            "--executable-path=/fictional/Google Chrome", "--viewport=1280x720", "--no-usage-statistics",
            "--no-performance-crux", "--no-category-emulation", "--redact-network-headers",
            "--allowed-url-pattern=http://127.0.0.1:1234/*"])
        for port in (False, 0, 65536, "1234"):
            with self.assertRaises(ValueError):
                pilot.browser_configuration(self.root, Path("/node"), Path("/server"), Path("/chrome"), port)

    def test_browser_catalog_and_arguments_with_node_stdlib_only(self):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root)}
        result = subprocess.run(["node", "--test", str(pilot.RESOURCES / "browser-assertions.test.mjs")],
                                capture_output=True, text=True, timeout=30, env=env, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_browser_cleanup_attempts_detached_descendants_after_group_failure(self):
        owned = Mock()
        owned.stop.return_value = False
        with patch.object(pilot, "stop_group", side_effect=RuntimeError("fictional group failure")):
            with self.assertRaisesRegex(RuntimeError, "verify all owned"):
                pilot.stop_browser_processes(Mock(), owned)
        owned.stop.assert_called_once_with()

    def test_browser_runtime_is_retained_when_ownership_is_ambiguous(self):
        paths = iter([self.root / "runtime", self.root / "failure"])

        def temporary(**_kwargs):
            path = next(paths)
            path.mkdir()
            return str(path)

        owned = Mock()
        owned.capture.side_effect = RuntimeError("Fictional observer failure")
        owned.stop.side_effect = RuntimeError("Owned process identity became ambiguous")
        child = Mock()
        child.poll.return_value = None
        with patch.object(pilot.tempfile, "mkdtemp", side_effect=temporary), \
                patch.object(pilot, "ThreadingHTTPServer", side_effect=[Mock(server_port=1234), Mock(server_port=1235)]), \
                patch.object(pilot.threading, "Thread"), patch.object(pilot, "OwnedProcesses", return_value=owned), \
                patch.object(pilot.subprocess, "run", return_value=Mock(stdout="v24.18.0\n")), \
                patch.object(pilot.subprocess, "Popen", return_value=child), \
                patch.object(pilot, "stop_group", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Cleanup incomplete; owned runtime retained"):
                pilot.run_browser(Path("/fictional/node"), Path("/fictional/pi"), Path("/fictional/adapter"),
                                  Path("/fictional/server"), Path("/fictional/Chrome"))
        self.assertTrue((self.root / "runtime").is_dir())
        owned.stop.assert_called_once_with()

    def test_owned_process_metadata_errors_do_not_imply_exit(self):
        for result in (Mock(returncode=0, stdout="", stderr=""),
                       Mock(returncode=1, stdout="", stderr="Fictional query error")):
            with patch.object(pilot.subprocess, "run", return_value=result):
                with self.assertRaises(RuntimeError):
                    pilot.OwnedProcesses.identity(12345)

    def test_owned_process_capture_survives_title_change_and_cleans_detached_child(self):
        processes = {
            12345: ("Sat Sep 5 10:00:00 2026", "node", [12346]),
            12346: ("Sat Sep 5 10:00:01 2026", "node", []),
        }

        def query(args, **_kwargs):
            pid = int(args[2])
            if pid not in processes:
                return Mock(returncode=1, stdout="", stderr="")
            birth, title, children = processes[pid]
            if args[0] == "/bin/ps":
                return Mock(returncode=0, stdout=birth + (" " + title if "comm=" in args else ""), stderr="")
            return Mock(returncode=0 if children else 1, stdout="\n".join(map(str, children)), stderr="")

        def terminate(pid, sig):
            self.assertEqual((pid, sig), (12347, pilot.signal.SIGTERM))
            del processes[pid]

        owned = pilot.OwnedProcesses(12345)
        with patch.object(pilot.subprocess, "run", side_effect=query), \
                patch.object(pilot.time, "sleep"), patch.object(pilot.os, "kill", side_effect=terminate) as kill:
            owned.capture()
            processes[12346] = (processes[12346][0], "chrome-devtools-mcp", [12347])
            processes[12347] = ("Sat Sep 5 10:00:02 2026", "Google Chrome", [])
            owned.capture()
            self.assertEqual(set(owned.identities), {12345, 12346, 12347})
            self.assertEqual(set(owned.alive()), {12345, 12346, 12347})
            del processes[12345]
            del processes[12346]
            self.assertTrue(owned.stop())
            kill.assert_called_once_with(12347, pilot.signal.SIGTERM)

    def test_owned_process_cleanup_refuses_reused_pid_and_reports_ambiguity(self):
        owned = pilot.OwnedProcesses(12345)
        owned.identities = {12345: "original identity"}
        with patch.object(owned, "identity", return_value="different identity"), patch.object(pilot.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                owned.stop()
            kill.assert_not_called()

    def test_owned_process_cleanup_rechecks_identity_before_signal(self):
        owned = pilot.OwnedProcesses(12345)
        owned.identities = {12345: "original identity"}
        with patch.object(owned, "alive", side_effect=[[12345]] * 51 + [[]]), \
                patch.object(owned, "identity", return_value="different identity"), \
                patch.object(pilot.time, "sleep"), patch.object(pilot.os, "kill") as kill:
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                owned.stop()
            kill.assert_not_called()

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

    def test_transitive_pi_main_is_pinned_as_well_as_loader(self):
        pi = self.root / "pi"
        adapter = self.root / "node_modules/pi-mcp-adapter"
        resources = self.root / "resources"
        (pi / "dist/core/extensions").mkdir(parents=True)
        adapter.mkdir(parents=True)
        resources.mkdir()
        (pi / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent", "version": "0.85.1"}))
        (adapter / "package.json").write_text(json.dumps({"name": "pi-mcp-adapter", "version": "2.32.1"}))
        for path in (pi / "dist/core/extensions/loader.js", pi / "dist/main.js", resources / "package-lock.json"):
            path.write_text("fictional source")
        digest = hashlib.sha256(b"fictional source").hexdigest()
        (resources / "provenance.json").write_text(json.dumps({
            "lockSha256": digest, "piLoaderSha256": digest, "piImportFiles": {"dist/main.js": digest},
            "installedPackages": {"pi-mcp-adapter": {"treeSha256": pilot.tree_digest(adapter)}}}))
        with patch.object(pilot, "RESOURCES", resources):
            pilot.validate_inputs(Path(sys.executable), pi, adapter)
            (pi / "dist/main.js").write_text("fictional unexpected import")
            with self.assertRaisesRegex(ValueError, "transitive entrypoint"):
                pilot.validate_inputs(Path(sys.executable), pi, adapter)

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
        self.assertEqual([row["event"] for row in rows], ["start", "environment", "initialize", "call", "exit"])
        self.assertEqual(rows[1]["checks"], {
            "literalPreserved": False, "configuredValueDelivered": False,
            "adapterParentInherited": False, "outerCredentialAbsent": True,
        })

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
