import re
import unittest

from helpers import attendee, capture_logs, event, load, person

mb = load("dt-morning-brief.py", "dt_morning_brief")

ROOM_RE = re.compile(r"\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl", re.IGNORECASE)


class MdEnum(unittest.TestCase):
    def test_folds_case_space_and_underscore(self):
        for raw in ("family", "Family", " FAMILY "):
            self.assertEqual(mb.md_enum(raw), "family")
        for raw in ("close-friend", "Close Friend", "close_friend", "CLOSE  FRIEND"):
            self.assertEqual(mb.md_enum(raw), "close-friend")

    def test_blank(self):
        self.assertEqual(mb.md_enum(""), "")
        self.assertEqual(mb.md_enum(None), "")

    def test_every_canonical_key_is_its_own_fixed_point(self):
        for key in set(mb.RECONNECT_DAYS) | mb.RECONNECT_NEVER | mb.ENTITY_STATUSES:
            self.assertEqual(mb.md_enum(key), key)


class SkipAttendeePattern(unittest.TestCase):
    def test_default_matches_rooms_not_people(self):
        rooms = ["Durham - Wolfpack VC-Z 10 ppl", "Conference Room B", "Boardroom 12 ppl"]
        people = ["Anthony Fielding", "Tam Okafor", "Jonathan Marsh", "Vic Mackey",
                  "Aaron Brooks", "Victoria Cole"]
        for r in rooms:
            self.assertTrue(ROOM_RE.search(r), r)
        for p in people:
            self.assertIsNone(ROOM_RE.search(p), p)

    def test_default_constant_matches_the_documented_pattern(self):
        self.assertEqual(mb.DEFAULT_SKIP_ATTENDEE, ROOM_RE.pattern)


class RealAttendees(unittest.TestCase):
    def test_filters_self_nonperson_and_rooms(self):
        ev = event("UI Sync", [
            attendee("Alec Custer", "alec@x.com", is_self=True),
            attendee("Anthony Fielding", "a@x.com"),
            attendee("Durham - Wolfpack VC-Z 10 ppl", "room@x.com"),
            attendee("A Resource", "r@x.com", is_person=False),
            attendee("", ""),
        ])
        got = [a["name"] for a in mb.real_attendees(ev, ROOM_RE)]
        self.assertEqual(got, ["Anthony Fielding"])

    def test_email_only_attendee_is_kept(self):
        ev = event("x", [attendee("", "ghost@x.com")])
        self.assertEqual(len(mb.real_attendees(ev, ROOM_RE)), 1)

    def test_is_person_false_drops_everyone(self):
        """The JXA enum bug made is_person false for every human; this is the shape
        of that failure, so the filter must be the only thing keeping them out."""
        ev = event("x", [attendee("Anthony Fielding", "a@x.com", is_person=False)])
        self.assertEqual(mb.real_attendees(ev, ROOM_RE), [])


class ContactBumps(unittest.TestCase):
    def setUp(self):
        self.people = [person("Anthony Fielding", aliases="Anthony", email="a@x.com"),
                       person("Jake Pendry", aliases="Jake")]

    def test_matches_by_email_and_uses_event_date(self):
        ev = event("UI Sync", [attendee("Someone Else", "a@x.com")], date="2026-05-01")
        ops = mb.contact_bumps([ev], self.people, "2026-07-07", ROOM_RE)
        self.assertEqual(ops, [{"op": "bump_lastcontact",
                                "uuid": self.people[0]["uuid"], "date": "2026-05-01"}])

    def test_falls_back_to_day_when_event_has_no_date(self):
        ev = event("UI Sync", [attendee("Anthony Fielding")])
        del ev["date"]
        ops = mb.contact_bumps([ev], self.people, "2026-07-07", ROOM_RE)
        self.assertEqual(ops[0]["date"], "2026-07-07")

    def test_title_match_without_attendees(self):
        ev = event("Call with Jake", [])
        ops = mb.contact_bumps([ev], self.people, "2026-07-07", ROOM_RE)
        self.assertEqual(ops[0]["uuid"], self.people[1]["uuid"])

    def test_deduped_per_person_per_day(self):
        evs = [event("UI Sync", [attendee("Anthony Fielding")], date="2026-07-07"),
               event("Standup", [attendee("Anthony Fielding")], date="2026-07-07")]
        self.assertEqual(len(mb.contact_bumps(evs, self.people, "x", ROOM_RE)), 1)

    def test_same_person_on_two_days_yields_two_ops(self):
        evs = [event("a", [attendee("Anthony Fielding")], date="2026-07-06"),
               event("b", [attendee("Anthony Fielding")], date="2026-07-07")]
        self.assertEqual(len(mb.contact_bumps(evs, self.people, "x", ROOM_RE)), 2)

    def test_skips_all_day_declined_and_skip_calendars(self):
        cal = sorted(mb.SKIP_CALENDARS)[0]
        evs = [event("a", [attendee("Anthony Fielding")], all_day=True),
               event("b", [attendee("Anthony Fielding")], declined=True),
               event("c", [attendee("Anthony Fielding")], calendar=cal)]
        self.assertEqual(mb.contact_bumps(evs, self.people, "x", ROOM_RE), [])

    def test_room_never_bumps(self):
        ev = event("a", [attendee("Conference Room B", "cr@x.com")])
        self.assertEqual(mb.contact_bumps([ev], self.people, "x", ROOM_RE), [])

    def test_ambiguous_name_matches_nobody(self):
        people = [person("Jonathan Marsh", aliases="Jonathan"),
                  person("Jonathan Vega", aliases="Jonathan")]
        ev = event("a", [attendee("Jonathan")])
        self.assertEqual(mb.contact_bumps([ev], people, "x", ROOM_RE), [])


class BuildReconnect(unittest.TestCase):
    TODAY = "2026-07-09"

    def reconnect(self, people):
        with capture_logs(mb) as cap:
            out = mb.build_reconnect(people, self.TODAY)
        return out or "", cap.messages()

    def test_silent_statuses_never_surface(self):
        for status in ("dormant", "archived", "deceased", "Deceased"):
            people = [person("X", relationship="family", entitystatus=status)]
            out, warnings = self.reconnect(people)
            self.assertNotIn("X", out, status)
            self.assertEqual(warnings, [], status)

    def test_acquaintance_is_silent_without_warning(self):
        out, warnings = self.reconnect([person("X", relationship="acquaintance")])
        self.assertNotIn("X", out)
        self.assertEqual(warnings, [])

    def test_blank_relationship_is_silent_without_warning(self):
        out, warnings = self.reconnect([person("X")])
        self.assertNotIn("X", out)
        self.assertEqual(warnings, [])

    def test_unknown_relationship_warns_and_skips(self):
        out, warnings = self.reconnect([person("X", relationship="brother")])
        self.assertNotIn("X", out)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown Relationship", warnings[0])

    def test_unknown_status_warns_and_fails_open(self):
        out, warnings = self.reconnect(
            [person("X", relationship="family", entitystatus="activ")])
        self.assertIn("X", out)
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown EntityStatus", warnings[0])

    def test_folded_values_resolve(self):
        out, warnings = self.reconnect(
            [person("X", relationship="Close Friend", entitystatus="Active")])
        self.assertIn("X", out)
        self.assertEqual(warnings, [])

    def test_no_lastcontact_is_maximally_overdue(self):
        people = [person("Never", relationship="family"),
                  person("Stale", relationship="family", lastcontact="2026-01-01")]
        out, _ = self.reconnect(people)
        self.assertIn("no recorded contact", out)
        self.assertLess(out.index("Never"), out.index("Stale"))

    def test_threshold_boundary(self):
        # colleague = 90 days; strictly greater than the threshold surfaces.
        out, _ = self.reconnect(
            [person("Edge", relationship="colleague", lastcontact="2026-04-10")])
        self.assertNotIn("Edge", out)
        out, _ = self.reconnect(
            [person("Over", relationship="colleague", lastcontact="2026-04-09")])
        self.assertIn("Over", out)

    def test_malformed_lastcontact_warns_and_surfaces(self):
        # The field is free text; a hand-typed date must not hide the person.
        for raw in ("July 8, 2025", "2025-7-8", "not-a-date"):
            out, warnings = self.reconnect(
                [person("Bad", relationship="family", lastcontact=raw)])
            self.assertIn("Bad", out, raw)
            self.assertIn("no recorded contact", out, raw)
            self.assertEqual(len(warnings), 1, raw)
            self.assertIn("unparseable LastContact", warnings[0])

    def test_limit(self):
        people = [person(f"P{i}", relationship="family") for i in range(15)]
        out, _ = self.reconnect(people)
        self.assertEqual(out.count("\n- "), mb.RECONNECT_LIMIT)


class PersonSummaryLine(unittest.TestCase):
    def test_role_and_employer_combine(self):
        line = mb.person_summary_line(
            person("Bob", role="Architect", employer="Globex", city="Chicago",
                   lastcontact="2026-06-20"))
        self.assertIn("Architect at Globex · Chicago · last contact 2026-06-20", line)

    def test_employer_alone(self):
        self.assertIn("— Globex", mb.person_summary_line(person("Bob", employer="Globex")))

    def test_bare_person_has_no_trailing_separator(self):
        self.assertTrue(mb.person_summary_line(person("Bob")).endswith(")"))


class PersonIndexEmail(unittest.TestCase):
    def test_bare_and_mailto_stored_emails_both_match_attendees(self):
        """The Email field is url-typed in DT: scripts store bare addresses
        but a GUI edit can save mailto: — both must match calendar emails."""
        for stored in ("jane.doe@example.com",
                       "mailto:jane.doe@example.com",
                       "MAILTO:Jane.Doe@Example.com"):
            p = person("Jane Doe", email=stored)
            index = mb.person_index([p])
            self.assertIs(
                mb.match_person(index, "", "jane.doe@example.com"), p, stored)


class RecentLogBullets(unittest.TestCase):
    def body_person(self, *bullets):
        p = person("Bob")
        p["body"] = "# Bob\n\n## Biographical Log\n\n" + "\n".join(bullets)
        return p

    def test_selects_newest_by_date_not_append_order(self):
        """A backlog drain appends old facts after current ones; the brief
        must not surface 2024 facts as 'recent'."""
        p = self.body_person(
            "- 2026-06-01 — new job.",
            "- 2026-07-01 — moved.",
            "- 2024-01-01 — backfilled old fact.",
            "- 2024-02-01 — another old fact.",
        )
        got = mb.recent_log_bullets(p, limit=2)
        self.assertEqual([ln.strip()[2:12] for ln in got],
                         ["2026-06-01", "2026-07-01"])

    def test_renders_in_document_order(self):
        p = self.body_person(
            "- 2026-07-01 — later fact filed first.",
            "- 2026-06-01 — earlier fact filed second.",
        )
        got = mb.recent_log_bullets(p, limit=2)
        self.assertEqual([ln.strip()[2:12] for ln in got],
                         ["2026-07-01", "2026-06-01"])

    def test_skips_created_marker_and_non_bullets(self):
        p = self.body_person(
            "- 2026-01-01 — Created.",
            "prose line",
            "- 2026-06-01 — real fact.",
        )
        self.assertEqual(len(mb.recent_log_bullets(p)), 1)

    def test_strips_fact_provenance_markers(self):
        p = self.body_person(
            "- 2026-06-01 — moved. ([source](x-devonthink-item://S))"
            " <!-- fact:ab12cd34 -->",
        )
        got = mb.recent_log_bullets(p)
        self.assertEqual(len(got), 1)
        self.assertNotIn("fact:", got[0])
        self.assertIn("moved.", got[0])


if __name__ == "__main__":
    unittest.main()
