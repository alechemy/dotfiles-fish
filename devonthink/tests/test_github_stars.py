import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

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


class ManualDetection(unittest.TestCase):
    """Must align with pipeline_log.py's rule (PIPELINE_MANUAL=1, or either
    stream a TTY) — a hand run with piped stdout still has to tag itself
    /manual so dt-watchdog doesn't page on its failures."""

    def test_stderr_tty_alone_is_manual(self):
        with mock.patch.object(gs.sys.stdout, "isatty", return_value=False), \
             mock.patch.object(gs.sys.stderr, "isatty", return_value=True), \
             mock.patch.dict(gs.os.environ, {}, clear=True):
            self.assertTrue(gs._is_manual())

    def test_pipeline_manual_env_alone_is_manual(self):
        with mock.patch.object(gs.sys.stdout, "isatty", return_value=False), \
             mock.patch.object(gs.sys.stderr, "isatty", return_value=False), \
             mock.patch.dict(gs.os.environ, {"PIPELINE_MANUAL": "1"},
                             clear=True):
            self.assertTrue(gs._is_manual())

    def test_neither_stream_nor_env_is_not_manual(self):
        with mock.patch.object(gs.sys.stdout, "isatty", return_value=False), \
             mock.patch.object(gs.sys.stderr, "isatty", return_value=False), \
             mock.patch.dict(gs.os.environ, {}, clear=True):
            self.assertFalse(gs._is_manual())


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


class ClassifyGhFailure(unittest.TestCase):
    """gh reports "cannot reach GitHub" and "GitHub said no" very differently.
    Conflating them either pages the user to re-authenticate a working token or
    skips a real failure forever."""

    def test_missing_credentials_is_auth_by_exit_code(self):
        # An empty GH_CONFIG_DIR exits 4 and prints a login prompt with no HTTP
        # status. Inferring "transient" from the missing status would skip this
        # silently every 30 minutes, forever.
        stderr = ("To get started with GitHub CLI, please run:  gh auth login\n"
                  "Alternatively, populate the GH_TOKEN environment variable")
        self.assertEqual(gs.classify_gh_failure(4, stderr), "auth")

    def test_bad_token_is_auth(self):
        self.assertEqual(
            gs.classify_gh_failure(1, "gh: Bad credentials (HTTP 401)"), "auth")

    def test_unknown_statusless_failure_is_fatal(self):
        self.assertEqual(gs.classify_gh_failure(1, "server error"), "fatal")

    def test_bare_eof_stays_fatal(self):
        # Too generic to treat as transport: a real one escalates via the
        # 3-hour counter anyway, and guessing wrong here skips silently.
        self.assertEqual(gs.classify_gh_failure(1, "EOF"), "fatal")

    def test_transport_failures_are_transient(self):
        for stderr in (
            'Get "https://api.github.com/user/starred": dial tcp '
            '140.82.114.6:443: connect: connection refused',
            'Get "https://api.github.com/user/starred": read tcp: connection timed out',
            'Get "https://api.github.com/user/starred": write: broken pipe',
            'Get "https://api.github.com/user/starred": unexpected EOF',
            'Get "https://api.github.com/user/starred": dial tcp: network is down',
        ):
            self.assertEqual(gs.classify_gh_failure(1, stderr), "transient", stderr)

    def test_retryable_http_statuses_are_transient(self):
        for stderr in ("gh: API rate limit exceeded (HTTP 403)",
                       "gh: Request Timeout (HTTP 408)",
                       "gh: Too Many Requests (HTTP 429)",
                       "gh: Server Error (HTTP 502)"):
            self.assertEqual(gs.classify_gh_failure(1, stderr), "transient", stderr)

    def test_other_http_errors_are_fatal(self):
        self.assertEqual(
            gs.classify_gh_failure(1, "gh: Not Found (HTTP 404)"), "fatal")
        self.assertEqual(
            gs.classify_gh_failure(
                1, "gh: Resource not accessible by token (HTTP 403)"), "fatal")


class StallsAreOnlyStalls(_SilenceLog):
    """The stall counter exists so a persistent stall eventually pages. It must
    not be fed by failures that prove GitHub answered, or "consecutive" stops
    meaning consecutive and the escalation fires on a machine that is fine."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.orig_dir, self.orig_stall = gs.STATE_DIR, gs.STALL_FILE
        gs.STATE_DIR = self.dir.name
        gs.STALL_FILE = os.path.join(self.dir.name, "stalls")

    def tearDown(self):
        gs.STATE_DIR, gs.STALL_FILE = self.orig_dir, self.orig_stall
        self.dir.cleanup()
        super().tearDown()

    def _count(self):
        if not os.path.exists(gs.STALL_FILE):
            return 0
        with open(gs.STALL_FILE) as f:
            return int(f.read().strip())

    def test_consecutive_stalls_accumulate_then_escalate(self):
        lines = []
        gs.log = lines.append
        for _ in range(gs.STALL_ALERT_AFTER):
            gs.record_stall("did not complete", scheduled=True)
        self.assertEqual(self._count(), gs.STALL_ALERT_AFTER)
        self.assertTrue(lines[-1].startswith("WARNING "))
        self.assertFalse(any(l.startswith("WARNING") for l in lines[:-1]))

    def test_manual_failures_never_touch_the_counter(self):
        # Manual runs are exempt from the watchdog, so counting their failures
        # would let six hand-run attempts make the next scheduled failure page
        # as the seventh "consecutive" one.
        for _ in range(gs.STALL_ALERT_AFTER + 2):
            gs.record_stall("did not complete", scheduled=False)
        self.assertEqual(self._count(), 0)

    def test_manual_runs_do_not_clear_the_counter(self):
        with open(gs.STALL_FILE, "w") as f:
            f.write("3")
        gs.clear_stalls(scheduled=False)
        self.assertEqual(self._count(), 3)

    def test_success_clears_the_counter(self):
        gs.record_stall("did not complete", scheduled=True)
        gs.clear_stalls(scheduled=True)
        self.assertEqual(self._count(), 0)

    def test_timeout_raises_gh_unavailable_not_timeout_expired(self):
        def fake_run(cmd, **kwargs):
            raise gs.subprocess.TimeoutExpired(cmd, gs.GH_TIMEOUT)

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.GhUnavailable):
                gs.run_gh(["api", "/user/starred"])
        finally:
            gs.subprocess.run = orig

    def test_unrunnable_gh_is_a_local_error_not_a_crash(self):
        # A non-executable gh raises PermissionError, not FileNotFoundError.
        # Uncaught, it reaches the top-level handler and pages as FATAL.
        def fake_run(cmd, **kwargs):
            raise PermissionError(13, "Permission denied")

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.GhLocalError) as caught:
                gs.run_gh(["api", "/user/starred"])
        finally:
            gs.subprocess.run = orig
        self.assertIn("Permission denied", str(caught.exception))


class FetchRaisesTypedErrors(_SilenceLog):
    def test_transport_failure_mid_pagination_is_unavailable_not_fetch_error(self):
        page1 = [api_star(f"owner/R{i}") for i in range(100)]

        def fake_run(cmd, **kwargs):
            if _page(cmd) == 1:
                return FakeResult(json.dumps(page1))
            return FakeResult("", returncode=1, stderr='dial tcp: connection refused')

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.GhUnavailable):
                gs.fetch_stars(set(), first_run=False)
        finally:
            gs.subprocess.run = orig

    def test_missing_credentials_raises_auth_error(self):
        def fake_run(cmd, **kwargs):
            return FakeResult("", returncode=4, stderr="please run:  gh auth login")

        orig = gs.subprocess.run
        gs.subprocess.run = fake_run
        try:
            with self.assertRaises(gs.GhAuthError):
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
        self.orig_stall = gs.STALL_FILE
        # The suite's stdout is a pipe, so MANUAL_RUN is already False — pin it
        # so a run under PIPELINE_MANUAL=1 still exercises the scheduled path.
        self.orig_manual = gs.MANUAL_RUN
        gs.MANUAL_RUN = False
        gs.STATE_DIR = self.dir.name
        gs.STATE_FILE = os.path.join(self.dir.name, "state.json")
        gs.STALL_FILE = os.path.join(self.dir.name, "stalls")
        gs.save_imported({"owner/EXISTING"}, [star("owner/PRIORFAIL")])

    def tearDown(self):
        gs.STATE_DIR, gs.STATE_FILE = self.orig_dir, self.orig_file
        gs.STALL_FILE = self.orig_stall
        gs.MANUAL_RUN = self.orig_manual
        self.dir.cleanup()
        super().tearDown()

    def _run_main_with_page2(self, stderr, returncode=1):
        page1 = [api_star(f"owner/NEW{i}") for i in range(100)]
        import_calls = []

        def fake_run(cmd, **kwargs):
            if cmd[0] == gs.GH_BIN and cmd[1] == "api":
                if _page(cmd) == 1:
                    return FakeResult(json.dumps(page1))
                return FakeResult("", returncode=returncode, stderr=stderr)
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
        return import_calls

    def test_page_failure_commits_nothing_and_preserves_retry(self):
        import_calls = self._run_main_with_page2("server error")

        self.assertEqual(import_calls, [])
        imported, retry = gs.load_imported()
        self.assertEqual(imported, {"owner/EXISTING"})
        self.assertEqual([r["full_name"] for r in retry], ["owner/PRIORFAIL"])

    def test_every_non_retryable_outcome_resets_the_stall_counter(self):
        # The counter means "consecutive retryable failures". Any other outcome
        # ends the run, so leaving a stale count standing would let unrelated
        # stalls hours apart add up to a bogus "N consecutive" escalation.
        for stderr in ("gh: Not Found (HTTP 404)", "server error"):
            with open(gs.STALL_FILE, "w") as f:
                f.write("3")
            self._run_main_with_page2(stderr)
            self.assertFalse(os.path.exists(gs.STALL_FILE), stderr)

    def test_fatal_api_error_pages_the_user(self):
        # dt-watchdog only notifies on lines carrying its failure tokens. A
        # fatal API error that logs without one is a silently dead importer.
        lines = []
        gs.log = lines.append
        self._run_main_with_page2("gh: Not Found (HTTP 404)")
        self.assertTrue(
            any(" ERROR " in f"[github-stars] {l}" for l in lines),
            f"no watchdog-visible failure line in: {lines}")


if __name__ == "__main__":
    unittest.main()
