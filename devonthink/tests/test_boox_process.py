import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from helpers import load

jp = load("boox-process.py", "boox_process")

TODAY = date(2026, 7, 11)  # a Saturday
YEAR = 2026


class ParseDateLine(unittest.TestCase):
    def parse(self, line):
        return jp.parse_date_line(line, YEAR, TODAY)

    def test_accepted_formats(self):
        for line in (
            "# Sat, Jul 4",
            "# Saturday, July 4th",
            "Sat Jul 4",
            "# 2026-07-04 Sat",
            "# 7/4",
            "# 7/4/26",
            "# 7/4/2026",
            "## sat. jul 4",
        ):
            self.assertEqual(self.parse(line), date(2026, 7, 4), line)

    def test_weekday_is_a_check_digit(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Fri, Jul 4")  # 2026-07-04 is a Saturday

    def test_no_weekday_still_parses(self):
        self.assertEqual(self.parse("# Jul 4"), date(2026, 7, 4))

    def test_year_anchored_to_notebook(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Sat, Jul 4, 2025")

    def test_future_dates_rejected(self):
        with self.assertRaises(jp.DateParseError):
            self.parse("# Thu, Dec 31")

    def test_invalid_and_missing_dates_rejected(self):
        for line in ("# Jul 32", "# groceries and errands", "", "#"):
            with self.assertRaises(jp.DateParseError):
                self.parse(line)

    def test_date_mentioned_in_prose_is_not_matched(self):
        # Only the first line is ever passed in; a heading that is prose
        # with no date must park rather than fish one out.
        with self.assertRaises(jp.DateParseError):
            self.parse("# planning the trip")


class AssemblePages(unittest.TestCase):
    def test_cont_heading_merges_into_previous_section(self):
        pages = [
            "# Trip Notes\n\n## Packing\n- boots\n- rain shell",
            "## Packing (cont.)\n- headlamp\n\n## Food\n- oats",
        ]
        got = jp.assemble_pages(pages)
        self.assertEqual(got.count("## Packing"), 1)
        self.assertIn("- headlamp", got)
        self.assertIn("## Food", got)

    def test_cont_only_merges_across_a_boundary(self):
        got = jp.assemble_pages(["## Ideas (cont.)\n- first page keeps it"])
        self.assertIn("## Ideas (cont.)", got)

    def test_blank_pages_dropped(self):
        self.assertEqual(jp.assemble_pages(["# A", "  ", "# B"]), "# A\n\n# B")


class ExtractTasks(unittest.TestCase):
    def test_bullets_under_tasks_header(self):
        text = ("# Sat, Jul 4\n\nprose\n\n## Tasks\n"
                "- fix the bike tire\n- [ ] call the plumber\n"
                "* renew passport\n\n## Later\n- not a task")
        self.assertEqual(
            jp.extract_tasks(text),
            ["fix the bike tire", "call the plumber", "renew passport"])

    def test_no_section_no_tasks(self):
        self.assertEqual(jp.extract_tasks("# Sat, Jul 4\n\n- groceries"), [])

    def test_action_items_variant_and_colon(self):
        text = "Action Items:\n- send the deck"
        self.assertEqual(jp.extract_tasks(text), ["send the deck"])


class FirstHeadingLine(unittest.TestCase):
    def test_skips_leading_blanks(self):
        self.assertEqual(
            jp.first_heading_line("\n\n# Sat, Jul 4\ntext"), "# Sat, Jul 4")

    def test_empty_document(self):
        self.assertEqual(jp.first_heading_line(""), "")


class LoadConfig(unittest.TestCase):
    def write(self, contents):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(contents)
        self.addCleanup(os.unlink, path)
        return path

    def test_model_never_inherited_from_entities_conf(self):
        saved = (jp.ENTITIES_CONFIG, jp.CONFIG_FILE)
        jp.ENTITIES_CONFIG = self.write(
            "OMLX_MODEL=Some-Text-Model\n"
            "OMLX_URL=http://127.0.0.1:9999\n"
            "OMLX_API_KEY=k\n")
        jp.CONFIG_FILE = self.write("")
        try:
            config = jp.load_config()
        finally:
            jp.ENTITIES_CONFIG, jp.CONFIG_FILE = saved
        self.assertEqual(config["OMLX_URL"], "http://127.0.0.1:9999")
        self.assertEqual(config["OMLX_API_KEY"], "k")
        self.assertEqual(config["OMLX_MODEL"], jp.DEFAULTS["OMLX_MODEL"])

    def test_journal_conf_overrides(self):
        saved = (jp.ENTITIES_CONFIG, jp.CONFIG_FILE)
        jp.ENTITIES_CONFIG = self.write("OMLX_URL=http://127.0.0.1:9999\n")
        jp.CONFIG_FILE = self.write("OMLX_MODEL=Other-VL\nMAX_PER_RUN=2\n")
        try:
            config = jp.load_config()
        finally:
            jp.ENTITIES_CONFIG, jp.CONFIG_FILE = saved
        self.assertEqual(config["OMLX_MODEL"], "Other-VL")
        self.assertEqual(config["MAX_PER_RUN"], "2")
        self.assertEqual(config["OMLX_URL"], "http://127.0.0.1:9999")


class LinkDailyNote(unittest.TestCase):
    """link_daily_note must route the write through a single idempotent
    bridge op — no Python-side read of the daily note's body, no
    insert-daily-note-section.py subprocess in between."""

    def test_routes_through_insert_under_section_with_no_body_surgery(self):
        calls = []

        def fake_run_bridge(ops, timeout=300):
            calls.append(ops)
            if ops[0]["op"] == "get_or_create_daily":
                return [{"uuid": "DAILY-1", "text": "# Day\n", "created": False}]
            return [{"uuid": "DAILY-1", "changed": True}]

        with mock.patch.object(jp, "run_bridge", side_effect=fake_run_bridge) \
                as bridge, mock.patch.object(jp.subprocess, "run") as sub_run:
            jp.link_daily_note(TODAY, "JOURNAL-UUID")

        sub_run.assert_not_called()
        self.assertEqual(bridge.call_count, 2)
        self.assertEqual(calls[0][0]["op"], "get_or_create_daily")
        self.assertEqual(calls[1], [{
            "op": "insert_under_section",
            "uuid": "DAILY-1",
            "header": jp.DAILY_SECTION,
            "line": "- [\U0001F4D4 Journal](x-devonthink-item://JOURNAL-UUID)",
        }])


def _unlink_if_present(path):
    if os.path.exists(path):
        os.unlink(path)


class LoadState(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(_unlink_if_present, path)
        self.path = path
        saved = jp.STATE_FILE
        jp.STATE_FILE = path
        self.addCleanup(setattr, jp, "STATE_FILE", saved)

    def test_corrupt_json_fails_closed(self):
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertRaises(RuntimeError):
            jp.load_state()

    def test_missing_file_starts_from_empty_schema(self):
        os.unlink(self.path)
        self.assertEqual(
            jp.load_state(),
            {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}})


class RebuildState(unittest.TestCase):
    def test_missing_journal_group_is_not_an_error(self):
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}}
        with mock.patch.object(
                jp, "run_bridge",
                side_effect=RuntimeError("group not found: /15_JOURNAL")):
            jp.rebuild_state(state)

    def test_bridge_unavailable_propagates(self):
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}}
        with mock.patch.object(
                jp, "run_bridge",
                side_effect=jp.BridgeUnavailable("DEVONthink not running")):
            with self.assertRaises(jp.BridgeUnavailable):
                jp.rebuild_state(state)


class AutoRebuildIfMissing(unittest.TestCase):
    def test_rebuilds_when_state_file_was_absent(self):
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}}
        with mock.patch.object(jp, "rebuild_state") as rebuild:
            jp.auto_rebuild_if_missing(state, state_file_existed=False,
                                       dry_run=False)
        rebuild.assert_called_once_with(state)

    def test_skips_when_state_file_existed(self):
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}}
        with mock.patch.object(jp, "rebuild_state") as rebuild:
            jp.auto_rebuild_if_missing(state, state_file_existed=True,
                                       dry_run=False)
        rebuild.assert_not_called()

    def test_skips_during_dry_run(self):
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {}}
        with mock.patch.object(jp, "rebuild_state") as rebuild:
            jp.auto_rebuild_if_missing(state, state_file_existed=False,
                                       dry_run=True)
        rebuild.assert_not_called()


class UpsertEntry(unittest.TestCase):
    def test_adopts_existing_dt_record_instead_of_duplicating(self):
        entries = {}
        calls = []

        def fake_run_bridge(ops, timeout=300):
            calls.append(ops)
            op = ops[0]["op"]
            if op == "get_at_path":
                return [{"uuid": "EXISTING-UUID", "name": "2026-07-04 Journal"}]
            if op == "create_record":
                self.fail("must not create a duplicate record")
            return [{"uuid": "EXISTING-UUID"}]

        with mock.patch.object(jp, "run_bridge", side_effect=fake_run_bridge):
            uuid, changed = jp.upsert_entry(
                "2026 Journal", date(2026, 7, 4), 0, "sig",
                "# Fri, Jul 4\ntext", entries)

        self.assertEqual(uuid, "EXISTING-UUID")
        self.assertTrue(changed)
        self.assertEqual(entries["2026-07-04"]["uuid"], "EXISTING-UUID")
        get_at_path = next(c for c in calls if c[0]["op"] == "get_at_path")
        self.assertEqual(get_at_path[0]["path"],
                         "/15_JOURNAL/2026/2026-07-04 Journal")

    def test_creates_when_no_dt_record_and_no_state_entry(self):
        entries = {}

        def fake_run_bridge(ops, timeout=300):
            op = ops[0]["op"]
            if op == "get_at_path":
                return [None]
            if op == "create_record":
                return [{"uuid": "NEW-UUID"}]
            self.fail(f"unexpected op {op}")

        with mock.patch.object(jp, "run_bridge", side_effect=fake_run_bridge):
            uuid, changed = jp.upsert_entry(
                "2026 Journal", date(2026, 7, 4), 0, "sig",
                "# Fri, Jul 4\ntext", entries)

        self.assertEqual(uuid, "NEW-UUID")
        self.assertTrue(changed)
        self.assertEqual(entries["2026-07-04"]["uuid"], "NEW-UUID")


class FileNoteIfNeeded(unittest.TestCase):
    def test_persists_filed_sha_and_dirty_after_filing(self):
        nb = {"pages": [{"text": "# Note\nbody"}], "dirty": True}
        state = {"schema": jp.STATE_SCHEMA_VERSION, "notebooks": {"stem": nb}}
        saved = []

        with mock.patch.object(jp, "run_bridge", return_value=[[]]), \
             mock.patch.object(jp, "extract_metadata", return_value=None), \
             mock.patch.object(jp, "convert_tiff", return_value="/tmp/x.tiff"), \
             mock.patch.object(jp, "file_regular_note",
                               return_value=("UUID-1", "imported")), \
             mock.patch.object(jp, "save_state",
                               side_effect=lambda s: saved.append(True)):
            result = jp.file_note_if_needed(
                nb, "stem", "/tmp/x.pdf", "/tmp/work", {}, state)

        self.assertTrue(result)
        self.assertTrue(nb["filed_sha"])
        self.assertFalse(nb["dirty"])
        self.assertTrue(saved, "save_state must persist filed_sha/dirty")


class FileRegularNote(unittest.TestCase):
    def _run(self, meta):
        calls = []

        def fake_run_bridge(ops, timeout=300):
            calls.append(ops)
            if ops[0]["op"] == "find_by_field":
                return [[{"uuid": "UUID-1"}]]
            return [{"uuid": "UUID-1"}] * len(ops)

        with mock.patch.object(jp, "run_bridge", side_effect=fake_run_bridge):
            jp.file_regular_note("stem", "/tmp/x.tiff", "markdown", meta)
        all_ops = [op for call in calls for op in call]
        return next(op for op in all_ops if op["op"] == "set_fields")["fields"]

    def test_meta_none_leaves_event_date_untouched_on_update(self):
        fields = self._run(None)
        self.assertNotIn("EventDate", fields)

    def test_meta_with_date_writes_event_date_on_update(self):
        fields = self._run(
            {"eventDate": "2026-07-04", "tags": [], "summary": ""})
        self.assertEqual(fields["EventDate"], "2026-07-04")


if __name__ == "__main__":
    unittest.main()
