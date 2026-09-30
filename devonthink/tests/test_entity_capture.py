import unittest

from helpers import load, person

capture = load("entity_capture.py", "entity_capture_test")


class CaptureResolution(unittest.TestCase):
    def resolve(self, text="Wren moved to Denver.", people=(), subjects=None):
        if subjects is None:
            subjects = [{"mention": "Wren"}]
        return capture.resolve(text, subjects, list(people))

    def test_new_single_name_with_empty_roster(self):
        result = self.resolve()
        self.assertEqual(result["status"], "new")
        self.assertEqual(result["subjects"][0]["name"], "Wren")
        self.assertEqual(result["subjects"][0]["passage"], "Wren moved to Denver.")

    def test_existing_single_name(self):
        result = self.resolve(people=[person("Wren", "P1")])
        self.assertEqual(result["status"], "existing")
        self.assertEqual(result["subjects"][0]["uuid"], "P1")

    def test_new_single_name_without_competing_identity(self):
        self.assertEqual(self.resolve(people=[person("Rowan Vale")])["status"], "new")

    def test_model_expansion_cannot_choose_between_short_names(self):
        result = self.resolve(people=[person("Wren Vale"), person("Wren Pike")],
                              subjects=[{"mention": "Wren", "match": "Wren Vale"}])
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "ambiguous_name")
        self.assertEqual(len(result["choices"]), 2)

    def test_single_name_record_does_not_hide_short_name_collision(self):
        result = self.resolve(people=[person("Wren"), person("Wren Vale")])
        self.assertEqual(result["status"], "unresolved")

    def test_unique_short_name_resolves_without_alias(self):
        result = self.resolve(people=[person("Wren Vale", "P1")])
        self.assertEqual(result["subjects"][0]["uuid"], "P1")

    def test_surname_alone_does_not_block_new_full_name(self):
        result = self.resolve("Wren Vale moved.", [person("Rowan Vale")],
                              [{"mention": "Wren Vale"}])
        self.assertEqual(result["status"], "new")

    def test_distinct_spellings_are_not_merged(self):
        result = self.resolve("Ren moved.", [person("Wren")], [{"mention": "Ren"}])
        self.assertEqual(result["status"], "new")

    def test_case_and_accent_alias(self):
        result = self.resolve("RENEE moved.", [person("Rowan Vale", "P1", aliases="Renée")],
                              [{"mention": "RENEE"}])
        self.assertEqual(result["subjects"][0]["uuid"], "P1")

    def test_conflicting_email_is_not_overridden_by_name(self):
        result = self.resolve("Wren moved. Email wren@x.com.",
                              [person("Wren", "P1"), person("Rowan", "P2", email="wren@x.com")],
                              [{"mention": "Wren", "email": "wren@x.com"}])
        self.assertEqual(result["reason"], "conflicting_identifiers")

    def test_invented_name_is_not_evidence(self):
        result = self.resolve(subjects=[{"mention": "Wren Vale"}])
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["reason"], "ungrounded_subject")

    def test_invented_email_is_not_evidence(self):
        result = self.resolve(subjects=[{"mention": "Wren", "email": "wren@x.com"}])
        self.assertEqual(result["status"], "deferred")

    def test_suppression_is_preserved(self):
        result = self.resolve(people=[person("Wren", filingsuppressed="true")])
        self.assertEqual(result["reason"], "suppressed")

    def test_ignored_new_identity_is_preserved(self):
        result = capture.resolve("Wren moved.", [{"mention": "Wren"}], [], ignored={"wren"})
        self.assertEqual(result["reason"], "ignored")

    def test_empty_enrichment_and_incidental_relative_preserve_whole_note(self):
        text = "Wren moved to Denver. Her daughter starts college in October.\n- Send a card."
        result = self.resolve(text, subjects=[{"mention": "Wren", "facts": []}])
        self.assertEqual(result["subjects"][0]["passage"], text)

    def test_long_capture_is_not_truncated(self):
        text = "Wren said " + "something mundane.\n" * 10000
        self.assertEqual(self.resolve(text)["subjects"][0]["passage"], text)

    def test_multiple_subjects_require_source_passages(self):
        text = "Wren moved. Rowan retired."
        result = self.resolve(text, subjects=[{"mention": "Wren"}, {"mention": "Rowan"}])
        self.assertEqual(result["reason"], "passage_selection_needed")

    def test_separate_passages_are_attributed_without_duplication(self):
        result = self.resolve("Wren moved. Rowan retired.", subjects=[
            {"mention": "Wren", "passage": "Wren moved."},
            {"mention": "Rowan", "passage": "Rowan retired."}])
        self.assertEqual(result["status"], "new")
        self.assertEqual([s["passage"] for s in result["subjects"]],
                         ["Wren moved.", "Rowan retired."])

    def test_unattributed_text_never_reports_partial_success(self):
        result = self.resolve("Wren moved. Rowan retired. Also a mystery.", subjects=[
            {"mention": "Wren", "passage": "Wren moved."},
            {"mention": "Rowan", "passage": "Rowan retired."}])
        self.assertEqual(result["reason"], "passage_selection_needed")

    def test_no_subject_remains_pending(self):
        result = self.resolve(subjects=[])
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(result["reason"], "no_subject")
