import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "stow/devonthink/.local/bin/singlefile-watcher.sh"


class SingleFileWatcherTests(unittest.TestCase):
    def test_capture_during_backlog_is_queued_and_ingested_serially(self):
        source = SCRIPT.read_text()
        loop = source[source.index("/opt/homebrew/bin/fswatch"):]
        loop = loop.replace("/opt/homebrew/bin/fswatch", "fake_fswatch", 1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.html").write_text("old capture")
            watcher = root / "watcher.py"
            watcher.write_text('''import os
import time
from pathlib import Path
root = Path(os.environ["STAGING_DIR"])
while not (root / "backlog-started").exists():
    time.sleep(0.01)
(root / "new.html").write_text("new capture")
for name in ("old.html", "new.html"):
    os.write(1, os.fsencode(root / name) + b"\\0")
''')
            harness = '''set -euo pipefail
fake_fswatch() { /usr/bin/python3 "$WATCHER"; }
ingest_html() {
    [[ -f "$1" ]] || return 0
    mkdir "$STAGING_DIR/ingesting"
    if [[ "$2" == backlog ]]; then
        touch "$STAGING_DIR/backlog-started"
        while [[ ! -f "$STAGING_DIR/new.html" ]]; do sleep 0.01; done
    fi
    printf '%s\n' "$(basename "$1")" >> "$STAGING_DIR/imported"
    rm "$1"
    rmdir "$STAGING_DIR/ingesting"
}
'''
            env = dict(os.environ, STAGING_DIR=str(root), WATCHER=str(watcher),
                       HOME=tmp, PIPELINE_MANUAL="1")
            result = subprocess.run(["/bin/bash", "-c", harness + loop],
                                    env=env, capture_output=True, text=True,
                                    timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "imported").read_text().splitlines(),
                             ["old.html", "new.html"])


if __name__ == "__main__":
    unittest.main()
