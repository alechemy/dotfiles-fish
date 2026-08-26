import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "stow/devonthink/.local/bin/boox-import-watcher.sh"


class BooxImportWatcherTests(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text()

    def run_bash(self, body):
        self.assertIn('[[ "${BASH_SOURCE[0]}" == "$0" ]]', self.source)
        return subprocess.run(
            ["/bin/bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{body}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_waiter_is_sourceable_for_unit_tests(self):
        self.assertIn('[[ "${BASH_SOURCE[0]}" == "$0" ]]', self.source)

    def test_watcher_subscribes_before_delayed_backlog_sweep(self):
        watcher = self.source.index("/opt/homebrew/bin/fswatch")
        backlog = self.source.index("find \"$WATCH_DIR\"")
        self.assertLess(watcher, backlog)

    def test_fresh_file_has_remaining_grace_period(self):
        output = self.run_bash("""
BOOX_INGEST_DELAY_SECONDS=120
file_added_epoch() { echo 1000; }
now_epoch() { echo 1060; }
ingest_delay_remaining /tmp/example.pdf
""")
        self.assertEqual(output, "60")

    def test_old_file_is_immediately_eligible(self):
        output = self.run_bash("""
BOOX_INGEST_DELAY_SECONDS=120
file_added_epoch() { echo 1000; }
now_epoch() { echo 1121; }
ingest_delay_remaining /tmp/example.pdf
""")
        self.assertEqual(output, "0")

    def test_future_birth_time_cannot_extend_the_configured_delay(self):
        output = self.run_bash("""
BOOX_INGEST_DELAY_SECONDS=120
file_added_epoch() { echo 1200; }
now_epoch() { echo 1000; }
ingest_delay_remaining /tmp/example.pdf
""")
        self.assertEqual(output, "120")

    def test_deleted_file_is_skipped_during_grace_period(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf = Path(tmpdir) / "note.pdf"
            pdf.touch()
            output = self.run_bash(f"""
BOOX_INGEST_DELAY_SECONDS=120
target={shlex.quote(str(pdf))}
file_added_epoch() {{ echo 1000; }}
now_epoch() {{ echo 1000; }}
sleep() {{ rm -f "$target"; }}
wait_for_ingest_age "$target"
""")
        self.assertEqual(output, "gone")


if __name__ == "__main__":
    unittest.main()
