import importlib.machinery
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from helpers import BIN

loader = importlib.machinery.SourceFileLoader("capture_arrival_test", str(BIN / "entity-capture-arrival"))
spec = importlib.util.spec_from_loader(loader.name, loader)
arrival = importlib.util.module_from_spec(spec)
loader.exec_module(arrival)


class Arrival(unittest.TestCase):
    def test_arrivals_coalesce_and_remain_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(arrival.notify(tmp, gate=0))
            request = Path(tmp) / ".local/state/devonthink/entity-capture-arrivals/pending"
            stat = request.stat()
            self.assertTrue(arrival.notify(tmp, gate=0))
            self.assertEqual(request.stat().st_ino, stat.st_ino)
            self.assertEqual(stat.st_mode & 0o777, 0o600)
            self.assertEqual(request.read_bytes(), b"")
            request.unlink()
            self.assertTrue(arrival.notify(tmp, gate=0))

    def test_follower_does_not_wake_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(arrival.notify(tmp, gate=1))
            self.assertEqual(os.listdir(tmp), [])
