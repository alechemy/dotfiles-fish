import json
import logging
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import attendee, capture_logs, contact, event, load, person

mb = load("dt-morning-brief.py", "dt_morning_brief")

import entity_candidates as ec

ROOM_RE = re.compile(r"\bVC\b|\bConference\b|\bRoom\b|\d+\s?ppl", re.IGNORECASE)


def backlog(pending=0, approved=0, parked=None,
            review_uuid="REV", approved_uuid="APR"):
    return {"pending": pending, "approved": approved, "parked": parked or {},
            "review_uuid": review_uuid, "approved_uuid": approved_uuid}


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


class CalendarContexts(unittest.TestCase):
    def test_matches_calendar_title_or_stable_identifier(self):
        contexts = mb.load_calendar_contexts({
            "PERSONAL_CALENDARS": "Home",
            "WORK_CALENDARS": "cal-work,source-corp",
        })
        self.assertEqual(
            mb.event_context(event("x", calendar="Home"), contexts), "personal")
        self.assertEqual(
            mb.event_context(event("x", calendar_id="cal-work"), contexts), "work")
        self.assertEqual(
            mb.event_context(event("x", source_id="source-corp"), contexts), "work")

    def test_unclassified_calendar_is_neutral(self):
        self.assertEqual(
            mb.event_context(event("x"), mb.load_calendar_contexts({})), "neutral")

    def test_overlapping_selectors_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "both personal and work"):
            mb.load_calendar_contexts({
                "PERSONAL_CALENDARS": "shared",
                "WORK_CALENDARS": "shared",
            })


class SkipCalendars(unittest.TestCase):
    def test_matches_calendar_title_or_stable_identifier(self):
        cals = {"Shared", "cal-shared", "source-shared"}
        for ev in (event("x", calendar="Shared"),
                   event("x", calendar_id="cal-shared"),
                   event("x", source_id="source-shared")):
            self.assertTrue(mb.skipped(ev, cals))
        self.assertFalse(mb.skipped(event("x", calendar="Other"), cals))

    def test_a_renamed_calendar_still_skips_by_identifier(self):
        ev = event("x", calendar="New Name", calendar_id="cal-shared")
        self.assertTrue(mb.skipped(ev, {"cal-shared"}))

    def test_matching_is_normalized_both_ways(self):
        self.assertTrue(mb.skipped(event("x", calendar="SHARED"), {"shared"}))
        self.assertTrue(mb.skipped(event("x", calendar="shared"), {"SHARED"}))


class PersonResolutionEvidence(unittest.TestCase):
    def setUp(self):
        self.person = person("Jordan Vale", aliases="Jordan", email="jv@x.com")
        self.index = mb.person_index([self.person])

    def test_email_is_strongest(self):
        got = mb.resolve_person(self.index, "Someone Else", "jv@x.com")
        self.assertIs(got["person"], self.person)
        self.assertEqual(got["evidence"], "email")
        self.assertTrue(got["strong"])

    def test_structured_full_name_is_strong(self):
        got = mb.resolve_person(self.index, "Jordan Vale", "")
        self.assertEqual(got["evidence"], "full-name")
        self.assertTrue(got["strong"])

    def test_bare_alias_is_weak(self):
        got = mb.resolve_person(self.index, "Jordan", "")
        self.assertEqual(got["evidence"], "bare-name")
        self.assertFalse(got["strong"])


class RealAttendees(unittest.TestCase):
    def test_filters_self_nonperson_and_rooms(self):
        ev = event("UI Sync", [
            attendee("Sam Doe", "sam@x.com", is_self=True),
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

    def test_skips_all_day_unanswered_and_skip_calendars(self):
        cal = sorted(mb.SKIP_CALENDARS)[0]
        evs = [event("a", [attendee("Anthony Fielding")], all_day=True),
               event("b", [attendee("Anthony Fielding")], rsvp="unknown"),
               event("c", [attendee("Anthony Fielding")], calendar=cal)]
        self.assertEqual(mb.contact_bumps(evs, self.people, "x", ROOM_RE), [])

    def test_an_event_you_never_accepted_is_not_contact(self):
        """Sitting in an invite you ignored is not evidence you met someone."""
        for rsvp in ("unknown", "pending", "declined", None):
            with self.subTest(rsvp=rsvp):
                ev = event("a", [attendee("Anthony Fielding")], rsvp=rsvp)
                self.assertEqual(
                    mb.contact_bumps([ev], self.people, "x", ROOM_RE), [])

    def test_tentative_still_bumps(self):
        ev = event("a", [attendee("Anthony Fielding")], rsvp="tentative")
        self.assertEqual(len(mb.contact_bumps([ev], self.people, "x", ROOM_RE)), 1)

    def test_a_cancelled_meeting_is_not_contact(self):
        ev = event("a", [attendee("Anthony Fielding")], canceled=True)
        self.assertEqual(mb.contact_bumps([ev], self.people, "x", ROOM_RE), [])

    def test_room_never_bumps(self):
        ev = event("a", [attendee("Conference Room B", "cr@x.com")])
        self.assertEqual(mb.contact_bumps([ev], self.people, "x", ROOM_RE), [])

    def test_ambiguous_name_matches_nobody(self):
        people = [person("Jonathan Marsh", aliases="Jonathan"),
                  person("Jonathan Vega", aliases="Jonathan")]
        ev = event("a", [attendee("Jonathan")])
        self.assertEqual(mb.contact_bumps([ev], people, "x", ROOM_RE), [])


class IdentityProvenance(unittest.TestCase):
    def setUp(self):
        self.person = person("Jordan Vale", aliases="Jordan", email="jv@x.com")
        self.people = [self.person]
        self.contexts = mb.load_calendar_contexts({
            "PERSONAL_CALENDARS": "Home",
            "WORK_CALENDARS": "Work",
        })

    def observations(self, events):
        return mb.calendar_observations(
            events, self.people, ROOM_RE, self.contexts, set())

    def test_only_strong_identity_evidence_teaches_context(self):
        strong = event("Review", [attendee("Someone Else", "jv@x.com")],
                       calendar="Work", event_id="event-1")
        weak = event("Call with Jordan", calendar="Work", event_id="event-2")
        got = self.observations([strong, weak])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["person_uuid"], self.person["uuid"])
        self.assertEqual(got[0]["context"], "work")
        self.assertEqual(got[0]["evidence"], "email")

    def test_recording_is_idempotent_and_context_conflict_needs_history(self):
        state = mb.empty_identity_provenance()
        events = [
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-1"),
            event("Planning", [attendee("Someone Else", "jv@x.com")],
                  calendar="Work", event_id="event-2"),
        ]
        observations = self.observations(events)
        self.assertTrue(mb.record_calendar_observations(state, observations))
        self.assertFalse(mb.record_calendar_observations(state, observations))
        self.assertEqual(
            mb.context_counts(state, self.person["uuid"]), {"work": 2})
        self.assertTrue(
            mb.context_conflicts(state, self.person["uuid"], "personal"))
        self.assertFalse(
            mb.context_conflicts(state, self.person["uuid"], "work"))
        self.assertFalse(
            mb.context_conflicts(state, self.person["uuid"], "neutral"))

    def test_recurring_occurrences_count_separately(self):
        state = mb.empty_identity_provenance()
        observations = self.observations([
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  date="2026-07-06", event_id="series-1"),
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  date="2026-07-13", event_id="series-1"),
        ])
        mb.record_calendar_observations(state, observations)
        self.assertEqual(
            mb.context_counts(state, self.person["uuid"]), {"work": 2})

    def test_one_observation_does_not_create_a_hard_context_boundary(self):
        state = mb.empty_identity_provenance()
        observations = self.observations([
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-1")
        ])
        mb.record_calendar_observations(state, observations)
        self.assertFalse(
            mb.context_conflicts(state, self.person["uuid"], "personal"))

    def test_context_conflict_blocks_only_weak_title_evidence(self):
        state = mb.empty_identity_provenance()
        mb.record_calendar_observations(state, self.observations([
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-1"),
            event("Planning", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-2"),
        ]))
        self.assertEqual(
            mb.title_matches(self.people, "Call with Jordan", "personal", state), [])
        self.assertEqual(
            [p["name"] for p in mb.title_matches(
                self.people, "Call with Jordan", "work", state)],
            ["Jordan Vale"])
        self.assertEqual(
            [p["name"] for p in mb.title_matches(
                self.people, "Call with Jordan Vale", "personal", state)],
            ["Jordan Vale"])

    def test_context_conflict_is_visible_and_cannot_bump_lastcontact(self):
        state = mb.empty_identity_provenance()
        mb.record_calendar_observations(state, self.observations([
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-1"),
            event("Planning", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-2"),
        ]))
        personal = event("Call with Jordan", calendar="Home", event_id="event-3")
        blocks = mb.brief_blocks(
            [personal], self.people, ROOM_RE, set(), set(), (), self.contexts,
            state)
        self.assertEqual(blocks[0]["people"], [])
        self.assertIn("identity unresolved", blocks[0]["warnings"][0])
        self.assertEqual(
            mb.contact_bumps(
                [personal], self.people, "2026-07-13", ROOM_RE, set(), set(),
                self.contexts, state),
            [])

    def test_exact_email_overrides_a_context_mismatch(self):
        state = mb.empty_identity_provenance()
        mb.record_calendar_observations(state, self.observations([
            event("Review", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-1"),
            event("Planning", [attendee("Jordan Vale")], calendar="Work",
                  event_id="event-2"),
        ]))
        personal = event(
            "Dinner", [attendee("Someone Else", "jv@x.com")],
            calendar="Home", event_id="event-3")
        got = mb.contact_bumps(
            [personal], self.people, "2026-07-13", ROOM_RE, set(), set(),
            self.contexts, state)
        self.assertEqual(got[0]["uuid"], self.person["uuid"])


class CalendarPersonCandidates(unittest.TestCase):
    def setUp(self):
        self.contexts = mb.load_calendar_contexts({
            "PERSONAL_CALENDARS": "Home",
            "WORK_CALENDARS": "Work",
        })

    def candidates(self, events, people=(), contacts=(), provenance=None):
        return mb.calendar_person_candidates(
            events, list(people), list(contacts), ROOM_RE, self.contexts, set(),
            mb.SKIP_CALENDARS, provenance)

    def test_unmatched_structured_attendee_is_a_candidate(self):
        got = self.candidates([
            event("Lease review",
                  [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-1")
        ])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["name"], "Jordan Pike")
        self.assertEqual(got[0]["email"], "jp@x.com")
        self.assertEqual(got[0]["evidence"], "calendar attendee")
        self.assertEqual(got[0]["context"], "personal")

    def test_existing_person_is_not_a_candidate(self):
        people = [person("Jordan Pike", email="jp@x.com")]
        got = self.candidates([
            event("Lease review",
                  [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-1")
        ], people)
        self.assertEqual(got, [])

    def test_ambiguous_attendee_key_is_not_a_candidate(self):
        """resolve_person returns None for both a plain miss and an ambiguous
        multi-hit key; a candidate must not be manufactured for the latter."""
        people = [person("Casey Doyle", uuid="P1"), person("Casey Doyle", uuid="P2")]
        got = self.candidates([
            event("Lease review", [attendee("Casey Doyle")],
                  calendar="Home", event_id="event-1")
        ], people)
        self.assertEqual(got, [])

    def test_title_full_name_requires_contacts_corroboration(self):
        ev = event("Meeting with Jordan Pike", calendar="Home",
                   event_id="event-1")
        self.assertEqual(self.candidates([ev]), [])
        got = self.candidates([ev], contacts=[contact("Jordan Pike")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"], "calendar title + Contacts")

    def test_title_parser_does_not_propose_uncorroborated_nouns(self):
        ev = event("Meeting with Project Hail Mary", calendar="Home",
                   event_id="event-1")
        self.assertEqual(self.candidates([ev]), [])

    def test_unique_contact_can_resolve_a_bare_personal_title(self):
        people = [
            person("Jordan Vale", aliases="Jordan"),
            person("Jordan Reyes", aliases="Jordan"),
        ]
        provenance = mb.empty_identity_provenance()
        for p in people:
            mb.record_calendar_observations(provenance, [
                {"id": f"{p['uuid']}-1", "person_uuid": p["uuid"],
                 "context": "work", "date": "2026-07-01",
                 "evidence": "full-name"},
                {"id": f"{p['uuid']}-2", "person_uuid": p["uuid"],
                 "context": "work", "date": "2026-07-08",
                 "evidence": "full-name"},
            ])
        ev = event("Meet with Jordan", calendar="Home", event_id="event-1")
        got = self.candidates(
            [ev], people, [contact("Jordan Pike")], provenance)
        self.assertEqual([candidate["name"] for candidate in got], ["Jordan Pike"])
        self.assertEqual(
            got[0]["evidence"], "calendar title + unique Contacts name")

    def test_bare_title_does_not_create_a_candidate_without_context_conflict(self):
        people = [person("Jordan Vale", aliases="Jordan")]
        ev = event("Meet with Jordan", calendar="Home", event_id="event-1")
        self.assertEqual(
            self.candidates([ev], people, [contact("Jordan Pike")]), [])

    def test_same_identity_is_deduplicated_across_events(self):
        events = [
            event("Lease review", [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-1"),
            event("Inspection", [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-2"),
        ]
        self.assertEqual(len(self.candidates(events)), 1)

    def test_title_and_attendee_evidence_share_one_candidate_identity(self):
        events = [
            event("Meeting with Jordan Pike", calendar="Home",
                  event_id="event-1"),
            event("Inspection", [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-2"),
        ]
        got = self.candidates(events, contacts=[contact("Jordan Pike")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"], "calendar attendee")
        self.assertEqual(got[0]["email"], "jp@x.com")

    def test_candidates_carry_a_stable_event_fingerprint(self):
        got = self.candidates([
            event("Lease review",
                  [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-1")
        ])
        again = self.candidates([
            event("Lease review",
                  [attendee("Jordan Pike", "jp@x.com")],
                  calendar="Home", event_id="event-1")
        ])
        self.assertTrue(got[0]["sid"].startswith("cal:"))
        self.assertEqual(got[0]["sid"], again[0]["sid"])


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


class MatchContact(unittest.TestCase):
    def index(self, *people):
        return mb.person_index(list(people))

    def test_email_beats_name(self):
        by_email = person("Jake Pendry", email="jake@x.com")
        by_name = person("Jake Old")
        index = self.index(by_email, by_name)
        c = contact("Jake Old", emails=["jake@x.com"])
        self.assertIs(mb.match_contact(index, c), by_email)

    def test_matches_by_name_and_by_nickname_alias(self):
        p = person("Jacob Pendry", aliases="Jake")
        index = self.index(p)
        self.assertIs(mb.match_contact(index, contact("Jacob Pendry")), p)
        self.assertIs(mb.match_contact(index, contact("J. D.", nickname="Jake")), p)

    def test_ambiguous_key_is_no_match(self):
        index = self.index(person("Jonathan Marsh", aliases="Jonathan"),
                           person("Jonathan Vega", aliases="Jonathan"))
        self.assertIsNone(mb.match_contact(index, contact("Jonathan")))

    def test_unmatched_contact(self):
        index = self.index(person("Jake Pendry"))
        self.assertIsNone(mb.match_contact(index, contact("Someone Else")))


class BirthdayOccurrence(unittest.TestCase):
    def occ(self, month, day, start, lookahead=14):
        return mb.birthday_occurrence(month, day, mb.date.fromisoformat(start),
                                      lookahead)

    def test_window_boundaries(self):
        self.assertEqual(str(self.occ(7, 11, "2026-07-11")), "2026-07-11")
        self.assertEqual(str(self.occ(7, 25, "2026-07-11")), "2026-07-25")
        self.assertIsNone(self.occ(7, 26, "2026-07-11"))

    def test_wraps_year_end(self):
        self.assertEqual(str(self.occ(1, 3, "2026-12-28")), "2027-01-03")

    def test_feb29_in_leap_year(self):
        self.assertEqual(str(self.occ(2, 29, "2028-02-20")), "2028-02-29")

    def test_feb29_falls_on_feb28_in_non_leap_year(self):
        self.assertEqual(str(self.occ(2, 29, "2026-02-20")), "2026-02-28")

    def test_feb28_birthday_unaffected_by_leap_rule(self):
        self.assertEqual(str(self.occ(2, 28, "2028-02-20")), "2028-02-28")


class BuildBirthdays(unittest.TestCase):
    TODAY = "2026-07-11"

    def build(self, contacts, people, today=TODAY):
        return mb.build_birthdays(contacts, people, today) or ""

    def test_roster_matched_birthday_renders_with_link_and_marker(self):
        p = person("Jake Pendry")
        out = self.build([contact("Jake Pendry",
                                  birthday={"month": 7, "day": 15})], [p])
        self.assertIn(f"<!-- birthdays:{self.TODAY} -->", out)
        self.assertIn(f"- 2026-07-15 — [Jake Pendry](x-devonthink-item://"
                      f"{p['uuid']}) — birthday", out)

    def test_unmatched_contact_never_surfaces(self):
        out = self.build([contact("Stranger", birthday={"month": 7, "day": 12})],
                         [person("Jake Pendry")])
        self.assertEqual(out, "")

    def test_age_from_year(self):
        out = self.build([contact("Jake Pendry",
                                  birthday={"month": 7, "day": 15, "year": 1986})],
                         [person("Jake Pendry")])
        self.assertIn("turns 40", out)

    def test_sentinel_year_gets_no_age(self):
        for year in (1604, 5, 2100):
            out = self.build([contact("Jake Pendry",
                                      birthday={"month": 7, "day": 15, "year": year})],
                             [person("Jake Pendry")])
            self.assertIn("— birthday", out, year)
            self.assertNotIn("turns", out, year)

    def test_age_uses_occurrence_year_across_new_year(self):
        out = self.build([contact("Jake Pendry",
                                  birthday={"month": 1, "day": 3, "year": 1990})],
                         [person("Jake Pendry")], today="2026-12-28")
        self.assertIn("- 2027-01-03", out)
        self.assertIn("turns 37", out)

    def test_today_is_flagged(self):
        out = self.build([contact("Jake Pendry",
                                  birthday={"month": 7, "day": 11})],
                         [person("Jake Pendry")])
        self.assertIn("(today!)", out)

    def test_outside_window_is_silent(self):
        out = self.build([contact("Jake Pendry",
                                  birthday={"month": 7, "day": 26})],
                         [person("Jake Pendry")])
        self.assertEqual(out, "")

    def test_sorted_by_date(self):
        people = [person("Aaron Brooks"), person("Jake Pendry")]
        out = self.build(
            [contact("Jake Pendry", birthday={"month": 7, "day": 13}),
             contact("Aaron Brooks", birthday={"month": 7, "day": 20})], people)
        self.assertLess(out.index("Jake Pendry"), out.index("Aaron Brooks"))

    def test_two_cards_for_one_person_yield_one_line(self):
        p = person("Jake Pendry", email="jake@x.com")
        out = self.build(
            [contact("Jake Pendry", birthday={"month": 7, "day": 15}),
             contact("Jakey", emails=["jake@x.com"],
                     birthday={"month": 7, "day": 15})], [p])
        self.assertEqual(out.count("Jake Pendry"), 1)

    def test_birthdayless_and_partial_cards_are_ignored(self):
        out = self.build(
            [contact("Jake Pendry"),
             contact("Jake Pendry", birthday={"month": 7}),
             contact("Jake Pendry", birthday={"day": 15})],
            [person("Jake Pendry")])
        self.assertEqual(out, "")


class AppleTimestamps(unittest.TestCase):
    def test_local_midnight_roundtrip(self):
        self.assertEqual(
            mb.apple_ts_to_local_date(mb.apple_ns("2026-07-10")), "2026-07-10")

    def test_legacy_seconds_rows_agree_with_nanoseconds(self):
        ns = mb.apple_ns("2026-07-10")
        self.assertEqual(mb.apple_ts_to_local_date(ns),
                         mb.apple_ts_to_local_date(ns // 1_000_000_000))


class NormHandle(unittest.TestCase):
    def test_phone_formatting_variants_fold_together(self):
        for raw in ("+12125550142", "(212) 555-0142", "212-555-0142",
                    "1 212 555 0142"):
            self.assertEqual(mb.norm_handle(raw), "2125550142", raw)

    def test_email_casefolds(self):
        self.assertEqual(mb.norm_handle("Jake@X.com"), "jake@x.com")

    def test_short_code_passes_through(self):
        self.assertEqual(mb.norm_handle("87892"), "87892")

    def test_empty(self):
        self.assertEqual(mb.norm_handle(""), "")
        self.assertEqual(mb.norm_handle(None), "")


class HandleIndex(unittest.TestCase):
    def test_matched_card_maps_phones_and_emails(self):
        p = person("Jake Pendry")
        index = mb.handle_index(
            [contact("Jake Pendry", phones=["(212) 555-0142"],
                     emails=["Jake@X.com"])], [p])
        self.assertIs(index["2125550142"], p)
        self.assertIs(index["jake@x.com"], p)

    def test_unmatched_card_contributes_nothing(self):
        index = mb.handle_index(
            [contact("Stranger", phones=["2125550142"])],
            [person("Jake Pendry")])
        self.assertEqual(index, {})

    def test_handle_claimed_by_two_people_is_dropped(self):
        people = [person("Marisa Voss"), person("Martin Voss")]
        index = mb.handle_index(
            [contact("Marisa Voss", phones=["212-555-0142"]),
             contact("Martin Voss", phones=["+12125550142"])], people)
        self.assertNotIn("2125550142", index)

    def test_two_cards_for_one_person_do_not_collide(self):
        p = person("Jake Pendry", email="jake@x.com")
        index = mb.handle_index(
            [contact("Jake Pendry", phones=["212-555-0142"]),
             contact("Jakey", emails=["jake@x.com"], phones=["2125550142"])],
            [p])
        self.assertIs(index["2125550142"], p)


class MessageBumps(unittest.TestCase):
    def test_newest_date_across_handles_wins(self):
        p = person("Jake Pendry")
        index = mb.handle_index(
            [contact("Jake Pendry", phones=["212-555-0142"],
                     emails=["jake@x.com"])], [p])
        ops = mb.message_bumps(
            [("+12125550142", mb.apple_ns("2026-07-08")),
             ("jake@x.com", mb.apple_ns("2026-07-09"))], index)
        self.assertEqual(ops, [{"op": "bump_lastcontact",
                                "uuid": p["uuid"], "date": "2026-07-09"}])

    def test_unknown_handles_are_ignored(self):
        index = mb.handle_index(
            [contact("Jake Pendry", phones=["212-555-0142"])],
            [person("Jake Pendry")])
        ops = mb.message_bumps(
            [("87892", mb.apple_ns("2026-07-09")),
             ("stranger@x.com", mb.apple_ns("2026-07-09"))], index)
        self.assertEqual(ops, [])


class DurableBumps(unittest.TestCase):
    def test_drops_ops_dated_today_or_later(self):
        ops = [{"op": "bump_lastcontact", "uuid": "u1", "date": "2026-07-14"},
               {"op": "bump_lastcontact", "uuid": "u2", "date": "2026-07-15"}]
        self.assertEqual(mb.durable_bumps(ops, "2026-07-15"), [ops[0]])

    def test_keeps_everything_strictly_before_today(self):
        ops = [{"op": "bump_lastcontact", "uuid": "u1", "date": "2026-07-01"}]
        self.assertEqual(mb.durable_bumps(ops, "2026-07-15"), ops)


class NovelBumps(unittest.TestCase):
    def test_drops_ops_that_would_not_raise_the_stored_value(self):
        p = person("Jake Pendry", lastcontact="2026-07-14")
        ops = [{"op": "bump_lastcontact", "uuid": p["uuid"], "date": "2026-07-14"}]
        self.assertEqual(mb.novel_bumps(ops, [p]), [])

    def test_keeps_ops_that_would_raise_it(self):
        p = person("Jake Pendry", lastcontact="2026-07-10")
        ops = [{"op": "bump_lastcontact", "uuid": p["uuid"], "date": "2026-07-14"}]
        self.assertEqual(mb.novel_bumps(ops, [p]), ops)

    def test_no_stored_lastcontact_always_raises(self):
        p = person("Jake Pendry")
        ops = [{"op": "bump_lastcontact", "uuid": p["uuid"], "date": "2026-07-14"}]
        self.assertEqual(mb.novel_bumps(ops, [p]), ops)

    def test_op_for_a_person_not_in_the_roster_is_kept(self):
        ops = [{"op": "bump_lastcontact", "uuid": "ghost", "date": "2026-07-14"}]
        self.assertEqual(mb.novel_bumps(ops, []), ops)


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


class NewsBullets(unittest.TestCase):
    def body_person(self, *bullets, **kw):
        p = person("Bob", **kw)
        p["body"] = "# Bob\n\n## Biographical Log\n\n" + "\n".join(bullets)
        return p

    def news(self, p, told=None, **kw):
        return mb.news_bullets(p, set() if told is None else told, **kw)

    def test_selects_newest_by_date_not_append_order(self):
        """A backlog drain appends old facts after current ones; the brief
        must not surface 2024 facts as 'recent'."""
        p = self.body_person(
            "- 2026-06-01 — new job.",
            "- 2026-07-01 — moved.",
            "- 2024-01-01 — backfilled old fact.",
            "- 2024-02-01 — another old fact.",
        )
        got = self.news(p, limit=2)
        self.assertEqual([ln.strip()[2:12] for ln in got],
                         ["2026-06-01", "2026-07-01"])

    def test_renders_in_document_order(self):
        p = self.body_person(
            "- 2026-07-01 — later fact filed first.",
            "- 2026-06-01 — earlier fact filed second.",
        )
        got = self.news(p, limit=2)
        self.assertEqual([ln.strip()[2:12] for ln in got],
                         ["2026-07-01", "2026-06-01"])

    def test_skips_created_marker_and_non_bullets(self):
        p = self.body_person(
            "- 2026-01-01 — Created.",
            "prose line",
            "- 2026-06-01 — real fact.",
        )
        self.assertEqual(len(self.news(p)), 1)

    def test_strips_fact_provenance_markers(self):
        p = self.body_person(
            "- 2026-06-01 — moved. ([source](x-devonthink-item://S))"
            " <!-- fact:ab12cd34 -->",
        )
        got = self.news(p)
        self.assertEqual(len(got), 1)
        self.assertNotIn("fact:", got[0])
        self.assertIn("moved.", got[0])

    def test_a_fact_older_than_your_last_meeting_is_not_news(self):
        """The complaint that started this: an April note about a colleague you
        sit with weekly, resurfacing every week in July."""
        p = self.body_person("- 2026-04-16 — took a sick day.",
                             lastcontact="2026-07-10")
        self.assertEqual(self.news(p), [])

    def test_a_fact_filed_since_you_last_met_is_news(self):
        p = self.body_person("- 2026-07-13 — moved to Denver.",
                             lastcontact="2026-07-10")
        self.assertEqual(len(self.news(p)), 1)

    def test_a_fact_from_the_day_you_last_met_is_still_news(self):
        """It came out of that meeting; you have not been told it yet."""
        p = self.body_person("- 2026-07-10 — is changing teams.",
                             lastcontact="2026-07-10")
        self.assertEqual(len(self.news(p)), 1)

    def test_someone_you_have_never_met_has_no_old_news(self):
        p = self.body_person("- 2024-01-01 — founded the company.")
        self.assertEqual(len(self.news(p)), 1)

    def test_an_unparseable_last_contact_hides_nothing(self):
        p = self.body_person("- 2024-01-01 — founded the company.",
                             lastcontact="last spring")
        self.assertEqual(len(self.news(p)), 1)

    def test_a_fact_already_told_today_is_not_told_again(self):
        """The same fact under both meetings a person attends today."""
        p = self.body_person("- 2026-07-13 — moved to Denver.",
                             lastcontact="2026-07-10")
        told = set()
        self.assertEqual(len(self.news(p, told)), 1)
        self.assertEqual(self.news(p, told), [])

    def test_one_fact_filed_to_two_people_is_told_once(self):
        marker = " <!-- fact:ab12cd34 -->"
        told = set()
        a = self.body_person("- 2026-07-13 — closed the Acme deal." + marker,
                             lastcontact="2026-07-10")
        b = self.body_person("- 2026-07-13 — closed the Acme deal." + marker,
                             lastcontact="2026-07-10")
        self.assertEqual(len(self.news(a, told)), 1)
        self.assertEqual(self.news(b, told), [])


class MessageBumpRetryComposition(unittest.TestCase):
    """C01: a same-day text falls inside "since yesterday"'s query window,
    so it must not durably promote LastContact past yesterday — that would
    outrun apply_bumps' in-memory cutoff and hide the fact on the next
    launchd retry, which rebuilds the Briefing section from a fresh DT read."""

    def test_yesterdays_fact_survives_a_same_day_text_across_runs(self):
        run_n_today = "2026-07-15"
        yesterday = "2026-07-14"
        fact = f"- {yesterday} — closed the Meridian deal."

        def roster(lastcontact):
            p = person("Priya Raman", lastcontact=lastcontact)
            p["body"] = "# Priya Raman\n\n## Biographical Log\n\n" + fact
            return p

        contacts = [contact("Priya Raman", emails=["priya@x.com"])]
        index = mb.handle_index(contacts, [roster("2026-07-01")])
        raw_mops = mb.message_bumps(
            [("priya@x.com", mb.apple_ns(run_n_today))], index)
        self.assertEqual(raw_mops[0]["date"], run_n_today)

        p_n = roster("2026-07-01")
        mb.apply_bumps([p_n], raw_mops, run_n_today)
        self.assertEqual(mb.news_bullets(p_n, set()), ["  " + fact])

        durable = mb.durable_bumps(raw_mops, run_n_today)
        dt_lastcontact = "2026-07-01"
        for op in durable:
            if op["date"] > dt_lastcontact:
                dt_lastcontact = op["date"]

        p_n1 = roster(dt_lastcontact)
        self.assertEqual(
            mb.news_bullets(p_n1, set()), ["  " + fact],
            "a same-day text must not durably promote LastContact past "
            "yesterday, or the next retry loses a fact this run already showed")


class RenderReview(unittest.TestCase):
    TODAY = "2026-07-09"

    def test_pending_links_to_the_review_group(self):
        got = mb.render_review(backlog(pending=2), self.TODAY)
        self.assertIn(
            "- 2 filing proposals awaiting review in "
            "[20_ENTITIES/_Review](x-devonthink-item://REV)", got)

    def test_approved_links_to_the_approved_group(self):
        got = mb.render_review(backlog(approved=1), self.TODAY)
        self.assertIn(
            "- 1 approved proposal in "
            "[20_ENTITIES/_Review/Approved](x-devonthink-item://APR) "
            "did not apply", got)

    def test_missing_uuid_degrades_to_the_bare_path(self):
        got = mb.render_review(backlog(pending=1, review_uuid=None), self.TODAY)
        self.assertIn("awaiting review in `20_ENTITIES/_Review`", got)
        self.assertNotIn("x-devonthink-item://None", got)

    def test_empty_backlog_renders_nothing(self):
        self.assertIsNone(mb.render_review(backlog(), self.TODAY))
        self.assertIsNone(mb.render_review(None, self.TODAY))


class BuildSnapshot(unittest.TestCase):
    TODAY = "2026-07-09"

    def snap(self, blocks=(), overdue=(), bdays=(), backlog=None,
             journal_info=None, otd=None):
        return mb.build_snapshot(self.TODAY, list(blocks), list(overdue),
                                 list(bdays), backlog, journal_info, otd)

    def test_meetings_carry_people_and_unmatched(self):
        people = [person("Bob Marsh", role="Architect", employer="Globex",
                         city="Chicago", lastcontact="2026-06-20",
                         email="bob@x.com")]
        ev = event("UI Sync", [attendee("Bob Marsh", "bob@x.com"),
                               attendee("Ghost Person", "g@x.com")],
                   date=self.TODAY)
        blocks = mb.brief_blocks([ev], people, ROOM_RE)
        snap = self.snap(blocks=blocks)
        m = snap["meetings"][0]
        self.assertEqual(m["time"], "9:00am")
        self.assertEqual(m["title"], "UI Sync")
        self.assertEqual(m["people"], [
            {"name": "Bob Marsh", "role": "Architect", "employer": "Globex",
             "city": "Chicago", "last": "2026-06-20"}])
        self.assertEqual(m["unmatched"], ["Ghost Person (g@x.com)"])

    def test_person_snapshot_omits_empty_fields(self):
        self.assertEqual(mb.person_snapshot(person("Bob")), {"name": "Bob"})

    def test_reconnect_capped_with_null_days_for_never_contacted(self):
        people = [person(f"P{i}", relationship="family") for i in range(15)]
        snap = self.snap(overdue=mb.reconnect_overdue(people, self.TODAY))
        self.assertEqual(len(snap["reconnect"]), mb.RECONNECT_LIMIT)
        self.assertIsNone(snap["reconnect"][0]["days"])
        self.assertEqual(snap["reconnect"][0]["relationship"], "family")

    def test_birthday_today_flag_and_date(self):
        rows = mb.birthday_rows(
            [contact("Jake Pendry", birthday={"month": 7, "day": 9})],
            [person("Jake Pendry")], self.TODAY)
        snap = self.snap(bdays=rows)
        self.assertEqual(snap["birthdays"], [
            {"date": self.TODAY, "name": "Jake Pendry", "age": None,
             "today": True}])

    def test_review_counts_parked_dict_becomes_count(self):
        snap = self.snap(backlog=backlog(pending=2, approved=1,
                                         parked={"u1": {}, "u2": {}}))
        self.assertEqual(snap["review"],
                         {"pending": 2, "approved": 1, "parked": 2})

    def test_review_none_when_backlog_unknown(self):
        self.assertIsNone(self.snap()["review"])

    def test_journal_state_token(self):
        cases = [({"pending": 2, "parked": 0, "staged": 0}, "pending"),
                 ({"pending": 0, "parked": 0, "staged": 1}, "pending"),
                 ({"pending": 0, "parked": 3, "staged": 0}, "parked"),
                 ({"pending": 0, "parked": 0, "staged": 0}, "missing")]
        for info, expected in cases:
            snap = self.snap(journal_info=info)
            self.assertEqual(snap["journal"]["state"], expected, info)
        self.assertIsNone(self.snap()["journal"])

    def test_on_this_day_appends_last_years_daily_note(self):
        otd = ([{"years": 2, "name": "Trip", "uuid": "U", "kind": "markdown"}],
               {"name": "2025-07-09", "uuid": "D"})
        snap = self.snap(otd=otd)
        self.assertEqual(snap["on_this_day"], [
            {"years": 2, "name": "Trip", "kind": "markdown"},
            {"years": 1, "name": "2025-07-09", "kind": "daily note"}])

    def test_snapshot_is_json_serializable(self):
        import json
        people = [person("Bob Marsh")]
        ev = event("Sync", [attendee("Bob Marsh")], date=self.TODAY)
        snap = self.snap(blocks=mb.brief_blocks([ev], people, ROOM_RE),
                         overdue=mb.reconnect_overdue(
                             [person("X", relationship="family")], self.TODAY))
        json.dumps(snap)


class JournalStatusLines(unittest.TestCase):
    TODAY = "2026-07-11"

    def state(self, entry_dates, pages=()):
        return {"notebooks": {"2026 Journal": {
            "entries": {d: {"uuid": "U", "text_sha": "S"} for d in entry_dates},
            "pages": list(pages),
        }}}

    def test_silent_before_first_entry(self):
        self.assertIsNone(mb.journal_status_lines(self.TODAY, {}, 0))
        self.assertIsNone(
            mb.journal_status_lines(self.TODAY, self.state([]), 0))

    def test_silent_when_yesterday_filed(self):
        got = mb.journal_status_lines(
            self.TODAY, self.state(["2026-07-10"]), 0)
        self.assertIsNone(got)

    def test_missing_yesterday_warns(self):
        got = mb.journal_status_lines(
            self.TODAY, self.state(["2026-07-09"]), 0)
        self.assertIn("No journal entry arrived", got)

    def test_silent_after_habit_lapses(self):
        got = mb.journal_status_lines(
            self.TODAY, self.state(["2026-07-01"]), 0)
        self.assertIsNone(got)

    def test_pending_pages_soften_the_warning(self):
        got = mb.journal_status_lines(
            self.TODAY,
            self.state(["2026-07-09"],
                       pages=[{"date": "", "parked": ""}]), 0)
        self.assertIn("pending OCR", got)
        self.assertNotIn("No journal entry arrived", got)

    def test_staged_export_softens_the_warning(self):
        got = mb.journal_status_lines(
            self.TODAY, self.state(["2026-07-09"]), 1)
        self.assertIn("staged", got)

    def test_parked_pages_surface(self):
        got = mb.journal_status_lines(
            self.TODAY,
            self.state(["2026-07-09"],
                       pages=[{"date": "", "parked": "weekday mismatch"}]), 0)
        self.assertIn("parked", got)
        self.assertIn("--status", got)

    def test_regular_notebooks_ignored(self):
        state = self.state(["2026-07-09"])
        state["notebooks"]["Kitchen Ideas"] = {
            "entries": {}, "pages": [{"date": "", "text": "", "parked": ""}]}
        got = mb.journal_status_lines(self.TODAY, state, 0)
        self.assertIn("No journal entry arrived", got)


class BriefingSuppressed(unittest.TestCase):
    """Suppression is a durable, UUID-keyed policy on the Person record — not a
    name list in a file that can go stale or be deleted."""

    def setUp(self):
        self.people = [person("Wendell Boon", aliases="Wen, Wendy",
                              email="wb@x.com", briefingsuppressed="1"),
                       person("Priya Raman")]

    def test_the_flag_reads_both_ways_devonthink_writes_it(self):
        """Set by script it reads back '1'; ticked in the GUI, 'true'."""
        for raw in ("1", "true", "True"):
            self.assertTrue(mb.md_flag(raw), raw)
        for raw in ("", "0", "false", None):
            self.assertFalse(mb.md_flag(raw), raw)

    def test_the_record_supplies_aliases_and_email(self):
        """A calendar title uses the nickname and an attendee arrives as a bare
        email — only the record knows all three are one person."""
        keys = mb.suppression_keys(self.people)
        self.assertIn("wen", keys)
        self.assertIn("wendy", keys)
        self.assertIn("wb@x.com", keys)
        self.assertEqual([p["name"] for p in [p for p in self.people if not mb.is_suppressed(p)]],
                         ["Priya Raman"])

    def test_a_bare_first_name_is_never_synthesised(self):
        """"Wendell" would suppress every unrelated Wendell, silently punching a
        hole in a timeline that promises the whole day. A first name earns a key
        only by being a recorded alias."""
        keys = mb.suppression_keys(self.people)
        self.assertNotIn("wendell", keys)
        self.assertFalse(mb.names_excluded("Lunch with Wendell Crane",
                                           mb.excluded_re(keys)))

    def test_contacts_card_only_widens_the_vocabulary(self):
        cards = [contact("Wendell Boon", nickname="Wubs", emails=["w2@x.com"]),
                 contact("Priya Raman", nickname="Pree")]
        keys = mb.suppression_keys(self.people, cards)
        self.assertIn("wubs", keys)
        self.assertIn("w2@x.com", keys)
        self.assertNotIn("pree", keys)

    def test_nobody_flagged_keeps_everyone(self):
        people = [person("Priya Raman")]
        self.assertEqual(mb.suppression_keys(people), set())
        self.assertEqual(len([p for p in people if not mb.is_suppressed(p)]), 1)

    def test_suppressed_person_never_reaches_reconnect(self):
        people = [person("Wendell Boon", relationship="friend",
                         lastcontact="2020-01-01", entitystatus="active",
                         briefingsuppressed="1")]
        self.assertEqual(
            mb.reconnect_overdue([p for p in people if not mb.is_suppressed(p)], "2026-07-13"), [])


class LoadConfig(unittest.TestCase):
    def setUp(self):
        self.original = mb.CONFIG_FILE
        self.dir = tempfile.mkdtemp()
        mb.CONFIG_FILE = os.path.join(self.dir, "entities.conf")

    def tearDown(self):
        if os.path.exists(mb.CONFIG_FILE):
            os.chmod(mb.CONFIG_FILE, 0o600)
        shutil.rmtree(self.dir, ignore_errors=True)
        mb.CONFIG_FILE = self.original

    def test_absent_config_means_never_configured(self):
        self.assertEqual(mb.load_config(), {})

    def test_unreadable_config_raises_rather_than_briefing_everyone(self):
        """SKIP_CALENDARS is a privacy control; degrading to {} would silently
        brief a calendar the user asked never to see."""
        with open(mb.CONFIG_FILE, "w") as f:
            f.write("SKIP_CALENDARS=Some Shared Calendar\n")
        os.chmod(mb.CONFIG_FILE, 0o000)
        if os.access(mb.CONFIG_FILE, os.R_OK):
            self.skipTest("running as root; mode bits do not apply")
        with self.assertRaises(OSError):
            mb.load_config()

    def test_comments_and_blank_lines_are_ignored(self):
        with open(mb.CONFIG_FILE, "w") as f:
            f.write("# a comment\n\nSKIP_CALENDARS=Shared\nnot-a-pair\n")
        self.assertEqual(mb.load_config(), {"SKIP_CALENDARS": "Shared"})


class ExcludedPersonNeverReachesOutput(unittest.TestCase):
    """Rendered-output tests, deliberately. Filtering the roster does not redact
    raw calendar text, and an implementation test that inspects the key set
    cannot see that: the title, the attendee label and a past record's name are
    strings no roster filter ever reads."""

    KEYS = {"tamsin", "tamsin quill", "tq"}

    def render(self, events, people=()):
        blocks = mb.brief_blocks(events, list(people), ROOM_RE, self.KEYS)
        return mb.render_brief(blocks, "2026-07-13") or ""

    def test_a_title_naming_them_keeps_its_slot_but_loses_its_content(self):
        """Deleting the event outright would leave a silent hole in a timeline
        that promises the whole day."""
        got = self.render([event("Lunch with Tamsin Quill"),
                           event("Tamsin: flight to LAX"),
                           event("Perio cleaning")])
        self.assertNotIn("Tamsin", got)
        self.assertEqual(got.count(mb.REDACTED_TITLE), 2)
        self.assertIn("9:00am", got)
        self.assertIn("Perio cleaning", got)

    def test_an_event_they_merely_attend_survives_minus_them(self):
        """Suppression is narrow: the event is not theirs, only their presence."""
        ev = event("Planning", [attendee("Tamsin Quill", "tq@x.com"),
                                attendee("Rhea Sandoval", "rs@x.com")])
        got = self.render([ev])
        self.assertNotIn("Tamsin", got)
        self.assertNotIn("tq@x.com", got)
        self.assertIn("Planning", got)
        self.assertIn("Rhea Sandoval", got)
        self.assertNotIn(mb.REDACTED_TITLE, got)

    def test_no_lastcontact_bump_from_a_title_naming_them(self):
        people = [person("Tamsin Quill", aliases="Tamsin",
                         briefingsuppressed="1")]
        keys = mb.suppression_keys(people)
        ops = mb.contact_bumps([event("Dinner with Tamsin")],
                               [p for p in people if not mb.is_suppressed(p)], "2026-07-13",
                               ROOM_RE, mb.SKIP_CALENDARS, keys)
        self.assertEqual(ops, [])

    def test_a_similar_name_is_not_over_suppressed(self):
        got = self.render([event("Sync with Tamsina Reyes")])
        self.assertIn("Tamsina Reyes", got)

    def test_a_possessive_title_is_suppressed(self):
        """An apostrophe in the trailing boundary would exempt exactly the form
        a personal calendar tends to use."""
        for title in ["Tamsin's birthday", "Tamsin’s flight", "TQ's appt"]:
            got = self.render([event(title)])
            self.assertNotIn("amsin", got, title)
            self.assertIn(mb.REDACTED_TITLE, got, title)

    def test_an_email_only_attendee_is_suppressed(self):
        got = self.render([event("Planning", [attendee("", "tq@x.com")])])
        self.assertNotIn("tq@x.com", got)
        self.assertIn("Planning", got)

    def test_a_redacted_event_leaks_no_location_or_people(self):
        ev = event("Dinner with Tamsin", [attendee("Rhea Sandoval", "rs@x.com")])
        ev["location"] = "14 Alder Street"
        got = self.render([ev])
        self.assertNotIn("Alder", got)
        self.assertNotIn("Rhea", got)
        self.assertIn(mb.REDACTED_TITLE, got)


class SuppressedNameInSomeoneElsesRecord(unittest.TestCase):
    """A visible person's own record can name a suppressed one. Those fields and
    log bullets render into the daily note and the TRMNL snapshot, so the roster
    is scrubbed at the boundary rather than at each place that renders it."""

    def setUp(self):
        self.roster = [
            person("Avery North", email="an@x.com", briefingsuppressed="1"),
            person("Taylor Reed", role="Assistant to Avery North",
                   city="Chicago"),
        ]
        self.roster[1]["body"] = (
            "# Taylor Reed\n\n**Partner:** Avery North\n\n## Biographical Log\n\n"
            "- 2026-05-01 — Met Avery North for lunch.\n"
            "- 2026-05-02 — Shipped the reporting service.\n")
        self.ex_re = mb.excluded_re(mb.suppression_keys(self.roster))
        self.visible = mb.redact_person(
            [p for p in self.roster if not mb.is_suppressed(p)][0], self.ex_re)

    def test_a_field_naming_them_is_dropped(self):
        self.assertEqual(self.visible["md"]["mdrole"], "")

    def test_an_unrelated_field_survives(self):
        self.assertEqual(self.visible["md"]["mdcity"], "Chicago")

    def test_a_log_bullet_naming_them_is_dropped(self):
        bullets = mb.news_bullets(self.visible, set())
        self.assertFalse(any("Avery" in b for b in bullets), bullets)
        self.assertTrue(any("reporting service" in b for b in bullets))

    def test_the_rendered_summary_line_never_names_them(self):
        self.assertNotIn("Avery", mb.person_summary_line(self.visible))

    def test_the_trmnl_snapshot_never_names_them(self):
        self.assertNotIn("Avery", json.dumps(mb.person_snapshot(self.visible)))

    def test_a_body_line_outside_the_log_is_scrubbed_too(self):
        self.assertNotIn("Avery", self.visible["body"])


class SuppressionKeyClosure(unittest.TestCase):
    """Absorption is a fixed point, so it cannot depend on the order Contacts
    happens to return cards in — but it only ever traverses an identifier that
    exactly one card claims. A handle two cards share (a household landline)
    proves nothing about identity, so it links nothing."""

    CARD = {"name": "Avery North", "nickname": "Ave", "id": "a",
            "emails": ["an@x.com"], "phones": ["+1 (555) 010-1234"]}
    SHARED = {"name": "Rhea Sandoval", "nickname": "Rhe", "id": "b",
              "emails": ["rs@x.com"], "phones": ["+1 (555) 010-1234"]}

    def people(self):
        return [person("Avery North", email="an@x.com", briefingsuppressed="1")]

    def test_absorption_is_order_independent(self):
        forward = mb.suppression_keys(self.people(), [self.CARD, self.SHARED])
        reverse = mb.suppression_keys(self.people(), [self.SHARED, self.CARD])
        self.assertEqual(forward, reverse)
        self.assertIn("ave", forward)

    def test_phones_are_folded_through_norm_handle(self):
        keys = mb.suppression_keys(self.people(), [self.CARD])
        self.assertIn(mb.norm_handle("+1 (555) 010-1234"), keys)

    def test_a_tel_attendee_url_is_suppressed(self):
        """EventKit hands a phone participant back as a tel: URL, which no name
        regex would ever match."""
        keys = mb.suppression_keys(self.people(), [self.CARD])
        a = attendee("", "tel:+1-555-010-1234")
        self.assertTrue(mb.attendee_excluded(a, mb.excluded_re(keys), keys))

    def test_a_shared_handle_never_links_a_second_card_in(self):
        keys = mb.suppression_keys(self.people(), [self.CARD, self.SHARED])
        self.assertNotIn("rhea sandoval", keys)
        self.assertNotIn("rs@x.com", keys)
        self.assertNotIn("rhe", keys)

    def test_an_unrelated_card_is_not_absorbed(self):
        other = {"name": "Priya Raman", "nickname": "Pree", "id": "c",
                 "emails": ["pr@x.com"], "phones": ["+1 555-010-9999"]}
        keys = mb.suppression_keys(self.people(), [self.CARD, other])
        self.assertNotIn("pree", keys)
        self.assertNotIn("pr@x.com", keys)


class UnicodeCaseFolding(unittest.TestCase):
    def test_a_case_pair_that_is_not_one_to_one_still_folds(self):
        """lower() leaves "Straße" as "straße", so "STRASSE" would sail past the
        redaction. Only casefold() folds the pair."""
        self.assertEqual(mb.norm("Test Straße"), mb.norm("Test STRASSE"))
        people = [person("Test Straße", briefingsuppressed="1")]
        keys = mb.suppression_keys(people)
        ex_re = mb.excluded_re(keys)
        for title in ["Call with Test STRASSE", "Call with Test Straße",
                      "call with test strasse"]:
            self.assertTrue(mb.text_excluded(title, ex_re, keys), title)

    def test_a_merely_similar_name_still_does_not_match(self):
        people = [person("Test Straße", briefingsuppressed="1")]
        keys = mb.suppression_keys(people)
        self.assertFalse(mb.text_excluded("Call with Testa Strassen",
                                          mb.excluded_re(keys), keys))


class SuppressedRecordsStayInTheIndex(unittest.TestCase):
    """A suppressed person still owns their keys. Dropping them from the roster
    would promote a visible person to sole owner of a shared alias, and the
    suppressed person's Contacts card would then resolve to them — handing over
    their birthday and their Messages handle."""

    def setUp(self):
        self.roster = [
            person("Robin Sandoval", aliases="Robin", email="rs@x.com",
                   briefingsuppressed="1"),
            person("Robin Chen", aliases="Robin", relationship="friend",
                   lastcontact="2020-01-01"),
        ]
        self.index = mb.person_index(self.roster)

    def test_a_shared_alias_stays_ambiguous(self):
        self.assertEqual(len(self.index["robin"]), 2)
        self.assertIsNone(mb.match_person(self.index, "Robin", ""))

    def test_their_contacts_card_resolves_to_nobody(self):
        card = contact("Robin S", nickname="Robin", phones=["555-0100"])
        self.assertIsNone(mb.match_contact(self.index, card))

    def test_a_card_matching_them_uniquely_also_resolves_to_nobody(self):
        card = contact("Robin Sandoval", emails=["rs@x.com"])
        self.assertIsNone(mb.match_contact(self.index, card))

    def test_they_never_reach_reconnect(self):
        overdue = mb.reconnect_overdue(self.roster, "2026-12-01")
        self.assertNotIn("Robin Sandoval", [p["name"] for _, _, p in overdue])

    def test_they_never_match_a_title(self):
        got = mb.title_matches(self.roster, "Call with Robin Sandoval")
        self.assertEqual(got, [])

    def test_they_never_take_a_lastcontact_bump(self):
        ops = mb.contact_bumps(
            [event("Sync", [attendee("Robin Sandoval", "rs@x.com")])],
            self.roster, "2026-07-13", ROOM_RE)
        self.assertEqual(ops, [])

    def test_load_people_keeps_them_so_the_caller_can_fail_closed(self):
        """main() refuses to brief when Contacts is unavailable and anyone is
        suppressed, which it can only detect if they are still in the list."""
        self.assertTrue(any(mb.is_suppressed(p) for p in self.roster))


class SuppressedPhoneInFreeText(unittest.TestCase):
    """A phone key is canonical digits and can never match the punctuation a
    human actually writes, so every phone-shaped run has to fold through
    norm_handle before it is judged."""

    def setUp(self):
        self.people = [person("Avery North", email="an@x.com",
                              briefingsuppressed="1")]
        self.cards = [contact("Avery North", emails=["an@x.com"],
                              phones=["212-555-0101"])]
        self.keys = mb.suppression_keys(self.people, self.cards)
        self.ex_re = mb.excluded_re(self.keys)

    def excluded(self, text):
        return mb.text_excluded(text, self.ex_re, self.keys)

    def test_a_formatted_number_in_a_title_is_suppressed(self):
        for title in ["Call +1 (212) 555-0101", "Dial 212.555.0101 at noon",
                      "(212) 555 0101", "call 2125550101"]:
            self.assertTrue(self.excluded(title), title)

    def test_a_bare_number_in_the_attendee_name_field_is_suppressed(self):
        """A client that resolved nothing puts the number in the name, not the
        email — and EventKit puts it in the email as a tel: URL."""
        self.assertTrue(mb.attendee_excluded(
            attendee("+1 (212) 555-0101", ""), self.ex_re, self.keys))
        self.assertTrue(mb.attendee_excluded(
            attendee("", "tel:+1-212-555-0101"), self.ex_re, self.keys))

    def test_an_unrelated_number_is_not_suppressed(self):
        self.assertFalse(self.excluded("Call 415-555-9999"))

    def test_a_flight_number_is_not_a_phone(self):
        self.assertFalse(self.excluded("SAN to BNA - WN 3478"))


class SharedHandleIsAmbiguous(unittest.TestCase):
    def test_a_household_phone_does_not_absorb_the_other_card(self):
        """A landline on both partners' cards proves nothing about identity."""
        people = [person("Avery North", email="an@x.com",
                         briefingsuppressed="1")]
        cards = [contact("Avery North", emails=["an@x.com"],
                         phones=["212-555-0101"]),
                 contact("Rhea Sandoval", nickname="Rhe", emails=["rs@x.com"],
                         phones=["212-555-0101"])]
        keys = mb.suppression_keys(people, cards)
        self.assertNotIn("rhea sandoval", keys)
        self.assertNotIn("rs@x.com", keys)
        self.assertNotIn("rhe", keys)
        # the number is still theirs, so it stays redacted
        self.assertIn(mb.norm_handle("212-555-0101"), keys)

    def test_an_unshared_handle_still_absorbs(self):
        people = [person("Avery North", email="an@x.com",
                         briefingsuppressed="1")]
        cards = [contact("Avery North", nickname="Ave", emails=["an@x.com"],
                         phones=["212-555-0102"])]
        keys = mb.suppression_keys(people, cards)
        self.assertIn("ave", keys)


class TitleMatchAmbiguity(unittest.TestCase):
    def test_an_alias_shared_by_two_people_identifies_neither(self):
        """A title carries no email to disambiguate with, so matching both would
        bump LastContact on someone who was never there."""
        people = [person("Jordan Vale", aliases="Jordan"),
                  person("Jordan Reyes", aliases="Jordan")]
        self.assertEqual(mb.title_matches(people, "Call with Jordan"), [])
        self.assertEqual(
            mb.contact_bumps([event("Call with Jordan")], people, "2026-07-13",
                             ROOM_RE), [])

    def test_an_unambiguous_alias_still_matches(self):
        people = [person("Jordan Vale", aliases="Jordan")]
        got = mb.title_matches(people, "Call with Jordan")
        self.assertEqual([p["name"] for p in got], ["Jordan Vale"])

    def test_a_bare_alias_does_not_claim_an_unknown_full_name(self):
        people = [person("Jordan Vale", aliases="Jordan")]
        got = mb.title_matches(people, "Meeting with Jordan Pike")
        self.assertEqual(got, [])
        self.assertEqual(
            mb.contact_bumps([event("Meeting with Jordan Pike")], people,
                             "2026-07-13", ROOM_RE), [])

    def test_a_standalone_alias_still_matches_beside_an_unknown_full_name(self):
        people = [person("Jordan Vale", aliases="Jordan")]
        got = mb.title_matches(people, "Jordan Pike and Jordan")
        self.assertEqual([p["name"] for p in got], ["Jordan Vale"])

    def test_a_span_swallowed_by_a_longer_name_loses(self):
        """"Call with Avery North" names one person, not two — the record aliased
        "Avery" is not in the room, and bumping them would be a false write."""
        people = [person("Avery North"), person("Cleo Fenn", aliases="Avery")]
        got = mb.title_matches(people, "Call with Avery North")
        self.assertEqual([p["name"] for p in got], ["Avery North"])
        ops = mb.contact_bumps([event("Call with Avery North")], people,
                               "2026-07-13", ROOM_RE)
        self.assertEqual(len(ops), 1)

    def test_disjoint_names_still_match_separately(self):
        people = [person("Avery North"), person("Bram Vale")]
        got = mb.title_matches(people, "Lunch with Avery North and Bram Vale")
        self.assertEqual([p["name"] for p in got], ["Avery North", "Bram Vale"])

    def test_an_alias_repeated_outside_a_longer_name_still_matches(self):
        """The alias occurs twice: once swallowed by the longer name, once
        standing alone. Judging only the first occurrence made this depend on
        which way round the title was written."""
        people = [person("Zorplin Alpha"), person("Bram Vale", aliases="Zorplin")]
        for title in ["Zorplin Alpha and Zorplin", "Zorplin and Zorplin Alpha"]:
            got = [p["name"] for p in mb.title_matches(people, title)]
            self.assertEqual(sorted(got), ["Bram Vale", "Zorplin Alpha"], title)


class ParkedSourceRedaction(unittest.TestCase):
    def test_an_excluded_name_in_last_error_is_suppressed(self):
        """parked_lines renders last_error, and an extraction error quotes the
        text it choked on."""
        parked = {"u1": {"name": "Generic Note",
                         "last_error": "ambiguous person: Tamsin Quill"}}
        got = mb.render_review(backlog(parked=parked), "2026-07-13") or ""
        self.assertIn("Tamsin", got)
        ex_re = mb.excluded_re({"tamsin quill"})
        kept = {u: i for u, i in parked.items()
                if not mb.names_excluded(f"{i['name']} {i['last_error']}", ex_re)}
        self.assertEqual(kept, {})


class SeriesDetection(unittest.TestCase):
    """Exchange sends each occurrence of a series as an independent event with
    its own identifier and no recurrence rule, so a series is only ever
    recognized by having run before."""

    TODAY = "2026-07-14"

    def test_the_same_meeting_on_an_earlier_day_is_a_repeat(self):
        hist = [event("Platform Sync", date="2026-07-07")]
        self.assertIn(mb.series_key(event("Platform Sync", date=self.TODAY)),
                      mb.repeat_series(hist, self.TODAY))

    def test_case_and_emoji_do_not_split_a_series(self):
        hist = [event("board game night 🎲", date="2026-07-07")]
        self.assertIn(mb.series_key(event("Board Game Night")),
                      mb.repeat_series(hist, self.TODAY))

    def test_the_same_title_on_another_calendar_is_another_meeting(self):
        hist = [event("Standup", date="2026-07-07", calendar="Personal")]
        self.assertNotIn(mb.series_key(event("Standup", date=self.TODAY)),
                         mb.repeat_series(hist, self.TODAY))

    def test_an_invite_you_ignored_still_proves_the_series_is_not_new(self):
        hist = [event("All Hands", date="2026-07-07", rsvp="unknown")]
        self.assertIn(mb.series_key(event("All Hands", date=self.TODAY)),
                      mb.repeat_series(hist, self.TODAY))

    def test_today_never_marks_itself_a_repeat(self):
        """Otherwise a first occurrence would suppress its own attendees."""
        hist = [event("Kickoff", date=self.TODAY)]
        self.assertEqual(mb.repeat_series(hist, self.TODAY), set())

    def test_an_all_day_event_starts_no_series(self):
        hist = [event("Holiday", date="2026-07-07", all_day=True)]
        self.assertEqual(mb.repeat_series(hist, self.TODAY), set())


class SeriesLookbackStamp(unittest.TestCase):
    """C21: the 180-day lookback is expensive, so a same-day retry tick
    reuses the prior tick's repeats instead of re-fetching."""

    TODAY = "2026-07-14"

    def test_no_stamp_yields_no_cached_repeats(self):
        state = mb.empty_identity_provenance()
        self.assertIsNone(mb.cached_repeats(state, self.TODAY))

    def test_a_same_day_stamp_is_reused(self):
        state = mb.empty_identity_provenance()
        repeats = {mb.series_key(event("Platform Sync"))}
        mb.stamp_series_lookback(state, self.TODAY, repeats)
        self.assertEqual(mb.cached_repeats(state, self.TODAY), repeats)

    def test_a_stale_day_stamp_is_not_reused(self):
        state = mb.empty_identity_provenance()
        mb.stamp_series_lookback(
            state, "2026-07-13", {mb.series_key(event("Platform Sync"))})
        self.assertIsNone(mb.cached_repeats(state, self.TODAY))

    def test_repeats_round_trip_through_json(self):
        state = mb.empty_identity_provenance()
        repeats = {mb.series_key(event("Platform Sync", calendar="Work")),
                   mb.series_key(event("Board Game Night", calendar="Home"))}
        mb.stamp_series_lookback(state, self.TODAY, repeats)
        reloaded = json.loads(json.dumps(state))
        self.assertEqual(mb.cached_repeats(reloaded, self.TODAY), repeats)


class BriefEventLinks(unittest.TestCase):
    """Every event title links into the note layer: to the note that owns
    the event, or to a dtnote:// URL whose handler opens-or-creates one on
    click. The links re-derive from LinkedEvent metadata each run, so the
    wholesale section replace can never lose an attachment."""

    TODAY = "2026-07-14"

    def blocks(self, events):
        return mb.brief_blocks(events, [], ROOM_RE, set())

    def key(self, title):
        return mb.be.event_key(self.TODAY, title)

    def test_a_noteless_event_gets_a_create_link(self):
        got = mb.render_brief(self.blocks([event("Call Priya")]), self.TODAY,
                              {})
        self.assertIn("- 9:00am — [Call Priya](dtnote://open"
                      "?date=2026-07-14&title=Call%20Priya)", got)

    def test_the_owning_meeting_note_takes_the_title_link(self):
        notes = {self.key("Call Priya"): [
            {"uuid": "N1", "name": "2026-07-14 Call Priya",
             "handwritten": False, "documenttype": "Meeting Notes"}]}
        got = mb.render_brief(self.blocks([event("Call Priya")]), self.TODAY,
                              notes)
        self.assertIn("- 9:00am — [Call Priya](x-devonthink-item://N1)", got)
        self.assertNotIn("dtnote://", got)

    def test_a_handwritten_match_renders_as_a_sub_bullet(self):
        """A handwritten attachment doesn't claim the title: clicking it must
        still create the typed note, whatever else got attached."""
        notes = {self.key("Call Priya"): [
            {"uuid": "HW", "name": "Call with Priya",
             "handwritten": True, "documenttype": ""}]}
        got = mb.render_brief(self.blocks([event("Call Priya")]), self.TODAY,
                              notes)
        self.assertIn("  - [✏️ Call with Priya](x-devonthink-item://HW)", got)
        self.assertIn("dtnote://", got)

    def test_owning_note_links_the_title_while_others_stay_bullets(self):
        notes = {self.key("Call Priya"): [
            {"uuid": "HW", "name": "Call with Priya",
             "handwritten": True, "documenttype": ""},
            {"uuid": "N1", "name": "2026-07-14 Call Priya",
             "handwritten": False, "documenttype": "Meeting Notes"}]}
        got = mb.render_brief(self.blocks([event("Call Priya")]), self.TODAY,
                              notes)
        self.assertIn("- 9:00am — [Call Priya](x-devonthink-item://N1)", got)
        self.assertIn("  - [✏️ Call with Priya](x-devonthink-item://HW)", got)
        self.assertEqual(got.count("x-devonthink-item://N1"), 1)

    def test_a_tentative_suffix_stays_outside_the_link(self):
        notes = {self.key("Call Priya"): [
            {"uuid": "N1", "name": "2026-07-14 Call Priya",
             "handwritten": False, "documenttype": "Meeting Notes"}]}
        got = mb.render_brief(
            self.blocks([event("Call Priya", rsvp="tentative")]), self.TODAY,
            notes)
        self.assertIn(
            "- 9:00am — [Call Priya](x-devonthink-item://N1) (tentative)",
            got)

    def test_a_redacted_title_never_becomes_a_link(self):
        """A dtnote URL embeds the title — a private event's real title must
        not leak into it, and the placeholder must not be clickable either."""
        got = mb.render_brief(
            mb.brief_blocks([event("Lunch with Priya Raman")], [], ROOM_RE,
                            {"priya raman"}),
            self.TODAY, {})
        self.assertIn(f"- 9:00am — {mb.REDACTED_TITLE}", got)
        self.assertNotIn("dtnote://", got)
        self.assertNotIn("Lunch", got)

    def test_the_create_link_needs_no_note_index(self):
        """A dtnote URL derives from the event alone, so a render with no
        LinkedEvent lookups at all still makes every title clickable."""
        got = mb.render_brief(self.blocks([event("Call Priya")]), self.TODAY)
        self.assertIn("- 9:00am — [Call Priya](dtnote://open"
                      "?date=2026-07-14&title=Call%20Priya)", got)


class BriefNews(unittest.TestCase):
    """The roster ages, the news does not: a standing meeting drops who is in
    the room but keeps what has been filed about them since you last met."""

    def setUp(self):
        self.priya = person("Priya Raman", aliases="Priya", email="p@x.com",
                            lastcontact="2026-07-10")
        self.priya["body"] = ("## Biographical Log\n\n"
                              "- 2026-04-01 — old news. <!-- fact:aaaa1111 -->\n"
                              "- 2026-07-13 — moved to Denver."
                              " <!-- fact:bbbb2222 -->")
        self.people = [self.priya]

    def blocks(self, events, **kw):
        return mb.brief_blocks(events, self.people, ROOM_RE, set(), **kw)

    def rendered(self, events, **kw):
        return mb.render_brief(self.blocks(events, **kw), "2026-07-14") or ""

    def test_a_repeat_occurrence_still_carries_the_news(self):
        ev = event("Weekly Sync", [attendee("Priya Raman", "p@x.com")])
        got = self.rendered([ev], repeats={mb.series_key(ev)})
        self.assertIn("moved to Denver", got)
        self.assertNotIn("old news", got)

    def test_news_on_a_repeat_is_attributed_without_the_roster_line(self):
        """With no roster to hang it on, the fact still has to say who it is
        about — but not re-state their role, city and last contact."""
        ev = event("Weekly Sync", [attendee("Priya Raman", "p@x.com")])
        got = self.rendered([ev], repeats={mb.series_key(ev)})
        self.assertIn("- [Priya Raman](x-devonthink-item://uuid-priya-raman)",
                      got)
        self.assertNotIn("last contact", got)

    def test_a_first_occurrence_carries_the_roster_and_the_news(self):
        got = self.rendered([event("Kickoff",
                                   [attendee("Priya Raman", "p@x.com")])])
        self.assertIn("last contact 2026-07-10", got)
        self.assertIn("moved to Denver", got)

    def test_a_person_in_two_meetings_is_briefed_under_the_first_only(self):
        """The reported bug: one fact printed under both of the day's meetings."""
        evs = [event("Standup", [attendee("Priya Raman", "p@x.com")]),
               event("Retro", [attendee("Priya Raman", "p@x.com")])]
        evs[1]["start"] = "2026-07-14T14:00:00"
        got = self.rendered(evs)
        self.assertEqual(got.count("moved to Denver"), 1)

    def test_a_repeat_with_no_news_is_a_bare_slot(self):
        self.priya["md"]["mdlastcontact"] = "2026-07-14"
        ev = event("Weekly Sync", [attendee("Priya Raman", "p@x.com")])
        got = self.rendered([ev], repeats={mb.series_key(ev)})
        self.assertEqual(got.strip().splitlines()[-1],
                         "- 9:00am — [Weekly Sync](dtnote://open"
                         "?date=2026-07-14&title=Weekly%20Sync)")

    def test_a_redacted_event_never_carries_news(self):
        ev = event("Lunch with Priya Raman", [attendee("Priya Raman", "p@x.com")])
        got = mb.brief_blocks([ev], self.people, ROOM_RE, {"priya raman"})
        self.assertEqual(got[0]["title"], mb.REDACTED_TITLE)
        self.assertEqual(got[0]["news"], [])

    def test_the_day_is_one_tight_list_nested_under_each_slot(self):
        """The slot is a bullet, not a heading, so everything it carries has to
        hang off it — a blank line or a lost indent would end the list and
        promote the roster to the day's top level."""
        got = self.rendered([event("Kickoff",
                                   [attendee("Priya Raman", "p@x.com")])])
        body = got.split("-->\n\n", 1)[1]
        self.assertEqual(body.splitlines(), [
            "- 9:00am — [Kickoff](dtnote://open?date=2026-07-14"
            "&title=Kickoff)",
            "  - [Priya Raman](x-devonthink-item://uuid-priya-raman)"
            " — last contact 2026-07-10",
            "    - 2026-07-13 — moved to Denver.",
        ])


class BriefBlocksTimeline(unittest.TestCase):
    def setUp(self):
        self.people = [person("Priya Raman", aliases="Priya", email="p@x.com")]

    def blocks(self, events, **kw):
        return mb.brief_blocks(events, self.people, ROOM_RE, set(), **kw)

    def test_an_event_with_nobody_still_briefs(self):
        """The timeline is the day, not just the people in it."""
        got = self.blocks([event("Perio cleaning")])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["people"], [])
        self.assertEqual(got[0]["unmatched"], [])

    def test_roster_person_named_in_a_title_is_enriched(self):
        got = self.blocks([event("Lunch with Priya")])
        self.assertEqual([p["name"] for p in got[0]["people"]], ["Priya Raman"])
        self.assertEqual(got[0]["unmatched"], [])

    def test_attendee_and_title_naming_the_same_person_is_not_doubled(self):
        ev = event("Lunch with Priya", [attendee("Priya Raman", "p@x.com")])
        got = self.blocks([ev])
        self.assertEqual([p["name"] for p in got[0]["people"]], ["Priya Raman"])

    def test_configured_skip_calendar_is_dropped(self):
        got = self.blocks([event("Date night", calendar="Shared")],
                          skip_cals=mb.SKIP_CALENDARS | {"Shared"})
        self.assertEqual(got, [])

    def test_skip_entry_pinned_to_an_identifier_survives_a_rename(self):
        got = self.blocks([event("Date night", calendar="New Name",
                                 calendar_id="cal-shared")],
                          skip_cals=mb.SKIP_CALENDARS | {"cal-shared"})
        self.assertEqual(got, [])

    def test_all_day_and_unaccepted_still_never_brief(self):
        evs = [event("Holiday", all_day=True),
               event("Ignored", [attendee("Priya Raman", "p@x.com")],
                     rsvp="unknown")]
        self.assertEqual(self.blocks(evs), [])

    def test_only_an_accepted_invitation_briefs(self):
        """An invite you never answered looks exactly like one you accepted on
        every other field, so RSVP is the only thing separating them."""
        for rsvp, briefs in [("accepted", True), ("tentative", True),
                             ("unknown", False), ("pending", False),
                             ("declined", False), (None, False)]:
            with self.subTest(rsvp=rsvp):
                ev = event("CAB", [attendee("Priya Raman", "p@x.com")],
                           rsvp=rsvp)
                self.assertEqual(len(self.blocks([ev])), 1 if briefs else 0)

    def test_your_own_event_has_no_rsvp_and_always_briefs(self):
        """No attendees means nobody invited you: it is your own calendar entry."""
        self.assertEqual(len(self.blocks([event("Perio cleaning", rsvp=None)])), 1)

    def test_an_event_you_organize_briefs_even_without_your_rsvp(self):
        ev = event("Standup", [attendee("Priya Raman", "p@x.com")],
                   rsvp=None, organizer_is_self=True)
        self.assertEqual(len(self.blocks([ev])), 1)

    def test_a_cancelled_event_never_briefs(self):
        """Exchange leaves a cancelled meeting on the calendar with your
        acceptance intact, so RSVP alone would still brief it."""
        ev = event("Product Sync", [attendee("Priya Raman", "p@x.com")],
                   rsvp="accepted", canceled=True)
        self.assertEqual(self.blocks([ev]), [])

    def test_tentative_briefs_and_says_so(self):
        got = self.blocks([event("Roadmap review", rsvp="tentative",
                                 attendees=[attendee("Priya Raman", "p@x.com")])])
        self.assertEqual(got[0]["title"], "Roadmap review (tentative)")

    def test_a_repeat_occurrence_keeps_its_slot_but_sheds_its_roster(self):
        ev = event("Weekly Sync", [attendee("Priya Raman", "p@x.com")])
        got = self.blocks([ev], repeats={mb.series_key(ev)})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["title"], "Weekly Sync")
        self.assertEqual(got[0]["people"], [])
        self.assertEqual(got[0]["unmatched"], [])

    def test_a_first_occurrence_still_carries_its_people(self):
        ev = event("Kickoff", [attendee("Priya Raman", "p@x.com")])
        got = self.blocks([ev], repeats={("Calendar", "some other meeting")})
        self.assertEqual([p["name"] for p in got[0]["people"]], ["Priya Raman"])

    def test_a_title_match_is_suppressed_on_a_repeat_too(self):
        """Whoever the body names, a standing meeting names them every time."""
        ev = event("Lunch with Priya")
        got = self.blocks([ev], repeats={mb.series_key(ev)})
        self.assertEqual(got[0]["people"], [])

    def test_a_repeating_tentative_event_keeps_its_marker(self):
        ev = event("Team Social", [attendee("Priya Raman", "p@x.com")],
                   rsvp="tentative")
        got = self.blocks([ev], repeats={mb.series_key(ev)})
        self.assertEqual(got[0]["title"], "Team Social (tentative)")

    def test_person_less_event_renders_without_a_trailing_blank(self):
        got = mb.render_brief(self.blocks([event("Perio cleaning")]), "2026-07-13")
        self.assertTrue(
            got.endswith("- 9:00am — [Perio cleaning](dtnote://open"
                         "?date=2026-07-13&title=Perio%20cleaning)"),
            repr(got))


class SectionsForUpsert(unittest.TestCase):
    """C42: a calendar or contacts fetch failure must not upsert its
    dependent section as empty — that would erase whatever an earlier run
    wrote correctly. The failed section is dropped from the batch entirely."""

    def test_calendar_failure_excludes_only_the_briefing(self):
        sections = [(mb.BRIEF_HEADER, "brief content"),
                    (mb.BIRTHDAYS_HEADER, "bday content"),
                    (mb.REVIEW_HEADER, "review content")]
        got = mb.sections_for_upsert(sections, {"calendar"})
        self.assertEqual([h for h, _ in got],
                         [mb.BIRTHDAYS_HEADER, mb.REVIEW_HEADER])

    def test_contacts_failure_excludes_only_birthdays(self):
        sections = [(mb.BRIEF_HEADER, "brief content"),
                    (mb.BIRTHDAYS_HEADER, "bday content")]
        got = mb.sections_for_upsert(sections, {"contacts"})
        self.assertEqual([h for h, _ in got], [mb.BRIEF_HEADER])

    def test_no_failure_keeps_every_section_including_empty_ones(self):
        sections = [(mb.BRIEF_HEADER, None), (mb.REVIEW_HEADER, "x")]
        self.assertEqual(mb.sections_for_upsert(sections, set()), sections)

    def test_unaffected_sections_survive_either_failure(self):
        sections = [(mb.REVIEW_HEADER, "x"), (mb.JOURNAL_HEADER, "y"),
                    (mb.ON_THIS_DAY_HEADER, "z"), (mb.RECONNECT_HEADER, "w")]
        got = mb.sections_for_upsert(sections, {"calendar", "contacts"})
        self.assertEqual(got, sections)

    def test_both_failures_exclude_both_dependent_sections(self):
        sections = [(mb.BRIEF_HEADER, "a"), (mb.BIRTHDAYS_HEADER, "b"),
                    (mb.REVIEW_HEADER, "c")]
        got = mb.sections_for_upsert(sections, {"calendar", "contacts"})
        self.assertEqual([h for h, _ in got], [mb.REVIEW_HEADER])


class RequestedDateValidation(unittest.TestCase):
    """C02: a --date in the future would write LastContact into the future,
    which the raise-only bridge guard then defends against every correction
    until the real date catches up."""

    def test_a_future_date_is_rejected(self):
        self.assertIsNotNone(
            mb.validate_requested_date("2026-07-17", "2026-07-16"))

    def test_a_past_date_is_fine(self):
        self.assertIsNone(
            mb.validate_requested_date("2026-07-01", "2026-07-16"))

    def test_todays_date_is_fine(self):
        self.assertIsNone(
            mb.validate_requested_date("2026-07-16", "2026-07-16"))


class EffectiveDryRun(unittest.TestCase):
    """C02: a --date replay of a past day must run read-only, same as an
    explicit --dry-run, so it never durably bumps LastContact or writes the
    daily note."""

    def test_explicit_dry_run_is_preserved(self):
        self.assertTrue(mb.effective_dry_run(True, "2026-07-16", "2026-07-16"))

    def test_a_past_date_forces_read_only(self):
        self.assertTrue(mb.effective_dry_run(False, "2026-07-01", "2026-07-16"))

    def test_todays_date_stays_live(self):
        self.assertFalse(mb.effective_dry_run(False, "2026-07-16", "2026-07-16"))


class LoadIdentityProvenanceQuarantine(unittest.TestCase):
    """C03: a corrupt or unsupported state file must not take the whole
    brief down — it is quarantined aside and the run continues with empty
    state."""

    def setUp(self):
        self.original = mb.IDENTITY_PROVENANCE_FILE
        self.dir = tempfile.mkdtemp()
        mb.IDENTITY_PROVENANCE_FILE = os.path.join(
            self.dir, "identity-provenance.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        mb.IDENTITY_PROVENANCE_FILE = self.original

    def test_missing_file_is_not_quarantined(self):
        with capture_logs(mb) as cap:
            state = mb.load_identity_provenance_or_quarantine("20260716T051500")
        self.assertEqual(state, mb.empty_identity_provenance())
        self.assertEqual(cap.messages(), [])

    def test_malformed_json_is_quarantined_and_state_is_empty(self):
        with open(mb.IDENTITY_PROVENANCE_FILE, "w") as f:
            f.write("{not json")
        with capture_logs(mb) as cap:
            state = mb.load_identity_provenance_or_quarantine("20260716T051500")
        self.assertEqual(state, mb.empty_identity_provenance())
        self.assertFalse(os.path.exists(mb.IDENTITY_PROVENANCE_FILE))
        self.assertTrue(os.path.exists(
            mb.IDENTITY_PROVENANCE_FILE + ".corrupt-20260716T051500"))
        messages = cap.messages()
        self.assertEqual(len(messages), 1)
        self.assertIn("lookback", messages[0])

    def test_unsupported_version_is_quarantined(self):
        with open(mb.IDENTITY_PROVENANCE_FILE, "w") as f:
            json.dump({"version": 99, "people": {}}, f)
        state = mb.load_identity_provenance_or_quarantine("20260716T051500")
        self.assertEqual(state, mb.empty_identity_provenance())
        self.assertTrue(os.path.exists(
            mb.IDENTITY_PROVENANCE_FILE + ".corrupt-20260716T051500"))

    def test_a_readable_valid_file_is_never_quarantined(self):
        state = mb.empty_identity_provenance()
        state["people"]["U"] = {"contexts": {}}
        with open(mb.IDENTITY_PROVENANCE_FILE, "w") as f:
            json.dump(state, f)
        with capture_logs(mb) as cap:
            got = mb.load_identity_provenance_or_quarantine("20260716T051500")
        self.assertEqual(got, state)
        self.assertTrue(os.path.exists(mb.IDENTITY_PROVENANCE_FILE))
        self.assertEqual(cap.messages(), [])


class PruneContextObservations(unittest.TestCase):
    """C03: context observations exist only to recognize a series inside
    SERIES_LOOKBACK_DAYS, so anything older is unbounded growth with no
    future read."""

    def test_drops_observations_older_than_the_lookback_window(self):
        state = mb.empty_identity_provenance()
        mb.record_calendar_observations(state, [
            {"id": "old", "person_uuid": "U", "context": "work",
             "date": "2025-01-01", "evidence": "email"},
            {"id": "new", "person_uuid": "U", "context": "work",
             "date": "2026-07-01", "evidence": "email"},
        ])
        changed = mb.prune_context_observations(state, "2026-07-16")
        self.assertTrue(changed)
        self.assertEqual(
            list(state["people"]["U"]["contexts"]["work"]), ["new"])

    def test_the_lookback_boundary_is_kept(self):
        state = mb.empty_identity_provenance()
        cutoff = (mb.date.fromisoformat("2026-07-16")
                 - mb.timedelta(days=mb.SERIES_LOOKBACK_DAYS)).isoformat()
        mb.record_calendar_observations(state, [
            {"id": "edge", "person_uuid": "U", "context": "work",
             "date": cutoff, "evidence": "email"},
        ])
        self.assertFalse(mb.prune_context_observations(state, "2026-07-16"))
        self.assertIn("edge", state["people"]["U"]["contexts"]["work"])

    def test_reports_no_change_when_nothing_is_stale(self):
        state = mb.empty_identity_provenance()
        mb.record_calendar_observations(state, [
            {"id": "new", "person_uuid": "U", "context": "work",
             "date": "2026-07-01", "evidence": "email"},
        ])
        self.assertFalse(mb.prune_context_observations(state, "2026-07-16"))

class AcquireLock(unittest.TestCase):
    """C41: a non-blocking exclusive lock, mirroring entity-filing.py's own,
    so an overlapping launchd retry never last-writer-wins the identity
    provenance state file."""

    def setUp(self):
        self.original = mb.LOCK_FILE
        self.dir = tempfile.mkdtemp()
        mb.LOCK_FILE = os.path.join(self.dir, "dt-morning-brief.lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        mb.LOCK_FILE = self.original

    def test_acquires_when_free(self):
        fd = mb.acquire_lock()
        self.assertIsNotNone(fd)
        fd.close()

    def test_returns_none_when_already_held(self):
        import fcntl
        holder = open(mb.LOCK_FILE, "w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertIsNone(mb.acquire_lock())
        finally:
            holder.close()

    def test_creates_the_state_dir_if_missing(self):
        mb.LOCK_FILE = os.path.join(self.dir, "nested", "dt-morning-brief.lock")
        fd = mb.acquire_lock()
        self.assertIsNotNone(fd)
        fd.close()


class CalendarPersonCandidateCap(unittest.TestCase):
    """C04: one large meeting must not flood _Review and the Things mirror
    with a proposal per unmatched attendee."""

    def setUp(self):
        self.contexts = mb.load_calendar_contexts({})

    def candidates(self, events):
        return mb.calendar_person_candidates(
            events, [], [], ROOM_RE, self.contexts, set(), mb.SKIP_CALENDARS,
            None)

    def test_a_large_meeting_is_capped_per_event(self):
        attendees = [attendee(f"Guest Number{i}", f"g{i}@x.com")
                    for i in range(40)]
        ev = event("All Hands", attendees, calendar="Work", event_id="event-1")
        got = self.candidates([ev])
        self.assertEqual(len(got), mb.CANDIDATE_CAP_PER_EVENT)

    def test_the_drop_is_logged_with_a_count_and_the_event_not_every_name(self):
        attendees = [attendee(f"Guest Number{i}", f"g{i}@x.com")
                    for i in range(40)]
        ev = event("All Hands", attendees, calendar="Work", event_id="event-1")
        with capture_logs(mb) as cap:
            self.candidates([ev])
        messages = cap.messages(level=logging.INFO)
        self.assertEqual(len(messages), 1)
        self.assertIn("35", messages[0])
        self.assertIn("All Hands", messages[0])
        for i in range(40):
            self.assertNotIn(f"Guest Number{i}", messages[0])

    def test_two_small_meetings_are_not_capped_against_each_other(self):
        ev1 = event("Sync A", [attendee("Aaron Brooks", "ab@x.com")],
                   calendar="Work", event_id="event-1")
        ev2 = event("Sync B", [attendee("Casey Doyle", "cd@x.com")],
                   calendar="Work", event_id="event-2")
        got = self.candidates([ev1, ev2])
        self.assertEqual(len(got), 2)

    def test_an_evidence_upgrade_does_not_count_against_the_cap(self):
        """A candidate already known from an earlier event's title mention is
        upgraded to attendee evidence here, not counted as a new proposal."""
        events = [
            event("Meeting with Jordan Pike", calendar="Work",
                 event_id="event-1"),
            event("Inspection", [attendee("Jordan Pike", "jp@x.com")],
                 calendar="Work", event_id="event-2"),
        ]
        got = mb.calendar_person_candidates(
            events, [], [contact("Jordan Pike")], ROOM_RE, self.contexts,
            set(), mb.SKIP_CALENDARS, None)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["evidence"], "calendar attendee")


class CalendarCandidateStore(unittest.TestCase):
    """C78 successor: the candidate record itself is the never-repropose
    memory. One stranger holds one Pending record across meetings; an
    Ignored record drops them; a differently-emailed same-named person gets
    their own record; attendance never files facts or bumps LastContact."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original_lock = ec.CANDIDATE_LOCK_FILE
        ec.CANDIDATE_LOCK_FILE = os.path.join(self.dir, "candidates.lock")

    def tearDown(self):
        ec.CANDIDATE_LOCK_FILE = self.original_lock
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_candidate(self, name="Jordan Pike", email="jp@x.com",
                       sid="cal:aaaaaaaaaaaaaaaaaaaaaaaa"):
        return {"key": mb.calendar_candidate_key(name), "name": name,
                "email": email, "evidence": "calendar attendee",
                "context": "neutral", "event_title": "Lease review",
                "event_date": "2026-07-13", "sid": sid, "near": []}

    def propose(self, candidates, listing=None, people=()):
        listing = listing or {"pending": [], "approved": [], "ignored": []}
        calls = []

        def bridge(ops):
            calls.append(ops)
            results = []
            for op in ops:
                if op["op"] == "list_candidates":
                    results.append(listing)
                elif op["op"] == "dump_people":
                    results.append(list(people))
                else:
                    results.append({"uuid": "NEW-1"})
            return results

        with mock.patch.object(mb, "run_bridge", bridge):
            mb.propose_calendar_candidates(candidates, False)
        return [op for ops in calls for op in ops]

    def pending_record(self, data):
        return {"uuid": "CAND-1", "name": ec.record_name(data), "md": {},
                "text": ec.render_candidate(data)}

    def test_new_stranger_creates_one_pending_candidate(self):
        ops = self.propose([self.make_candidate()])
        creates = [o for o in ops if o["op"] == "create_record"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["path"], ec.CANDIDATES_PATH)
        data = ec.parse_candidate(creates[0]["text"])
        self.assertEqual(data["emails"], ["jp@x.com"])
        sighting = data["sightings"]["cal:aaaaaaaaaaaaaaaaaaaaaaaa"]
        self.assertFalse(sighting["interacted"])
        self.assertEqual(sighting["facts"], [])

    def test_attendance_never_files_facts_or_lastcontact(self):
        ops = self.propose([self.make_candidate()])
        self.assertEqual(
            [o for o in ops if o["op"] in
             ("append_log", "bump_lastcontact", "ensure_person")], [])

    def test_second_meeting_attaches_to_the_existing_record(self):
        data = ec.new_candidate("Jordan Pike")
        ec.upsert_sighting(
            data, "cal:aaaaaaaaaaaaaaaaaaaaaaaa",
            {"person": "Jordan Pike", "email": "jp@x.com",
             "kind": "calendar", "date": "2026-07-13",
             "title": "Lease review", "interacted": False, "facts": [],
             "updates": {}, "evidence": "calendar attendee"})
        listing = {"pending": [self.pending_record(data)],
                   "approved": [], "ignored": []}
        ops = self.propose(
            [self.make_candidate(sid="cal:bbbbbbbbbbbbbbbbbbbbbbbb")],
            listing)
        self.assertEqual([o for o in ops if o["op"] == "create_record"], [])
        sets = [o for o in ops if o["op"] == "set_text"]
        self.assertEqual(len(sets), 1)
        updated = ec.parse_candidate(sets[0]["text"])
        self.assertEqual(len(updated["sightings"]), 2)

    def test_ignored_candidate_is_silently_dropped(self):
        data = ec.new_candidate("Jordan Pike")
        ec.upsert_sighting(
            data, "cal:aaaaaaaaaaaaaaaaaaaaaaaa",
            {"person": "Jordan Pike", "email": "jp@x.com",
             "kind": "calendar", "date": "2026-07-13",
             "title": "Lease review", "interacted": False, "facts": [],
             "updates": {}, "evidence": "calendar attendee"})
        listing = {"pending": [], "approved": [],
                   "ignored": [self.pending_record(data)]}
        ops = self.propose(
            [self.make_candidate(sid="cal:bbbbbbbbbbbbbbbbbbbbbbbb")],
            listing)
        self.assertEqual(
            [o for o in ops if o["op"] in ("create_record", "set_text")], [])

    def test_differently_emailed_same_name_gets_a_second_record(self):
        data = ec.new_candidate("Jordan Pike")
        ec.upsert_sighting(
            data, "cal:aaaaaaaaaaaaaaaaaaaaaaaa",
            {"person": "Jordan Pike", "email": "jp1@x.com",
             "kind": "calendar", "date": "2026-07-13",
             "title": "Lease review", "interacted": False, "facts": [],
             "updates": {}, "evidence": "calendar attendee"})
        listing = {"pending": [self.pending_record(data)],
                   "approved": [], "ignored": []}
        ops = self.propose(
            [self.make_candidate(email="jp2@x.com",
                                 sid="cal:bbbbbbbbbbbbbbbbbbbbbbbb")],
            listing)
        creates = [o for o in ops if o["op"] == "create_record"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(
            ec.parse_candidate(creates[0]["text"])["emails"], ["jp2@x.com"])

    def test_roster_resolved_attendee_is_not_recorded(self):
        ops = self.propose(
            [self.make_candidate()],
            people=[person("Jordan Pike", email="jp@x.com")])
        self.assertEqual(
            [o for o in ops if o["op"] in ("create_record", "set_text")], [])


class ShouldRecordSuccess(unittest.TestCase):
    def test_records_on_a_real_run(self):
        self.assertTrue(mb.should_record_success(False))

    def test_skips_on_a_dry_run(self):
        self.assertFalse(mb.should_record_success(True))


if __name__ == "__main__":
    unittest.main()
