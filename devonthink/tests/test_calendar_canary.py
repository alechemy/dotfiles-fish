"""Live checks against the real macOS Calendar.

Everything else in this suite runs on fixtures. These cannot: the bugs they
guard live in the JXA/ObjC bridge, where `p.participantType` comes back as the
string "1" and `=== 1` is silently false. A fixture can't reproduce that — only
a real EventKit round trip can. Skips (rather than fails) when there is no data
or no Calendars grant, so a fresh machine or a follower Mac stays green.
"""

import json
import os
import subprocess
import unittest
from datetime import date, timedelta

CALENDAR = os.path.expanduser("~/.local/bin/calendar-events-json.js")
WINDOW_DAYS = 120
RSVP_STATES = {None, "unknown", "pending", "accepted", "declined", "tentative",
               "delegated", "completed", "in-process"}


def dump(start, end):
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", CALENDAR, start, end],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise unittest.SkipTest(f"calendar dump failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


@unittest.skipUnless(os.path.exists(CALENDAR), "calendar-events-json.js not stowed")
class CalendarCanary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        today = date.today()
        cls.data = dump((today - timedelta(days=WINDOW_DAYS)).isoformat(),
                        (today - timedelta(days=1)).isoformat())
        if not cls.data.get("ok"):
            raise unittest.SkipTest(f"calendar unavailable: {cls.data.get('error')}")
        cls.events = cls.data["events"]

    def test_some_attendee_is_recognized_as_a_person(self):
        with_attendees = [e for e in self.events if e["attendees"]]
        if not with_attendees:
            self.skipTest("no events with attendees in the window")
        people = [a for e in with_attendees for a in e["attendees"] if a["is_person"]]
        self.assertTrue(
            people,
            f"{len(with_attendees)} events carry attendees but none is_person — "
            "EventKit's participantType is being compared without Number() coercion",
        )

    def test_self_is_identified_on_events_with_attendees(self):
        with_attendees = [e for e in self.events if e["attendees"]]
        if not with_attendees:
            self.skipTest("no events with attendees in the window")
        self.assertTrue(
            any(a["is_self"] for e in with_attendees for a in e["attendees"]),
            "no attendee on any event is is_self — contact bumps would count you",
        )

    def test_every_event_carries_its_own_date(self):
        for e in self.events:
            self.assertRegex(e["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertEqual(e["date"], e["start"][:10])

    def test_every_event_carries_stable_provenance_identifiers(self):
        for e in self.events:
            self.assertTrue(e["calendar_id"])
            self.assertTrue(e["source_id"])
            self.assertTrue(e["event_id"])

    def test_rsvp_is_a_known_state(self):
        for e in self.events:
            self.assertIn(e["rsvp"], RSVP_STATES)
            self.assertIsInstance(e["organizer_is_self"], bool)
            self.assertIsInstance(e["canceled"], bool)

    def test_some_invitation_reads_as_accepted(self):
        """The brief drops every event whose RSVP is not `accepted` (or
        `tentative`), so an EventKit or JXA regression that stopped resolving
        participantStatus would not fail loudly — it would quietly empty the
        briefing of exactly the meetings that matter most."""
        invited = [e for e in self.events
                   if any(a["is_self"] for a in e["attendees"])]
        if not invited:
            self.skipTest("no events invite you in the window")
        self.assertTrue(
            any(e["rsvp"] == "accepted" for e in invited),
            f"you are an attendee on {len(invited)} events but accepted none — "
            "participantStatus is not resolving, and the brief is now empty",
        )

    def test_range_is_honored(self):
        dates = {e["date"] for e in self.events}
        self.assertGreater(len(dates), 1, "range dump collapsed to a single day")


if __name__ == "__main__":
    unittest.main()
