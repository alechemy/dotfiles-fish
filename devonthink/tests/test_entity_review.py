"""Entity Review server: queue views, decision ops, request gating, and the
structured-edit approval path."""

import json
import unittest

from helpers import load, person

srv = load("entity-review-server.py", "entity_review_server")
ec = srv.ec
ef = srv.ef

FEN = person("Fenwick Doyle")
SAB = person("Sable Trent", email="sable@x.com")
PEOPLE = [FEN, SAB]
SRC = {"uuid": "SRC-0000-UUID", "name": "Trip Notes", "kind": "note",
       "eventdate": "2026-07-01", "added": "2026-07-01",
       "modified": "2026-07-01T00:00:00"}


def sighting(date="2026-07-10", kind="extraction", facts=(), updates=None,
             interacted=False, title="A note"):
    return {"person": "", "email": "", "kind": kind, "date": date,
            "hash": "h" * 8, "interacted": interacted,
            "facts": [list(f) for f in facts], "updates": updates or {},
            "evidence": kind, "name": title}


def make_candidate(name, sightings=(), emails=()):
    data = ec.new_candidate(name)
    for i, s in enumerate(sightings):
        ec.upsert_sighting(data, f"dt:SRC-{i:04d}-UUID", s)
    for e in emails:
        ec.add_email(data, e)
    return data


def entry_for(data, uuid="CAND-0001-UUID", md=None, notice="", near=()):
    return {"uuid": uuid, "name": ec.record_name(data), "md": md or {},
            "text": ec.render_candidate(data, near=near, notice=notice)}


def index():
    return ef.roster_index(PEOPLE)


class Gate(unittest.TestCase):
    def test_foreign_host_without_marker_is_refused(self):
        self.assertTrue(srv.gate({"Host": "evil.example"}, "GET"))

    def test_loopback_hosts_pass(self):
        self.assertEqual(srv.gate({"Host": "127.0.0.1:7819"}, "GET"), "")
        self.assertEqual(srv.gate({"Host": "localhost"}, "GET"), "")

    def test_proxied_tailnet_host_needs_the_marker(self):
        headers = {"Host": "mac.tailnet.ts.net"}
        self.assertTrue(srv.gate(headers, "GET"))
        headers["X-Entity-Review"] = "1"
        self.assertEqual(srv.gate(headers, "GET"), "")

    def test_foreign_origin_post_is_refused(self):
        self.assertTrue(srv.gate(
            {"Host": "127.0.0.1", "Origin": "https://evil.example"}, "POST"))

    def test_null_origin_post_is_refused(self):
        self.assertTrue(srv.gate({"Host": "127.0.0.1", "Origin": "null"},
                                 "POST"))

    def test_same_origin_post_passes(self):
        self.assertEqual(srv.gate(
            {"Host": "mac.tailnet.ts.net", "X-Entity-Review": "1",
             "Origin": "https://mac.tailnet.ts.net"}, "POST"), "")

    def test_cross_site_fetch_metadata_is_refused(self):
        self.assertTrue(srv.gate(
            {"Host": "127.0.0.1", "Sec-Fetch-Site": "cross-site"}, "POST"))


class CandidateDecisionOps(unittest.TestCase):
    def test_plain_track_clears_gestures_and_moves_to_approved(self):
        ops = srv.candidate_decision_ops("track", "CAND-0001-UUID")
        self.assertEqual(ops[0]["fields"],
                         {"TrackTarget": "", "CreateDistinct": 0})
        self.assertEqual(ops[1], {"op": "move_to", "uuid": "CAND-0001-UUID",
                                  "group": ec.CANDIDATES_APPROVED_PATH})

    def test_track_into_target_sets_tracktarget(self):
        ops = srv.candidate_decision_ops("track", "CAND-0001-UUID",
                                         target=FEN["uuid"])
        self.assertEqual(ops[0]["fields"]["TrackTarget"], FEN["uuid"])

    def test_track_distinct_sets_createdistinct(self):
        ops = srv.candidate_decision_ops("track", "CAND-0001-UUID",
                                         distinct=True)
        self.assertEqual(ops[0]["fields"]["CreateDistinct"], 1)

    def test_target_and_distinct_together_are_refused(self):
        with self.assertRaises(srv.RequestError):
            srv.candidate_decision_ops("track", "CAND-0001-UUID",
                                       target=FEN["uuid"], distinct=True)

    def test_ignore_forget_undo_shapes(self):
        self.assertEqual(
            srv.candidate_decision_ops("ignore", "U-00000001")[0]["group"],
            ec.CANDIDATES_IGNORED_PATH)
        self.assertEqual(
            srv.candidate_decision_ops("forget", "U-00000001")[0]["op"],
            "trash")
        undo = srv.candidate_decision_ops("undo", "U-00000001")
        self.assertEqual(undo[0]["fields"],
                         {"TrackTarget": "", "CreateDistinct": 0})
        self.assertEqual(undo[1]["group"], ec.CANDIDATES_PATH)

    def test_unknown_action_is_refused(self):
        with self.assertRaises(srv.RequestError):
            srv.candidate_decision_ops("promote", "U-00000001")


class ProposalDecisionOps(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(
            srv.proposal_decision_ops("approve", "P-00000001")[0]["group"],
            ef.APPROVED_PATH)
        self.assertEqual(
            srv.proposal_decision_ops("reject", "P-00000001")[0]["op"],
            "trash")
        self.assertEqual(
            srv.proposal_decision_ops("undo", "P-00000001")[0]["group"],
            ef.REVIEW_PATH)
        with self.assertRaises(srv.RequestError):
            srv.proposal_decision_ops("defer", "P-00000001")


class CandidateDisposition(unittest.TestCase):
    def dispo(self, data, md=None):
        return srv.candidate_disposition(data, md or {}, PEOPLE, index())

    def test_single_roster_hit_files_into_that_person(self):
        d = self.dispo(make_candidate("Fenwick Doyle"))
        self.assertEqual(d["kind"], "file_into")
        self.assertEqual(d["target"], {"uuid": FEN["uuid"],
                                       "name": "Fenwick Doyle"})

    def test_conflated_identifiers_are_ambiguous_with_no_choices(self):
        d = self.dispo(make_candidate("Fenwick Doyle",
                                      emails=["sable@x.com"]))
        self.assertEqual(d["kind"], "ambiguous")
        self.assertEqual({h["uuid"] for h in d["hits"]},
                         {FEN["uuid"], SAB["uuid"]})
        self.assertEqual(d["choices"], [])

    def test_shared_name_ambiguity_offers_both_as_choices(self):
        people = [person("Fenwick Doyle"),
                  person("Fenwick Doyle", uuid="uuid-fenwick-2")]
        d = srv.candidate_disposition(make_candidate("Fenwick Doyle"), {},
                                      people, ef.roster_index(people))
        self.assertEqual(d["kind"], "ambiguous")
        self.assertEqual({c["uuid"] for c in d["choices"]},
                         {"uuid-fenwick-doyle", "uuid-fenwick-2"})

    def test_single_token_name_needs_choice(self):
        self.assertEqual(self.dispo(make_candidate("Juniper"))["kind"],
                         "needs_choice")

    def test_shared_surname_needs_choice_with_resolvable_near(self):
        d = self.dispo(make_candidate("Karsten Doyle"))
        self.assertEqual(d["kind"], "needs_choice")
        self.assertEqual(d["near"][0], {"name": "Fenwick Doyle",
                                        "uuid": FEN["uuid"]})

    def test_clean_two_token_name_is_new_ok(self):
        self.assertEqual(self.dispo(make_candidate("Tobias Quill"))["kind"],
                         "new_ok")

    def test_createdistinct_metadata_lifts_the_confirmation_gate(self):
        d = self.dispo(make_candidate("Juniper"),
                       md={"mdcreatedistinct": "1"})
        self.assertEqual(d["kind"], "new_ok")

    def test_tracktarget_metadata_resolves_to_that_person(self):
        d = self.dispo(make_candidate("Juniper"),
                       md={"mdtracktarget": FEN["uuid"]})
        self.assertEqual(d["kind"], "file_into")
        self.assertEqual(d["target"]["uuid"], FEN["uuid"])


class CandidateRenameOps(unittest.TestCase):
    def test_rename_rerenders_and_keeps_the_old_name_as_variant(self):
        data = make_candidate("Juniper")
        ops = srv.candidate_rename_ops("CAND-0001-UUID", "Juniper Wask",
                                       ec.render_candidate(data), PEOPLE)
        self.assertEqual([op["op"] for op in ops], ["set_text", "set_name"])
        self.assertEqual(ops[1]["name"], "Candidate: Juniper Wask")
        renamed = ec.parse_candidate(ops[0]["text"])
        self.assertEqual(renamed["name"], "Juniper Wask")
        self.assertIn("Juniper", renamed["name_variants"])

    def test_unchanged_name_is_a_no_op(self):
        data = make_candidate("Juniper")
        self.assertEqual(
            srv.candidate_rename_ops("CAND-0001-UUID", " juniper ",
                                     ec.render_candidate(data), PEOPLE), [])

    def test_unreadable_body_refuses_rename(self):
        with self.assertRaises(srv.RequestError):
            srv.candidate_rename_ops("CAND-0001-UUID", "Juniper Wask",
                                     "no fence", PEOPLE)


class CandidateView(unittest.TestCase):
    def test_view_carries_evidence_and_flags(self):
        data = make_candidate("Tobias Quill", sightings=[
            sighting(date="2026-07-05",
                     facts=[("2026-07-05", "Moved to Oslo.")]),
            sighting(date="2026-07-08", kind="fact", title="Fact capture",
                     facts=[("2026-07-08", "Runs a bakery.")]),
        ])
        view = srv.candidate_view(entry_for(data), PEOPLE, index(), {})
        self.assertFalse(view["broken"])
        self.assertTrue(view["urgent"])
        self.assertEqual(view["seen"], {"count": 2, "last": "2026-07-08"})
        self.assertEqual(view["sightings"][0]["facts"],
                         [["2026-07-05", "Moved to Oslo."]])
        self.assertEqual(view["sightings"][0]["source_uuid"], "SRC-0000-UUID")

    def test_bounce_notice_surfaces(self):
        view = srv.candidate_view(
            entry_for(make_candidate("Tobias Quill"),
                      notice="approval needs confirmation"),
            PEOPLE, index(), {})
        self.assertEqual(view["notice"], "approval needs confirmation")

    def test_unparseable_body_marks_broken(self):
        view = srv.candidate_view(
            {"uuid": "X-0000-UUID", "name": "Candidate: Junk", "md": {},
             "text": "no data fence"}, PEOPLE, index(), {})
        self.assertTrue(view["broken"])


class BuildQueueTest(unittest.TestCase):
    def listing(self, pending, approved=()):
        return {"pending": list(pending), "approved": list(approved),
                "ignored": []}

    def test_urgent_candidates_sort_first_and_queued_counts(self):
        plain = entry_for(make_candidate("Tobias Quill"),
                          uuid="CAND-0001-UUID")
        urgent = entry_for(
            make_candidate("Marta Quill", sightings=[
                sighting(kind="fact", facts=[("2026-07-08", "A fact.")])]),
            uuid="CAND-0002-UUID")
        queue = srv.build_queue(
            self.listing([plain, urgent], approved=[plain]),
            {"pending": [], "approved": [{"uuid": "P-00000001",
                                          "name": "File: X", "md": {},
                                          "text": ""}]},
            PEOPLE)
        self.assertEqual([c["uuid"] for c in queue["candidates"]],
                         ["CAND-0002-UUID", "CAND-0001-UUID"])
        self.assertEqual(queue["queued"],
                         {"candidates": 1, "proposals": 1})

    def test_same_name_pending_candidates_become_peers(self):
        a = entry_for(make_candidate("Tobias Quill"), uuid="CAND-0001-UUID")
        b = entry_for(make_candidate("Tobias Quill",
                                     emails=["tq@x.com"]),
                      uuid="CAND-0002-UUID")
        queue = srv.build_queue(self.listing([a, b]),
                                {"pending": [], "approved": []}, PEOPLE)
        by_uuid = {c["uuid"]: c for c in queue["candidates"]}
        self.assertEqual(by_uuid["CAND-0001-UUID"]["peers"],
                         [{"uuid": "CAND-0002-UUID", "name": "Tobias Quill"}])

    def test_roster_is_sorted_with_alias_lists(self):
        queue = srv.build_queue(self.listing([]),
                                {"pending": [], "approved": []},
                                [person("Zed Ora", aliases="Z, Zeddy"),
                                 person("Ana Bell")])
        self.assertEqual([r["name"] for r in queue["roster"]],
                         ["Ana Bell", "Zed Ora"])
        self.assertEqual(queue["roster"][1]["aliases"], ["Z", "Zeddy"])


def ops_for_existing():
    lines = [ef.fact_line("2026-07-01", f"Fact number {i}", SRC["uuid"])
             for i in range(2)]
    return [
        {"op": "set_field", "uuid": FEN["uuid"], "field": "city",
         "value": "Oslo", "effective_date": "2026-07-01",
         "expected_previous": ""},
        {"op": "append_log", "uuid": FEN["uuid"], "lines": lines},
        {"op": "bump_lastcontact", "uuid": FEN["uuid"], "date": "2026-07-01"},
        {"op": "mark_filed", "uuid": SRC["uuid"]},
    ]


def prop_text(ops):
    return "\n".join([
        "# File: Trip Notes", "",
        f"Source: [Trip Notes](x-devonthink-item://{SRC['uuid']})"
        " (2026-07-01)",
        "", "## Ops", "", "```json", json.dumps(ops, indent=2), "```", ""])


def prop_entry(text, uuid="PROP-0001-UUID"):
    return {"uuid": uuid, "name": "File: Trip Notes", "md": {}, "text": text}


class ProposalView(unittest.TestCase):
    def test_editable_proposal_renders_plans_and_source(self):
        view = srv.proposal_view(prop_entry(prop_text(ops_for_existing())),
                                 PEOPLE)
        self.assertFalse(view["broken"])
        self.assertTrue(view["editable"])
        self.assertEqual(view["source"],
                         {"uuid": SRC["uuid"], "name": "Trip Notes",
                          "date": "2026-07-01"})
        plan = view["plans"][0]
        self.assertEqual(plan["kind"], "existing")
        self.assertEqual(plan["name"], "Fenwick Doyle")
        self.assertTrue(plan["interacted"])
        self.assertEqual(plan["updates"], {"city": "Oslo"})
        self.assertEqual(len(plan["facts"]), 2)

    def test_unknown_op_freezes_editing(self):
        ops = ops_for_existing() + [{"op": "sort_logs"}]
        view = srv.proposal_view(prop_entry(prop_text(ops)), PEOPLE)
        self.assertFalse(view["editable"])

    def test_capture_stub_is_empty_with_captured_text(self):
        text = ef.fallback_review_body(SRC, "2026-07-01",
                                       "raw captured words")
        view = srv.proposal_view(prop_entry(text), PEOPLE)
        self.assertTrue(view["empty"])
        self.assertFalse(view["editable"])
        self.assertEqual(view["captured"], "raw captured words")

    def test_missing_fence_marks_broken(self):
        view = srv.proposal_view(prop_entry("# File: X\n\nno fence"), PEOPLE)
        self.assertTrue(view["broken"])


class SpecFromPayload(unittest.TestCase):
    def test_valid_payload_round_trips(self):
        people_ext, events_ext = srv.spec_from_payload({
            "people": [{"name": "Fenwick Doyle", "interacted": True,
                        "facts": [{"date": "2026-07-01", "fact": "Sails."}],
                        "updates": {"city": "Oslo"}}],
            "events": [{"name": "Harbor Day", "date": "2026-07-01",
                        "location": "", "attendees": ["Fenwick Doyle"],
                        "summary": "A fine day."}],
        })
        self.assertEqual(people_ext[0]["match"], None)
        self.assertEqual(people_ext[0]["updates"], {"city": "Oslo"})
        self.assertEqual(events_ext[0]["location"], None)
        self.assertEqual(events_ext[0]["attendees"], ["Fenwick Doyle"])

    def test_factless_person_is_dropped_not_an_error(self):
        with self.assertRaises(srv.RequestError):
            srv.spec_from_payload({"people": [
                {"name": "Fenwick Doyle", "facts": [], "updates": {}}]})

    def test_bad_date_and_unknown_field_are_refused(self):
        with self.assertRaises(srv.RequestError):
            srv.spec_from_payload({"people": [
                {"name": "A B", "facts": [{"date": "yesterday",
                                           "fact": "x"}]}]})
        with self.assertRaises(srv.RequestError):
            srv.spec_from_payload({"people": [
                {"name": "A B", "facts": [],
                 "updates": {"shoe_size": "12"}}]})

    def test_limits_are_hard_errors(self):
        facts = [{"date": "2026-07-01", "fact": f"f{i}"} for i in range(13)]
        with self.assertRaises(srv.RequestError):
            srv.spec_from_payload({"people": [{"name": "A B",
                                               "facts": facts}]})
        with self.assertRaises(srv.RequestError):
            srv.spec_from_payload({"events": [
                {"name": "E", "date": "2026-07-01",
                 "attendees": [f"a{i} b" for i in range(21)]}]})


class ScriptedBridge:
    def __init__(self, text, people, source):
        self.text, self.people, self.source = text, people, source
        self.batches = []

    def __call__(self, ops, timeout=None):
        self.batches.append(ops)
        out = []
        for op in ops:
            if op["op"] == "get_text":
                out.append({"uuid": op["uuid"], "text": self.text})
            elif op["op"] == "dump_people":
                out.append(self.people)
            elif op["op"] == "get_source":
                out.append(dict(self.source))
            else:
                out.append({"uuid": op.get("uuid", "")})
        return out


class ApproveWithEdits(unittest.TestCase):
    def test_dropping_a_fact_regenerates_the_fence(self):
        bridge = ScriptedBridge(prop_text(ops_for_existing()), PEOPLE, SRC)
        payload = {"people": [{
            "name": "Fenwick Doyle", "interacted": True,
            "facts": [{"date": "2026-07-01", "fact": "Fact number 0."}],
            "updates": {"city": "Oslo"}}], "events": []}
        res = srv.approve_with_edits("PROP-0001-UUID", payload,
                                     confirm=False, bridge=bridge)
        self.assertEqual(res, {"ok": True})
        final = bridge.batches[-1]
        self.assertEqual([op["op"] for op in final], ["set_text", "move_to"])
        self.assertEqual(final[1]["group"], ef.APPROVED_PATH)
        new_ops = ef.proposal_ops(final[0]["text"])
        logs = [op for op in new_ops if op["op"] == "append_log"]
        self.assertEqual(len(logs), 1)
        self.assertEqual(len(logs[0]["lines"]), 1)
        self.assertIn("Fact number 0.", logs[0]["lines"][0])
        self.assertNotIn("Fact number 1", final[0]["text"])

    def test_new_person_resembling_roster_needs_confirm_then_applies(self):
        ops = [{"op": "ensure_person", "name": "Karsten Doyle", "fields": {},
                "log_lines": [ef.fact_line("2026-07-01", "Sails often",
                                           SRC["uuid"])]},
               {"op": "mark_filed", "uuid": SRC["uuid"]}]
        payload = {"people": [{
            "name": "Karsten Doyle", "interacted": False,
            "facts": [{"date": "2026-07-01", "fact": "Sails often."}],
            "updates": {}}], "events": []}
        bridge = ScriptedBridge(prop_text(ops), PEOPLE, SRC)
        res = srv.approve_with_edits("PROP-0001-UUID", payload,
                                     confirm=False, bridge=bridge)
        self.assertEqual(res["confirm"][0]["name"], "Karsten Doyle")
        self.assertNotEqual(bridge.batches[-1][-1]["op"], "move_to")

        bridge2 = ScriptedBridge(prop_text(ops), PEOPLE, SRC)
        res2 = srv.approve_with_edits("PROP-0001-UUID", payload,
                                      confirm=True, bridge=bridge2)
        self.assertEqual(res2, {"ok": True})
        new_ops = ef.proposal_ops(bridge2.batches[-1][0]["text"])
        ensure = next(op for op in new_ops if op["op"] == "ensure_person")
        self.assertTrue(ensure["confirm_new"])

    def test_frozen_proposal_refuses_edits(self):
        text = ef.fallback_review_body(SRC, "2026-07-01", "words")
        bridge = ScriptedBridge(text, PEOPLE, SRC)
        with self.assertRaises(srv.RequestError):
            srv.approve_with_edits("PROP-0001-UUID", {"people": []},
                                   confirm=False, bridge=bridge)


class HandlerPlumbing(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.kicks = []
        self._run_bridge = ef.run_bridge
        self._scheduler = srv.scheduler
        ef.run_bridge = lambda ops, timeout=300: (
            self.calls.append(ops) or [{"uuid": ""}] * len(ops))
        outer = self

        class Sched:
            def kick(self, delay=None):
                outer.kicks.append(delay)

            def status(self):
                return {"state": "idle", "last_result": "",
                        "last_finished": ""}

        srv.scheduler = Sched()

    def tearDown(self):
        ef.run_bridge = self._run_bridge
        srv.scheduler = self._scheduler

    def test_candidate_decision_runs_ops_and_kicks_apply(self):
        res = srv.handle_candidate("CAND-0001-UUID",
                                   {"action": "track",
                                    "target": FEN["uuid"]})
        self.assertEqual(res, {"ok": True})
        self.assertEqual(self.calls[0][1]["group"],
                         ec.CANDIDATES_APPROVED_PATH)
        self.assertEqual(len(self.kicks), 1)

    def test_undo_does_not_kick_apply(self):
        srv.handle_candidate("CAND-0001-UUID", {"action": "undo"})
        self.assertEqual(self.kicks, [])

    def test_corrected_name_prepends_rename_ops(self):
        text = ec.render_candidate(make_candidate("Juniper"))

        def bridge(ops, timeout=300):
            self.calls.append(ops)
            out = []
            for op in ops:
                if op["op"] == "get_text":
                    out.append({"uuid": op["uuid"], "text": text})
                elif op["op"] == "dump_people":
                    out.append(PEOPLE)
                else:
                    out.append({"uuid": op.get("uuid", "")})
            return out

        ef.run_bridge = bridge
        srv.handle_candidate("CAND-0001-UUID",
                             {"action": "track", "distinct": True,
                              "name": "Juniper Wask"})
        ops = self.calls[-1]
        self.assertEqual([op["op"] for op in ops],
                         ["set_text", "set_name", "set_fields", "move_to"])

    def test_corrected_name_with_target_is_refused(self):
        with self.assertRaises(srv.RequestError):
            srv.handle_candidate("CAND-0001-UUID",
                                 {"action": "track", "target": FEN["uuid"],
                                  "name": "Juniper Wask"})

    def test_proposal_reject_trashes(self):
        srv.handle_proposal("PROP-0001-UUID", {"action": "reject"})
        self.assertEqual(self.calls[0][0]["op"], "trash")
        self.assertEqual(len(self.kicks), 1)

    def test_queue_reports_closed_devonthink(self):
        def unavailable(ops, timeout=300):
            raise ef.BridgeUnavailable("not running")
        ef.run_bridge = unavailable
        queue = srv.handle_queue()
        self.assertEqual(queue["dt"], "closed")
        self.assertEqual(queue["candidates"], [])
        self.assertEqual(queue["apply"]["state"], "idle")


if __name__ == "__main__":
    unittest.main()
