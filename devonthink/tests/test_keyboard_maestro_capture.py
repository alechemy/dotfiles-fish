import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2] / "keyboard-maestro"


class KeyboardMaestroCaptureTests(unittest.TestCase):
    def test_delimiter_and_unicode_round_trip_through_both_consumers(self):
        title = "Review <<<SPLIT>>> café"
        body = "# " + title + "\n\nBody <<<SPLIT>>> with \\\"quotes\\\"."
        result = subprocess.run(
            ["/usr/bin/python3", str(ROOT / "classify-note.py")],
            env=dict(os.environ, RAW_TEXT=body),
            capture_output=True, text=True, check=True)
        mode, payload = result.stdout.split("\n", 1)
        self.assertEqual(mode, "markdown")
        for name in ("km-new-inbox-note.applescript", "km-new-archive-note.applescript"):
            with self.subTest(consumer=name):
                source = (ROOT / name).read_text()
                start = source.index("    set titleEnd to")
                end = source.index("    end try", start) + len("    end try")
                harness = ('on run argv\nset payload to item 1 of argv\n'
                           + source[start:end]
                           + '\nreturn theTitle & linefeed & theBody\nend run')
                decoded = subprocess.run(["/usr/bin/osascript", "-", payload],
                                         input=harness, capture_output=True,
                                         text=True, check=True, timeout=15)
                self.assertEqual(decoded.stdout.rstrip("\n"), title + "\n" + body)

    def test_lint_and_readback_failures_clean_up_and_preserve_raw_body(self):
        source = (ROOT / "km-new-inbox-note.applescript").read_text()
        start = source.index('    set tmpPath to ""')
        end = source.index('    tell application id "DNtp"', start)
        body = source[start:end]
        for mode in ("lint-fails", "readback-fails", "success"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                helper = Path(tmp) / "lint"
                helper.write_text({
                    "lint-fails": '#!/bin/sh\nprintf partial > "$1"\nexit 1\n',
                    "readback-fails": '#!/bin/sh\nrm "$1"\n',
                    "success": '#!/bin/sh\nprintf formatted > "$1"\n',
                }[mode])
                helper.chmod(0o755)
                code = body.replace("/tmp/km-inbox-note.", tmp + "/km-inbox-note.")
                code = code.replace("$HOME/.local/bin/lint-markdown-file", str(helper))
                result = subprocess.run(["/usr/bin/osascript", "-"],
                                        input='set theBody to "raw body"\n' + code + '\nreturn theBody',
                                        capture_output=True, text=True,
                                        check=True, timeout=15)
                self.assertEqual(result.stdout.strip(), "formatted" if mode == "success" else "raw body")
                self.assertEqual(list(Path(tmp).iterdir()), [helper])


if __name__ == "__main__":
    unittest.main()
