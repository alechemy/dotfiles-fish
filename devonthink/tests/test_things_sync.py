import json
import logging
import os
import tempfile
import unittest

from helpers import capture_logs, load, person

ef = load("entity-filing.py", "entity_filing_things")
tb = load("things_bridge.py", "things_bridge_under_test")

SOURCE = {"uuid": "SRC-1", "name": "2026-03-16 Call", "eventdate": "2026-03-16"}
DATE = "2026-03-16"


def existing_plan(**kw):
    plan = {"kind": "existing", "name": "Alison Vance", "uuid": "P-1",
            "md": {}, "weak_match": False, "interacted": True,
            "facts": [(DATE, "Ran a marathon.")],
            "updates": {"employer": "Acme"}}
    plan.update(kw)
    return plan


def new_plan(**kw):
    plan = {"kind": "new", "name": "Sam Reyes", "single_token": False,
            "near": [], "interacted": False,
            "facts": [(DATE, "Works on ML infra.")], "updates": {}}
    plan.update(kw)
    return plan


def event_plan(**kw):
    plan = {"kind": "event", "name": "Portland Hiking Trip", "date": "2026-07-05",
            "location": "Forest Park", "attendees": ["Alison Vance", "Sam Reyes"],
            "summary": "Day hike to celebrate the launch."}
    plan.update(kw)
    return plan


def note_for(plans):
    return ef.things_note_body("SRC-1", "PROP-1", plans)


def fence_body(ops):
    return "# File: X\n\n## Ops\n\n```json\n" + json.dumps(ops) + "\n```\n"


class NoteRoundTrip(unittest.TestCase):
    def test_people_and_event_round_trip(self):
        note = note_for([existing_plan(), new_plan(), event_plan()])
        people, events = ef.parse_things_note(note)
        self.assertEqual(people, [
            {"name": "Alison Vance", "match": None, "interacted": True,
             "facts": [{"date": DATE, "fact": "Ran a marathon."}],
             "updates": {"employer": "Acme"}},
            {"name": "Sam Reyes", "match": None, "interacted": False,
             "facts": [{"date": DATE, "fact": "Works on ML infra."}],
             "updates": {}},
        ])
        self.assertEqual(events, [
            {"name": "Portland Hiking Trip", "date": "2026-07-05",
             "location": "Forest Park",
             "attendees": ["Alison Vance", "Sam Reyes"],
             "summary": "Day hike to celebrate the launch."}])

    def test_marker_line_survives_and_resolves(self):
        note = note_for([new_plan()])
        self.assertEqual(ef.proposal_uuid_from_notes(note), "PROP-1")

    def test_event_without_location_or_summary(self):
        note = note_for([event_plan(location="", summary="", attendees=[])])
        _, events = ef.parse_things_note(note)
        self.assertEqual(events, [
            {"name": "Portland Hiking Trip", "date": "2026-07-05",
             "location": None, "attendees": [], "summary": None}])

    def test_parsed_output_feeds_plan_builders(self):
        note = note_for([existing_plan()])
        people_ext, events_ext = ef.parse_things_note(note)
        roster = [person("Alison Vance", uuid="P-1")]
        plans = ef.build_person_plans(people_ext, ef.roster_index(roster),
                                      set(), roster, DATE)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["kind"], "existing")
        self.assertEqual(plans[0]["uuid"], "P-1")
        self.assertTrue(plans[0]["interacted"])


class GrammarStrictness(unittest.TestCase):
    def parse(self, *spec_lines):
        return ef.parse_things_note(
            "intro ignored\n" + ef.SPEC_SENTINEL + "\n" + "\n".join(spec_lines))

    def assert_error(self, fragment, *spec_lines):
        with self.assertRaises(ef.SpecParseError) as ctx:
            self.parse(*spec_lines)
        self.assertIn(fragment, str(ctx.exception))

    def test_ios_dash_variants_parse(self):
        for sep in ("—", "–", "--", "-"):
            people, _ = self.parse("PERSON Maya Chen (new)",
                                   f"- 2026-03-16 {sep} Moved to Lisbon.")
            self.assertEqual(people[0]["facts"],
                             [{"date": DATE, "fact": "Moved to Lisbon."}])

    def test_trailing_whitespace_tolerated(self):
        people, _ = self.parse("PERSON Maya Chen (new)   ",
                               "- 2026-03-16 — Moved to Lisbon.  ")
        self.assertEqual(people[0]["name"], "Maya Chen")

    def test_inner_parenthetical_kept_in_name(self):
        people, _ = self.parse("PERSON Jane (JJ) Doe (new, met)",
                               "- 2026-03-16 — Met at the pier.")
        self.assertEqual(people[0]["name"], "Jane (JJ) Doe")
        self.assertTrue(people[0]["interacted"])

    def test_unknown_parenthetical_stays_in_name(self):
        people, _ = self.parse("PERSON Maya Chen (the sculptor)",
                               "- 2026-03-16 — Moved to Lisbon.")
        self.assertEqual(people[0]["name"], "Maya Chen (the sculptor)")

    def test_location_containing_at_and_parens(self):
        _, events = self.parse("EVENT Dinner (2026-07-05 at Cafe (Rear) at Pier 9)")
        self.assertEqual(events[0]["location"], "Cafe (Rear) at Pier 9")

    def test_sentinel_required_exactly_once(self):
        with self.assertRaises(ef.SpecParseError):
            ef.parse_things_note("PERSON Maya Chen (new)")
        self.assert_error("exactly one", ef.SPEC_SENTINEL,
                          "PERSON Maya Chen (new)")

    def test_fact_without_date_errors(self):
        self.assert_error("unparseable", "PERSON Maya Chen (new)",
                          "- Met at the conference.")

    def test_invalid_date_errors(self):
        self.assert_error("EVENT line", "EVENT Party (2026-02-30)")

    def test_duplicate_field_errors(self):
        self.assert_error("duplicate 'employer'", "PERSON Maya Chen (new)",
                          "- employer = Acme", "- employer = Initech")

    def test_duplicate_person_errors(self):
        self.assert_error("duplicate person", "PERSON Maya Chen (new)",
                          "- employer = Acme", "PERSON maya chen (new)",
                          "- role = CTO")

    def test_child_line_before_header_errors(self):
        self.assert_error("belongs to no", "- 2026-03-16 — Orphan fact.")

    def test_control_characters_error(self):
        self.assert_error("control characters", "PERSON Maya\x07Chen (new)")

    def test_second_event_summary_errors(self):
        self.assert_error("more than one summary",
                          "EVENT Party (2026-07-05)",
                          "- 2026-07-05 — First summary.",
                          "- 2026-07-05 — Second summary.")

    def test_unknown_field_errors(self):
        self.assert_error("unparseable", "PERSON Maya Chen (new)",
                          "- nickname = MC")

    def test_zero_entries_error(self):
        self.assert_error("no PERSON or EVENT")

    def test_fact_too_long_errors(self):
        self.assert_error("fact too long", "PERSON Maya Chen (new)",
                          "- 2026-03-16 — " + "x" * 401)

    def test_too_many_facts_errors(self):
        lines = [f"- 2026-03-{d:02d} — Fact number {d}." for d in range(1, 14)]
        self.assert_error("more than 12 facts", "PERSON Maya Chen (new)", *lines)

    def test_too_many_attendees_errors(self):
        names = ", ".join(f"Guest {i}" for i in range(21))
        self.assert_error("more than 20 attendees",
                          "EVENT Party (2026-07-05)", f"- with: {names}")

    def test_event_name_too_long_errors(self):
        self.assert_error("event name too long",
                          f"EVENT {'x' * 81} (2026-07-05)")


class PlansFromOps(unittest.TestCase):
    def invert(self, plans, people=()):
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": SOURCE["uuid"]})
        return ef.plans_from_ops(ops, list(people))

    def test_existing_plan_inverts_via_roster(self):
        plans, editable, src = self.invert(
            [existing_plan()], [person("Alison Vance", uuid="P-1")])
        self.assertTrue(editable)
        self.assertEqual(src, "SRC-1")
        self.assertEqual(plans, [{
            "kind": "existing", "name": "Alison Vance", "interacted": True,
            "facts": [(DATE, "Ran a marathon.")],
            "updates": {"employer": "Acme"}}])

    def test_new_plan_inverts_without_roster(self):
        plans, editable, _ = self.invert([new_plan(interacted=True)])
        self.assertTrue(editable)
        self.assertEqual(plans, [{
            "kind": "new", "name": "Sam Reyes", "interacted": True,
            "facts": [(DATE, "Works on ML infra.")], "updates": {}}])

    def test_event_plan_inverts(self):
        plans, editable, _ = self.invert([event_plan()])
        self.assertTrue(editable)
        self.assertEqual(plans, [event_plan()])

    def test_uuid_missing_from_roster_disables_editing(self):
        plans, editable, _ = self.invert([existing_plan()], [])
        self.assertFalse(editable)
        self.assertEqual(plans, [])

    def test_hand_edited_fact_line_disables_editing(self):
        ops = [{"op": "ensure_person", "name": "Sam Reyes", "fields": {},
                "log_lines": ["- a hand-written line"]}]
        plans, editable, _ = ef.plans_from_ops(ops, [])
        self.assertFalse(editable)
        self.assertEqual(plans[0]["facts"], [])

    def test_unknown_op_disables_editing(self):
        _, editable, _ = ef.plans_from_ops([{"op": "relink_entities"}], [])
        self.assertFalse(editable)

    def test_pre_feature_fences_tolerated(self):
        ops = [{"op": "ensure_person", "name": "Sam Reyes", "confirm_new": True,
                "fields": {"lastcontact": DATE}, "log_lines": []},
               {"op": "set_field", "uuid": "P-1", "field": "city",
                "value": "Lisbon", "effective_date": DATE},
               {"op": "ensure_event", "name": "Party", "date": "2026-07-05",
                "source_uuid": "SRC-1"},
               {"op": "mark_filed", "uuid": "SRC-1"}]
        plans, editable, src = ef.plans_from_ops(
            ops, [person("Alison Vance", uuid="P-1")])
        self.assertTrue(editable)
        self.assertEqual(src, "SRC-1")
        kinds = [p["kind"] for p in plans]
        self.assertEqual(kinds, ["new", "existing", "event"])
        self.assertTrue(plans[0]["interacted"])
        self.assertEqual(plans[1]["updates"], {"city": "Lisbon"})

    def test_note_render_of_inverted_plans_reparses(self):
        plans, editable, src = self.invert(
            [existing_plan(), new_plan(), event_plan()],
            [person("Alison Vance", uuid="P-1")])
        self.assertTrue(editable)
        people_ext, events_ext = ef.parse_things_note(
            ef.things_note_body(src, "PROP-1", plans))
        self.assertEqual([p["name"] for p in people_ext],
                         ["Alison Vance", "Sam Reyes"])
        self.assertEqual(events_ext[0]["name"], "Portland Hiking Trip")


class OpsHash(unittest.TestCase):
    def test_key_order_invariant(self):
        a = [{"op": "mark_filed", "uuid": "SRC-1"}]
        b = [{"uuid": "SRC-1", "op": "mark_filed"}]
        self.assertEqual(ef.ops_hash(a), ef.ops_hash(b))
        self.assertNotEqual(ef.ops_hash(a),
                            ef.ops_hash([{"op": "mark_filed", "uuid": "SRC-2"}]))


class StripBanner(unittest.TestCase):
    def test_strips_stacked_banners_and_is_idempotent(self):
        base = "Review, edit...\n" + ef.SPEC_SENTINEL + "\nPERSON X (new)"
        once = f"{ef.BANNER_PREFIX} problem one\n\n{base}"
        twice = f"{ef.BANNER_PREFIX} problem two\n\n{once}"
        self.assertEqual(ef.strip_banner(twice), base)
        self.assertEqual(ef.strip_banner(base), base)


class SettleSnapshot(unittest.TestCase):
    def test_notes_edit_changes_snapshot(self):
        row = task_row(status=3, notes="a", mod=5.0)
        self.assertEqual(ef.settle_snapshot(row), ef.settle_snapshot(dict(row)))
        self.assertNotEqual(ef.settle_snapshot(row),
                            ef.settle_snapshot(task_row(status=3, notes="b",
                                                        mod=5.0)))


def task_row(uuid="T-1", status=0, trashed=0, notes="", mod=1.0):
    return {"uuid": uuid, "title": "File: X", "notes": notes, "status": status,
            "trashed": trashed, "project": "PROJ-1", "heading": None,
            "stopDate": None, "userModificationDate": mod}


def map_entry(**kw):
    e = {"task_uuid": "T-1", "source_uuid": "SRC-1", "fence_hash": None,
         "prepared_hash": None, "warned": {}, "edit_disabled": False,
         "created": "2026-07-11T00:00:00"}
    e.update(kw)
    return e


class FakeThings:
    def __init__(self):
        self.updates = []
        self.update_ok = True
        self.project_tasks = []
        self.rows = {}
        self.added = []
        self.token = "tok"
        self.prewarm_calls = 0
        self.build_url = tb.build_url
        self.add_todo_params = tb.add_todo_params
        self.ThingsError = tb.ThingsError

    def auth_token(self):
        return self.token

    def prewarm(self):
        self.prewarm_calls += 1

    def project_alive(self, uuid):
        return True

    def ensure_project(self, title):
        return "PROJ-1"

    def read_project_tasks(self, project):
        return self.project_tasks

    def read_tasks(self, uuids):
        return {u: self.rows[u] for u in uuids if u in self.rows}

    def update_todo(self, task_uuid, token, attrs, expect):
        self.updates.append((task_uuid, dict(attrs)))
        return self.update_ok

    def add_todo(self, project, title, notes, marker, when=None):
        self.added.append((title, notes, marker, when))
        return f"T-NEW-{len(self.added)}"


class FakeBridge:
    def __init__(self, **results):
        self.results = results
        self.calls = []
        self.batches = []

    def __call__(self, ops, timeout=300):
        self.batches.append(ops)
        out = []
        for op in ops:
            self.calls.append(op)
            result = self.results.get(op["op"])
            if isinstance(result, Exception):
                raise result
            out.append(result(op) if callable(result) else result)
        return out


class PhaseHarness(unittest.TestCase):
    """Drives the impure phases with the bridge, Things, and map I/O stubbed."""

    def setUp(self):
        self.fake = FakeThings()
        self.saved = []
        self._orig = {name: getattr(ef, name) for name in
                      ("things_bridge", "run_bridge", "save_things_map",
                       "load_things_map")}
        ef.things_bridge = self.fake
        ef.save_things_map = lambda m: self.saved.append(json.loads(json.dumps(m)))
        self.config = {"THINGS_SYNC": "on", "THINGS_PROJECT": "Entity Filing",
                       "SELF_NAME": ""}

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(ef, name, value)

    def use_map(self, m):
        ef.load_things_map = lambda: m

    def bridge(self, **results):
        fake = FakeBridge(**results)
        ef.run_bridge = fake
        return fake

    def fresh_map(self, **tasks):
        return {"version": 1, "project_uuid": "PROJ-1", "tasks": dict(tasks)}


class Decisions(PhaseHarness):
    def test_row_gone_drops_mapping_without_trashing(self):
        m = self.fresh_map(**{"PROP-1": map_entry()})
        self.use_map(m)
        bridge = self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(m["tasks"], {})
        self.assertNotIn("trash", [c["op"] for c in bridge.calls])

    def test_terminal_state_defers_one_run_then_rejects(self):
        entry = map_entry()
        m = self.fresh_map(**{"PROP-1": entry})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=2, notes="n")
        bridge = self.bridge(list_group=[], trash={"uuid": "PROP-1"})
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("settle", entry)
        self.assertIn("PROP-1", m["tasks"])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(m["tasks"], {})
        self.assertIn("trash", [c["op"] for c in bridge.calls])

    def test_settle_resets_when_notes_change_between_runs(self):
        entry = map_entry()
        m = self.fresh_map(**{"PROP-1": entry})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=2, notes="first")
        self.bridge(list_group=[], trash={"uuid": "PROP-1"})
        ef.things_decisions(self.config, dry_run=False)
        self.fake.rows["T-1"] = task_row(status=2, notes="second")
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])

    def test_reopened_task_clears_settle(self):
        entry = map_entry(settle={"status": 3, "trashed": 0,
                                  "notes_sha": "x", "mod": 1.0})
        m = self.fresh_map(**{"PROP-1": entry})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=0)
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertNotIn("settle", entry)

    def test_dt_side_approval_completes_task_before_apply(self):
        m = self.fresh_map(**{"PROP-1": map_entry()})
        self.use_map(m)
        self.bridge(list_group=lambda op: [{"uuid": "PROP-1", "name": "File: X"}]
                    if op["path"] == ef.APPROVED_PATH else [])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.fake.updates, [("T-1", {"completed": "true"})])
        self.assertEqual(m["tasks"], {})

    def test_dt_side_approval_without_token_keeps_mapping(self):
        self.fake.update_ok = False
        m = self.fresh_map(**{"PROP-1": map_entry()})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=0)
        self.bridge(list_group=lambda op: [{"uuid": "PROP-1", "name": "File: X"}]
                    if op["path"] == ef.APPROVED_PATH else [])
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])

    def test_rebuild_from_task_notes_and_duplicate_fail_closed(self):
        m = self.fresh_map()
        self.use_map(m)
        self.fake.project_tasks = [
            task_row(uuid="T-A", notes=f"{ef.PROPOSAL_MARKER}PROP-A"),
            task_row(uuid="T-B1", notes=f"{ef.PROPOSAL_MARKER}PROP-B"),
            task_row(uuid="T-B2", notes=f"{ef.PROPOSAL_MARKER}PROP-B"),
        ]
        self.fake.rows = {"T-A": self.fake.project_tasks[0]}
        self.bridge(list_group=lambda op: (
            [{"uuid": "PROP-A", "name": "File: A"},
             {"uuid": "PROP-B", "name": "File: B"}]
            if op["path"] == ef.REVIEW_PATH else []))
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(list(m["tasks"]), ["PROP-A"])
        self.assertIsNone(m["tasks"]["PROP-A"]["fence_hash"])
        self.assertTrue(any("two live Things tasks" in msg
                            for msg in logs.messages()))

    def test_things_failure_degrades_to_warning(self):
        m = self.fresh_map()
        self.use_map(m)

        def boom(project):
            raise tb.ThingsError("cannot open Things database")

        self.fake.read_project_tasks = boom
        self.bridge(list_group=[])
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertTrue(any("decisions phase failed" in msg
                            for msg in logs.messages()))

    def test_rebuild_ignores_logbook_markers_for_resolved_proposals(self):
        m = self.fresh_map()
        self.use_map(m)
        self.fake.project_tasks = [
            task_row(uuid="T-OLD", status=3,
                     notes=f"{ef.PROPOSAL_MARKER}PROP-RESOLVED")]
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(m["tasks"], {})

    def test_reopened_task_clears_prepared_hash_and_bounce_count(self):
        entry = map_entry(prepared_hash="stale-hash", bounce_count=2,
                          settle={"status": 3, "trashed": 0,
                                  "notes_sha": "x", "mod": 1.0})
        m = self.fresh_map(**{"PROP-1": entry})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=0)
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertNotIn("settle", entry)
        self.assertNotIn("prepared_hash", entry)
        self.assertNotIn("bounce_count", entry)

    def test_approved_in_dt_survives_stale_things_cancel(self):
        self.fake.update_ok = False
        m = self.fresh_map(**{"PROP-1": map_entry()})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=2, notes="n")
        bridge = self.bridge(list_group=lambda op: (
            [{"uuid": "PROP-1", "name": "File: X"}]
            if op["path"] == ef.APPROVED_PATH else []))
        ef.things_decisions(self.config, dry_run=False)
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(m["tasks"], {})
        self.assertNotIn("trash", [c["op"] for c in bridge.calls])
        self.assertTrue(any("already approved in DEVONthink" in msg
                            for msg in logs.messages()))

    def test_dead_project_skips_decisions_this_tick(self):
        m = self.fresh_map(**{"PROP-1": map_entry()})
        self.use_map(m)
        self.fake.project_alive = lambda uuid: False
        bridge = self.bridge(list_group=RuntimeError("must not be called"))
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(m["tasks"], {})
        self.assertIsNone(m["project_uuid"])
        self.assertEqual(bridge.calls, [])
        self.assertTrue(any("lost mirror" in msg for msg in logs.messages()))

    def test_prewarm_called_only_when_mappings_exist(self):
        m = self.fresh_map()
        self.use_map(m)
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.fake.prewarm_calls, 0)

        m["tasks"]["PROP-1"] = map_entry()
        self.fake.rows["T-1"] = task_row(status=0)
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.fake.prewarm_calls, 1)

    def test_rebuild_without_sentinel_bounces_instead_of_freezing(self):
        m = self.fresh_map()
        self.use_map(m)
        row = task_row(uuid="T-1",
                       notes=f"stray note\n{ef.PROPOSAL_MARKER}PROP-1", status=0)
        self.fake.project_tasks = [row]
        self.fake.rows = {"T-1": row}
        self.bridge(list_group=lambda op: (
            [{"uuid": "PROP-1", "name": "File: X"}]
            if op["path"] == ef.REVIEW_PATH else []))
        ef.things_decisions(self.config, dry_run=False)
        self.assertFalse(m["tasks"]["PROP-1"]["edit_disabled"])

        ops = ef.ops_for_plan(existing_plan(), SOURCE, DATE)
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        row["status"] = 3
        settled(m["tasks"]["PROP-1"], row)
        bridge = self.bridge(list_group=[],
                             get_text={"uuid": "PROP-1", "text": fence_body(ops)},
                             get_source=SOURCE)
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertNotIn("move_to", [c["op"] for c in bridge.calls])
        self.assertIn("could not parse the edited note",
                      self.fake.updates[-1][1]["notes"])

    def test_rebuild_cancels_orphaned_task_for_dead_proposal(self):
        m = self.fresh_map()
        self.use_map(m)
        row = task_row(uuid="T-DEAD",
                       notes=f"{ef.PROPOSAL_MARKER}PROP-DEAD", status=0)
        self.fake.project_tasks = [row]
        self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.fake.updates, [("T-DEAD", {"canceled": "true"})])
        self.assertEqual(m["tasks"], {})

    def test_rebuild_baselines_fence_hash_on_completion(self):
        plans = [existing_plan()]
        ops = ef.ops_for_plan(plans[0], SOURCE, DATE)
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        m = self.fresh_map()
        self.use_map(m)
        row = task_row(uuid="T-1", status=3, notes=note_for(plans))
        self.fake.project_tasks = [row]
        self.fake.rows = {"T-1": row}
        self.bridge(list_group=lambda op: (
            [{"uuid": "PROP-1", "name": "File: X"}]
            if op["path"] == ef.REVIEW_PATH else []))
        ef.things_decisions(self.config, dry_run=False)
        entry = m["tasks"]["PROP-1"]
        self.assertIsNone(entry["fence_hash"])
        settled(entry, row)
        self.bridge(
            list_group=[], get_text={"uuid": "PROP-1", "text": fence_body(ops)},
            get_source=SOURCE, dump_people=[person("Alison Vance", uuid="P-1")],
            set_text={"uuid": "PROP-1"}, move_to={"uuid": "PROP-1"})
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertTrue(any("baselined the ops fence" in msg
                            for msg in logs.messages(level=logging.INFO)))
        self.assertEqual(m["tasks"], {})


def settled(entry, row):
    entry["settle"] = ef.settle_snapshot(row)
    return entry


class BatchedListGroupCalls(PhaseHarness):
    """C24: _Review and _Review/Approved are each listed once per phase, not
    once per path — two ops in one bridge call, not two osascript spawns."""

    def test_decisions_lists_review_and_approved_in_one_bridge_call(self):
        m = self.fresh_map()
        self.use_map(m)
        bridge = self.bridge(list_group=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(len(bridge.batches), 1)
        self.assertEqual([o["op"] for o in bridge.batches[0]],
                         ["list_group", "list_group"])
        self.assertEqual({o["path"] for o in bridge.batches[0]},
                         {ef.REVIEW_PATH, ef.APPROVED_PATH})

    def test_reconcile_lists_review_and_approved_in_one_bridge_call(self):
        m = self.fresh_map()
        self.use_map(m)
        bridge = self.bridge(list_group=[])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(len(bridge.batches), 1)
        self.assertEqual([o["op"] for o in bridge.batches[0]],
                         ["list_group", "list_group"])
        self.assertEqual({o["path"] for o in bridge.batches[0]},
                         {ef.REVIEW_PATH, ef.APPROVED_PATH})


class ApproveCompleted(PhaseHarness):
    def proposal_setup(self, plans=None, roster=None, entry=None, notes=None,
                       **bridge_overrides):
        roster = roster if roster is not None else [person("Alison Vance",
                                                           uuid="P-1")]
        plans = plans if plans is not None else [existing_plan()]
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        self.entry = entry or map_entry(fence_hash=ef.ops_hash(ops))
        row = task_row(status=3, notes=notes if notes is not None
                       else note_for(plans))
        self.row = settled_row = row
        settled(self.entry, settled_row)
        self.fake.rows["T-1"] = settled_row
        m = self.fresh_map(**{"PROP-1": self.entry})
        self.use_map(m)
        results = {"list_group": [],
                   "get_text": {"uuid": "PROP-1", "text": fence_body(ops)},
                   "get_source": SOURCE,
                   "dump_people": roster,
                   "set_text": {"uuid": "PROP-1"},
                   "move_to": {"uuid": "PROP-1"},
                   "trash": {"uuid": "PROP-1"}}
        results.update(bridge_overrides)
        self.m = m
        return self.bridge(**results), m

    def test_clean_completion_applies(self):
        bridge, m = self.proposal_setup()
        ef.things_decisions(self.config, dry_run=False)
        ops_run = [c["op"] for c in bridge.calls]
        self.assertIn("set_text", ops_run)
        self.assertIn("move_to", ops_run)
        self.assertEqual(m["tasks"], {})
        set_text = next(c for c in bridge.calls if c["op"] == "set_text")
        self.assertIn("Ran a marathon.", set_text["text"])

    def test_edited_note_changes_regenerated_ops(self):
        plans = [existing_plan()]
        edited = note_for(plans).replace("Ran a marathon.", "Ran an ultra.")
        bridge, _ = self.proposal_setup(plans=plans, notes=edited)
        ef.things_decisions(self.config, dry_run=False)
        set_text = next(c for c in bridge.calls if c["op"] == "set_text")
        self.assertIn("Ran an ultra.", set_text["text"])
        self.assertNotIn("Ran a marathon.", set_text["text"])

    def test_parse_error_bounces_with_banner(self):
        bridge, m = self.proposal_setup(notes="=== proposed v1 ===\ngarbage line")
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertNotIn("move_to", [c["op"] for c in bridge.calls])
        task, attrs = self.fake.updates[-1]
        self.assertEqual(attrs["completed"], "false")
        self.assertTrue(attrs["notes"].startswith(ef.BANNER_PREFIX))
        self.assertIn("garbage line", attrs["notes"])
        self.assertTrue(any("bounced" in msg for msg in logs.messages()))

    def test_fence_edited_in_dt_bounces(self):
        bridge, m = self.proposal_setup(
            entry=map_entry(fence_hash="something-else"))
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertNotIn("move_to", [c["op"] for c in bridge.calls])
        self.assertIn("edited in DEVONthink", self.fake.updates[-1][1]["notes"])

    def test_prepared_hash_resumes_move_only(self):
        plans = [existing_plan()]
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        bridge, m = self.proposal_setup(
            plans=plans,
            entry=map_entry(fence_hash="stale", prepared_hash=ef.ops_hash(ops)))
        ef.things_decisions(self.config, dry_run=False)
        ops_run = [c["op"] for c in bridge.calls]
        self.assertIn("move_to", ops_run)
        self.assertNotIn("set_text", ops_run)
        self.assertEqual(m["tasks"], {})

    def test_edit_disabled_moves_frozen_ops(self):
        plans = [existing_plan()]
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        bridge, m = self.proposal_setup(
            plans=plans, notes="not a parseable spec at all",
            entry=map_entry(fence_hash=ef.ops_hash(ops), edit_disabled=True))
        ef.things_decisions(self.config, dry_run=False)
        ops_run = [c["op"] for c in bridge.calls]
        self.assertIn("move_to", ops_run)
        self.assertNotIn("set_text", ops_run)
        self.assertEqual(m["tasks"], {})

    def test_zero_plan_note_bounces(self):
        plans = [new_plan()]
        note = "\n".join(["header", ef.SPEC_SENTINEL, "PERSON Sam Reyes (new)"])
        bridge, m = self.proposal_setup(plans=plans, notes=note, roster=[])
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertIn("nothing left to file", self.fake.updates[-1][1]["notes"])

    def test_ambiguous_name_bounces(self):
        roster = [person("Jonathan Marsh", uuid="P-1", aliases="Jonathan"),
                  person("Jonathan Vega", uuid="P-2", aliases="Jonathan")]
        plans = [new_plan(name="Jonathan", single_token=True)]
        bridge, m = self.proposal_setup(plans=plans, roster=roster)
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertIn("ambiguous name", self.fake.updates[-1][1]["notes"])

    def test_near_match_warns_then_confirms_only_that_person(self):
        roster = [person("Alison Vance", uuid="P-1")]
        plans = [new_plan(name="Alison")]
        bridge, m = self.proposal_setup(plans=plans, roster=roster)
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertIn("alison", m["tasks"]["PROP-1"]["warned"])
        self.assertIn("resembles Alison Vance",
                      self.fake.updates[-1][1]["notes"])

        row = self.fake.rows["T-1"]
        row["status"] = 3
        settled(m["tasks"]["PROP-1"], row)
        ef.things_decisions(self.config, dry_run=False)
        ensure = [c for c in bridge.calls if c["op"] == "ensure_person"]
        set_text = [c for c in bridge.calls if c["op"] == "set_text"]
        self.assertTrue(set_text)
        self.assertIn('"confirm_new": true', set_text[-1]["text"])
        self.assertEqual(m["tasks"], {})

    def test_missing_source_bounces(self):
        bridge, m = self.proposal_setup(
            get_source=RuntimeError("bridge op get_source failed: not found"))
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertIn("source record is missing",
                      self.fake.updates[-1][1]["notes"])

    def test_bounce_without_token_leaves_task_completed(self):
        self.fake.update_ok = False
        bridge, m = self.proposal_setup(notes="=== proposed v1 ===\ngarbage")
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertIn("PROP-1", m["tasks"])
        self.assertTrue(any("could not re-open" in msg
                            for msg in logs.messages()))

    def test_dry_run_fires_nothing(self):
        bridge, m = self.proposal_setup()
        ef.things_decisions(self.config, dry_run=True)
        self.assertNotIn("set_text", [c["op"] for c in bridge.calls])
        self.assertNotIn("move_to", [c["op"] for c in bridge.calls])
        self.assertEqual(self.fake.updates, [])
        self.assertIn("PROP-1", m["tasks"])

    def test_reopen_clears_stale_prepared_hash_before_new_edit_applies(self):
        plans = [existing_plan()]
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        stale_hash = ef.ops_hash(ops)
        entry = map_entry(fence_hash=stale_hash, prepared_hash=stale_hash)
        m = self.fresh_map(**{"PROP-1": entry})
        self.use_map(m)
        self.fake.rows["T-1"] = task_row(status=0, notes=note_for(plans))
        self.bridge(list_group=[], get_text={"uuid": "PROP-1",
                                             "text": fence_body(ops)})
        ef.things_decisions(self.config, dry_run=False)
        self.assertIsNone(entry.get("prepared_hash"))

        edited_notes = note_for(plans).replace("Ran a marathon.", "Ran an ultra.")
        row = self.fake.rows["T-1"]
        row["status"], row["notes"] = 3, edited_notes
        settled(entry, row)
        bridge = self.bridge(
            list_group=[], get_text={"uuid": "PROP-1", "text": fence_body(ops)},
            get_source=SOURCE, dump_people=[person("Alison Vance", uuid="P-1")],
            set_text={"uuid": "PROP-1"}, move_to={"uuid": "PROP-1"})
        ef.things_decisions(self.config, dry_run=False)
        set_text_call = next(c for c in bridge.calls if c["op"] == "set_text")
        self.assertIn("Ran an ultra.", set_text_call["text"])
        self.assertNotIn("Ran a marathon.", set_text_call["text"])
        self.assertEqual(m["tasks"], {})

    def test_near_match_confirm_requires_truly_unchanged_notes(self):
        roster = [person("Alison Vance", uuid="P-1")]
        plans = [new_plan(name="Alison")]
        bridge, m = self.proposal_setup(plans=plans, roster=roster)
        ef.things_decisions(self.config, dry_run=False)
        self.assertIn("alison", m["tasks"]["PROP-1"]["warned"])

        row = self.fake.rows["T-1"]
        row["status"] = 3
        row["notes"] = row["notes"] + "\n"
        settled(m["tasks"]["PROP-1"], row)
        ef.things_decisions(self.config, dry_run=False)
        self.assertNotIn("set_text", [c["op"] for c in bridge.calls])
        self.assertIn("PROP-1", m["tasks"])
        self.assertIn("resembles Alison Vance", self.fake.updates[-1][1]["notes"])

    def test_bounce_increments_count(self):
        self.proposal_setup(notes="=== proposed v1 ===\ngarbage")
        ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.m["tasks"]["PROP-1"]["bounce_count"], 1)

    def test_bounce_stops_after_limit(self):
        self.proposal_setup(notes="=== proposed v1 ===\ngarbage")
        self.entry["bounce_count"] = ef.BOUNCE_LIMIT
        with capture_logs(ef) as logs:
            ef.things_decisions(self.config, dry_run=False)
        self.assertEqual(self.fake.updates, [])
        self.assertTrue(any("manual review" in msg for msg in logs.messages()))

    def test_bounce_reopen_note_sized_against_url_limit(self):
        huge_notes = "x" * (ef.THINGS_URL_LIMIT * 2)
        self.proposal_setup(notes=huge_notes,
                            get_text={"uuid": "PROP-1", "text": "no fence here"})
        ef.things_decisions(self.config, dry_run=False)
        notes = self.fake.updates[-1][1]["notes"]
        self.assertTrue(notes.startswith(ef.BANNER_PREFIX))
        self.assertNotIn("x" * 50, notes)
        self.assertLessEqual(
            len(tb.build_url("update", {"auth-token": "tok", "id": "T-1",
                                        "completed": "false", "notes": notes})),
            ef.THINGS_URL_LIMIT)


class Reconcile(PhaseHarness):
    def pending_proposal(self, plans, roster):
        ops = []
        for plan in plans:
            ops.extend(ef.ops_for_plan(plan, SOURCE, DATE))
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        return self.bridge(
            list_group=lambda op: [] if op["path"] == ef.APPROVED_PATH
            else [{"uuid": "PROP-1", "name": "File: X"},
                  {"uuid": "GROUP-APPROVED", "name": "Approved"}],
            get_text={"uuid": "PROP-1", "text": fence_body(ops)},
            dump_people=roster)

    def test_creates_task_for_unmapped_proposal(self):
        m = self.fresh_map()
        self.use_map(m)
        self.pending_proposal([new_plan()], [])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(len(self.fake.added), 1)
        title, notes, marker, when = self.fake.added[0]
        self.assertEqual(title, "File: X")
        self.assertEqual(marker, "PROP-1")
        self.assertEqual(when, "today")
        self.assertIn(ef.SPEC_SENTINEL, notes)
        self.assertIn("PROP-1", m["tasks"])
        self.assertFalse(m["tasks"]["PROP-1"]["edit_disabled"])
        self.assertEqual(m["tasks"]["PROP-1"]["source_uuid"], "SRC-1")

    def test_uninvertible_proposal_created_edit_disabled(self):
        m = self.fresh_map()
        self.use_map(m)
        self.pending_proposal([existing_plan()], roster=[])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertTrue(m["tasks"]["PROP-1"]["edit_disabled"])
        _, notes, _, _ = self.fake.added[0]
        self.assertNotIn(ef.SPEC_SENTINEL, notes)
        self.assertIn(ef.PROPOSAL_MARKER, notes)

    def test_oversized_note_falls_back_to_stub(self):
        plans = [new_plan(facts=[(DATE, f"Fact {i}: " + "x" * 390)
                                 for i in range(9)])]
        m = self.fresh_map()
        self.use_map(m)
        self.pending_proposal(plans, [])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertTrue(m["tasks"]["PROP-1"]["edit_disabled"])

    def test_oversized_stub_title_skips_mirroring(self):
        m = self.fresh_map()
        self.use_map(m)
        huge_name = "File: " + "x" * ef.THINGS_URL_LIMIT
        ops = ef.ops_for_plan(existing_plan(), SOURCE, DATE)
        ops.append({"op": "mark_filed", "uuid": "SRC-1"})
        self.bridge(
            list_group=lambda op: [] if op["path"] == ef.APPROVED_PATH
            else [{"uuid": "PROP-1", "name": huge_name}],
            get_text={"uuid": "PROP-1", "text": fence_body(ops)}, dump_people=[])
        with capture_logs(ef) as logs:
            ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(self.fake.added, [])
        self.assertEqual(m["tasks"], {})
        self.assertTrue(any("exceeds the URL size limit even as a stub" in msg
                            for msg in logs.messages()))

    def test_empty_ops_stub_skips_silently(self):
        m = self.fresh_map()
        self.use_map(m)
        self.bridge(
            list_group=lambda op: [] if op["path"] == ef.APPROVED_PATH
            else [{"uuid": "PROP-1", "name": "File: X"}],
            get_text={"uuid": "PROP-1", "text": fence_body([])})
        with capture_logs(ef) as logs:
            ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(self.fake.added, [])
        self.assertEqual(m["tasks"], {})
        self.assertFalse(logs.messages())

    def test_poison_proposal_does_not_stall_others_or_cancel_pass(self):
        m = self.fresh_map(**{"PROP-GONE": map_entry(task_uuid="T-9")})
        self.use_map(m)
        self.fake.rows["T-9"] = task_row(uuid="T-9", status=0)
        ops_good = ef.ops_for_plan(new_plan(), SOURCE, DATE)
        ops_good.append({"op": "mark_filed", "uuid": "SRC-1"})

        def get_text(op):
            if op["uuid"] == "PROP-BAD":
                raise RuntimeError("bridge op get_text failed: boom")
            return {"uuid": "PROP-GOOD", "text": fence_body(ops_good)}

        self.bridge(
            list_group=lambda op: [] if op["path"] == ef.APPROVED_PATH
            else [{"uuid": "PROP-BAD", "name": "File: Bad"},
                  {"uuid": "PROP-GOOD", "name": "File: Good"}],
            get_text=get_text, dump_people=[])
        with capture_logs(ef) as logs:
            ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(len(self.fake.added), 1)
        self.assertEqual(self.fake.added[0][2], "PROP-GOOD")
        self.assertEqual(self.fake.updates, [("T-9", {"canceled": "true"})])
        self.assertTrue(any("Things mirror for proposal PROP-BAD failed" in msg
                            for msg in logs.messages()))

    def test_dead_project_drops_mappings_and_remirrors(self):
        m = self.fresh_map(**{"PROP-OLD": map_entry(task_uuid="T-OLD")})
        self.use_map(m)
        self.fake.project_alive = lambda uuid: False
        self.pending_proposal([new_plan()], [])
        with capture_logs(ef) as logs:
            ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(m["project_uuid"], "PROJ-1")
        self.assertNotIn("PROP-OLD", m["tasks"])
        self.assertIn("PROP-1", m["tasks"])
        self.assertTrue(any("lost mirror" in msg for msg in logs.messages()))

    def test_resolved_proposal_cancels_open_task(self):
        m = self.fresh_map(**{"PROP-GONE": map_entry(task_uuid="T-9")})
        self.use_map(m)
        self.fake.rows["T-9"] = task_row(uuid="T-9", status=0)
        self.bridge(list_group=[])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(self.fake.updates, [("T-9", {"canceled": "true"})])
        self.assertEqual(m["tasks"], {})

    def test_resolved_proposal_with_terminal_task_just_drops(self):
        m = self.fresh_map(**{"PROP-GONE": map_entry(task_uuid="T-9")})
        self.use_map(m)
        self.fake.rows["T-9"] = task_row(uuid="T-9", status=3)
        self.bridge(list_group=[])
        ef.things_reconcile(self.config, dry_run=False)
        self.assertEqual(self.fake.updates, [])
        self.assertEqual(m["tasks"], {})

    def test_sync_off_is_inert(self):
        bridge = self.bridge(list_group=RuntimeError("must not be called"))
        ef.things_reconcile({"THINGS_SYNC": "off"}, dry_run=False)
        self.assertEqual(bridge.calls, [])


class AddUrl(unittest.TestCase):
    def test_when_is_encoded_in_the_add_url(self):
        url = tb.build_url("add", tb.add_todo_params(
            "PROJ-1", "File: X", "note", ef.THINGS_WHEN))
        self.assertIn("when=today", url)

    def test_when_omitted_when_unset(self):
        self.assertNotIn("when", tb.add_todo_params("PROJ-1", "File: X", "note"))


class MapFile(unittest.TestCase):
    def test_corrupt_map_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.json")
            with open(path, "w") as f:
                f.write("{not json")
            orig = ef.THINGS_MAP_FILE
            ef.THINGS_MAP_FILE = path
            try:
                with capture_logs(ef) as logs:
                    self.assertIsNone(ef.load_things_map())
                self.assertTrue(any("unreadable" in msg
                                    for msg in logs.messages()))
            finally:
                ef.THINGS_MAP_FILE = orig

    def test_missing_map_returns_empty(self):
        orig = ef.THINGS_MAP_FILE
        ef.THINGS_MAP_FILE = "/nonexistent/map.json"
        try:
            m = ef.load_things_map()
        finally:
            ef.THINGS_MAP_FILE = orig
        self.assertEqual(m["tasks"], {})

    def test_wrong_schema_version_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.json")
            with open(path, "w") as f:
                json.dump({"version": 999, "project_uuid": None, "tasks": {}}, f)
            orig = ef.THINGS_MAP_FILE
            ef.THINGS_MAP_FILE = path
            try:
                with capture_logs(ef) as logs:
                    self.assertIsNone(ef.load_things_map())
                self.assertTrue(any("unrecognized schema" in msg
                                    for msg in logs.messages()))
            finally:
                ef.THINGS_MAP_FILE = orig


class UpdateTodoShortCircuit(unittest.TestCase):
    def test_already_satisfied_skips_the_fire(self):
        def boom_fire(url):
            raise AssertionError("must not fire a URL when already satisfied")

        orig_read, orig_fire = tb.read_tasks, tb._fire
        tb.read_tasks = lambda uuids: {
            "T-1": {"uuid": "T-1", "status": 3, "notes": "n"}}
        tb._fire = boom_fire
        try:
            self.assertTrue(tb.update_todo(
                "T-1", "tok", {"completed": "true"}, {"status": 3}))
        finally:
            tb.read_tasks, tb._fire = orig_read, orig_fire


class AuthToken(unittest.TestCase):
    def test_parses_managed_block(self):
        with tempfile.NamedTemporaryFile("w", suffix=".zshenv",
                                         delete=False) as f:
            f.write("# >>> things-token >>>\n"
                    "export THINGS_AUTH_TOKEN='fake-token-123'\n"
                    "# <<< things-token <<<\n")
            path = f.name
        orig, orig_env = tb.ZSHENV, os.environ.pop("THINGS_AUTH_TOKEN", None)
        tb.ZSHENV = path
        try:
            self.assertEqual(tb.auth_token(), "fake-token-123")
        finally:
            tb.ZSHENV = orig
            if orig_env is not None:
                os.environ["THINGS_AUTH_TOKEN"] = orig_env
            os.unlink(path)

    def test_env_overrides_and_missing_file_is_none(self):
        orig_env = os.environ.pop("THINGS_AUTH_TOKEN", None)
        orig = tb.ZSHENV
        tb.ZSHENV = "/nonexistent/zshenv"
        try:
            self.assertIsNone(tb.auth_token())
            os.environ["THINGS_AUTH_TOKEN"] = "env-token"
            self.assertEqual(tb.auth_token(), "env-token")
        finally:
            tb.ZSHENV = orig
            os.environ.pop("THINGS_AUTH_TOKEN", None)
            if orig_env is not None:
                os.environ["THINGS_AUTH_TOKEN"] = orig_env


if __name__ == "__main__":
    unittest.main()
