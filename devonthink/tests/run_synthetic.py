#!/usr/bin/python3
import os
import sys
import unittest
from pathlib import Path

EXCLUDED = {"test_calendar_canary", "test_contacts_canary"}


def main():
    os.environ["PIPELINE_MANUAL"] = "1"
    directory = Path(__file__).resolve().parent
    sys.path.insert(0, str(directory))
    names = [path.stem for path in sorted(directory.glob("test_*.py")) if path.stem not in EXCLUDED]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
