import json
import os
import tempfile
import unittest

from helpers import load

tp = load("trmnl-push-brief.py", "trmnl_push_brief")


def snapshot(meetings=1, people=2, unmatched=0, reconnect=0, birthdays=0,
             otd=0, title="Weekly planning sync"):
    return {
        "date": "2026-07-09",
        "generated_at": "2026-07-09T05:15:00",
        "meetings": [
            {"time": "9:00am", "title": title,
             "people": [
                 {"name": f"Person Name {p}", "role": "Senior Architect",
                  "employer": "Globex Corp", "city": "Chicago",
                  "last": "2026-06-20"}
                 for p in range(people)],
             "unmatched": [f"Stranger {u} (s{u}@x.com)"
                           for u in range(unmatched)]}
            for _ in range(meetings)],
        "reconnect": [
            {"name": f"Old Friend {r}", "relationship": "close-friend",
             "days": 90 + r, "last": "2026-04-01"}
            for r in range(reconnect)],
        "birthdays": [
            {"date": "2026-07-15", "name": f"Birthday Person {b}",
             "age": 40, "today": False}
            for b in range(birthdays)],
        "review": {"pending": 3, "approved": 1, "parked": 2},
        "journal": {"state": "missing", "pending": 0, "parked": 0,
                    "staged": 0},
        "on_this_day": [
            {"years": 1 + o % 5, "name": f"Anniversary record {o}",
             "kind": "markdown"}
            for o in range(otd)],
    }


class Compact(unittest.TestCase):
    def test_small_payload_passes_through_untruncated(self):
        snap = snapshot()
        payload, applied = tp.compact(snap, 2048)
        self.assertEqual(applied, [])
        self.assertIs(payload["truncated"], False)
        self.assertEqual(payload["meetings"], snap["meetings"])

    def test_compact_never_mutates_the_snapshot(self):
        snap = snapshot(meetings=8, people=4, unmatched=6, reconnect=10,
                        birthdays=8, otd=10)
        before = json.dumps(snap)
        tp.compact(snap, 500)
        self.assertEqual(json.dumps(snap), before)

    def test_ladder_applies_in_order_and_result_fits(self):
        payload, applied = tp.compact(
            snapshot(meetings=6, people=3, unmatched=4, reconnect=10,
                     birthdays=6, otd=10), 1200)
        self.assertIsNotNone(payload)
        self.assertIs(payload["truncated"], True)
        labels = [label for label, _ in tp.LADDER]
        self.assertEqual(applied, labels[:len(applied)])
        self.assertLessEqual(len(tp.body_bytes(payload)), 1200)

    def test_busy_day_fits_free_tier_budget(self):
        payload, _ = tp.compact(
            snapshot(meetings=6, people=3, unmatched=2, reconnect=10,
                     birthdays=5, otd=10), tp.DEFAULT_PAYLOAD_LIMIT)
        self.assertIsNotNone(payload)
        self.assertLessEqual(len(tp.body_bytes(payload)),
                             tp.DEFAULT_PAYLOAD_LIMIT)
        self.assertGreaterEqual(len(payload["meetings"]), 4)

    def test_impossible_budget_returns_none(self):
        payload, applied = tp.compact(snapshot(meetings=4), 10)
        self.assertIsNone(payload)
        self.assertEqual(applied, [label for label, _ in tp.LADDER])

    def test_unmatched_cap_records_overflow(self):
        payload = snapshot(unmatched=5)
        tp._cap_unmatched(2)(payload)
        m = payload["meetings"][0]
        self.assertEqual(len(m["unmatched"]), 2)
        self.assertEqual(m["more_unmatched"], 3)

    def test_unmatched_cap_is_cumulative(self):
        payload = snapshot(unmatched=5)
        tp._cap_unmatched(2)(payload)
        tp._cap_unmatched(0)(payload)
        m = payload["meetings"][0]
        self.assertEqual(m["unmatched"], [])
        self.assertEqual(m["more_unmatched"], 5)

    def test_title_truncation_appends_ellipsis(self):
        payload = snapshot(title="A very long meeting title that overflows")
        tp._truncate_titles(20)(payload)
        title = payload["meetings"][0]["title"]
        self.assertEqual(len(title), 20)
        self.assertTrue(title.endswith("…"))

    def test_short_title_untouched(self):
        payload = snapshot(title="Standup")
        tp._truncate_titles(20)(payload)
        self.assertEqual(payload["meetings"][0]["title"], "Standup")


class ConfigParsing(unittest.TestCase):
    def parse(self, content):
        with tempfile.NamedTemporaryFile("w", suffix=".conf",
                                         delete=False) as f:
            f.write(content)
        old = tp.CONFIG_FILE
        tp.CONFIG_FILE = f.name
        try:
            return tp.load_config()
        finally:
            tp.CONFIG_FILE = old
            os.unlink(f.name)

    def test_key_value_with_comments_and_junk(self):
        cfg = self.parse(
            "# comment\n"
            "\n"
            "TRMNL_WEBHOOK_URL = https://trmnl.com/api/custom_plugins/abc\n"
            "TRMNL_PAYLOAD_LIMIT=5120\n"
            "not a config line\n")
        self.assertEqual(cfg["TRMNL_WEBHOOK_URL"],
                         "https://trmnl.com/api/custom_plugins/abc")
        self.assertEqual(cfg["TRMNL_PAYLOAD_LIMIT"], "5120")

    def test_missing_file_is_empty_config(self):
        old = tp.CONFIG_FILE
        tp.CONFIG_FILE = "/nonexistent/trmnl.conf"
        try:
            self.assertEqual(tp.load_config(), {})
        finally:
            tp.CONFIG_FILE = old


class StateRoundtrip(unittest.TestCase):
    def test_save_then_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = tp.STATE_FILE
            tp.STATE_FILE = os.path.join(tmp, "state.json")
            try:
                tp.save_state({"hash": "abc", "status": "ok"})
                self.assertEqual(tp.load_state(),
                                 {"hash": "abc", "status": "ok"})
            finally:
                tp.STATE_FILE = old

    def test_corrupt_or_missing_state_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = tp.STATE_FILE
            tp.STATE_FILE = os.path.join(tmp, "state.json")
            try:
                self.assertEqual(tp.load_state(), {})
                with open(tp.STATE_FILE, "w") as f:
                    f.write("not json")
                self.assertEqual(tp.load_state(), {})
            finally:
                tp.STATE_FILE = old


if __name__ == "__main__":
    unittest.main()
