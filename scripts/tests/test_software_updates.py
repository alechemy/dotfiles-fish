#!/usr/bin/env python3
"""Exercise scheduled updates with disposable installations and command fixtures."""

from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stow/mise/.local/bin"))
import software_updates as updates
import software_update_audit as audit

spec = importlib.util.spec_from_file_location("setup_updates", ROOT / "scripts/setup-software-updates.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        (self.home / ".config/mise").mkdir(parents=True)
        (self.home / ".config/mise/config.toml").write_text('[tools]\nnode = "24"\n')
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.gate = self.bin / "should-run-background-job"
        self.gate.write_text("#!/bin/sh\nexit 0\n")
        self.gate.chmod(0o755)
        self.records = {
            name: [dict(version=version, requested_version=requested,
                        source=dict(path=str(self.home / ".config/mise/config.toml")),
                        active=True, installed=True, install_path=str(self.home / "installs" / name))]
            for name, version, requested in (("node", "24.1.0", "24"), ("python", "3.14.0", "3.14"),
                                              ("pnpm", "11.0.0", "11"), ("npm:defuddle", "0.1.0", "latest"),
                                              ("npm:@mermaid-js/mermaid-cli", "11.17.0", "11.17.0"),
                                              ("github:Goldziher/uncomment", "3.5.1", "3.5.1"))}
        self.save_records()
        stub = self.bin / "mise"
        stub.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
home = pathlib.Path(os.environ['HOME'])
args = sys.argv[1:]
with (home/'calls').open('a') as f: f.write(json.dumps(args)+'\\n')
assert pathlib.Path.cwd() == home
assert 'MISE_NODE_VERSION' not in os.environ
records = json.loads((home/'records.json').read_text())
if args[:3] == ['config', 'ls', '--json']:
    configs = [dict(path=str(home/'.config/mise/config.toml'))]
    if (home/'mise.toml').exists(): configs.append(dict(path=str(home/'mise.toml')))
    print(json.dumps(configs))
elif args[0] == 'ls': print(json.dumps(records))
elif args[0] == 'outdated': print(json.dumps({{'node': {{}}}} if (home/'node-outdated').exists() else {{}}))
elif args[0] == 'upgrade':
    assert '--no-prune' in args and '--bump' not in args
    if (home/'fail-tool').exists() and (home/'fail-tool').read_text() == args[-1]: sys.exit(1)
elif args[0] == 'which':
    tools = {{'node':'node','python3':'python','pnpm':'pnpm','defuddle':'npm:defuddle','mmdc':'npm:@mermaid-js/mermaid-cli'}}
    tool = tools[args[1]]
    base = home/'wrong' if (home/'wrong-resolution').exists() else pathlib.Path(records[tool][0]['install_path'])
    print(base/'bin'/args[1])
elif args[0] == 'exec':
    if (home/'fail-probe').exists(): sys.exit(1)
else: sys.exit(2)
''')
        stub.chmod(0o755)
        original = updates.environment
        def fixture_environment(home):
            env = original(home)
            env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
            return env
        self.env_patch = patch.object(updates, "environment", fixture_environment)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(patch.stopall)
        patch.object(updates.Job, "notify", lambda *args: None).start()

    def save_records(self):
        (self.home / "records.json").write_text(json.dumps(self.records))

    def run_update(self, force=False):
        with updates.Job(self.home, "mise", updates.DAY, force) as job:
            return updates.update_mise(job) if job else 0

    def calls(self):
        return [json.loads(line) for line in (self.home / "calls").read_text().splitlines()]

    def state(self):
        return updates.read_json(self.home / ".local/state/software-updates/mise.json")

    def test_runtime_ranges_pins_and_order(self):
        self.assertEqual(self.run_update(), 0)
        calls = [row for row in self.calls() if row[0] == "upgrade"]
        self.assertEqual([row[-1] for row in calls], ["node", "pnpm", "python", "npm:defuddle"])
        self.assertEqual(self.state()["status"], "success")
        self.assertTrue(self.state()["previous_versions_retained"])
        self.assertTrue(any(row[:3] == ["exec", "--", "mmdc"] for row in self.calls()))

    def test_missing_tool_is_selected_for_installation(self):
        self.records["npm:defuddle"][0].update(installed=False, active=False)
        self.save_records()
        tools = updates.global_tools(self.home, updates.environment(self.home))
        self.assertIn("npm:defuddle", tools)

    def test_exact_runtime_pin_is_preserved(self):
        self.records["node"][0]["requested_version"] = "24.1.0"
        self.save_records()
        self.assertEqual(self.run_update(), 0)
        self.assertFalse(any(row[0] == "upgrade" and row[-1] == "node" for row in self.calls()))

    def test_project_config_fails_closed(self):
        (self.home / "mise.toml").write_text('[tools]\nnode="25"\n')
        with self.assertRaises(updates.CheckError):
            self.run_update()
        self.assertFalse(any(row[0] == "upgrade" for row in self.calls()))
        self.assertEqual(self.state()["status"], "failed")

    def test_environment_override_removed(self):
        with patch.dict(os.environ, {"MISE_NODE_VERSION": "25", "MISE_GLOBAL_CONFIG_FILE": "/bad"}):
            self.assertEqual(self.run_update(), 0)

    def test_battery_defers_without_success(self):
        self.gate.write_text("#!/bin/sh\nexit 1\n")
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.state()["status"], "deferred")
        self.assertNotIn("last_success", self.state())
        self.assertFalse((self.home / "calls").exists())
        self.assertEqual(self.run_update(force=True), 0)
        self.assertEqual(self.state()["status"], "success")

    def test_recent_login_does_no_work(self):
        self.run_update()
        calls = self.calls()
        state = self.state()
        self.run_update()
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.state(), state)

    def test_overlap_preserves_active_state(self):
        with updates.Job(self.home, "mise", updates.DAY, True) as first:
            state = self.state()
            with updates.Job(self.home, "mise", updates.DAY, True) as second:
                self.assertFalse(second)
                self.assertEqual(self.state(), state)
            first.finish([])

    def test_partial_failure_continues_and_preserves_last_success(self):
        self.run_update()
        success = self.state()["last_success"]
        (self.home / "fail-tool").write_text("python")
        self.assertEqual(self.run_update(force=True), 1)
        self.assertEqual(self.state()["failures"], ["python"])
        self.assertEqual(self.state()["last_success"], success)
        self.assertEqual(self.state()["tools"][-1]["status"], "verified")

    def test_node_failure_defers_dependent_tools(self):
        (self.home / "fail-tool").write_text("node")
        self.assertEqual(self.run_update(), 1)
        calls = [row[-1] for row in self.calls() if row[0] == "upgrade"]
        self.assertEqual(calls, ["node", "python"])
        self.assertIn("npm:defuddle", self.state()["failures"])

    def test_unmanaged_global_blocks_node_upgrade_before_installing(self):
        root = Path(self.records["node"][0]["install_path"]) / "lib/node_modules/fixture"
        root.mkdir(parents=True)
        (root / "package.json").write_text('{"name":"fixture","version":"1.0.0"}')
        (self.home / "node-outdated").touch()
        self.assertEqual(self.run_update(), 1)
        self.assertFalse(any(row[0] == "upgrade" and row[-1] == "node" for row in self.calls()))
        self.assertIn("migration", self.state()["tools"][0]["reason"])

    def test_wrong_resolution_is_failure(self):
        (self.home / "wrong-resolution").touch()
        self.assertEqual(self.run_update(), 1)
        self.assertIn("node", self.state()["failures"])

    def test_unbounded_runtime_selector_is_held(self):
        self.records["node"][0]["requested_version"] = "lts"
        self.save_records()
        self.assertEqual(self.run_update(), 1)
        self.assertFalse(any(row[0] == "upgrade" and row[-1] == "node" for row in self.calls()))

    def test_command_failure_does_not_disclose_output(self):
        with self.assertRaises(updates.CheckError) as caught:
            updates.command(["/bin/sh", "-c", "echo secret >&2; exit 1"], self.home, updates.environment(self.home))
        self.assertNotIn("secret", str(caught.exception))

    def test_timeout_terminates_command(self):
        with self.assertRaisesRegex(updates.CheckError, "timed out"):
            updates.command(["/bin/sleep", "5"], self.home, updates.environment(self.home), timeout=0.02)


class AuditTests(unittest.TestCase):
    def test_weekly_schedule_does_not_skip_for_completion_time(self):
        sunday = datetime(2026, 9, 20, 10, 15, tzinfo=timezone.utc)
        self.assertTrue(updates.audit_due("2026-09-13T10:20:00+00:00", sunday))
        self.assertTrue(updates.audit_due("2026-09-17T12:00:00+00:00", sunday))
        self.assertFalse(updates.audit_due("2026-09-20T10:20:00+00:00", sunday.replace(hour=12)))

    def test_stale_receipt_current_app(self):
        self.assertEqual(audit.classify("5.0", "5.0", "4.8.10"), "stale-receipt")
        self.assertEqual(audit.classify("4.4.0", "4.4", "4.3.2"), "stale-receipt")

    def test_alternate_channel_and_ambiguous_versions(self):
        self.assertEqual(audit.classify("0.12.0-beta.5", "0.11.4", "0.11.2"), "alternate-channel")
        self.assertEqual(audit.classify("11.0", "10.0"), "ahead-of-catalog")
        self.assertEqual(audit.classify(None, "10.0", "9.0"), "unknown")
        self.assertEqual(audit.classify("2771", "2.11"), "ahead-of-catalog")
        self.assertEqual(audit.classify("10.0", "latest"), "unknown")
        self.assertEqual(audit.classify("10.0", "11.0", pinned=True), "intentional-pin")
        self.assertEqual(audit.classify("10.0", "11.0", disabled=True), "disabled")

    def test_brew_checks_actual_bundle_even_without_candidate(self):
        path = Path("/Applications/Fixture.app")
        info = dict(formulae=[], casks=[dict(token="fixture", name=["Fixture"], artifacts=[],
                                            installed="1.0", version="2.0", auto_updates=True)])
        rows, owned = audit.brew_rows(info, dict(formulae=[], casks=[]),
                                     {path: {"CFBundleShortVersionString": "2.0"}})
        self.assertEqual(rows[0]["status"], "stale-receipt")
        self.assertEqual(owned, {path})
        self.assertFalse(rows[0]["candidate"])

    def test_feed_honors_channel_and_os(self):
        xml = b'''<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>
        <item><enclosure sparkle:shortVersionString="2.0"/></item>
        <item><sparkle:channel>beta</sparkle:channel><enclosure sparkle:shortVersionString="3.0"/></item>
        <item><sparkle:minimumSystemVersion>30</sparkle:minimumSystemVersion><enclosure sparkle:shortVersionString="4.0"/></item>
        </channel></rss>'''
        self.assertEqual(audit.feed_version(xml, "26.0"), "2.0")

    def test_feed_rejects_credentials_queries_and_local_hosts(self):
        for url in ("http://example.org/feed", "https://user:password@example.org/feed",
                    "https://example.org/feed?token=secret", "https://127.0.0.1/feed"):
            with self.assertRaises(updates.CheckError):
                audit.public_url(url)

    def test_malformed_bundle_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "Contents").mkdir()
            (path / "Contents/Info.plist").write_text("<plist><dict>\x01</dict></plist>")
            self.assertEqual(audit.app_info(path), {})

    def test_mas_empty_and_json_lines(self):
        self.assertEqual(audit.json_records(""), [])
        self.assertEqual(len(audit.json_records('{"name":"A"}\n{"name":"B"}')), 2)
        with self.assertRaises(updates.CheckError):
            audit.json_records("bad")

    def test_partial_audit_failure_still_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with updates.Job(home, "audit", 7 * updates.DAY, True) as job:
                with patch.object(audit, "app_inventory", return_value={}), \
                     patch.object(audit, "json_command", side_effect=updates.CheckError("private failure")), \
                     patch.object(audit, "mise_rows", return_value=[]), \
                     patch.object(audit, "npm_rows", return_value=[]), \
                     patch.object(audit, "uv_rows", return_value=[]), \
                     patch.object(audit, "mas_rows", return_value=[]), \
                     patch.object(updates.Job, "notify"):
                    self.assertEqual(audit.audit(job), 1)
                report = (job.directory / "report.json").read_text()
                self.assertNotIn("private failure", report)
                self.assertEqual(json.loads(report)["sources"]["homebrew"], "unknown")
                self.assertEqual(job.state["status"], "failed")


class SetupTests(unittest.TestCase):
    def test_wrap_preserves_schedule_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            label = "com.github.domt4.homebrew-autoupdate"
            path = home / "Library/LaunchAgents" / (label + ".plist")
            path.parent.mkdir(parents=True)
            original = str(home / "Library/Application Support" / label / "brew_autoupdate")
            data = dict(Label=label, Program=original, ProgramArguments=[original],
                        StartCalendarInterval=dict(Hour=6, Minute=0), RunAtLoad=True,
                        EnvironmentVariables=dict(FIXTURE="preserved"))
            path.write_bytes(plistlib.dumps(data))
            self.assertTrue(setup.wrap_homebrew(home))
            result = plistlib.loads(path.read_bytes())
            self.assertNotIn("Program", result)
            self.assertEqual(result["StartCalendarInterval"], data["StartCalendarInterval"])
            self.assertEqual(result["EnvironmentVariables"], data["EnvironmentVariables"])
            self.assertEqual(result["ProgramArguments"][-1], "homebrew")
            self.assertFalse(setup.wrap_homebrew(home))

    def test_native_notifications_are_replaced_by_job_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "Library/Application Support/com.github.domt4.homebrew-autoupdate/brew_autoupdate"
            path.parent.mkdir(parents=True)
            text = '#!/bin/sh\n"/fixture/notifier/notify.sh" "$status" "$run_log" always "app"\n'
            path.write_text(text)
            path.chmod(0o555)
            setup.silence_native_notifications(home)
            self.assertEqual(path.read_text(), text.replace(' always ', ' never '))
            self.assertEqual(path.stat().st_mode & 0o777, 0o555)
            before = path.stat().st_mtime_ns
            setup.silence_native_notifications(home)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_unrecognized_homebrew_entrypoint_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            path = home / "Library/LaunchAgents/com.github.domt4.homebrew-autoupdate.plist"
            path.parent.mkdir(parents=True)
            original = plistlib.dumps(dict(ProgramArguments=["/custom/updater"]))
            path.write_bytes(original)
            with self.assertRaises(ValueError):
                setup.wrap_homebrew(home)
            self.assertEqual(path.read_bytes(), original)

    def test_launch_agents_keep_one_mise_schedule(self):
        directory = ROOT / "stow/mise/Library/LaunchAgents"
        templates = list(directory.glob("*.template"))
        self.assertEqual(len(templates), 2)
        for path in templates:
            data = plistlib.loads(path.read_bytes())
            self.assertTrue(data["RunAtLoad"])
            self.assertIn(data["ProgramArguments"][0], ("/bin/bash", "/usr/bin/python3"))


if __name__ == "__main__":
    unittest.main()
