import os
import shutil
import tempfile
import unittest

from helpers import load, person

ef = load("entity-filing.py", "entity_filing")

import entity_candidates as ec


def sighting(date="2026-07-13", person_name="Jordan Pike", email="",
             kind="granola", facts=(), updates=None, interacted=True,
             hash_="h1"):
    return {"person": person_name, "email": email, "name": "Source note",
            "kind": kind, "date": date, "hash": hash_,
            "interacted": interacted, "facts": [list(f) for f in facts],
            "updates": dict(updates or {}), "evidence": "extraction"}


def make(name="Jordan Pike", sid="dt:SRC-1", **kw):
    data = ec.new_candidate(name)
    ec.upsert_sighting(data, sid, sighting(person_name=name, **kw))
    return data


def listing(pending=(), approved=(), ignored=()):
    def rec(data, i):
        return {"uuid": f"CAND-{i}", "name": ec.record_name(data),
                "md": {}, "text": ec.render_candidate(data)}
    return {
        "pending": [rec(d, f"P{i}") for i, d in enumerate(pending)],
        "approved": [rec(d, f"A{i}") for i, d in enumerate(approved)],
        "ignored": [rec(d, f"I{i}") for i, d in enumerate(ignored)],
    }


class SightingIds(unittest.TestCase):
    def test_dt_id_embeds_the_source_uuid(self):
        self.assertEqual(ec.dt_sighting_id("SRC-1"), "dt:SRC-1")

    def test_cal_fingerprint_is_stable_and_person_free(self):
        ev = {"event_id": "e1", "calendar_id": "c1", "source_id": "s1",
              "start": "2026-07-13T09:00:00", "end": "2026-07-13T10:00:00",
              "title": "Lease review"}
        self.assertEqual(ec.cal_fingerprint(ev), ec.cal_fingerprint(dict(ev)))
        self.assertTrue(ec.cal_fingerprint(ev).startswith("cal:"))
        other = dict(ev, event_id="e2")
        self.assertNotEqual(ec.cal_fingerprint(ev), ec.cal_fingerprint(other))


class UpsertSighting(unittest.TestCase):
    def test_same_sid_replaces_not_appends(self):
        data = make(facts=[("2026-07-13", "old fact")])
        ec.upsert_sighting(data, "dt:SRC-1", sighting(
            facts=[("2026-07-14", "new fact")], hash_="h2"))
        self.assertEqual(len(data["sightings"]), 1)
        self.assertEqual(data["sightings"]["dt:SRC-1"]["facts"],
                         [["2026-07-14", "new fact"]])

    def test_observed_name_and_email_become_variants_and_emails(self):
        data = make()
        ec.upsert_sighting(data, "dt:SRC-2", sighting(
            person_name="J. Pike", email="JP@X.com"))
        self.assertIn("J. Pike", data["name_variants"])
        self.assertEqual(data["emails"], ["jp@x.com"])

    def test_fact_capture_marks_urgent(self):
        data = make()
        self.assertFalse(data["urgent"])
        ec.upsert_sighting(data, "dt:SRC-3", sighting(kind="fact"))
        self.assertTrue(data["urgent"])

    def test_facts_are_capped_per_sighting(self):
        data = make(facts=[("2026-07-13", f"fact {i}") for i in range(20)])
        self.assertEqual(
            len(data["sightings"]["dt:SRC-1"]["facts"]),
            ec.MAX_FACTS_PER_SIGHTING)

    def test_recurring_calendar_sightings_are_pruned_oldest_first(self):
        data = ec.new_candidate("Jordan Pike")
        for i in range(ec.MAX_CAL_SIGHTINGS + 3):
            ec.upsert_sighting(data, f"cal:{i:024d}", sighting(
                date=f"2026-06-{i + 1:02d}", kind="calendar"))
        self.assertEqual(len(data["sightings"]), ec.MAX_CAL_SIGHTINGS)
        self.assertNotIn("cal:" + "0" * 24, data["sightings"])

    def test_dt_sightings_are_never_pruned(self):
        data = ec.new_candidate("Jordan Pike")
        for i in range(ec.MAX_CAL_SIGHTINGS + 3):
            ec.upsert_sighting(data, f"dt:SRC-{i}", sighting())
        self.assertEqual(len(data["sightings"]), ec.MAX_CAL_SIGHTINGS + 3)


class Keys(unittest.TestCase):
    def test_name_only_candidate_claims_its_normalized_name(self):
        self.assertEqual(ec.candidate_keys(make()), ["jordan pike"])

    def test_email_keyed_candidate_claims_emails_not_name(self):
        data = make(email="jp@x.com")
        self.assertEqual(ec.candidate_keys(data), ["jp@x.com"])

    def test_detached_candidate_claims_nothing(self):
        data = make()
        data["detached"] = True
        self.assertEqual(ec.candidate_keys(data), [])


class RenderParse(unittest.TestCase):
    def test_round_trip(self):
        data = make(facts=[("2026-07-13", "moved to Denver")],
                    updates={"employer": "Initech"})
        self.assertEqual(ec.parse_candidate(ec.render_candidate(data)), data)

    def test_cr_endings_are_normalized(self):
        data = make()
        body = ec.render_candidate(data).replace("\n", "\r")
        self.assertEqual(ec.parse_candidate(body), data)

    def test_no_fence_raises(self):
        with self.assertRaises(ValueError):
            ec.parse_candidate("# Candidate: X\n\nno fence here\n")

    def test_wrong_version_raises(self):
        data = make()
        data["v"] = 99
        with self.assertRaises(ValueError):
            ec.parse_candidate(ec.render_candidate(data))

    def test_needs_confirmation_for_near_matches_and_single_tokens(self):
        self.assertTrue(ec.needs_confirmation(make(), ["Jordan Vale"]))
        self.assertTrue(ec.needs_confirmation(ec.new_candidate("Jordan"), []))
        self.assertFalse(ec.needs_confirmation(make(), []))

    def test_confirmation_instructions_render_when_needed(self):
        body = ec.render_candidate(make(), near=["Jordan Vale"])
        self.assertIn("TrackTarget", body)
        self.assertIn("CreateDistinct", body)

    def test_bounce_notice_renders(self):
        body = ec.render_candidate(make(), notice="alias collision")
        self.assertIn("Needs attention", body)
        self.assertIn("alias collision", body)


class Recompute(unittest.TestCase):
    def test_derived_fields_rebuild_from_sightings_alone(self):
        data = make(email="jp@x.com", kind="fact")
        ec.upsert_sighting(data, "dt:SRC-2",
                           sighting(person_name="J. Pike"))
        data["name_variants"] = ["Jordan Pike", "stale"]
        data["emails"] = ["stale@x.com", "jp@x.com"]
        data["urgent"] = False
        ec.recompute_derived(data)
        self.assertEqual(data["name_variants"], ["Jordan Pike", "J. Pike"])
        self.assertEqual(data["emails"], ["jp@x.com"])
        self.assertTrue(data["urgent"])


class Index(unittest.TestCase):
    def test_email_lookup_wins_over_name(self):
        keyed = make(email="jp@x.com")
        nameonly = make()
        idx = ec.CandidateIndex(listing(pending=[keyed, nameonly]))
        entry, action = idx.lookup("Jordan Pike", "jp@x.com")
        self.assertEqual(entry["data"], keyed)
        self.assertEqual(action, "attach")

    def test_email_miss_upgrades_a_lone_compatible_name_candidate(self):
        idx = ec.CandidateIndex(listing(pending=[make()]))
        entry, action = idx.lookup("Jordan Pike", "jp@x.com")
        self.assertEqual(action, "upgrade")
        self.assertIsNotNone(entry)

    def test_conflicting_email_creates_a_new_candidate(self):
        idx = ec.CandidateIndex(listing(pending=[make(email="jp1@x.com")]))
        entry, action = idx.lookup("Jordan Pike", "jp2@x.com")
        self.assertIsNone(entry)
        self.assertEqual(action, "create")

    def test_name_only_mention_never_lands_on_an_email_keyed_candidate(self):
        idx = ec.CandidateIndex(listing(pending=[make(email="jp@x.com")]))
        entry, action = idx.lookup("Jordan Pike")
        self.assertIsNone(entry)
        self.assertEqual(action, "create")

    def test_name_only_mention_attaches_via_variant(self):
        data = make()
        ec.add_variant(data, "J. Pike")
        idx = ec.CandidateIndex(listing(pending=[data]))
        entry, _action = idx.lookup("J. Pike")
        self.assertEqual(entry["data"], data)

    def test_detached_candidate_is_invisible_to_lookup(self):
        data = make()
        data["detached"] = True
        idx = ec.CandidateIndex(listing(pending=[data]))
        entry, action = idx.lookup("Jordan Pike")
        self.assertIsNone(entry)
        self.assertEqual(action, "create")

    def test_broken_fence_is_collected_not_indexed(self):
        lst = listing(pending=[make()])
        lst["pending"][0]["text"] = "no fence"
        idx = ec.CandidateIndex(lst)
        self.assertEqual(len(idx.broken), 1)
        self.assertIsNone(idx.lookup("Jordan Pike")[0])

    def test_quarantined_records_are_skipped_entirely(self):
        lst = listing(pending=[make()])
        lst["pending"][0]["name"] = ec.QUARANTINE_PREFIX + "Candidate: X"
        idx = ec.CandidateIndex(lst)
        self.assertEqual(idx.broken, [])
        self.assertEqual(idx.entries, [])

    def test_ignored_names_cover_canonical_and_variants_of_all_kinds(self):
        keyed = make(email="jp@x.com")
        ec.add_variant(keyed, "J. Pike")
        idx = ec.CandidateIndex(listing(ignored=[keyed]))
        self.assertEqual(idx.ignored_names(),
                         {"jordan pike", "j. pike"})

    def test_email_peers_lists_same_named_keyed_candidates(self):
        keyed = make(email="jp@x.com")
        idx = ec.CandidateIndex(listing(pending=[keyed]))
        self.assertEqual(idx.email_peers("Jordan Pike"),
                         [(ec.record_name(keyed), "CAND-P0")])


class UpsertMentions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.original_lock = ec.CANDIDATE_LOCK_FILE
        ec.CANDIDATE_LOCK_FILE = os.path.join(self.dir, "candidates.lock")
        import logging
        self.log = logging.getLogger("test-ec")
        self.log.addHandler(logging.NullHandler())
        self.log.propagate = False

    def tearDown(self):
        ec.CANDIDATE_LOCK_FILE = self.original_lock
        shutil.rmtree(self.dir, ignore_errors=True)

    def bridge_for(self, lst=None, people=()):
        calls = []

        def bridge(ops):
            calls.append(ops)
            results = []
            for op in ops:
                if op["op"] == "list_candidates":
                    results.append(
                        lst or {"pending": [], "approved": [], "ignored": []})
                elif op["op"] == "dump_people":
                    results.append(list(people))
                else:
                    results.append({"uuid": "NEW-1"})
            return results

        bridge.calls = calls
        return bridge

    def mention(self, name="Jordan Pike", email="", sid="dt:SRC-1"):
        return {"name": name, "email": email, "sid": sid,
                "sighting": sighting(person_name=name, email=email)}

    def test_created_disposition_writes_one_pending_record(self):
        bridge = self.bridge_for()
        out = ec.upsert_mentions(bridge, [self.mention()], self.log)
        self.assertEqual([d for _m, d in out], ["created"])
        creates = [o for ops in bridge.calls for o in ops
                   if o["op"] == "create_record"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["fields"], {"entitytype": "Candidate"})

    def test_roster_hit_resolves_instead_of_creating(self):
        bridge = self.bridge_for(
            people=[person("Jordan Pike", email="jp@x.com")])
        out = ec.upsert_mentions(
            bridge, [self.mention(email="jp@x.com")], self.log)
        self.assertEqual([d for _m, d in out], ["resolved"])
        self.assertEqual(
            [o for ops in bridge.calls for o in ops
             if o["op"] in ("create_record", "set_text")], [])

    def test_roster_alias_hit_resolves_a_bare_name(self):
        bridge = self.bridge_for(
            people=[person("Jordan Vale", aliases="Jordan Pike")])
        out = ec.upsert_mentions(bridge, [self.mention()], self.log)
        self.assertEqual([d for _m, d in out], ["resolved"])

    def test_ignored_name_is_dropped(self):
        lst = listing(ignored=[make()])
        bridge = self.bridge_for(lst)
        out = ec.upsert_mentions(bridge, [self.mention()], self.log)
        self.assertEqual([d for _m, d in out], ["ignored"])

    def test_two_mentions_of_one_new_person_share_one_created_record(self):
        bridge = self.bridge_for()
        out = ec.upsert_mentions(
            bridge,
            [self.mention(sid="dt:SRC-1"), self.mention(sid="dt:SRC-2")],
            self.log)
        self.assertEqual([d for _m, d in out], ["created", "attached"])
        creates = [o for ops in bridge.calls for o in ops
                   if o["op"] == "create_record"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(
            len(ec.parse_candidate(creates[0]["text"])["sightings"]), 2)

    def test_broken_records_are_quarantined_not_overwritten(self):
        lst = listing(pending=[make()])
        lst["pending"][0]["text"] = "no fence"
        bridge = self.bridge_for(lst)
        ec.upsert_mentions(bridge, [self.mention(name="Someone Else")],
                           self.log)
        renames = [o for ops in bridge.calls for o in ops
                   if o["op"] == "set_name"]
        self.assertEqual(len(renames), 1)
        self.assertTrue(renames[0]["name"].startswith(ec.QUARANTINE_PREFIX))
        sets = [o for ops in bridge.calls for o in ops
                if o["op"] == "set_text" and o["uuid"] == "CAND-P0"]
        self.assertEqual(sets, [])

    def test_dry_run_writes_nothing(self):
        bridge = self.bridge_for()
        out = ec.upsert_mentions(bridge, [self.mention()], self.log,
                                 dry_run=True)
        self.assertEqual([d for _m, d in out], ["created"])
        self.assertEqual(
            [o for ops in bridge.calls for o in ops
             if o["op"] in ("create_record", "set_text")], [])


class NormalizerParity(unittest.TestCase):
    def test_casefold_parity_with_entity_filing(self):
        for s in ("STRASSE", "Straße", "  Renée   VAN Dam ", "İstanbul"):
            self.assertEqual(ec.norm(s), ef.norm(s), s)

    def test_email_norm_strips_mailto(self):
        self.assertEqual(ec.norm_email("mailto:JP@X.com"), "jp@x.com")


if __name__ == "__main__":
    unittest.main()
