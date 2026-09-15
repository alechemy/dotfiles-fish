import subprocess
import unittest
from pathlib import Path


SCRIPT = (Path(__file__).parents[2] / "stow/devonthink/Library/Application Scripts"
          / "com.devon-technologies.think/Smart Rules/enrich-ai-metadata.applescript")


class EnrichAiDateTests(unittest.TestCase):
    def test_validates_real_iso_calendar_dates(self):
        source = SCRIPT.read_text()
        start = source.index("set validDate to true")
        end = source.index('if not validDate then set theDate to ""', start)
        validation = source[start:end] + 'if not validDate then set theDate to ""'
        harness = ("on run argv\nset theDate to item 1 of argv\n" + validation
                   + "\nreturn theDate\nend run")
        cases = {"2024-02-29": "2024-02-29", "2026-09-15": "2026-09-15",
                 "2026-02-29": "", "2026-04-31": "", "2026-13-01": "",
                 "2026-00-01": "", "2026-01-00": "", "abcd-ef-gh": "",
                 "2026-1-01": "", "0000-01-01": ""}
        for value, expected in cases.items():
            with self.subTest(value=value):
                result = subprocess.run(
                    ["/usr/bin/osascript", "-", value], input=harness,
                    capture_output=True, text=True, check=True, timeout=15)
                self.assertEqual(result.stdout.strip(), expected)


if __name__ == "__main__":
    unittest.main()
