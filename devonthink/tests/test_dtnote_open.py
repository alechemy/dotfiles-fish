"""dtnote-open: the URL parsing and note-selection logic behind the
briefing's create-on-click links. The bridge round-trips are out of scope
(driven by hand, like the rest of the DT I/O); what must never regress is
the pure logic that makes a click idempotent."""

import unittest

from helpers import load

dn = load("dtnote-open.py", "dtnote_open")
be = load("brief_events.py", "brief_events")


class ParseUrl(unittest.TestCase):
    def test_round_trips_a_rendered_link(self):
        url = be.dtnote_url("2026-07-14", "SE / Prod Engineering Sync")
        self.assertEqual(dn.parse_url(url),
                         ("2026-07-14", "SE / Prod Engineering Sync"))

    def test_rejects_other_schemes_and_commands(self):
        for bad in ("x-devonthink-item://UUID",
                    "dtnote://trash?date=2026-07-14&title=X",
                    "https://open?date=2026-07-14&title=X"):
            with self.assertRaises(ValueError):
                dn.parse_url(bad)

    def test_rejects_a_missing_or_invalid_date(self):
        for bad in ("dtnote://open?title=X",
                    "dtnote://open?date=2026-13-40&title=X"):
            with self.assertRaises(ValueError):
                dn.parse_url(bad)

    def test_rejects_an_empty_title(self):
        with self.assertRaises(ValueError):
            dn.parse_url("dtnote://open?date=2026-07-14&title=%20")


class OwningNote(unittest.TestCase):
    def test_only_a_meeting_note_claims_the_click(self):
        """A handwritten note linked to the same event must not swallow the
        click — the title always resolves to the typed note."""
        self.assertIsNone(dn.owning_note(
            [{"uuid": "HW", "documenttype": ""}]))
        self.assertEqual(
            dn.owning_note([{"uuid": "HW", "documenttype": ""},
                            {"uuid": "N1", "documenttype": "Meeting Notes"}]),
            "N1")

    def test_no_notes_means_create(self):
        self.assertIsNone(dn.owning_note([]))


if __name__ == "__main__":
    unittest.main()
