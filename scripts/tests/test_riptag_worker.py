#!/usr/bin/env python3

import json
import os
import sys
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


WORKER = Path(__file__).parents[2] / "stow/bin/.local/bin/riptag-worker.sh"


class LibraryPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "ExampleMedia/Audio"
        self.inbox = self.root / "downloads"
        self.inbox.mkdir()
        self.existing = self.inbox / "Existing album"
        self.existing.mkdir()
        (self.existing / "track.m4a").write_bytes(b"existing download")
        self.bin = self.root / ".local/bin"
        self.bin.mkdir(parents=True)
        self.rip_called = self.root / "rip-called"
        self.mount_called = self.root / "mount-called"
        self.rip = self.bin / "rip"
        self.rip.write_text(
            f"#!/bin/sh\ntouch {shlex.quote(str(self.rip_called))}\nexit 1\n"
        )
        self.rip.chmod(0o755)
        self.worker = self.root / "worker.sh"
        self.worker.write_text(WORKER.read_text())
        helper = WORKER.with_name("_music_nas.py")
        (self.root / "_music_nas.py").write_text(helper.read_text())
        self.config = {
            "version": 1,
            "mount": {"host": "nas.example.invalid", "user": "listener",
                      "shares": ["ExampleMedia"], "home_gateway": "192.0.2.1"},
            "ssh_hosts": ["music@nas.example.invalid"],
            "local_library_root": str(self.library),
            "remote": {"library_root": str(self.library), "inbox": str(self.inbox),
                       "python": sys.executable, "rip": str(self.rip),
                       "streamrip_config": str(self.root / "fictional-streamrip.toml")},
        }
        self.config_path = self.root / "nas.json"
        self.config_path.write_text(json.dumps(self.config))
        self.env = dict(os.environ, HOME=str(self.root), MUSIC_NAS_CONFIG=str(self.config_path),
                        STREAMRIP_DOWNLOADS=str(self.inbox), LOCAL_RIP=str(self.rip), LOCAL_PYTHON=sys.executable,
                        MUSIC_NAS_PYTHON=sys.executable, RIPTAG_LOCK_DIR=str(self.root / "worker.lock"))

    def mount_helper(self, succeeds):
        helper = self.bin / "mount-nas.sh"
        helper.write_text(
            f"#!/bin/sh\ntouch {shlex.quote(str(self.mount_called))}\n"
            + (f"mkdir -p {shlex.quote(str(self.library))}\n" if succeeds else "exit 0\n")
        )
        helper.chmod(0o755)

    def run_worker(self, *args):
        return subprocess.run(
            ["/bin/sh", str(self.worker), *args], env=self.env,
            capture_output=True, text=True, timeout=10,
        )

    def assert_blocked(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: Music library is unavailable", result.stdout)
        self.assertFalse(self.rip_called.exists())
        self.assertEqual(list(self.inbox.iterdir()), [self.existing])
        self.assertEqual((self.existing / "track.m4a").read_bytes(), b"existing download")
        self.assertFalse(self.library.exists())

    def test_missing_mount_blocks_download_without_helper(self):
        result = self.run_worker("--local", "https://example.com/album/test", "Rock")
        self.assert_blocked(result)
        self.assertIn("mount-nas.sh", result.stdout)

    def test_unsuccessful_mount_blocks_resume(self):
        self.mount_helper(succeeds=False)
        result = self.run_worker("--local", "--resume", "abc123", "Rock")
        self.assert_blocked(result)
        self.assertTrue(self.mount_called.exists())

    def test_remount_allows_download(self):
        self.mount_helper(succeeds=True)
        result = self.run_worker("--local", "https://example.com/album/test", "Rock")
        self.assertTrue(self.mount_called.exists())
        self.assertTrue(self.rip_called.exists())
        self.assertIn("streamrip download failed", result.stdout)

    def test_available_library_does_not_attempt_mount(self):
        self.library.mkdir(parents=True)
        self.mount_helper(succeeds=False)
        result = self.run_worker("--local", "https://example.com/album/test", "Rock")
        self.assertFalse(self.mount_called.exists())
        self.assertTrue(self.rip_called.exists())
        self.assertIn("streamrip download failed", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "Root bypasses directory permissions")
    def test_unwritable_library_blocks_download(self):
        self.library.mkdir(parents=True)
        self.library.chmod(0o555)
        self.addCleanup(self.library.chmod, 0o755)
        result = self.run_worker("--local", "https://example.com/album/test", "Rock")
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires read, write, and directory access", result.stdout)
        self.assertFalse(self.rip_called.exists())

    def test_missing_or_invalid_config_blocks_before_mount_or_state(self):
        self.mount_helper(succeeds=True)
        for data in (None, '{"version": 2}'):
            if data is None:
                self.config_path.unlink()
            else:
                self.config_path.write_text(data)
            for mode in (["--local"], []):
                result = self.run_worker(*mode, "https://example.com/album/test", "Rock")
                self.assertEqual(result.returncode, 1)
                self.assertIn("ERROR:", result.stderr)
            self.assertFalse(self.mount_called.exists())
            self.assertFalse(self.rip_called.exists())
            self.assertFalse((self.root / ".local/state").exists())
            self.assertEqual((self.existing / "track.m4a").read_bytes(), b"existing download")

    def test_nas_mode_does_not_attempt_local_mount(self):
        self.config_path.write_text(json.dumps({"version": 1, "remote": self.config["remote"]}))
        self.mount_helper(succeeds=True)
        result = self.run_worker("https://example.com/album/test", "Rock")
        self.assert_blocked(result)
        self.assertFalse(self.mount_called.exists())


class OwnedDownloadTests(LibraryPreflightTests):
    def setUp(self):
        super().setUp()
        self.library.mkdir(parents=True)
        self.env["LOCAL_PYTHON"] = sys.executable
        self.env["FIXTURE_ROOT"] = str(self.root)
        self.env["TAGGER_SCRIPT"] = str(self.root / "tagger.py")
        (self.root / "tagger.py").write_text(
            "import os, sys\nfrom pathlib import Path\n"
            "Path(os.environ['FIXTURE_ROOT'], 'tagged').write_text(sys.argv[-1])\n"
            "sys.exit(1)\n"
        )
        self.rip.write_text("#!" + sys.executable + "\n" +
            "import os, sys\nfrom pathlib import Path\n"
            "root = Path(os.environ['FIXTURE_ROOT'])\n"
            "if 'resume' in sys.argv:\n"
            "    dest = Path((root / 'owned').read_text())\n"
            "else:\n"
            "    dest = Path(sys.argv[sys.argv.index('-f') + 1]) / 'Owned album'\n"
            "    dest.mkdir(parents=True)\n"
            "    (root / 'owned').write_text(str(dest))\n"
            "(dest / 'song.m4a').write_bytes(b'fictional audio')\n"
            "(root / 'downloads' / 'Unrelated album').mkdir(exist_ok=True)\n"
            "mode = os.environ.get('FIXTURE_MODE')\n"
            "if mode == 'block':\n"
            "    print('READY', flush=True)\n"
            "    sys.stdin.readline()\n"
            "if mode == 'multiple':\n"
            "    (dest.parent / 'Second album').mkdir()\n"
            "if mode == 'partial':\n"
            "    print('rip resume abc123')\n"
        )

    # This class runs only ownership tests; preflight coverage belongs to its base.
    test_missing_mount_blocks_download_without_helper = None
    test_unsuccessful_mount_blocks_resume = None
    test_remount_allows_download = None
    test_available_library_does_not_attempt_mount = None
    test_unwritable_library_blocks_download = None
    test_nas_mode_does_not_attempt_local_mount = None
    test_missing_or_invalid_config_blocks_before_mount_or_state = None

    def test_overlapping_worker_is_blocked_and_unrelated_album_is_not_tagged(self):
        first = subprocess.Popen(
            ["/bin/sh", str(self.worker), "--local", "https://example.com/album/one", "Rock"],
            env=dict(self.env, FIXTURE_MODE="block"), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        while True:
            line = first.stdout.readline()
            self.assertTrue(line, "worker exited before barrier")
            if line.strip() == "READY":
                break
        second = self.run_worker("--local", "https://example.com/album/two", "Pop")
        self.assertEqual(second.returncode, 1)
        self.assertIn("another riptag worker", second.stdout)
        output, _ = first.communicate("continue\n", timeout=10)
        self.assertEqual(first.returncode, 1, output)  # fake tagger failed, no organizer/SSH
        self.assertEqual((self.root / "tagged").read_text(), (self.root / "owned").read_text())
        self.assertTrue((self.inbox / "Unrelated album").is_dir())
        self.assertFalse((self.root / "worker.lock").exists())

    def test_local_permissions_map_configured_roots_with_quoted_paths(self):
        remote_root = "/srv/Example remote's Audio"
        self.config["remote"]["library_root"] = remote_root
        self.config_path.write_text(json.dumps(self.config))
        (self.root / "tagger.py").write_text("pass\n")
        organizer = self.root / "organizer.py"
        album = self.library / "Artist's name" / "Album $(not-command)"
        organizer.write_text("import sys\nfrom pathlib import Path\n" +
                             "Path(sys.argv[sys.argv.index('--manifest')+1]).write_text(" + repr(str(album) + "\n") + ")\n")
        self.env["ORGANIZER_SCRIPT"] = str(organizer)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env["PATH"]
        ssh_calls = self.root / "ssh-calls.jsonl"
        ssh = self.bin / "ssh"
        ssh.write_text("#!" + sys.executable + "\nimport sys, json\n" +
                       "with open(" + repr(str(ssh_calls)) + ", 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
        ssh.chmod(0o755)
        runnability = self.bin / "runnability.py"
        runnability.write_text("#!/bin/sh\nexit 0\n")
        runnability.chmod(0o755)
        result = self.run_worker("--local", "https://example.com/album/one", "Rock")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = [json.loads(line)[-1] for line in ssh_calls.read_text().splitlines()]
        chmod = next(command for command in commands if command.startswith("chmod"))
        words = shlex.split(chmod)
        self.assertIn(remote_root + "/Artist's name/Album $(not-command)", words)
        self.assertNotIn(str(self.library), chmod)

    def test_projected_remote_config_drives_worker_without_full_private_file(self):
        unusual_rip = self.bin / "rip with ' punctuation;"
        self.rip.rename(unusual_rip)
        self.config["remote"]["rip"] = str(unusual_rip)
        bootstrap = self.bin / "python with ' spaces"
        bootstrap.symlink_to(sys.executable)
        self.config["remote"]["python"] = str(bootstrap)
        self.env["MUSIC_NAS_PYTHON"] = str(bootstrap)
        self.config_path = self.root / "projected config with ' spaces.json"
        self.env["MUSIC_NAS_CONFIG"] = str(self.config_path)
        self.config_path.write_text(json.dumps({"version": 1, "remote": self.config["remote"]}))
        result = self.run_worker("https://example.com/album/one", "Rock")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Tagging failed", result.stdout)
        self.assertEqual((self.root / "tagged").read_text(), (self.root / "owned").read_text())
        self.assertFalse((self.root / ".config/music/nas.json").exists())

    def test_multiple_owned_albums_are_rejected_before_tagging(self):
        self.env["FIXTURE_MODE"] = "multiple"
        result = self.run_worker("--local", "https://example.com/album/one", "Rock")
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected exactly one album", result.stdout)
        self.assertFalse((self.root / "tagged").exists())

    def test_resume_reuses_recorded_owned_directory_and_retains_metadata_on_tag_error(self):
        self.env["FIXTURE_MODE"] = "partial"
        self.env["RIPTAG_RESULT_DIR"] = str(self.root / "result")
        result = self.run_worker("--local", "https://example.com/album/one", "Rock")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((self.root / "result/resume-id").read_text(), "abc123")
        original = (self.root / "owned").read_text()
        self.env.pop("FIXTURE_MODE")
        result = self.run_worker("--local", "--resume", "abc123", "Rock")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.root / "tagged").read_text(), original)
        self.assertTrue((self.root / ".local/state/riptag/sessions/abc123.meta").exists())
        self.assertEqual(len(list(self.inbox.glob(".riptag-run.*"))), 1)


class FishResumeTests(unittest.TestCase):
    def test_remote_failure_resumes_remotely_through_wrapper(self):
        source = WORKER.parents[3] / "fish/.config/fish/functions/riptag.fish"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "calls.jsonl"
            helper = root / ".local/bin/_music_nas.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(WORKER.with_name("_music_nas.py").read_text())
            config = json.loads((WORKER.parents[4] / "docs/examples/music-nas.example.json").read_text())
            config["ssh_hosts"] = ["music@first.example.invalid", "music@second.example.invalid", "music@third.example.invalid"]
            config["remote"]["python"] = "/opt/example tools/it's python"
            config_path = root / "nas.json"
            config_path.write_text(json.dumps(config))
            ssh = bin_dir / "ssh"
            ssh.write_text("#!" + sys.executable + "\n" +
                "import json, os, sys\n"
                "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "cmd = sys.argv[-1]\n"
                "if cmd == 'true': sys.exit(0 if sys.argv[-2] == 'music@third.example.invalid' else 1)\n"
                "if 'mktemp -d /tmp/riptag.' in cmd: print('/tmp/riptag.fixture123')\n"
                "elif cmd.startswith('cat ~/.local/state'): print('Rock\\n\\n\\n\\n\\n/fictional/inbox/.riptag-run.fixture')\n"
                "elif cmd.startswith('cat '): print('abc123')\n"
                "elif 'bash ' in cmd: sys.exit(0 if '--resume' in cmd else 2)\n"
            )
            ssh.chmod(0o755)
            scp = bin_dir / "scp"
            scp.write_text("#!" + sys.executable + "\n" +
                "import json, os, sys\nfrom pathlib import Path\n"
                "payload = Path(sys.argv[-2])\n"
                "assert payload.stat().st_mode & 0o777 == 0o600\n"
                "assert Path(sys.argv[-3]).name == '_music_nas.py'\n"
                "Path(os.environ['CALLS'] + '.projection').write_text(payload.read_text())\n"
            )
            scp.chmod(0o755)
            command = f"source {shlex.quote(str(source))}; riptag https://example.com/album/fiction Rock; riptag --resume=abc123"
            result = subprocess.run(["fish", "--no-config", "--private", "-c", command],
                                    env=dict(os.environ, HOME=tmp, MUSIC_NAS_CONFIG=str(config_path), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], CALLS=str(calls)),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = [json.loads(line)[-1] for line in calls.read_text().splitlines()]
            workers = [cmd for cmd in commands if "bash " in cmd]
            self.assertEqual(len(workers), 2, commands)
            projection = json.loads(Path(str(calls) + ".projection").read_text())
            self.assertEqual(projection, {"version": 1, "remote": config["remote"]})
            self.assertIn("chmod 600", workers[0])
            self.assertIn("MUSIC_NAS_CONFIG=", workers[0])
            self.assertIn("MUSIC_NAS_PYTHON=" + shlex.quote(config["remote"]["python"]), workers[0])
            destinations = [json.loads(line)[-2] for line in calls.read_text().splitlines() if json.loads(line)[-1] == "true"]
            self.assertEqual(destinations[:3], config["ssh_hosts"])
            self.assertIn("'--resume' 'abc123'", workers[1])
            self.assertNotIn("--local", workers[1])
            self.assertIn("riptag --resume=abc123", result.stdout)
            self.assertTrue(any(cmd.startswith("cat ~/.local/state") for cmd in commands))


if __name__ == "__main__":
    unittest.main()
