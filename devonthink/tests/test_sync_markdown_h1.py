import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN

SCRIPT = BIN / "sync-markdown-h1.py"


def run(text, title):
    return subprocess.run(
        ["/usr/bin/python3", str(SCRIPT), title],
        input=text, capture_output=True, text=True, check=True,
    ).stdout


class ExistingH1(unittest.TestCase):
    def test_matching_h1_leaves_input_byte_identical(self):
        text = "# Title\n\nbody\n"
        self.assertEqual(run(text, "Title"), text)

    def test_differing_h1_is_replaced_in_place(self):
        self.assertEqual(run("# Old\n\nbody\n", "New"), "# New\n\nbody\n")

    def test_hash_inside_a_fenced_block_is_not_treated_as_h1(self):
        text = "# Real\n\n```\n# not h1\n```\n"
        self.assertEqual(run(text, "Real"), text)


class MissingH1(unittest.TestCase):
    def test_injected_after_frontmatter(self):
        out = run("---\nk: v\n---\nbody\n", "Injected")
        self.assertEqual(out, "---\nk: v\n---\n\n# Injected\n\nbody\n")

    def test_injected_at_top_when_no_frontmatter(self):
        self.assertEqual(run("body text\n", "Top"), "# Top\n\nbody text\n")


class EmptyInput(unittest.TestCase):
    def test_whitespace_only_input_is_returned_unchanged(self):
        self.assertEqual(run("", "X"), "")
        self.assertEqual(run("   \n\n", "X"), "   \n\n")


class CarriageReturnInput(unittest.TestCase):
    """A body written back by AppleScript is CR-delimited. Splitting it on \\n
    would collapse it to one line, and rewriting lines[0] would emit the H1
    alone — the document body silently destroyed."""

    def test_cr_body_keeps_its_content_and_is_normalized(self):
        self.assertEqual(
            run("# Old\r\rbody text\r\r- a bullet\r", "New"),
            "# New\n\nbody text\n\n- a bullet\n",
        )

    def test_crlf_body_keeps_its_content(self):
        self.assertEqual(
            run("# Old\r\n\r\nbody text\r\n", "New"), "# New\n\nbody text\n")


FRONTMATTER_CASES = {
    "unclosed_with_h1": ("---\nk: v\n\n# Title\n\nbody\n", "Title", 4, 4),
    "unclosed_with_whitespace_separator": (
        "---\nk: v\n \t \n# Title\n\nbody\n", "Title", 4, 4),
    "unclosed_without_h1": ("---\nk: v\n\nbody\n", None, 0, 4),
    "unclosed_without_blank": ("---\nk: v", None, 0, 3),
    "opening_delimiter_only": ("---", None, 0, 2),
    "closed_with_blank_and_metadata_heading": (
        "---\nk: v\n\n# Metadata\n---\n# Title\n\nbody\n", "Title", 6, 6),
    "closed_without_h1": ("---\nk: v\n---\nbody\n", None, 0, 4),
    "closed_without_body": ("---\nk: v\n---", None, 0, 4),
    "unclosed_with_backtick_fence": (
        "---\nk: v\n\n```\n# Code\n```\n# Title\nbody\n", "Title", 7, 4),
    "unclosed_with_tilde_fence": (
        "---\nk: v\n\n~~~\n# Code\n~~~\n# Title\nbody\n", "Title", 7, 4),
}


class FrontmatterRecovery(unittest.TestCase):
    def test_python_recovery_and_idempotence(self):
        for name, (text, heading, _, _) in FRONTMATTER_CASES.items():
            for ending in ("\n", "\r", "\r\n"):
                with self.subTest(case=name, ending=repr(ending)):
                    output = run(text.replace("\n", ending), "Title")
                    if heading:
                        self.assertEqual(output, text)
                    else:
                        self.assertEqual(output.count("# Title"), 1)
                        self.assertEqual(output.replace("# Title", "").split(), text.split())
                    self.assertEqual(run(output, "Title"), output)

    def test_python_replaces_recovered_heading_without_appending(self):
        text = "---\nk: v\n\n# Old\n\nbody\n"
        self.assertEqual(run(text, "New"), text.replace("# Old", "# New"))

    def test_python_injects_before_recovered_body(self):
        self.assertEqual(
            run("---\nk: v\n\nbody\n", "Title"),
            "---\nk: v\n\n# Title\n\nbody\n",
        )


class AppleScriptFrontmatterRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        source = (BIN.parents[1] / "Library" / "Application Scripts" /
                  "com.devon-technologies.think" / "Smart Rules" /
                  "sync-h1-and-filename.applescript").read_text()
        handlers = []
        for name in ("parseH1", "splitLines", "joinLines", "replaceText",
                     "trimWhitespace", "isEffectivelyEmpty", "injectH1"):
            matches = re.findall(
                rf"^on {name}\(.*?^end {name}$", source, re.MULTILINE | re.DOTALL)
            if len(matches) != 1:
                raise AssertionError(f"Expected one {name} handler")
            handlers.append(matches[0])
        isolated = "\n\n".join(handlers)
        if "tell application" in isolated or "do shell script" in isolated:
            raise AssertionError("Pure-handler fixture must not access applications or shell")
        wrapper = '''
property writtenText : ""
on setRecordText(r, t, logMsg)
    set writtenText to t
end setRecordText
on run argv
    set inputFile to POSIX file (item 1 of argv)
    set bodyText to ""
    if size of (info for inputFile) > 0 then set bodyText to read inputFile as «class utf8»
    set parsed to my parseH1(bodyText)
    if (item 2 of argv) is "parse" then
        set heading to h1 of parsed
        if heading is missing value then set heading to "<missing>"
        return heading & linefeed & (h1LineIndex of parsed) & linefeed & (frontmatterEndIndex of parsed)
    end if
    set writtenText to bodyText
    if h1 of parsed is missing value then
        my injectH1(missing value, "Title", bodyText, frontmatterEndIndex of parsed)
    end if
    if writtenText is "" then return ""
    set codepoints to (id of writtenText) as list
    set AppleScript's text item delimiters to ","
    return codepoints as text
end run
'''
        cls.script = cls.root / "h1.applescript"
        cls.script.write_text(isolated + "\n" + wrapper)

    def invoke(self, text, mode):
        fixture = self.root / "input.md"
        fixture.write_bytes(text.encode("utf-8"))
        result = subprocess.run(
            ["/usr/bin/osascript", str(self.script), str(fixture), mode],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if mode == "parse":
            heading, index, fm_end = result.stdout.strip().split("\n")
            return None if heading == "<missing>" else heading, int(index), int(fm_end)
        return "".join(chr(int(n)) for n in result.stdout.strip().split(",") if n)

    def test_shared_parser_cases_and_line_endings(self):
        for name, (text, heading, index, fm_end) in FRONTMATTER_CASES.items():
            for ending in ("\n", "\r", "\r\n"):
                with self.subTest(case=name, ending=repr(ending)):
                    self.assertEqual(self.invoke(text.replace("\n", ending), "parse"),
                                     (heading, index, fm_end))

    def test_injection_preserves_body_and_is_idempotent(self):
        for name, (text, heading, _, _) in FRONTMATTER_CASES.items():
            for ending in ("\n", "\r", "\r\n"):
                with self.subTest(case=name, ending=repr(ending)):
                    original = text.replace("\n", ending)
                    output = self.invoke(original, "inject")
                    if heading:
                        self.assertEqual(output, original)
                    else:
                        self.assertNotIn("\r", output)
                        self.assertEqual(output.count("# Title"), 1)
                        self.assertEqual(output.replace("# Title", "").split(), text.split())
                    self.assertEqual(self.invoke(output, "inject"), output)

    def test_existing_empty_record_behavior(self):
        self.assertEqual(self.invoke("", "inject"), "# Title\n\n")
        self.assertEqual(self.invoke(" \t ", "inject"), "# Title\n\n")


if __name__ == "__main__":
    unittest.main()
