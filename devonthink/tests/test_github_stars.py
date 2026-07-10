import json
import os
import tempfile
import unittest

from helpers import load

gs = load("import-github-stars.py", "import_github_stars")


def star(full_name, starred_at="2026-07-09T10:00:00Z"):
    return {
        "full_name": full_name,
        "url": f"https://github.com/{full_name}",
        "description": "",
        "starred_at": starred_at,
    }


class FakeResult:
    returncode = 0
    stderr = ""

    def __init__(self, out):
        self.stdout = out


class FetchStopsAtFirstImported(unittest.TestCase):
    """fetch_stars stops at the first already-imported repo, so it alone can
    never re-surface an older star whose import failed while a newer one
    succeeded. The retry list is what closes that gap."""

    def test_older_failed_star_is_not_refetched(self):
        page1 = [star("owner/S1", "2026-07-09T10:00:00Z"),
                 star("owner/S2", "2026-07-09T09:00:00Z")]

        def fake_run(cmd, **kwargs):
            page = int([p for p in cmd[2].split("&") if p.startswith("page=")][0][5:])
            return FakeResult(json.dumps(page1 if page == 1 else []))

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            fetched = gs.fetch_stars({"owner/S1"}, first_run=False)
        finally:
            gs.subprocess.run = orig
        self.assertEqual(fetched, [])


class MergePending(unittest.TestCase):
    def test_failed_star_from_prior_run_is_retried(self):
        # Prior run: S2 (older) failed, S1 (newer) succeeded. This run's fetch
        # stops at S1 and returns nothing; the retry entry must still import.
        pending = gs.merge_pending([], [star("owner/S2")], {"owner/S1"})
        self.assertEqual([s["full_name"] for s in pending], ["owner/S2"])

    def test_retry_entry_already_refetched_is_not_duplicated(self):
        s2 = star("owner/S2")
        pending = gs.merge_pending([s2], [star("owner/S2")], set())
        self.assertEqual([s["full_name"] for s in pending], ["owner/S2"])

    def test_retry_entry_since_imported_is_dropped(self):
        pending = gs.merge_pending([], [star("owner/S2")], {"owner/S2"})
        self.assertEqual(pending, [])

    def test_retries_import_before_newer_stars(self):
        # The import loop runs reversed(pending) (oldest first); retries are
        # older than anything freshly fetched, so they must land last here.
        pending = gs.merge_pending([star("owner/NEW")], [star("owner/OLD")], set())
        self.assertEqual([s["full_name"] for s in pending],
                         ["owner/NEW", "owner/OLD"])


class StateRoundTrip(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.orig_dir, self.orig_file = gs.STATE_DIR, gs.STATE_FILE
        gs.STATE_DIR = self.dir.name
        gs.STATE_FILE = os.path.join(self.dir.name, "state.json")

    def tearDown(self):
        gs.STATE_DIR, gs.STATE_FILE = self.orig_dir, self.orig_file
        self.dir.cleanup()

    def test_retry_list_survives_a_round_trip(self):
        gs.save_imported({"owner/S1"}, [star("owner/S2")])
        imported, retry = gs.load_imported()
        self.assertEqual(imported, {"owner/S1"})
        self.assertEqual([r["full_name"] for r in retry], ["owner/S2"])

    def test_v1_state_without_retry_key_loads_empty_retry(self):
        with open(gs.STATE_FILE, "w") as f:
            json.dump({"version": 1, "ids": ["owner/S1"]}, f)
        imported, retry = gs.load_imported()
        self.assertEqual(imported, {"owner/S1"})
        self.assertEqual(retry, [])

    def test_legacy_bare_list_loads_empty_retry(self):
        with open(gs.STATE_FILE, "w") as f:
            json.dump(["owner/S1"], f)
        imported, retry = gs.load_imported()
        self.assertEqual(imported, {"owner/S1"})
        self.assertEqual(retry, [])


if __name__ == "__main__":
    unittest.main()
