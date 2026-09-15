#!/usr/bin/env python3
"""Private NAS configuration tests. Every path and endpoint is disposable/fictional."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_music_safety import load as load_music

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "stow/bin/.local/bin"
HELPER = BIN / "_music_nas.py"
_spec = importlib.util.spec_from_file_location("_music_nas", HELPER)
nas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nas)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = json.loads((ROOT / "docs/examples/music-nas.example.json").read_text())
        self.config["local_library_root"] = str(self.root / "Example Library")
        self.path = self.root / "nas.json"
        self.path.write_text(json.dumps(self.config))
        self.env = dict(os.environ, HOME=str(self.root), MUSIC_NAS_CONFIG=str(self.path))
        self.env_patch = patch.dict(os.environ, self.env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def cli(self, *args, **extra_env):
        return subprocess.run([sys.executable, str(HELPER), *args],
                              env=dict(self.env, **extra_env), text=True, capture_output=True, timeout=10)

    def test_lazy_import_and_environment_override(self):
        with patch("builtins.open", side_effect=AssertionError("import read a file")):
            _spec.loader.exec_module(nas)
        self.assertEqual(nas.load()["local_library_root"], self.config["local_library_root"])
        self.assertEqual(self.cli("check").stdout, "")
        self.assertFalse((self.root / ".config").exists())

    def test_missing_and_invalid_fail_without_echoing_payload(self):
        self.path.unlink()
        result = self.cli("check")
        self.assertEqual(result.returncode, 3)
        self.assertIn("missing", result.stderr)
        self.path.write_text('{"private-canary": "value"')
        result = self.cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-canary", result.stderr)
        self.assertNotIn(str(self.path), result.stderr)

    def test_schema_rejects_wrong_shapes_keys_versions_hosts_controls_and_paths(self):
        cases = [None, [], {}, dict(self.config, version=True), dict(self.config, version=2),
                 dict(self.config, unexpected="value"), dict(self.config, ssh_hosts=[]),
                 dict(self.config, ssh_hosts=["-oProxyCommand=bad"]),
                 dict(self.config, ssh_hosts=["user@host;command"]),
                 dict(self.config, ssh_hosts=["user@999.999.999.999"]),
                 dict(self.config, local_library_root="relative"),
                 dict(self.config, local_library_root="/a/../b"),
                 dict(self.config, local_library_root="/"),
                 dict(self.config, local_library_root="/path\ncontrol"),
                 dict(self.config, local_library_root="/path\u2028line"),
                 dict(self.config, local_library_root="/path\ud800"),
                 dict(self.config, remote={}), dict(self.config, mount={})]
        for field, value in [("host", "host;bad"), ("user", "user@bad"), ("shares", ["a/b"]),
                             ("shares", ["name", "name"]), ("home_gateway", "not-an-ip")]:
            invalid = copy.deepcopy(self.config)
            invalid["mount"][field] = value
            cases.append(invalid)
        for field in self.config["remote"]:
            invalid = copy.deepcopy(self.config)
            invalid["remote"][field] = "~/relative"
            cases.append(invalid)
        for invalid in cases:
            with self.subTest(case=repr(invalid)[:80]), self.assertRaises(nas.ConfigError):
                nas.validate(invalid)
        self.path.write_text('{"version": 1, "version": 1}')
        with self.assertRaises(nas.ConfigError):
            nas.load()

    def test_getter_preserves_order_and_paths_as_data(self):
        self.config["ssh_hosts"] = ["music@first.example.invalid", "music@second.example.invalid", "music@third.example.invalid"]
        self.config["remote"]["rip"] = "/srv/example tools/it's $(not-a-command); rip"
        self.path.write_text(json.dumps(self.config))
        self.assertEqual(self.cli("get", "ssh_hosts").stdout.splitlines(), self.config["ssh_hosts"])
        value = self.cli("get", "remote.rip").stdout.rstrip("\n")
        self.assertEqual(value, self.config["remote"]["rip"])
        quoted = self.cli("quote", value, "--config-path", "a'b c").stdout.strip()
        self.assertEqual(shlex.split(quoted), [value, "--config-path", "a'b c"])
        self.assertFalse((self.root / "not-a-command").exists())

    def test_mapping_uses_path_components_and_rejects_outside_root(self):
        root = self.config["local_library_root"]
        self.assertEqual(nas.remote_path(root + "/Artist's Album/Song"), "/srv/example-media/Audio/Artist's Album/Song")
        for path in [root + "-other/Album", root + "/../outside"]:
            with self.assertRaises(nas.ConfigError):
                nas.remote_path(path)

    def test_projection_is_worker_only_mode600_and_exclusive(self):
        target = self.root / "worker.json"
        result = self.cli("project-worker", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        projection = json.loads(target.read_text())
        self.assertEqual(projection, {"version": 1, "remote": self.config["remote"]})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(nas.load(target, worker=True), projection)
        with self.assertRaises(nas.ConfigError):
            nas.load(target)
        with self.assertRaises(nas.ConfigError):
            nas.load(self.path, worker=True)
        before = target.read_bytes()
        self.assertNotEqual(self.cli("project-worker", str(target)).returncode, 0)
        self.assertEqual(target.read_bytes(), before)

    def test_mount_missing_skips_and_malformed_fails_before_probes(self):
        helper = self.root / ".local/bin/_music_nas.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(HELPER.read_text())
        script = ROOT / "stow/nas-mount/.local/bin/mount-nas.sh"
        self.path.unlink()
        result = subprocess.run(["/bin/bash", str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipping mounts", result.stdout)
        self.path.write_text('{"version": 2}')
        result = subprocess.run(["/bin/bash", str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no mount attempted", result.stdout)

    def test_fish_missing_config_and_local_overrides(self):
        helper = self.root / ".local/bin/_music_nas.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(HELPER.read_text())
        worker = helper.with_name("riptag-worker.sh")
        captured = self.root / "invoked.json"
        worker.write_text("#!" + sys.executable + "\n" +
                          "import json, os, sys\nfrom pathlib import Path\n" +
                          "Path(os.environ['CAPTURE']).write_text(json.dumps([sys.argv[1:], os.environ['LOCAL_RIP'], os.environ['LOCAL_PYTHON']]))\n")
        worker.chmod(0o755)
        fish = ROOT / "stow/fish/.config/fish/functions/riptag.fish"
        command = "source " + shlex.quote(str(fish)) + "; riptag --local https://example.com/album/test Rock"
        env = dict(self.env, CAPTURE=str(captured), LOCAL_RIP="/example/rip with space", LOCAL_PYTHON=sys.executable)
        self.path.unlink()
        result = subprocess.run(["fish", "--no-config", "--private", "-c", command], env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(captured.exists())
        self.assertFalse((self.root / ".local/state").exists())
        self.path.write_text(json.dumps(self.config))
        result = subprocess.run(["fish", "--no-config", "--private", "-c", command], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        argv, rip, python = json.loads(captured.read_text())
        self.assertIn("--local", argv)
        self.assertEqual(rip, env["LOCAL_RIP"])
        self.assertEqual(python, env["LOCAL_PYTHON"])

    def test_mount_uses_gateway_and_encoded_urls_from_config(self):
        helpers = self.root / ".local/bin"
        helpers.mkdir(parents=True)
        (helpers / "_music_nas.py").write_text(HELPER.read_text())
        self.config["mount"]["shares"] = ["Example Media", "ExampleArchive"]
        self.path.write_text(json.dumps(self.config))
        capture = self.root / "mount-calls"
        for name, body in {
            "route": "printf '  gateway: 192.0.2.1\\n'",
            "nc": "exit 0",
            "mount": "exit 0",
            "osascript": "printf '%s\\n' \"$*\" >> " + shlex.quote(str(capture)),
        }.items():
            path = helpers / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        source = (ROOT / "stow/nas-mount/.local/bin/mount-nas.sh").read_text()
        for name, executable in [("route", "/sbin/route"), ("nc", "/usr/bin/nc"), ("osascript", "/usr/bin/osascript")]:
            source = source.replace(executable, shlex.quote(str(helpers / name)))
        script = self.root / "mount-fixture.sh"
        script.write_text(source)
        result = subprocess.run(["/bin/bash", str(script)], env=dict(self.env, PATH=str(helpers) + os.pathsep + self.env["PATH"]), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = capture.read_text()
        self.assertIn("smb://listener@nas.example.invalid/Example%20Media", calls)
        self.assertIn("smb://listener@nas.example.invalid/ExampleArchive", calls)

    def test_runnability_sync_keeps_power_gate_and_uses_configured_root(self):
        helpers = self.root / ".local/bin"
        helpers.mkdir(parents=True)
        (helpers / "_music_nas.py").write_text(HELPER.read_text())
        gate = helpers / "should-run-background-job"
        gate.write_text("#!/bin/sh\nexit 1\n")
        gate.chmod(0o755)
        capture = self.root / "uv-calls"
        uv = helpers / "uv"
        uv.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + shlex.quote(str(capture)) + "\n")
        uv.chmod(0o755)
        source = (ROOT / "stow/runnability/.local/bin/runnability-sync.sh").read_text()
        script = self.root / "sync-fixture.sh"
        script.write_text(source.replace("UV=/opt/homebrew/bin/uv", "UV=" + shlex.quote(str(uv))))
        self.path.unlink()
        result = subprocess.run(["/bin/sh", str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        gate.write_text("#!/bin/sh\nexit 0\n")
        self.path.write_text(json.dumps(self.config))
        result = subprocess.run(["/bin/sh", str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(capture.exists())
        Path(self.config["local_library_root"]).mkdir()
        result = subprocess.run(["/bin/sh", str(script)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("analyze", capture.read_text())
        self.assertIn("write", capture.read_text())

    def test_python_import_help_and_database_only_commands_do_not_load_nas(self):
        self.path.unlink()
        for name in ("top-hits", "runnability", "music-doctor", "import-album"):
            module = load_music(name)
            with patch.object(module._music_nas, "load", side_effect=AssertionError("unexpected config read")):
                with patch.object(sys, "argv", [name, "--help"]), self.assertRaises(SystemExit) as error:
                    module.main()
                self.assertEqual(error.exception.code, 0)
        top = load_music("top-hits")
        for command in ("chart", "resolve", "approve"):
            with patch.object(top, "cmd_" + command) as action, patch.object(top._music_nas, "load", side_effect=AssertionError("unexpected config read")):
                top.main([command, "2000"])
                action.assert_called_once()
        doctor = load_music("music-doctor")
        for argv in (["report"], ["history"], ["stats", "--from-db"]):
            with patch.object(doctor, "open_db", return_value=Mock()), patch.object(doctor, "cmd_" + argv[0], return_value=0), patch.object(doctor._music_nas, "load", side_effect=AssertionError("unexpected config read")):
                self.assertEqual(doctor.main(argv), 0)
        runn = load_music("runnability")
        for command in ("score", "status"):
            with patch.object(sys, "argv", ["runnability", command]), patch.object(runn, "cmd_" + command, return_value=0), patch.object(runn._music_nas, "load", side_effect=AssertionError("unexpected config read")):
                self.assertEqual(runn.main(), 0)

    def test_missing_config_blocks_python_consumers_before_dispatch_or_database(self):
        self.path.unlink()
        top = load_music("top-hits")
        for command, extra in [("download", ["2000"]), ("assemble", ["2000"]), ("status", ["--years", "2000"]), ("run", ["--years", "2000"])]:
            with patch.object(top, "cmd_" + command) as action, self.assertRaises(SystemExit):
                top.main([command, *extra])
            action.assert_not_called()
        doctor = load_music("music-doctor")
        with patch.object(doctor, "open_db") as database, self.assertRaises(SystemExit):
            doctor.main(["scan"])
        database.assert_not_called()
        runn = load_music("runnability")
        with patch.object(sys, "argv", ["runnability", "analyze"]), patch.object(runn, "cmd_analyze") as analyze, self.assertRaises(SystemExit):
            runn.main()
        analyze.assert_not_called()
        importer = load_music("import-album")
        with patch.object(sys, "argv", ["import-album", str(self.root), "--genre", "Rock"]), patch.object(importer, "collect_audio") as collect, self.assertRaises(SystemExit):
            importer.main()
        collect.assert_not_called()

    def test_explicit_library_roots_do_not_require_config(self):
        self.path.unlink()
        root = str(self.root / "override library")
        Path(root).mkdir()
        doctor = load_music("music-doctor")
        with patch.object(doctor, "open_db", return_value=Mock()), patch.object(doctor, "cmd_scan", return_value=0) as scan, patch.object(doctor._music_nas, "load", side_effect=AssertionError("unexpected read")):
            self.assertEqual(doctor.main(["scan", "--library-root", root]), 0)
            self.assertEqual(scan.call_args.args[0].library_root, root)
        runn = load_music("runnability")
        with patch.object(sys, "argv", ["runnability", "analyze", "--library-root", root]), patch.object(runn, "cmd_analyze", return_value=0), patch.object(runn._music_nas, "load", side_effect=AssertionError("unexpected read")):
            self.assertEqual(runn.main(), 0)
            self.assertEqual(runn.LIBRARY_ROOT, Path(root))
        importer = load_music("import-album")
        with patch.object(sys, "argv", ["import-album", str(self.root), "--genre", "Rock", "--library-root", root]), patch.object(importer, "collect_audio", return_value=[]) as collect, patch.object(importer._music_nas, "load", side_effect=AssertionError("unexpected read")), self.assertRaises(SystemExit):
            importer.main()
        collect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
