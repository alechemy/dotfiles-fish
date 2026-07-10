"""Tests for import-granola-parse.py.

The file is gitignored (absent on fresh clones) and its module-level imports
pull in cryptography, sqlcipher3, and ccl-chromium-reader, none of which are
installed for system Python. Stub those into sys.modules before loading, and
skip the whole module cleanly if the file is missing or won't load — it must
never surface as an error on a machine without the Granola pipeline.
"""

import json
import sys
import types
import unittest

from helpers import BIN, load

GRANOLA = BIN / "import-granola-parse.py"
if not GRANOLA.exists():
    raise unittest.SkipTest("import-granola-parse.py not present (gitignored)")


def _inject_stub_deps():
    sys.modules.setdefault("sqlcipher3", types.ModuleType("sqlcipher3"))
    if "ccl_chromium_reader" not in sys.modules:
        ccl = types.ModuleType("ccl_chromium_reader")
        sub = types.ModuleType("ccl_chromium_reader.ccl_chromium_indexeddb")
        ccl.ccl_chromium_indexeddb = sub
        sys.modules["ccl_chromium_reader"] = ccl
        sys.modules["ccl_chromium_reader.ccl_chromium_indexeddb"] = sub
    for name in (
        "cryptography",
        "cryptography.hazmat",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.ciphers",
        "cryptography.hazmat.primitives.ciphers.aead",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["cryptography.hazmat.primitives.ciphers.aead"].AESGCM = object


try:
    _inject_stub_deps()
    gp = load("import-granola-parse.py", "import_granola_parse")
except Exception as exc:
    raise unittest.SkipTest(f"import-granola-parse.py could not be loaded: {exc}")


class ParseMeetingAllDay(unittest.TestCase):
    def test_bare_date_passes_through_without_timezone_conversion(self):
        # A dummy tz proves the all-day branch never consults local_tz: any
        # astimezone() call would raise on object(). A west-of-UTC conversion
        # would shift 2026-07-04 back to 2026-07-03.
        row = {
            "id": "m1",
            "title": "Offsite",
            "google_calendar_event": {"start": {"date": "2026-07-04"}},
            "created_at": "2026-07-01T12:00:00Z",
            "people": None,
        }
        meeting = gp.parse_meeting(row, object())
        self.assertEqual(meeting["event_date"], "2026-07-04")
        self.assertEqual(meeting["event_datetime"], "2026-07-04")

    def test_title_falls_back_to_summary_then_placeholder(self):
        summ = {
            "id": "m", "title": "", "created_at": "", "people": None,
            "google_calendar_event": {"summary": "Sync"},
        }
        self.assertEqual(gp.parse_meeting(summ, object())["title"], "Sync")
        bare = {
            "id": "m", "title": "", "created_at": "", "people": None,
            "google_calendar_event": None,
        }
        self.assertEqual(gp.parse_meeting(bare, object())["title"], "Untitled Meeting")


class PanelsToMarkdown(unittest.TestCase):
    def test_present_but_unparseable_content_counts_as_malformed(self):
        md, malformed = gp.panels_to_markdown([{"content": "{not valid json"}])
        self.assertEqual(malformed, 1)
        self.assertEqual(md, "")

    def test_absent_or_empty_content_is_not_malformed(self):
        md, malformed = gp.panels_to_markdown(
            [{"content": None}, {"content": ""}, {}]
        )
        self.assertEqual(malformed, 0)
        self.assertEqual(md, "")

    def test_valid_prosemirror_content_renders_without_malformed(self):
        doc = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}
            ],
        }
        md, malformed = gp.panels_to_markdown([{"content": json.dumps(doc)}])
        self.assertEqual(malformed, 0)
        self.assertIn("hello", md)


class MaybeJson(unittest.TestCase):
    def test_none_and_empty_string_are_none(self):
        self.assertIsNone(gp._maybe_json(None))
        self.assertIsNone(gp._maybe_json(""))

    def test_unparseable_string_is_none(self):
        self.assertIsNone(gp._maybe_json("{not json"))

    def test_valid_json_string_is_parsed(self):
        self.assertEqual(gp._maybe_json('{"a": 1}'), {"a": 1})

    def test_dict_and_list_pass_through_unchanged(self):
        d, lst = {"x": 1}, [1, 2]
        self.assertIs(gp._maybe_json(d), d)
        self.assertIs(gp._maybe_json(lst), lst)


if __name__ == "__main__":
    unittest.main()
