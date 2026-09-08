import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN

SCRIPT = BIN / "lint-markdown-file"
RULES = BIN.parents[1] / ".config/dt-pipeline/markdownlint-rules.cjs"
FIXTURE = Path(__file__).parent / "fixtures/markdown-rendering.md"


class MarkdownTildes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / ".markdownlint.json"
        self.config.write_text(json.dumps({"default": False}))
        self.rule_link = self.root / ".config/dt-pipeline/markdownlint-rules.cjs"
        self.rule_link.parent.mkdir(parents=True)
        self.rule_link.symlink_to(RULES)
        self.path = self.root / "fixture.md"
        self.env = {**os.environ, "HOME": str(self.root), "PIPELINE_MANUAL": "1"}

    def invoke(self, *args):
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=20,
        )

    def transform(self, text):
        self.path.write_bytes(text.encode("utf-8"))
        result = self.invoke(str(self.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return self.path.read_bytes().decode("utf-8")

    def check_case(self, text, expected):
        output = self.transform(text)
        self.assertEqual(output, expected)
        self.assertEqual(self.transform(output), output)

    def test_prose_escaping_preserves_pairs_and_existing_escapes(self):
        cases = [
            ("~12 ~words~ ~~deleted~~\n", "\\~12 \\~words\\~ ~~deleted~~\n"),
            (r"\~escaped \\~bare \\\~escaped" + "\n",
             r"\~escaped \\\~bare \\\~escaped" + "\n"),
            ("text ~~~odd~~~ ~~~~even~~~~\n", "text ~~\\~odd~~\\~ ~~~~even~~~~\n"),
            ("%%DT%% %%ET%% ~12\n", "%%DT%% %%ET%% \\~12\n"),
            ("📚 ~12 **~bold**\nnext ~13\n", "📚 \\~12 **\\~bold**\nnext \\~13\n"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.check_case(text, expected)

    def test_inline_code_is_literal(self):
        cases = [
            ("Before ~12 `~/file` after ~13\n", "Before \\~12 `~/file` after \\~13\n"),
            ("``~code ` tick`` and ~12\n", "``~code ` tick`` and \\~12\n"),
            ("`line one\n~line two` after ~12\n", "`line one\n~line two` after \\~12\n"),
            (r"\`~prose\`" + "\n", r"\`\~prose\`" + "\n"),
            ("Unmatched ` ~prose\n", "Unmatched ` \\~prose\n"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.check_case(text, expected)

    def test_fences_preserve_content_and_delimiters(self):
        cases = [
            "~~~text\n~/file\n~~~\n",
            "```sh\nprintf '%s' ~/file\n```\n",
            "~~~~text\n~~~\n~code\n~~~~\n",
            "~~~text\n```\n~code\n~~~\n",
            "~~~text\n~~~ not a closing fence\n~code\n~~~\n",
            "~~~text\n~unclosed\n",
            "```text\n~unclosed\n",
            "> ~~~text\n> ~/file\n> ~~~\n",
            "- Item\n\n  ~~~text\n  ~/file\n  ~~~\n",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.check_case(text, text)
        self.check_case("~~~\n~code\n~~~\n\n~prose\n",
                        "~~~\n~code\n~~~\n\n\\~prose\n")

    def test_indented_code_and_container_prose(self):
        self.check_case("    ~/file\n\n~prose\n", "    ~/file\n\n\\~prose\n")
        self.check_case(
            "> ~quote\n\n- ~item\n  continuation ~12\n",
            "> \\~quote\n\n- \\~item\n  continuation \\~12\n",
        )

    def test_frontmatter_html_and_link_destinations_remain_literal(self):
        cases = [
            ("---\npath: ~/file\n---\n\n~prose\n",
             "---\npath: ~/file\n---\n\n\\~prose\n"),
            ('<pre>\n~/file\n</pre>\n\n~prose\n',
             '<pre>\n~/file\n</pre>\n\n\\~prose\n'),
            ('[~label](https://example.invalid/~path "~title")\n',
             '[\\~label](https://example.invalid/~path "~title")\n'),
            ('[label][ref]\n\n[ref]: https://example.invalid/~path "~title"\n',
             '[label][ref]\n\n[ref]: https://example.invalid/~path "~title"\n'),
            ('<https://example.invalid/~path> https://example.invalid/~path\n',
             '<https://example.invalid/~path> https://example.invalid/~path\n'),
            ('[~label][~ref] [~ref] [~ref][] ![~ref][]\n\n[~ref]: /~path\n',
             '[\\~label][~ref] [~ref] [~ref][] ![~ref][]\n\n[~ref]: /~path\n'),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.check_case(text, expected)

    def test_line_endings_and_empty_input(self):
        for eol in ("\n", "\r\n", "\r"):
            with self.subTest(eol=repr(eol)):
                self.check_case("~12 `~/file`" + eol, "\\~12 `~/file`" + eol)
        self.check_case("", "")

    def test_pipeline_rendering_fixture_preserves_code(self):
        self.config.write_text("{}")
        text = FIXTURE.read_text()
        output = self.transform(text)
        self.assertIn("`~/fixture/input.md`", output)
        self.assertIn("\n~~~text\n", output)
        self.assertIn("\n~~~\n", output)
        self.assertIn("printf '%s\\n' ~/fixture/input.md", output)
        self.assertIn("The approximate value is \\~12 units.", output)
        self.assertIn("This final sentence must survive every preview change, edit, save, and source export.", output)
        self.assertEqual(self.transform(output), output)

    def test_tab_conversion_and_standard_fixes_remain_enabled(self):
        self.config.write_text("{}")
        output = self.transform("# Title\n\n-\tItem   \n\n~12\n")
        self.assertNotIn("\t", output)
        self.assertIn("\\~12", output)
        self.assertNotIn("Item   \n", output)

    def test_invalid_arguments_and_missing_rule_fail(self):
        self.assertEqual(self.invoke().returncode, 2)
        self.assertEqual(self.invoke(str(self.path)).returncode, 2)
        self.path.write_text("~12\n")
        self.rule_link.unlink()
        self.assertNotEqual(self.invoke(str(self.path)).returncode, 0)
        self.assertEqual(self.path.read_text(), "~12\n")

    def test_unfixable_lint_findings_remain_nonfatal(self):
        self.config.write_text(json.dumps({"default": False, "MD025": True}))
        self.check_case("# First\n\n~12\n\n# Second\n",
                        "# First\n\n\\~12\n\n# Second\n")

    def test_formatter_failures_are_not_a_success(self):
        self.rule_link.unlink()
        cases = [
            ("module.exports = ;", 3),
            ('module.exports = {names: ["DT001"], tags: ["DT001"], '
             'description: "Invalid fixture rule", parser: "none", function() {}};', 4),
        ]
        for rule, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                self.rule_link.write_text(rule)
                self.path.write_text("~12\n")
                result = self.invoke(str(self.path))
                self.assertEqual(result.returncode, expected_status)
                self.assertTrue(result.stderr)
                self.assertEqual(self.path.read_text(), "~12\n")


if __name__ == "__main__":
    unittest.main()
