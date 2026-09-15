from contextlib import ExitStack
import unittest
from unittest import mock

from helpers import load


mb = load("dt-morning-brief.py", "dt_morning_brief_success")


class BriefSuccessTests(unittest.TestCase):
    def test_required_source_or_merge_failure_does_not_refresh_success(self):
        for mode in ("healthy-empty", "calendar-failed", "contacts-failed", "merge-skipped", "dry-run"):
            with self.subTest(mode=mode), ExitStack() as stack:
                argv = ["dt-morning-brief"] + (["--dry-run"] if mode == "dry-run" else [])
                stack.enter_context(mock.patch.object(mb.sys, "argv", argv))
                stack.enter_context(mock.patch.object(mb.subprocess, "run", return_value=mock.Mock(returncode=0)))
                for name, value in {
                    "acquire_lock": object(), "load_config": {},
                    "load_skip_attendee_re": None, "load_skip_calendars": set(),
                    "load_calendar_contexts": {}, "load_identity_provenance_or_quarantine": {},
                    "load_people": ([], set()), "cached_repeats": [],
                    "contact_bumps": [], "query_messages": [], "message_bumps": [],
                    "propose_calendar_candidates": None, "review_backlog": {},
                    "load_journal_state": None, "on_this_day_rows": [],
                    "build_snapshot": {}, "write_snapshot": None, "push_snapshot": None,
                }.items():
                    stack.enter_context(mock.patch.object(mb, name, return_value=value))

                def source(script, *args, **kwargs):
                    if script == mb.CALENDAR:
                        return {"ok": mode != "calendar-failed", "events": []}
                    if script == mb.CONTACTS:
                        return {"ok": mode != "contacts-failed", "contacts": []}
                    raise AssertionError(script)

                stack.enter_context(mock.patch.object(mb, "run_osascript", side_effect=source))
                stack.enter_context(mock.patch.object(mb, "run_bridge", side_effect=[
                    [{"uuid": "FIXTURE"}],
                    [{"changed": False, "skipped": 1 if mode == "merge-skipped" else 0}],
                ]))
                stamp = stack.enter_context(mock.patch.object(mb, "record_success"))
                mb.main()
                self.assertEqual(stamp.called, mode == "healthy-empty")


if __name__ == "__main__":
    unittest.main()
