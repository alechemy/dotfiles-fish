import os
import shlex
import subprocess
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "stow/devonthink/.local/bin/dt-database-archive.sh"


class DatabaseArchiveTests(unittest.TestCase):
    def test_failed_archives_preserve_prior_copy_and_same_day_success_replaces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            helpers = home / ".local/bin"
            helpers.mkdir(parents=True)
            for name in ("pipeline-log", "pipeline-record-run", "pgrep"):
                path = helpers / name
                path.write_text("#!/bin/sh\nexit 0\n")
                path.chmod(0o755)
            fake_app = home / "fake-app.py"
            fake_app.write_text('''import os
import sys
import zipfile
from pathlib import Path
path = Path(sys.argv[-1])
if not path.name.endswith(".dtBase2.zip"):
    sys.exit("Invalid path extension. (-50)")
mode = os.environ["ARCHIVE_TEST_MODE"]
if mode in ("compress-fails", "crc-fails"):
    path.write_bytes(b"incomplete archive")
else:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fixture", mode)
if mode == "compress-fails":
    sys.exit(1)
print("ok")
''')
            runner = home / "archive.sh"
            runner.write_text(SCRIPT.read_text().replace(
                '/usr/bin/osascript "$TMPSCRIPT"',
                '/usr/bin/python3 ' + shlex.quote(str(fake_app)) + ' "$TMPSCRIPT"'))
            dest_dir = home / "Backups/DEVONthink"
            dest_dir.mkdir(parents=True)
            dest = dest_dir / f"Lorebook-{date.today().isoformat()}.dtBase2.zip"
            dest.write_bytes(b"previous good archive")
            orphan = dest_dir / ".Lorebook-orphan.partial.123.dtBase2.zip"
            orphan.write_bytes(b"retention must ignore me")
            env = dict(os.environ, HOME=tmp, PIPELINE_MANUAL="1",
                       PATH=str(helpers) + os.pathsep + os.environ["PATH"])
            for mode in ("compress-fails", "crc-fails", "first-success", "second-success"):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        ["/bin/bash", str(runner), "--force"],
                        env=dict(env, ARCHIVE_TEST_MODE=mode),
                        capture_output=True, text=True, timeout=20)
                    if mode.endswith("fails"):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(dest.read_bytes(), b"previous good archive")
                        self.assertFalse((home / ".local/state/devonthink/dt-database-archive.last-success").exists())
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        with zipfile.ZipFile(dest) as archive:
                            self.assertEqual(archive.read("fixture").decode(), mode)
                    self.assertEqual(list(dest_dir.glob(".*partial.*")), [orphan])


if __name__ == "__main__":
    unittest.main()
