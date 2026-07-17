"""Hardening fixes in entity-dt-bridge.js: linkEntities URL/bracket safety,
the ensure_event undated-match gap/backfill core, the shared LastContact
guard, alias union, source-kind classify() precedence, email normalization,
and the NFKD/casefold rewrite of normName.

All of these are pure functions in the bridge, so this drives them through
the real JXA via the same osascript eval harness as test_section_span.py and
test_entity_log_sort.py rather than reimplementing them in Python — a Python
copy would only prove the copy agrees with itself. No DEVONthink involved.

linkEntities and buildEntityIndex close over module-level `let` state
(entityIndex, bridgeCtx) that a *separate* eval() call cannot reach — a
direct eval's `let`/`const` bindings are scoped to that eval's own Script,
not the caller's function scope (only var/function declarations leak). So
those two cases re-eval the bridge source with a small setup tail appended,
all inside one Script, instead of trying to poke module state from outside.
"""

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path

BRIDGE = (Path(__file__).resolve().parents[2] / "stow" / "devonthink" /
          ".local" / "bin" / "entity-dt-bridge.js")

DAILY_PATH = "/10_DAILY"
JOURNAL_PATH = "/15_JOURNAL"
FACTS_PATH = "/20_ENTITIES/_Facts"

HARNESS = textwrap.dedent("""
    ObjC.import('Foundation')

    function readText(path) {
      return ObjC.unwrap($.NSString.stringWithContentsOfFileEncodingError(
        path, $.NSUTF8StringEncoding, $()))
    }

    function run(argv) {
      const bridgeSrc = readText(argv[0])
      const cases = JSON.parse(readText(argv[1]))
      eval(bridgeSrc)
      const pure = {
        normName: function (a) { return normName(a[0]) },
        unionAliases: function (a) { return unionAliases(a[0], a[1]) },
        normalizeEmail: function (a) { return normalizeEmail(a[0]) },
        classify: function (a) { return classify(a[0]) },
        bodyDateValue: function (a) { return bodyDateValue(a[0]) },
        eventMatchGap: function (a) {
          return eventMatchGap(a[0], a[1], a[2], a[3])
        },
        lastContactGuard: function (a) {
          return lastContactGuard(a[0], a[1])
        },
      }
      return JSON.stringify(cases.map(function (c) {
        if (c.fn === 'linkEntities') {
          const src = bridgeSrc + '\\n;(function(){ entityIndex = ' +
            JSON.stringify(c.entityIndex) + '; return linkEntities(' +
            JSON.stringify(c.args[0]) + ', ' + JSON.stringify(c.args[1]) +
            ') })()'
          return eval(src)
        }
        if (c.fn === 'buildEntityIndexNames') {
          const src = bridgeSrc + '\\n;(function(){\\n' +
            'bridgeCtx = { groupAt: function () { return { children: ' +
            'function () {\\n' +
            '  return ' + JSON.stringify(c.records) + '.map(function (r) {\\n' +
            '    return { type: function(){return "markdown"},\\n' +
            '             uuid: function(){return r.uuid},\\n' +
            '             name: function(){return r.name},\\n' +
            '             aliases: function(){return r.aliases||""} }\\n' +
            '  })\\n' +
            '} } } }\\n' +
            'return buildEntityIndex().map(function(e){return e.name})\\n' +
            '})()'
          return eval(src)
        }
        return pure[c.fn](c.args)
      }))
    }
""")


def run_cases(cases, tmp):
    harness = tmp / "harness.js"
    harness.write_text(HARNESS)
    payload = tmp / "cases.json"
    payload.write_text(json.dumps(cases))
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(harness), str(BRIDGE),
         str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def make_tmp(name):
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / name
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def call(fn, args, tmp):
    return run_cases([{"fn": fn, "args": args}], tmp)[0]


class LinkEntitiesUrlAndBracketSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-link-entities-test")

    def link(self, line, entity_index, exclude_uuid=None):
        return run_cases([{
            "fn": "linkEntities", "entityIndex": entity_index,
            "args": [line, exclude_uuid],
        }], self.tmp)[0]

    def test_bare_url_is_never_linked(self):
        out = self.link("See https://chen.dev for details.",
                         [{"name": "chen", "uuid": "U1"}])
        self.assertEqual(out, "See https://chen.dev for details.")

    def test_www_host_is_never_linked(self):
        out = self.link("Visit www.chen.dev today.",
                         [{"name": "chen", "uuid": "U1"}])
        self.assertEqual(out, "Visit www.chen.dev today.")

    def test_plain_mention_still_links(self):
        out = self.link("chen said hi", [{"name": "chen", "uuid": "U1"}])
        self.assertEqual(
            out, "[chen](x-devonthink-item://U1) said hi")

    def test_mention_outside_a_url_in_the_same_line_still_links(self):
        out = self.link("chen posted https://chen.dev today",
                         [{"name": "chen", "uuid": "U1"}])
        self.assertEqual(
            out, "[chen](x-devonthink-item://U1) posted https://chen.dev today")

    def test_existing_markdown_link_is_untouched(self):
        line = "See [chen](x-devonthink-item://OTHER) already linked."
        out = self.link(line, [{"name": "chen", "uuid": "U1"}])
        self.assertEqual(out, line)

    def test_bracketed_entity_name_excluded_from_index(self):
        names = run_cases([{
            "fn": "buildEntityIndexNames",
            "records": [
                {"uuid": "U1", "name": "Maya [Chen]", "aliases": ""},
                {"uuid": "U2", "name": "Sam Rivera", "aliases": ""},
            ],
        }], self.tmp)[0]
        self.assertNotIn("Maya [Chen]", names)
        self.assertIn("Sam Rivera", names)


class EnsureEventGapAndBackfillCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-event-gap-test")

    def body_date(self, lines):
        return call("bodyDateValue", [lines], self.tmp)

    def gap(self, op_date, md_date, body_date, match_days=45):
        return call("eventMatchGap",
                     [op_date, md_date, body_date, match_days], self.tmp)

    def test_body_date_value_extracts_filled_date(self):
        self.assertEqual(
            self.body_date(["# Event", "", "**Date:** 2026-07-16",
                             "**Where:** —"]),
            "2026-07-16")

    def test_body_date_value_blank_for_dash(self):
        self.assertEqual(self.body_date(["**Date:** —"]), "")

    def test_body_date_value_blank_for_empty(self):
        self.assertEqual(self.body_date(["**Date:**"]), "")

    def test_body_date_value_blank_when_no_date_line(self):
        self.assertEqual(self.body_date(["**Where:** —"]), "")

    def test_gap_uses_mdeventdate_when_present(self):
        self.assertEqual(
            self.gap("2026-07-16", "2026-07-16", "2020-01-01"), 0)

    def test_gap_falls_back_to_body_date_when_mdeventdate_blank(self):
        self.assertAlmostEqual(
            self.gap("2026-07-16", "", "2026-07-20"), 4)

    def test_gap_is_weakest_match_when_both_sides_undated(self):
        self.assertEqual(self.gap("2026-07-16", "", ""), 45)
        self.assertEqual(self.gap("", "", ""), 45)

    def test_undated_metadata_with_distant_body_date_is_not_a_forever_magnet(self):
        gap = self.gap("2026-07-16", "", "2024-01-01")
        self.assertGreater(gap, 45)


class LastContactGuardCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-lastcontact-guard-test")

    def guard(self, current, incoming):
        return call("lastContactGuard", [current, incoming], self.tmp)

    def test_first_contact_always_writes(self):
        self.assertEqual(self.guard("", "2026-07-16"),
                          {"changed": True, "invalid": False})

    def test_later_date_writes(self):
        self.assertEqual(self.guard("2026-07-01", "2026-07-16"),
                          {"changed": True, "invalid": False})

    def test_earlier_date_is_rejected(self):
        self.assertEqual(self.guard("2026-07-20", "2026-07-16"),
                          {"changed": False, "invalid": False})

    def test_equal_date_is_a_no_op(self):
        self.assertEqual(self.guard("2026-07-16", "2026-07-16"),
                          {"changed": False, "invalid": False})

    def test_non_iso_current_is_treated_as_absent(self):
        self.assertEqual(self.guard("not-a-date", "2026-07-16"),
                          {"changed": True, "invalid": False})

    def test_malformed_incoming_date_is_rejected_as_invalid(self):
        self.assertEqual(self.guard("2026-07-01", "07/16/2026"),
                          {"changed": False, "invalid": True})

    def test_incoming_date_shape_is_exact(self):
        self.assertEqual(self.guard("", "2026-7-16"),
                          {"changed": False, "invalid": True})


class AliasUnionCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-alias-union-test")

    def union(self, existing, incoming):
        return call("unionAliases", [existing, incoming], self.tmp)

    def test_preserves_existing_order_and_appends_new(self):
        self.assertEqual(self.union("Al, Bob", "Carol"), "Al, Bob, Carol")

    def test_dedupes_case_insensitively(self):
        self.assertEqual(self.union("Al, Bob", "bob, Carol"),
                          "Al, Bob, Carol")

    def test_incoming_can_be_an_array(self):
        self.assertEqual(self.union("Al", ["Al", "Zed"]), "Al, Zed")

    def test_no_incoming_aliases_is_a_no_op(self):
        self.assertEqual(self.union("Al, Bob", ""), "Al, Bob")

    def test_no_existing_aliases_takes_all_incoming(self):
        self.assertEqual(self.union("", "Dee"), "Dee")


class ClassifyPrecedence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-classify-test")

    def classify(self, location, handwritten, documenttype):
        return call("classify", [{
            "location": location, "handwritten": handwritten,
            "documenttype": documenttype,
        }], self.tmp)

    def test_daily_location_wins_even_with_meeting_doctype(self):
        self.assertEqual(
            self.classify(DAILY_PATH + "/2026-07-16", False,
                           "Meeting Notes"),
            "daily")

    def test_journal_location_wins_over_handwritten_flag(self):
        self.assertEqual(
            self.classify(JOURNAL_PATH + "/2026/2026-07-16 Journal", True, ""),
            "journal")

    def test_facts_location_is_fact(self):
        self.assertEqual(self.classify(FACTS_PATH, False, ""), "fact")

    def test_handwritten_flag_wins_over_meeting_doctype(self):
        self.assertEqual(
            self.classify("/00_INBOX", True, "Meeting Notes"), "handwritten")

    def test_meeting_is_the_weakest_marker(self):
        self.assertEqual(
            self.classify("/00_INBOX", False, "Meeting Notes"), "meeting")

    def test_no_markers_is_other(self):
        self.assertEqual(self.classify("/00_INBOX", False, ""), "other")


class EmailNormalize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-email-normalize-test")

    def norm(self, v):
        return call("normalizeEmail", [v], self.tmp)

    def test_strips_mailto_prefix(self):
        self.assertEqual(self.norm("mailto:jane@x.com"), "jane@x.com")

    def test_trims_and_lowercases(self):
        self.assertEqual(self.norm("  Jane@X.com  "), "jane@x.com")

    def test_mailto_variant_equals_plain(self):
        self.assertEqual(self.norm("MAILTO:Jane@X.com"),
                          self.norm("jane@x.com"))

    def test_empty_stays_empty(self):
        self.assertEqual(self.norm(""), "")


class NormNameUnicodeParity(unittest.TestCase):
    """Target semantics: NFKD, strip every combining mark, explicit ß->ss,
    then casefold — matching Python's NFKD + strip-marks + str.casefold()
    for the name domain (see entity-dt-bridge.js header)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = make_tmp("dt-normname-test")

    def norm(self, v):
        return call("normName", [v], self.tmp)

    def test_basic_ascii_is_lowercased_and_collapsed(self):
        self.assertEqual(self.norm("  Maya   Chen "), "maya chen")

    def test_strasse_casefold_parity(self):
        self.assertEqual(self.norm("Straße"), "strasse")

    def test_composed_and_decomposed_e_match(self):
        composed = self.norm("café")
        decomposed = self.norm("café")
        self.assertEqual(composed, decomposed)
        self.assertEqual(composed, "cafe")

    def test_combining_mark_outside_original_narrow_range_is_stripped(self):
        # U+05B4 HEBREW POINT HIRIQ is category Mn but outside the old
        # regex's U+0300-036F window — the exact gap C06/C30 describes.
        self.assertEqual(self.norm("Noִa"), self.norm("Noa"))
        self.assertEqual(self.norm("Noa"), "noa")


if __name__ == "__main__":
    unittest.main()
