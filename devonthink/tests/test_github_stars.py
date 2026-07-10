import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from helpers import load

gs = load("import-github-stars.py", "import_github_stars")


def star(full_name, starred_at="2026-07-09T10:00:00Z"):
    """The flattened internal shape fetch_stars emits and merge/state consume."""
    return {
        "full_name": full_name,
        "url": f"https://github.com/{full_name}",
        "description": "",
        "starred_at": starred_at,
    }


def api_star(full_name, starred_at="2026-07-09T10:00:00Z", description=""):
    """The nested shape the GitHub star list API returns."""
    return {
        "starred_at": starred_at,
        "repo": {
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "description": description,
        },
    }


def _page(cmd):
    return int([p for p in cmd[2].split("&") if p.startswith("page=")][0][5:])


class FakeResult:
    def __init__(self, out, returncode=0, stderr=""):
        self.stdout = out
        self.returncode = returncode
        self.stderr = stderr


class _SilenceLog(unittest.TestCase):
    def setUp(self):
        self._orig_log = gs.log
        gs.log = lambda *a, **k: None

    def tearDown(self):
        gs.log = self._orig_log


class FetchStopsAtFirstImported(_SilenceLog):
    """fetch_stars stops at the first already-imported repo, so it alone can
    never re-surface an older star whose import failed while a newer one
    succeeded. The retry list is what closes that gap."""

    def test_older_failed_star_is_not_refetched(self):
        page1 = [api_star("owner/S1", "2026-07-09T10:00:00Z"),
                 api_star("owner/S2", "2026-07-09T09:00:00Z")]

        def fake_run(cmd, **kwargs):
            return FakeResult(json.dumps(page1 if _page(cmd) == 1 else []))

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            fetched = gs.fetch_stars({"owner/S1"}, first_run=False)
        finally:
            gs.subprocess.run = orig
        self.assertEqual(fetched, [])


class PageFailureAborts(_SilenceLog):
    """A mid-pagination failure must abort the whole fetch: pages already
    fetched are discarded so the frontier never advances past unseen repos."""

    def test_returncode_failure_mid_pagination_raises(self):
        page1 = [api_star(f"owner/R{i}") for i in range(100)]

        def fake_run(cmd, **kwargs):
            if _page(cmd) == 1:
                return FakeResult(json.dumps(page1))
            return FakeResult("", returncode=1, stderr="server error")

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.FetchError):
                gs.fetch_stars(set(), first_run=False)
        finally:
            gs.subprocess.run = orig

    def test_unparseable_page_mid_pagination_raises(self):
        page1 = [api_star(f"owner/R{i}") for i in range(100)]

        def fake_run(cmd, **kwargs):
            if _page(cmd) == 1:
                return FakeResult(json.dumps(page1))
            return FakeResult("not json")

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.FetchError):
                gs.fetch_stars(set(), first_run=False)
        finally:
            gs.subprocess.run = orig


class ForceReachesOlderRepo(_SilenceLog):
    def setUp(self):
        super().setUp()
        self._orig_force = gs.FORCE_REPO

    def tearDown(self):
        gs.FORCE_REPO = self._orig_force
        super().tearDown()

    def test_force_paginates_past_frontier(self):
        gs.FORCE_REPO = "owner/OLD"
        page1 = [api_star(f"owner/N{i}") for i in range(100)]
        page2 = [api_star("owner/OLD"), api_star("owner/EVENOLDER")]
        imported = {f"owner/N{i}" for i in range(100)} | {"owner/OLD", "owner/EVENOLDER"}

        def fake_run(cmd, **kwargs):
            p = _page(cmd)
            if p == 1:
                return FakeResult(json.dumps(page1))
            if p == 2:
                return FakeResult(json.dumps(page2))
            return FakeResult(json.dumps([]))

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            fetched = gs.fetch_stars(imported, first_run=False)
        finally:
            gs.subprocess.run = orig
        self.assertEqual([s["full_name"] for s in fetched], ["owner/OLD"])


class FirstRunCutoff(_SilenceLog):
    def test_first_run_imports_only_last_24h(self):
        now = datetime.utcnow().replace(microsecond=0)
        recent = now.isoformat() + "Z"
        old = (now - timedelta(hours=48)).isoformat() + "Z"
        page1 = [api_star("owner/RECENT", recent), api_star("owner/OLD", old)]

        def fake_run(cmd, **kwargs):
            return FakeResult(json.dumps(page1 if _page(cmd) == 1 else []))

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            fetched = gs.fetch_stars(set(), first_run=True)
        finally:
            gs.subprocess.run = orig
        self.assertEqual([s["full_name"] for s in fetched], ["owner/RECENT"])


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


class RunAbortsOnFetchFailure(_SilenceLog):
    """A fetch abort is not a per-record import failure: nothing is imported
    and the retry queue is left intact, with no retries enqueued for repos that
    were never fetched."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.orig_dir, self.orig_file = gs.STATE_DIR, gs.STATE_FILE
        gs.STATE_DIR = self.dir.name
        gs.STATE_FILE = os.path.join(self.dir.name, "state.json")
        gs.save_imported({"owner/EXISTING"}, [star("owner/PRIORFAIL")])

    def tearDown(self):
        gs.STATE_DIR, gs.STATE_FILE = self.orig_dir, self.orig_file
        self.dir.cleanup()
        super().tearDown()

    def test_page_failure_commits_nothing_and_preserves_retry(self):
        page1 = [api_star(f"owner/NEW{i}") for i in range(100)]
        import_calls = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == gs.GH_BIN and cmd[1] == "api":
                if _page(cmd) == 1:
                    return FakeResult(json.dumps(page1))
                return FakeResult("", returncode=1, stderr="server error")
            if cmd[0] == "/usr/bin/osascript":
                import_calls.append(cmd)
                return FakeResult("ok: imported")
            return FakeResult("")

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            gs.main()
        finally:
            gs.subprocess.run = orig

        self.assertEqual(import_calls, [])
        imported, retry = gs.load_imported()
        self.assertEqual(imported, {"owner/EXISTING"})
        self.assertEqual([r["full_name"] for r in retry], ["owner/PRIORFAIL"])


if __name__ == "__main__":
    unittest.main()
