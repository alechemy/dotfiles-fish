#!/usr/bin/env python3
"""Exercise package updates without network access or live installations."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "stow/mise/.local/bin"))
import software_updates as updates
import software_package_updates as packages
import software_update_audit as audit


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.agent = self.home / ".pi/agent"
        self.agent.mkdir(parents=True)
        self.settings = self.agent / "settings.json"
        self.declarations = ["npm:@sting8k/pi-vcc", {"source": "npm:pi-cmux", "extensions": []},
                             "./local/provider-local-delegation/node_modules/pi-subagents",
                             "npm:held@1.0.0"]
        self.settings.write_text(json.dumps({"packages": self.declarations, "unrelated": "preserved"}))
        self.npm = self.agent / "npm"
        self.npm.mkdir()
        self.manifest("@sting8k/pi-vcc", "0.8.0")
        self.manifest("pi-cmux", "0.1.24")
        self.calls = []
        self.addCleanup(patch.stopall)
        self.notifications = patch.object(updates.Job, "notify").start()
        patch.object(packages, "command", side_effect=self.command).start()
        patch.object(packages, "json_command", side_effect=self.json_command).start()

    def manifest(self, name, version, root=None):
        path = (root or self.npm) / "node_modules" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "index.ts").write_text("export default () => {};\n")
        (path / "package.json").write_text(json.dumps({"name": name, "version": version,
                                                      "pi": {"extensions": ["./index.ts"]}}))

    def json_command(self, args, home, env, **kwargs):
        self.calls.append(args)
        if args[:2] == ["npm", "view"]:
            return {"latest": "0.10.1" if args[2] == "@sting8k/pi-vcc" else "0.1.24"}
        raise AssertionError(args)

    def command(self, args, home, env, **kwargs):
        self.calls.append(args)
        if args[:2] == ["/bin/ps", "-axo"]:
            return f"{os.getuid()} pi pi\n"
        if args[:2] == ["npm", "install"]:
            root = Path(args[args.index("--prefix") + 1])
            self.manifest("@sting8k/pi-vcc", "0.10.1", root)
        elif args[:3] == ["uv", "tool", "list"]:
            version = "0.77.2" if any(row[:3] == ["uv", "tool", "upgrade"] for row in self.calls) else "0.76.1"
            return f"agent-reader v0.1.0\n- agent-read\nosxphotos v{version}\n- osxphotos\nqmk v1.2.0\n- qmk\nunknown v1.0.0\n- unknown\n"
        return ""

    def run_pi(self):
        with updates.Job(self.home, "pi", updates.DAY, True) as job:
            result = packages.update_pi(job)
            return result, job.state

    def test_unversioned_only_and_local_source_resolution(self):
        rows = packages.pi_packages(self.home)
        self.assertEqual([r["automatic"] for r in rows], [True, True, False, False])
        self.assertEqual(rows[1]["declaration"], self.declarations[1])
        self.assertEqual(rows[2]["path"], self.agent / "local/provider-local-delegation/node_modules/pi-subagents")

    def test_rejects_duplicate_and_unsafe_package_sources(self):
        for declarations in (["npm:../escape"], ["npm:foo", "npm:foo@1.0.0"], [42]):
            with self.subTest(declarations=declarations):
                self.settings.write_text(json.dumps({"packages": declarations}))
                with self.assertRaises(updates.CheckError):
                    packages.pi_packages(self.home)

    def test_installs_exact_stable_specs_without_scripts_and_keeps_backup(self):
        original = self.settings.read_bytes()
        result, state = self.run_pi()
        self.assertEqual(result, 0)
        install = next(row for row in self.calls if row[:2] == ["npm", "install"])
        self.assertIn("@sting8k/pi-vcc@0.10.1", install)
        self.assertNotIn("pi-subagents", " ".join(install))
        for flag in ("--ignore-scripts", "--legacy-peer-deps", "--save-exact", "--no-audit", "--no-fund"):
            self.assertIn(flag, install)
        self.assertEqual(self.settings.read_bytes(), original)
        self.assertEqual(updates.read_json(self.npm / "node_modules/@sting8k/pi-vcc/package.json")["version"], "0.10.1")
        self.assertEqual(updates.read_json(Path(state["backup"]) / "node_modules/@sting8k/pi-vcc/package.json")["version"], "0.8.0")

    def test_updates_while_pi_is_running_and_notifies_after_success(self):
        result, state = self.run_pi()
        self.assertEqual(result, 0)
        self.assertEqual(state["status"], "success")
        self.assertIn("last_success", state)
        self.notifications.assert_called_once()
        self.assertIn("/reload", self.notifications.call_args.args[1])
        self.assertEqual(self.notifications.call_args.kwargs["level"], "INFO")

    def test_success_clears_obsolete_running_session_deferral(self):
        path = self.home / ".local/state/software-updates/pi.json"
        updates.save_json(path, {"status": "deferred", "reason": "Pi sessions are running",
                                 "first_deferred_at": "2026-01-01T00:00:00+00:00"})
        result, state = self.run_pi()
        self.assertEqual(result, 0)
        self.assertNotIn("reason", state)
        self.assertNotIn("first_deferred_at", state)

    def test_current_packages_do_not_send_reload_notification(self):
        self.run_pi()
        self.notifications.reset_mock()
        result, state = self.run_pi()
        self.assertEqual(result, 0)
        self.notifications.assert_not_called()

    def test_settings_change_during_staging_cancels_activation(self):
        original = self.command
        def changed(args, home, env, **kwargs):
            value = original(args, home, env, **kwargs)
            if args[:2] == ["npm", "install"]:
                self.settings.write_text(json.dumps({"packages": []}))
            return value
        with patch.object(packages, "command", side_effect=changed):
            result, state = self.run_pi()
        self.assertEqual(result, 1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(updates.read_json(self.npm / "node_modules/@sting8k/pi-vcc/package.json")["version"], "0.8.0")

    def test_install_or_manifest_failure_never_changes_live_tree(self):
        for effect in (updates.CheckError("fixture install failure"), "missing-version"):
            with self.subTest(effect=str(effect)):
                with patch.object(packages, "command", side_effect=effect if isinstance(effect, Exception) else None,
                                  return_value=""):
                    result, state = self.run_pi()
                self.assertEqual(result, 1)
                self.assertEqual(state["status"], "failed")
                self.assertEqual(updates.read_json(self.npm / "node_modules/@sting8k/pi-vcc/package.json")["version"], "0.8.0")
                self.assertFalse(any(call.kwargs.get("level") == "INFO" for call in self.notifications.call_args_list))

    def test_prerelease_latest_is_rejected_and_downgrade_is_skipped(self):
        with patch.object(packages, "json_command", return_value={"latest": "1.0.0-rc.1"}):
            result, state = self.run_pi()
        self.assertEqual(result, 1)
        self.assertFalse(any(row[:2] == ["npm", "install"] for row in self.calls))
        with patch.object(packages, "json_command", return_value={"latest": "0.0.1"}):
            result, state = self.run_pi()
        self.assertEqual(result, 0)
        self.assertFalse(any(row[:2] == ["npm", "install"] for row in self.calls))

    def test_backup_failure_rolls_back_atomic_activation(self):
        with patch.object(Path, "rename", side_effect=OSError("fixture backup failure")):
            result, state = self.run_pi()
        self.assertEqual(result, 1)
        self.assertEqual(updates.read_json(self.npm / "node_modules/@sting8k/pi-vcc/package.json")["version"], "0.8.0")

    def test_registry_configuration_and_node_injection_are_removed(self):
        with updates.Job(self.home, "pi", updates.DAY, True) as job:
            job.env.update(NPM_CONFIG_REGISTRY="https://example.invalid", npm_config_ignore_scripts="false",
                           NODE_OPTIONS="--inspect", NODE_PATH="/fixture")
            env = packages.npm_environment(job, self.home)
            self.assertEqual(env["NPM_CONFIG_REGISTRY"], "https://registry.npmjs.org")
            self.assertEqual(env["NPM_CONFIG_IGNORE_SCRIPTS"], "true")
            self.assertNotIn("npm_config_ignore_scripts", env)
            self.assertNotIn("NODE_OPTIONS", env)
            self.assertNotIn("NODE_PATH", env)
            job.finish([])

    def test_private_local_package_is_not_queried_on_public_registry(self):
        local = self.agent / "local/private-package"
        local.mkdir(parents=True)
        (local / "package.json").write_text('{"name":"private-package","version":"1.0.0"}')
        self.settings.write_text(json.dumps({"packages": ["./local/private-package"]}))
        with patch.object(audit, "json_command") as query:
            rows = audit.reviewed_packages(self.home, {})
        query.assert_not_called()
        self.assertEqual(rows[0]["status"], "review-required")

    def test_package_config_is_rejected_without_changing_live_installation(self):
        (self.npm / ".npmrc").write_text("registry=https://example.invalid\n")
        result, state = self.run_pi()
        self.assertEqual(result, 1)
        self.assertEqual(state["status"], "failed")
        self.assertFalse(any(row[:2] == ["npm", "install"] for row in self.calls))

    def test_update_does_not_inspect_or_control_running_sessions(self):
        self.run_pi()
        self.assertFalse(any(row[0] in ("/bin/ps", "kill", "pkill") for row in self.calls))

    def test_audit_covers_all_declarations_and_resolves_current_guard(self):
        local = self.agent / "local/provider-local-delegation/node_modules/pi-subagents"
        local.mkdir(parents=True)
        (local / "package.json").write_text('{"name":"pi-subagents","version":"0.65.0"}')
        self.manifest("held", "1.0.0")
        with patch.object(audit, "json_command", return_value={"latest": "1.0.1", "next": "2.0.0-rc.1"}):
            rows = audit.reviewed_packages(self.home, {})
        self.assertEqual(len(rows), 4)
        guard = next(row for row in rows if row["name"] == "pi-subagents")
        self.assertEqual((guard["installed"], guard["owner"]), ("0.65.0", "review"))
        vcc = next(row for row in rows if row["name"] == "@sting8k/pi-vcc")
        self.assertEqual((vcc["owner"], vcc["status"]), ("pi", "outdated"))
        self.assertEqual(vcc["channels"]["next"], "2.0.0-rc.1")

    def test_uv_only_upgrades_allowlisted_tools_and_probes_them(self):
        with updates.Job(self.home, "uv", updates.DAY, True) as job:
            self.assertEqual(packages.update_uv(job), 0)
            self.assertEqual(job.state["status"], "success")
        upgrades = [row for row in self.calls if row[:3] == ["uv", "tool", "upgrade"]]
        self.assertEqual([row[3] for row in upgrades], ["osxphotos", "qmk"])
        self.assertTrue(all("--prerelease" in row and "disallow" in row and "--no-config" in row for row in upgrades))
        self.assertTrue(any(row == [str(self.home / ".local/bin/osxphotos"), "--version"] for row in self.calls))

    def test_uv_failure_continues_with_other_tools(self):
        original = self.command
        def failure(args, home, env, **kwargs):
            if args[:4] == ["uv", "tool", "upgrade", "osxphotos"]:
                raise updates.CheckError("fixture failure")
            return original(args, home, env, **kwargs)
        with patch.object(packages, "command", side_effect=failure):
            with updates.Job(self.home, "uv", updates.DAY, True) as job:
                self.assertEqual(packages.update_uv(job), 1)
                self.assertEqual(job.state["failures"], ["osxphotos"])
        self.assertTrue(any(row[:4] == ["uv", "tool", "upgrade", "qmk"] for row in self.calls))


class WrapperTests(unittest.TestCase):
    def test_mise_failure_does_not_skip_pi_and_uv(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            helper = home / ".local/bin/software_updates.py"
            helper.parent.mkdir(parents=True)
            helper.write_text("import os, pathlib, sys\np=pathlib.Path(os.environ['HOME'])/'calls'\n"
                              "with p.open('a') as f: f.write(sys.argv[1]+'\\n')\n"
                              "sys.exit(1 if sys.argv[1]=='mise' else 0)\n")
            result = subprocess.run(["/bin/bash", str(ROOT / "stow/mise/.local/bin/update-npm-tools.sh")],
                                    env=dict(os.environ, HOME=str(home)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((home / "calls").read_text().splitlines(), ["mise", "pi", "uv"])


if __name__ == "__main__":
    unittest.main()
