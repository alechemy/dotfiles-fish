import copy
import json
import unittest

from helpers import load, person

capture = load("entity_capture.py", "capture_biographical")
TEXT = "Wren has two siblings. Her brother may move in October."
SOURCE = {"uuid": "AAA-111", "kind": "fact"}


class BiographicalCapture(unittest.TestCase):
    def prepare(self, text=TEXT, people=(), day="2026-09-01", decisions=None):
        result = capture.resolve(text, [{"mention": "Wren"}], list(people))
        return capture.prepare_unified(SOURCE, day, text, result, day,
                                       list(people), decisions=decisions)

    def test_new_capture_has_one_log_and_complete_qualifiers(self):
        manifest = self.prepare()
        self.assertEqual(manifest["version"], 2)
        rows = manifest["subjects"][0]["contribution"]["assertions"]
        self.assertEqual([r["baseline"]["text"] for r in rows],
                         ["Has two siblings.", "Her brother may move in October."])
        evidence = "".join(r["reference"]["evidence"] for r in rows)
        self.assertEqual(evidence, TEXT)
        capture.decode_manifest(capture.encode_manifest(manifest), SOURCE["uuid"])

    def test_compound_subject_and_quotation_are_not_stripped(self):
        for text in ('Wren and Rowan moved.', 'Wren said "Wren moved".',
                     'Wren did not move. Rowan moved.',
                     'Wren likes https://example.invalid/Wren.'):
            manifest = self.prepare(text)
            rows = manifest["subjects"][0]["contribution"]["assertions"]
            self.assertEqual("".join(r["reference"]["evidence"] for r in rows), text)
        self.assertEqual(self.prepare('Wren and Rowan moved.')["subjects"][0]
                         ["contribution"]["assertions"][0]["baseline"]["text"],
                         'Wren and Rowan moved.')

    def test_single_quotes_and_link_labels_keep_visible_context(self):
        for text in ("Wren said 'Wren moved. Wren has two siblings.'",
                     "Wren said ‘Wren moved. Wren has two siblings.’",
                     "Wren visited [Museum. Wren works](https://example.invalid)."):
            with self.subTest(text=text):
                rows = self.prepare(text)["subjects"][0]["contribution"]["assertions"]
                self.assertEqual(len(rows), 1)
                expected = text if text.startswith("Wren said") else "Visited [Museum. Wren works](https://example.invalid)."
                self.assertEqual(rows[0]["baseline"]["text"], expected)
                self.assertEqual(rows[0]["reference"]["evidence"], text)

    def test_cross_day_same_knowledge_requires_answer(self):
        from entity_biographical import attach, render
        old = self.prepare("Wren has two siblings.")
        row = old["subjects"][0]["contribution"]["assertions"][0]
        body = attach("# Wren\n\n", row, "BBB-222")
        people = [dict(person("Wren", "BBB-222"), body=body)]
        new = self.prepare("Wren has two siblings.", people, "2026-09-02")
        self.assertEqual(new["status"], "question")
        self.assertEqual(new["reason"], "assertion_equivalence")
        question = new["semantic_questions"][0]
        confirmed = self.prepare("Wren has two siblings.", people, "2026-09-02",
                                 {question["key"]: question["candidates"][0]["id"]})
        self.assertEqual(confirmed["status"], "filing")
        updated = attach(body, confirmed["subjects"][0]["contribution"]["assertions"][0], "BBB-222")
        self.assertEqual(updated.count("Has two siblings."), 2)
        self.assertEqual(len(render(updated)[0]["data"]["references"]), 2)

    def test_omitted_clause_and_false_equivalence_fail_closed(self):
        manifest = self.prepare()
        manifest["subjects"][0]["contribution"]["assertions"].pop()
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(manifest), SOURCE["uuid"])
        changed = self.prepare()
        changed["subjects"][0]["contribution"]["assertions"][0]["baseline"]["text"] = "Has three siblings."
        with self.assertRaises(ValueError):
            capture.decode_manifest(capture.encode_manifest(changed), SOURCE["uuid"])

    def test_two_owners_undo_and_passive_support(self):
        from entity_biographical import attach, detach, render
        row = self.prepare("Wren moved.")["subjects"][0]["contribution"]["assertions"][0]
        second = copy.deepcopy(row)
        second["reference"]["id"] = "c" * 64
        second["reference"]["source_uuid"] = "CCC-333"
        body = attach(attach("# Wren\n\n", row, "BBB-222"), second, "BBB-222")
        body = detach(body, row, "BBB-222")
        self.assertEqual(len(render(body)[0]["data"]["references"]), 1)
        self.assertNotIn("Moved.", detach(body, second, "BBB-222"))
        passive = copy.deepcopy(second)
        passive["reference"].update(id="d" * 64, kind="passive", evidence_kind="extracted_assertion")
        body = attach(attach("# Wren\n\n", row, "BBB-222"), passive, "BBB-222")
        self.assertIn("Moved.", detach(body, row, "BBB-222"))

    def test_multiline_lists_quotes_and_line_endings_keep_complete_information(self):
        from entity_biographical import attach, verify
        text = 'Wren has plans:\n  - Study French\n    - With Rowan\n\n"Wren may move."\nRowan will stay.'
        for ending in ("\n", "\r", "\r\n"):
            normalized = capture.source_text(text.replace("\n", ending))
            manifest = self.prepare(normalized)
            row = manifest["subjects"][0]["contribution"]["assertions"][0]
            self.assertEqual(row["reference"]["evidence"], text)
            body = attach("# Wren\n\n", row, "BBB-222")
            verify(body, row, "BBB-222")
            self.assertIn("    - With Rowan", body)
            capture.decode_manifest(capture.encode_manifest(manifest), SOURCE["uuid"])

    def test_nonexact_model_suggestions_require_explicit_confirmation(self):
        from entity_biographical import attach
        existing = self.prepare("Wren has two siblings.")
        body = attach("# Wren\n\n", existing["subjects"][0]["contribution"]["assertions"][0], "BBB-222")
        people = [dict(person("Wren", "BBB-222"), body=body)]
        text = "Wren has a brother and a sister."
        result = capture.resolve(text, [{"mention": "Wren"}], people)
        model = lambda value, day, rows: [rows[0]["id"]]
        pending = capture.prepare_unified(SOURCE, "2026-09-01", text, result, "2026-09-01", people, suggest=model)
        self.assertEqual(pending["status"], "question")
        separate = capture.prepare_unified(SOURCE, "2026-09-01", text, result, "2026-09-01", people,
            decisions={pending["semantic_questions"][0]["key"]: "separate"})
        self.assertNotEqual(separate["subjects"][0]["contribution"]["assertions"][0]["id"],
                            existing["subjects"][0]["contribution"]["assertions"][0]["id"])
        for changed in ("Wren may have two siblings.", "Wren has three siblings.", "Wren has no siblings."):
            self.assertEqual(self.prepare(changed, people)["status"], "filing")

    def test_unknown_revisions_are_only_valid_for_verified_protected_legacy(self):
        from entity_biographical import valid_data, legacy_rows
        ef = load("entity-filing.py", "bio_revision_format")
        line = ef.fact_line("2026-09-01", "Wren moved.", "AAA-111")
        row = legacy_rows("# Wren\n\n## Biographical Log\n" + line, "BBB-222", "Wren")[0]
        self.assertIsNone(row["data"]["references"][0]["source_revision"])
        valid_data(row["data"])
        for kind in ("capture", "passive"):
            data = copy.deepcopy(row["data"])
            data["references"][0]["kind"] = kind
            with self.assertRaises(ValueError):
                valid_data(data)
        data = copy.deepcopy(row["data"])
        data["references"][0]["fingerprint"] = "a" * 8
        with self.assertRaises(ValueError):
            valid_data(data)

    def test_literal_ownership_escape_is_injective_and_preserves_source(self):
        import entity_biographical as bio
        text = ('Wren likes `<!-- bio:v2:example -->`, <!-- fact:abcd -->, '
                '<!-- capture:example:begin --> and <!-- capture-receipt:example -->.\n'
                '<\u2060!-- bio:v2:already escaped --> <\u2060\u2060!-- fact:abcd --> '
                '&lt;!-- bio:v2:entity --&gt; &amp; "Wren" 😀')
        manifest = self.prepare(text)
        row = manifest["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", row, "BBB-222")
        self.assertEqual(row["reference"]["evidence"], text)
        self.assertEqual(bio.render(body, "BBB-222")[0]["visible"], row["baseline"]["text"])
        self.assertEqual(bio.decode_literal(bio.encode_literal(text)), text)
        self.assertNotEqual(bio.encode_literal(text), bio.encode_literal(bio.encode_literal(text)))
        self.assertEqual(body.count("<!-- bio:v2:"), 1)
        bio.verify(body, row, "BBB-222")
        self.assertNotIn("<!-- bio:v2:", bio.detach(body, row, "BBB-222"))
        capture.decode_manifest(capture.encode_manifest(manifest), SOURCE["uuid"])

    def test_copied_person_binding_blocks_every_ownership_mutation(self):
        import entity_biographical as bio
        row = self.prepare("Wren moved.")["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", row, "BBB-222")
        for action in (bio.attach, bio.verify, bio.detach, bio.protect):
            with self.subTest(action=action.__name__), self.assertRaises(ValueError):
                action(body, row, "DDD-444")
        for person_uuid in (None, "", "DDD-444"):
            data = copy.deepcopy(bio.render(body)[0]["data"])
            if person_uuid is None:
                del data["person_uuid"]
            else:
                data["person_uuid"] = person_uuid
            with self.subTest(binding=person_uuid), self.assertRaises(ValueError):
                bio.render("## Biographical Log\n" + bio.format_data(data), "BBB-222")

    def test_last_support_can_be_empty_only_for_protected_content(self):
        import entity_biographical as bio
        row = self.prepare("Wren moved.")["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", row, "BBB-222")
        protected = bio.detach(bio.protect(body, row, "BBB-222"), row, "BBB-222")
        data = bio.render(protected, "BBB-222")[0]["data"]
        self.assertTrue(data["protected"])
        self.assertEqual(data["references"], [])
        self.assertNotIn("[source]", protected)
        self.assertEqual(data["baseline"]["origin"], "capture")
        data["protected"] = False
        with self.assertRaises(ValueError):
            bio.render("## Biographical Log\n" + bio.format_data(data), "BBB-222")
        self.assertEqual(bio.render(bio.detach(body, row, "BBB-222")), [])

    def test_quoted_copies_never_supply_active_ownership(self):
        import entity_biographical as bio
        row = self.prepare("Wren moved.")["subjects"][0]["contribution"]["assertions"][0]
        body = bio.attach("# Wren\n\n", row, "BBB-222")
        for prefix in ("> ", "  > ", "> > ", "    > > "):
            quoted = "\n".join(prefix + line for line in body.splitlines())
            self.assertEqual(bio.render(quoted, "BBB-222"), [])
            self.assertEqual(len(bio.render(body + "\n\n" + quoted, "BBB-222")), 1)
            for action in (bio.verify, bio.detach, bio.protect):
                with self.subTest(prefix=prefix, action=action.__name__), self.assertRaises(ValueError):
                    action(quoted, row, "BBB-222")
        with self.assertRaises(ValueError):
            bio.render(body + "\n<!-- bio:v2:broken")

    def test_within_passage_duplicates_share_assertion_but_keep_both_spans(self):
        import entity_biographical as bio
        text = "Wren has two siblings. Wren has two siblings."
        manifest = self.prepare(text)
        rows = manifest["subjects"][0]["contribution"]["assertions"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertNotEqual(rows[0]["reference"]["id"], rows[1]["reference"]["id"])
        self.assertEqual("".join(row["reference"]["evidence"] for row in rows), text)
        body = "# Wren\n\n"
        for row in rows:
            body = bio.attach(body, row, "BBB-222")
        self.assertEqual(len(bio.render(body)), 1)
        self.assertEqual(len(bio.render(body)[0]["data"]["references"]), 2)
        body = bio.detach(body, rows[1], "BBB-222")
        bio.verify(body, rows[0], "BBB-222")
        self.assertEqual(bio.render(bio.detach(body, rows[0], "BBB-222")), [])
        capture.decode_manifest(capture.encode_manifest(manifest), SOURCE["uuid"])

    def test_manual_edits_and_malformed_markers_stop_deletion(self):
        from entity_biographical import attach, detach, protect
        row = self.prepare("Wren moved.")["subjects"][0]["contribution"]["assertions"][0]
        body = attach("# Wren\n\n", row, "BBB-222")
        edited = body.replace("Moved. ([source]", "Moved twice. ([source]")
        with self.assertRaises(ValueError):
            detach(edited, row, "BBB-222")
        self.assertIn("Moved twice.", detach(protect(edited, row, "BBB-222"), row, "BBB-222"))
        with self.assertRaises(ValueError):
            detach(body + body, row, "BBB-222")


if __name__ == "__main__":
    unittest.main()
