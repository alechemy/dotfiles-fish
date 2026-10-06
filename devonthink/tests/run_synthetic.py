#!/usr/bin/python3
import os
import sys
import tempfile
import unittest
from pathlib import Path

EXCLUDED = {"test_calendar_canary", "test_contacts_canary"}


def main():
    directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(directory))
    names = [path.stem for path in sorted(directory.glob("test_*.py")) if path.stem not in EXCLUDED]
    previous = dict(os.environ)
    previous_cache = sys.pycache_prefix
    with tempfile.TemporaryDirectory(prefix="dt-entity-synthetic-") as home:
        os.environ.clear()
        os.environ.update({key: previous[key] for key in ("PATH", "TMPDIR", "LANG", "LC_ALL") if key in previous})
        sys.pycache_prefix = str(Path(home) / ".cache/python")
        os.environ.update(HOME=home, PIPELINE_MANUAL="1", PYTHONPYCACHEPREFIX=sys.pycache_prefix)
        try:
            suite = unittest.defaultTestLoader.loadTestsFromNames(names)
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            sys.pycache_prefix = previous_cache
            os.environ.clear()
            os.environ.update(previous)


if __name__ == "__main__":
    raise SystemExit(main())
