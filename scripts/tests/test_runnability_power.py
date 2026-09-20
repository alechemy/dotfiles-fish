#!/usr/bin/env python3
"""Power transitions use synthetic files and workers, without reading audio."""
import importlib.util
import tempfile
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "stow/bin/.local/bin/runnability.py"


class RunnabilityPower(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("runnability", SOURCE)
        self.runn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runn)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.runn.LIBRARY_ROOT = self.root
        self.runn.DB_PATH = self.root / "features.db"
        self.runn.GATE = self.root / "gate"
        self.runn.GATE.touch()
        self.paths = [self.root / f"{i}.m4a" for i in range(6)]
        for path in self.paths:
            path.write_bytes(b"music")
        self.args = types.SimpleNamespace(force=False, paths=[], reanalyze=False,
                                          workers=2, dry_run=False)

    def seed(self):
        conn = self.runn.open_db()
        for path in self.paths:
            conn.execute("INSERT INTO features (relpath, size, mtime, analyzed_at, file_identity) VALUES (?, ?, ?, ?, ?)",
                         (path.name, path.stat().st_size, path.stat().st_mtime,
                          "fixture", self.runn.file_identity(path)))
        conn.commit()
        conn.close()

    def test_initial_battery_skips_analysis_and_writes(self):
        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "collect_paths") as collect, \
                patch.object(self.runn, "load_config") as config:
            power.return_value.returncode = 1
            self.assertEqual(self.runn.cmd_analyze(self.args), 0)
            self.assertEqual(self.runn.cmd_write(self.args), 0)
            collect.assert_not_called()
            config.assert_not_called()

    def test_battery_during_model_loading_does_not_start_workers(self):
        def models():
            power.return_value.returncode = 1
            return {}

        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "collect_paths", return_value=self.paths), \
                patch.object(self.runn, "ensure_models", side_effect=models), \
                patch.object(self.runn, "ProcessPoolExecutor") as pool:
            power.return_value.returncode = 0
            self.assertEqual(self.runn.cmd_analyze(self.args), 0)
            pool.assert_not_called()

    def test_battery_after_scan_does_not_start_models(self):
        def collect(args):
            power.return_value.returncode = 1
            return self.paths

        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "collect_paths", side_effect=collect), \
                patch.object(self.runn, "ensure_models") as models:
            power.return_value.returncode = 0
            self.assertEqual(self.runn.cmd_analyze(self.args), 0)
            models.assert_not_called()

    def test_analysis_drains_active_tracks_without_starting_backlog(self):
        def submit(fn, abspath, relpath):
            future = Future()
            future.set_result(dict(relpath=relpath, size=5, mtime=0,
                                   file_identity=self.runn.file_identity(abspath), error=None,
                                   bpm=100.0, beat_confidence=1.0, danceability=0.5))
            return future

        def wait(pending, **kwargs):
            power.return_value.returncode = 1
            clock.return_value += 60
            return set(pending), set()

        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "collect_paths", return_value=self.paths), \
                patch.object(self.runn, "ensure_models", return_value={}), \
                patch.object(self.runn, "ProcessPoolExecutor") as pool, \
                patch.object(self.runn.time, "monotonic", return_value=0) as clock, \
                patch.object(self.runn, "wait", side_effect=wait):
            power.return_value.returncode = 0
            worker = pool.return_value.__enter__.return_value
            worker.submit.side_effect = submit
            self.assertEqual(self.runn.cmd_analyze(self.args), 0)
            self.assertEqual(worker.submit.call_count, 2)
        conn = self.runn.open_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM features").fetchone()[0], 2)
        conn.close()

    def test_write_drains_active_tracks_and_preserves_identities(self):
        self.seed()

        def submit(fn, *args):
            future = Future()
            future.set_result(fn(*args))
            return future

        def write(path, *args):
            path.write_bytes(b"tagged")
            return True

        def wait(pending, **kwargs):
            power.return_value.returncode = 1
            clock.return_value += 60
            return set(pending), set()

        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "load_config", return_value={"cadence": {"target_spm": 170}}), \
                patch.object(self.runn, "score_row", return_value=(80, {})), \
                patch.object(self.runn, "_write_mp4", side_effect=write), \
                patch.object(self.runn, "ThreadPoolExecutor") as pool, \
                patch.object(self.runn.time, "monotonic", return_value=0) as clock, \
                patch.object(self.runn, "wait", side_effect=wait):
            power.return_value.returncode = 0
            worker = pool.return_value.__enter__.return_value
            worker.submit.side_effect = submit
            self.assertEqual(self.runn.cmd_write(self.args), 0)
            self.assertEqual(worker.submit.call_count, 2)
        conn = self.runn.open_db()
        for relpath, identity in conn.execute("SELECT relpath, file_identity FROM features"):
            self.assertEqual(identity, self.runn.file_identity(self.root / relpath))
        conn.close()
        self.assertEqual(sum(p.read_bytes() == b"tagged" for p in self.paths), 2)

    def test_analysis_on_ac_processes_the_whole_queue(self):
        def submit(fn, abspath, relpath):
            future = Future()
            future.set_result(dict(relpath=relpath, size=5, mtime=0, error=None,
                                   bpm=100.0, beat_confidence=1.0, danceability=0.5))
            return future

        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "collect_paths", return_value=self.paths), \
                patch.object(self.runn, "ensure_models", return_value={}), \
                patch.object(self.runn, "ProcessPoolExecutor") as pool:
            power.return_value.returncode = 0
            worker = pool.return_value.__enter__.return_value
            worker.submit.side_effect = submit
            self.assertEqual(self.runn.cmd_analyze(self.args), 0)
            self.assertEqual(worker.submit.call_count, len(self.paths))
        conn = self.runn.open_db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM features").fetchone()[0], len(self.paths))
        conn.close()

    def test_gate_caches_checks_but_refreshes_and_honors_force(self):
        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn.time, "monotonic", return_value=0) as clock:
            power.return_value.returncode = 0
            gate = self.runn._PowerGate(False)
            self.assertTrue(gate.allowed())
            self.assertTrue(gate.allowed())
            power.assert_called_once()
            power.return_value.returncode = 1
            self.assertFalse(gate.allowed(refresh=True))
            clock.return_value = 60
            self.assertFalse(gate.allowed())
            self.assertEqual(power.call_count, 3)
            power.reset_mock()
            self.assertTrue(self.runn._PowerGate(True).allowed())
            power.assert_not_called()

    def test_dry_run_does_not_check_power(self):
        self.seed()
        self.args.dry_run = True
        with patch.object(self.runn.subprocess, "run") as power, \
                patch.object(self.runn, "load_config", return_value={"cadence": {"target_spm": 170}}), \
                patch.object(self.runn, "score_row", return_value=(80, {})), \
                patch.object(self.runn, "_write_one", return_value=("0.m4a", "would-write", None, None, None, None)):
            self.assertEqual(self.runn.cmd_write(self.args), 0)
            power.assert_not_called()


if __name__ == "__main__":
    unittest.main()
