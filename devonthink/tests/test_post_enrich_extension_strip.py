"""A title's mid-string period must survive the extension strip.

Post-Enrich & Archive derives two things from the record name: the daily-note
bullet text (docBaseName) and the markdown H1 (titleForH1). Both stripped the
name with `sed 's/\\.[^.]*$//'`, meant to drop a file extension — but DEVONthink's
`name` carries no extension, so on a title like "…at Dr. Sirius Yoo / SKY …" the
strip ate everything from the first period, truncating both the bullet and the
H1 to "…at Dr". The strip must fire only for a real trailing extension.

Guards two regressions in one file:
  1. the strip uses the extension-only pattern, never the greedy `[^.]*` form;
  2. titleForH1 is built with `printf` — `echo` under `without altering line
     endings` leaks a trailing newline into the H1 (an extra blank line).
"""

import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "stow" / "devonthink" / "Library" / "Application Scripts"
          / "com.devon-technologies.think" / "Smart Rules"
          / "post-enrich-and-archive.applescript")

TRUNCATING_TITLE = ("HSA & Insurance for a Combined Septorhinoplasty at "
                    "Dr. Sirius Yoo - SKY Facial Plastic Surgery (San Diego)")


def strip_ext(name):
    """Run the guarded sed the AppleScript builds, mirroring `quoted form of`."""
    return subprocess.run(
        ["/bin/sh", "-c", "printf '%s' \"$1\" | sed -E 's/\\.[A-Za-z]{2,5}$//'",
         "sh", name],
        capture_output=True, text=True, check=True,
    ).stdout


class ExtensionStripGuard(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()
        self.sed_lines = [l for l in self.text.splitlines() if "| sed " in l]

    def test_no_greedy_extension_strip_remains(self):
        for line in self.sed_lines:
            self.assertNotIn(
                "[^.]*", line,
                "a greedy `\\.[^.]*$` strip truncates a title at its first "
                "period (e.g. \"at Dr. Smith\" → \"at Dr\") — use "
                "`\\.[A-Za-z]{2,5}$`")

    def test_both_derivations_use_the_guarded_pattern(self):
        guarded = [l for l in self.sed_lines if "[A-Za-z]{2,5}$" in l]
        self.assertEqual(
            len(guarded), 2,
            "expected the guarded extension strip on both docBaseName and "
            "titleForH1")

    def test_title_for_h1_uses_printf_not_echo(self):
        line = next(l for l in self.text.splitlines() if "titleForH1" in l)
        self.assertIn("printf '%s'", line)
        self.assertNotIn("echo ", line,
                          "echo leaks a trailing newline the H1 sync keeps")

    def test_mid_title_period_is_preserved(self):
        self.assertEqual(strip_ext(TRUNCATING_TITLE), TRUNCATING_TITLE)

    def test_real_extension_is_stripped(self):
        self.assertEqual(strip_ext("meeting-notes.md"), "meeting-notes")

    def test_version_suffix_is_not_an_extension(self):
        self.assertEqual(strip_ext("Report v1.2"), "Report v1.2")


if __name__ == "__main__":
    unittest.main()
