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
        self.library = self.root / "Media/Music"
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
        source = WORKER.read_text().replace("/Volumes/Media/Music", str(self.library))
        source = source.replace("/share/Media/Music-Inbox", str(self.inbox))
        source = source.replace("/share/Media/Music", str(self.library))
        source = source.replace(
            "/share/CACHEDEV1_DATA/python-apps/streamrip_env/bin/rip", str(self.rip)
        )
        for filename in ("rip-download.log", "rip-exit-status.txt", "riptag-resume-id"):
            source = source.replace(f"/tmp/{filename}", str(self.root / filename))
        self.worker.write_text(source)
        self.env = dict(os.environ, HOME=str(self.root),
                        STREAMRIP_DOWNLOADS=str(self.inbox), LOCAL_RIP=str(self.rip),
                        RIPTAG_LOCK_DIR=str(self.root / "worker.lock"))

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

    def test_nas_mode_does_not_attempt_local_mount(self):
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
            ssh = bin_dir / "ssh"
            ssh.write_text("#!" + sys.executable + "\n" +
                "import json, os, sys\n"
                "with open(os.environ['CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "cmd = sys.argv[-1]\n"
                "if cmd.startswith('mktemp'): print('/tmp/riptag.fixture123')\n"
                "elif cmd.startswith('cat ~/.local/state'): print('Rock\\n\\n\\n\\n\\n/fictional/inbox/.riptag-run.fixture')\n"
                "elif cmd.startswith('cat '): print('abc123')\n"
                "elif 'bash ' in cmd: sys.exit(0 if '--resume' in cmd else 2)\n"
            )
            ssh.chmod(0o755)
            scp = bin_dir / "scp"
            scp.write_text("#!/bin/sh\nexit 0\n")
            scp.chmod(0o755)
            command = f"source {shlex.quote(str(source))}; riptag https://example.com/album/fiction Rock; riptag --resume=abc123"
            result = subprocess.run(["fish", "--no-config", "--private", "-c", command],
                                    env=dict(os.environ, HOME=tmp, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], CALLS=str(calls)),
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = [json.loads(line)[-1] for line in calls.read_text().splitlines()]
            workers = [cmd for cmd in commands if "bash " in cmd]
            self.assertEqual(len(workers), 2, commands)
            self.assertIn("'--resume' 'abc123'", workers[1])
            self.assertNotIn("--local", workers[1])
            self.assertIn("riptag --resume=abc123", result.stdout)
            self.assertTrue(any(cmd.startswith("cat ~/.local/state") for cmd in commands))


if __name__ == "__main__":
    unittest.main()
