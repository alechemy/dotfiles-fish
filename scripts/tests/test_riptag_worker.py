#!/usr/bin/env python3

import os
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
                        STREAMRIP_DOWNLOADS=str(self.inbox), LOCAL_RIP=str(self.rip))

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


if __name__ == "__main__":
    unittest.main()
