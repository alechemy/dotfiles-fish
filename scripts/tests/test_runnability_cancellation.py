#!/usr/bin/env python3
"""Exercise write cancellation in subprocesses with synthetic local files."""
import importlib.util
import json
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "stow/bin/.local/bin/runnability.py"

HARNESS = r'''
import importlib.util
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

source, directory, mode = sys.argv[1:]
spec = importlib.util.spec_from_file_location("runnability", source)
runn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runn)
root = Path(directory)
runn.LIBRARY_ROOT = root
runn.DB_PATH = root / "features.db"
conn = runn.open_db()
for index in range(3):
    path = root / f"{index}.m4a"
    path.write_bytes(b"original")
    conn.execute("INSERT INTO features (relpath, size, mtime, analyzed_at, file_identity) VALUES (?, ?, ?, ?, ?)",
                 (path.name, path.stat().st_size, path.stat().st_mtime, "fixture", runn.file_identity(path)))
conn.commit()
conn.close()
runn.load_config = lambda: {"cadence": {"target_spm": 170}}
runn.score_row = lambda row, cfg: (80, {})

def write(path, *args):
    path.write_bytes(b"tagged")
    if mode == "waiting" and path.name == "0.m4a":
        print("started", flush=True)
        deadline = time.monotonic() + 10
        while not (root / "release").exists():
            if time.monotonic() > deadline:
                raise TimeoutError("fixture release timed out")
            time.sleep(0.01)
    return True

runn._write_mp4 = write
if mode in {"update", "db-error"}:
    open_db = runn.open_db

    class Connection:
        def __init__(self):
            self.conn = open_db()
            self.interrupted = False

        @property
        def row_factory(self):
            return self.conn.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.conn.row_factory = value

        def execute(self, sql, *args):
            if sql.startswith("UPDATE") and not self.interrupted:
                self.interrupted = True
                if mode == "db-error":
                    raise RuntimeError("fixture database failure")
                signal.raise_signal(signal.SIGINT)
            return self.conn.execute(sql, *args)

        def commit(self):
            self.conn.commit()

        def close(self):
            self.conn.close()

    runn.open_db = Connection

previous = signal.getsignal(signal.SIGINT)
try:
    status = runn.cmd_write(SimpleNamespace(dry_run=False, force=True, paths=[], workers=1))
finally:
    print(f"handler_restored={signal.getsignal(signal.SIGINT) == previous}", flush=True)
sys.exit(status)
'''


class RunnabilityCancellation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location("runnability", SOURCE)
        self.runn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runn)

    def start(self, mode):
        proc = subprocess.Popen(
            [sys.executable, "-c", HARNESS, str(SOURCE), str(self.root), mode],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)

    def line(self, stream):
        self.assertTrue(select.select([stream], [], [], 3)[0], "child output timed out")
        return stream.readline().strip()

    def assert_current(self):
        with sqlite3.connect(self.root / "features.db") as conn:
            rows = conn.execute("SELECT relpath, size, file_identity FROM features").fetchall()
        self.assertEqual(len(rows), 3)
        for relpath, size, identity in rows:
            path = self.root / relpath
            self.assertEqual(size, path.stat().st_size, relpath)
            self.assertEqual(identity, self.runn.file_identity(path), relpath)

    def test_ctrl_c_drains_active_write_and_cancels_queued_writes(self):
        proc = self.start("waiting")
        try:
            self.assertEqual(self.line(proc.stdout), "started")
            proc.send_signal(signal.SIGINT)
            self.assertIn("waiting for active tag writes", self.line(proc.stderr))
            proc.send_signal(signal.SIGINT)
            (self.root / "release").touch()
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 130, stdout + stderr)
            self.assertIn('"cancelled": 2', stdout)
            self.assertIn('"written": 1', stdout)
            self.assertIn("handler_restored=True", stdout)
            self.assertNotIn("Traceback", stderr)
            self.assertEqual((self.root / "0.m4a").read_bytes(), b"tagged")
            self.assertEqual((self.root / "1.m4a").read_bytes(), b"original")
            self.assertEqual((self.root / "2.m4a").read_bytes(), b"original")
            self.assert_current()
        finally:
            (self.root / "release").touch()
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=10)

    def test_ctrl_c_during_database_update_preserves_completed_writes(self):
        proc = self.start("update")
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 130, stdout + stderr)
        self.assertIn("handler_restored=True", stdout)
        self.assertNotIn("Traceback", stderr)
        self.assert_current()

    def test_normal_completion_restores_handler_and_persists_writes(self):
        proc = self.start("normal")
        stdout, stderr = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, stdout + stderr)
        self.assertIn(json.dumps({"written": 3}), stdout)
        self.assertIn("handler_restored=True", stdout)
        self.assert_current()

    def test_database_error_restores_handler(self):
        proc = self.start("db-error")
        stdout, stderr = proc.communicate(timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("fixture database failure", stderr)
        self.assertIn("handler_restored=True", stdout)


if __name__ == "__main__":
    unittest.main()
