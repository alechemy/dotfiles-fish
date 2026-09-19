#!/usr/bin/env python3
"""Comment cleanup uses explicit targets and retryable transcript checkpoints."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "stow/bin/.local/bin"


class CommentHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.bin = self.home / ".local/bin"
        self.bin.mkdir(parents=True)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = {"HOME": str(self.home), "PATH": str(self.bin) + ":" + os.environ["PATH"],
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "XDG_STATE_HOME": str(self.home / "state")}
        for name in ("agent-strip-comments", "uncomment-clean", "uncomment-scoped"):
            shutil.copy2(BIN / name, self.bin / name)
        self.stub("uncomment", "#!/bin/sh\nexit 0\n")
        self.git("init", "-qb", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        (self.repo / ".uncommentrc.toml").write_text("")
        self.target = self.repo / "target.py"
        self.target.write_text("original = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def stub(self, name, text):
        source = self.bin / name
        source.write_text(text)
        source.chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              check=True, capture_output=True, text=True, timeout=10)

    def run_clean(self, args):
        return subprocess.run(["bash", str(self.bin / "uncomment-clean"), *args],
                              cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=10)

    def transcript(self, rows=None):
        self.log = self.root / "transcript.jsonl"
        rows = rows or [
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "Fixture edit"}},
            {"type": "assistant", "timestamp": "2026-09-01T00:00:01Z", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(self.target)}}]}}]
        self.log.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.payload = json.dumps({"cwd": str(self.repo), "transcript_path": str(self.log)})

    def hook(self):
        return subprocess.run(["bash", str(self.bin / "agent-strip-comments")],
                              input=self.payload, cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=10)

    def offsets(self):
        return list((self.home / "state/agent-hooks").glob("*.offset"))

    def test_marker_recipe_only_advertises_the_retained_hook(self):
        marker = self.repo / ".uncommentrc.toml"
        marker.unlink()
        result = subprocess.run(
            ["bash", str(BIN / "comment-gate-init"), str(self.repo)],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Claude", result.stdout + marker.read_text())
        self.assertIn("Copilot CLI", result.stdout)
        self.assertIn("Pi has no comment hook", result.stdout)

    def test_all_preservation_values_are_untouched_in_scoped_edit(self):
        names = ("IMPORTANT", "NOTE", "WARNING", "SAFETY", "SECURITY", "keep")
        for name in names:
            (self.repo / name).write_text("unrelated whitespace  \n")
        self.target.write_text("original = 1\nadded = 2  \n")
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        for name in names:
            self.assertEqual((self.repo / name).read_text(), "unrelated whitespace  \n")

    def test_passthrough_does_not_guess_targets_from_options(self):
        sentinel = self.repo / "IMPORTANT"
        sentinel.write_text("leave me  \n")
        self.assertEqual(self.run_clean(["-i", "IMPORTANT", str(self.target)]).returncode, 0)
        self.assertEqual(sentinel.read_text(), "leave me  \n")

    def test_explicit_targets_preserve_markdown_and_binary(self):
        for name, content in (("fixture.md", b"hard break  \n"), ("fixture.bin", b"\0binary  \n")):
            target = self.repo / name
            target.write_bytes(content)
            self.assertEqual(self.run_clean(["--trim-target", str(target), "--", str(target)]).returncode, 0)
            self.assertEqual(target.read_bytes(), content)

    def test_failed_cleaner_preserves_original_and_checkpoint_for_retry(self):
        self.target.write_text("original = 1\nadded = 2  \n")
        self.transcript()
        self.stub("uncomment", "#!/bin/sh\nexit 7\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.offsets(), [])
        self.assertIn("added = 2  \n", self.target.read_text())
        self.stub("uncomment", "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.offsets()[0].read_text().strip(), str(self.log.stat().st_size))
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")

    @unittest.skipIf(os.getuid() == 0, "root bypasses file read permissions")
    def test_unreadable_target_retains_checkpoint_until_readable_retry(self):
        self.target.write_text("original = 1\nadded = 2  \n")
        self.transcript()
        self.target.chmod(0)
        try:
            self.assertEqual(self.hook().returncode, 0)
            self.assertEqual(self.offsets(), [])
        finally:
            self.target.chmod(0o600)
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        self.assertEqual(self.offsets()[0].read_text().strip(), str(self.log.stat().st_size))

    def test_interruption_does_not_skip_unchanged_transcript_retry(self):
        # SIGTERM the hook parent from its disposable scoped helper. No live PID.
        self.transcript()
        self.stub("uncomment-scoped", "#!/usr/bin/env python3\nimport os, signal\nos.kill(os.getppid(), signal.SIGTERM)\n")
        self.assertEqual(self.hook().returncode, -signal.SIGTERM)
        self.assertEqual(self.offsets(), [])
        marker = self.root / "retried"
        self.stub("uncomment-scoped", "#!/usr/bin/env python3\nfrom pathlib import Path\n"
                  + f"Path({str(marker)!r}).write_text('processed')\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(self.offsets()[0].read_text().strip(), str(self.log.stat().st_size))
        marker.unlink()
        self.assertEqual(self.hook().returncode, 0)
        self.assertFalse(marker.exists())

    def append_edit(self):
        with self.log.open("a") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(self.target)}}]},
                "timestamp": "2026-09-01T00:00:02Z"}) + "\n")

    def test_failed_tail_is_not_a_successful_no_edit_checkpoint(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        before = checkpoint.read_bytes()
        self.target.write_text("original = 1\nadded = 2  \n")
        self.append_edit()
        self.stub("tail", "#!/bin/sh\nexit 7\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.assertIn("added = 2  \n", self.target.read_text())
        (self.bin / "tail").unlink()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        self.assertEqual(checkpoint.read_text().strip(), str(self.log.stat().st_size))

    def test_incomplete_or_malformed_append_retains_checkpoint_for_repaired_retry(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        before = checkpoint.read_bytes()
        valid = self.log.read_text()
        for append in ('{"type":', '{"name":"Edit", bad-json}\n'):
            with self.subTest(append=append):
                self.target.write_text("original = 1\nadded = 2  \n")
                self.log.write_text(valid + append)
                self.assertEqual(self.hook().returncode, 0)
                self.assertEqual(checkpoint.read_bytes(), before)
                self.assertIn("added = 2  \n", self.target.read_text())
        self.log.write_text(valid)
        self.append_edit()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        self.assertEqual(checkpoint.read_text().strip(), str(self.log.stat().st_size))

    def test_append_completed_during_tail_uses_validated_byte_boundary(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        valid = self.log.read_text()
        self.append_edit()
        row = self.log.read_text()[len(valid):]
        self.log.write_text(valid + row[:20])
        remainder = self.root / "remainder"
        remainder.write_text(row[20:])
        real_tail = shutil.which("tail")
        self.stub("tail", "#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\n"
                  + f"remainder = Path({str(remainder)!r})\n"
                  + "if remainder.exists():\n"
                  + f"    with Path({str(self.log)!r}).open('a') as stream: stream.write(remainder.read_text())\n"
                  + "    remainder.unlink()\n"
                  + f"os.execv({real_tail!r}, [{real_tail!r}, *sys.argv[1:]])\n")
        self.target.write_text("original = 1\nadded = 2  \n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_text().strip(), str(self.log.stat().st_size))
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        (self.bin / "tail").unlink()
        self.append_edit()
        self.target.write_text("original = 1\nadded = 3  \n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_text().strip(), str(self.log.stat().st_size))
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 3\n")

    def test_failed_snapshot_read_preserves_files_and_checkpoint(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        before = checkpoint.read_bytes()
        self.target.write_text("original = 1\nadded = 2  \n")
        self.append_edit()
        self.stub("cat", '#!/bin/sh\nif [ "$1" = ' + repr(str(self.log))
                  + ' ]; then exit 7; fi\nexec /bin/cat "$@"\n')
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.assertIn("added = 2  \n", self.target.read_text())
        (self.bin / "cat").unlink()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")

    def test_failed_jq_processing_stage_never_mutates_or_checkpoints(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        before = checkpoint.read_bytes()
        self.target.write_text("original = 1\nadded = 2  \n")
        self.append_edit()
        real_jq = shutil.which("jq")
        for query in ('select(.type=="user"', 'select(.type=="assistant"',
                      'select(.toolUseResult'):
            with self.subTest(query=query):
                self.stub("jq", "#!/usr/bin/env python3\nimport os, sys\n"
                          + f"if any({query!r} in arg for arg in sys.argv[1:]): sys.exit(7)\n"
                          + f"os.execv({real_jq!r}, [{real_jq!r}, *sys.argv[1:]])\n")
                self.assertEqual(self.hook().returncode, 0)
                self.assertEqual(checkpoint.read_bytes(), before)
                self.assertIn("added = 2  \n", self.target.read_text())
        (self.bin / "jq").unlink()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")

    def test_valid_copilot_snapshot_retains_changed_path_intersection(self):
        self.transcript([{"type": "message", "content": "Edited target.py"}])
        self.payload = json.dumps({"cwd": str(self.repo), "transcriptPath": str(self.log)})
        self.target.write_text("original = 1\nadded = 2  \n")
        unrelated = self.repo / "unrelated.py"
        unrelated.write_text("unrelated = 3  \n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.target.read_text(), "original = 1\nadded = 2\n")
        self.assertEqual(unrelated.read_text(), "unrelated = 3  \n")

    def test_supplied_missing_or_invalid_transcript_never_falls_back_to_dirty_files(self):
        self.transcript()
        self.target.write_text("original = 1\nadded = 2  \n")
        self.log.unlink()
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.offsets(), [])
        self.assertIn("added = 2  \n", self.target.read_text())
        self.log.write_text('{"type":')
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(self.offsets(), [])
        self.assertIn("added = 2  \n", self.target.read_text())

    def test_failed_increment_retains_prior_checkpoint_and_retries(self):
        self.transcript()
        self.assertEqual(self.hook().returncode, 0)
        checkpoint = self.offsets()[0]
        before = checkpoint.read_bytes()
        with self.log.open("a") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(self.target)}}]},
                "timestamp": "2026-09-01T00:00:02Z"}) + "\n")
        self.stub("uncomment", "#!/bin/sh\nexit 1\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_bytes(), before)
        self.stub("uncomment", "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.hook().returncode, 0)
        self.assertEqual(checkpoint.read_text().strip(), str(self.log.stat().st_size))


if __name__ == "__main__":
    unittest.main()
