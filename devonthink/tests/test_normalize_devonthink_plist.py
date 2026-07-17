"""Pure compare/merge logic for CustomMetaData.plist reconciliation.

scripts/reconcile-devonthink-seed.sh routes its status check and --apply
through normalize-devonthink-plist.py's --custom-metadata-* modes instead of a
whole-file byte/XML compare, because the merge (mirrored from
scripts/seed-devonthink-config.sh) reassigns `index` on the fields it adds —
a byte compare would report that reassignment as permanent drift. The bash
wiring itself is verified by reading, not exercised here.
"""

import importlib.util
import os
import plistlib
import tempfile
import unittest
from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[2] / "scripts" /
         "normalize-devonthink-plist.py")

_spec = importlib.util.spec_from_file_location(
    "normalize_devonthink_plist", SCRIPT)
ndp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ndp)


def field(identifier, index=0, **kw):
    return {"identifier": identifier, "index": index, "title": identifier,
            "type": "string", **kw}


def write_plist(tmp, name, data):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        plistlib.dump(data, f)
    return path


class CustomMetadataStatus(unittest.TestCase):
    def test_missing_live_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A")])
            live = os.path.join(tmp, "live.plist")
            self.assertEqual(ndp.custom_metadata_status(seed, live), "missing")

    def test_same_when_every_seed_identifier_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A"), field("B")])
            live = write_plist(tmp, "live.plist",
                               [field("A"), field("B", index=5)])
            self.assertEqual(ndp.custom_metadata_status(seed, live), "same")

    def test_differs_when_a_seed_identifier_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A"), field("C")])
            live = write_plist(tmp, "live.plist", [field("A")])
            self.assertEqual(ndp.custom_metadata_status(seed, live), "differs")

    def test_index_reassignment_alone_is_not_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A", index=1)])
            live = write_plist(tmp, "live.plist", [field("A", index=99)])
            self.assertEqual(ndp.custom_metadata_status(seed, live), "same")


class CustomMetadataMerge(unittest.TestCase):
    def test_creates_verbatim_when_live_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A", index=7)])
            live = os.path.join(tmp, "live.plist")
            added = ndp.custom_metadata_merge(seed, live)
            self.assertEqual(added, ["A"])
            with open(live, "rb") as f:
                self.assertEqual(plistlib.load(f), [field("A", index=7)])

    def test_appends_missing_identifiers_with_reassigned_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist",
                               [field("A", index=1), field("B", index=2)])
            live = write_plist(tmp, "live.plist", [field("A", index=1)])
            added = ndp.custom_metadata_merge(seed, live)
            self.assertEqual(added, ["B"])
            with open(live, "rb") as f:
                result = plistlib.load(f)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[1]["identifier"], "B")
            self.assertEqual(result[1]["index"], 2)

    def test_never_touches_an_existing_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist",
                               [field("A", index=1, title="Renamed")])
            live = write_plist(tmp, "live.plist",
                               [field("A", index=9, title="Original")])
            added = ndp.custom_metadata_merge(seed, live)
            self.assertEqual(added, [])
            with open(live, "rb") as f:
                self.assertEqual(plistlib.load(f),
                                 [field("A", index=9, title="Original")])

    def test_index_continues_from_lives_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("C")])
            live = write_plist(
                tmp, "live.plist", [field("A", index=3), field("B", index=10)])
            ndp.custom_metadata_merge(seed, live)
            with open(live, "rb") as f:
                result = plistlib.load(f)
            self.assertEqual(result[-1]["index"], 11)

    def test_no_write_when_nothing_to_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = write_plist(tmp, "seed.plist", [field("A")])
            live = write_plist(tmp, "live.plist", [field("A", index=1)])
            inode_before = os.stat(live).st_ino
            added = ndp.custom_metadata_merge(seed, live)
            self.assertEqual(added, [])
            self.assertEqual(os.stat(live).st_ino, inode_before)


if __name__ == "__main__":
    unittest.main()
