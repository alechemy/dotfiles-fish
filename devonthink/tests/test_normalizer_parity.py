"""One name must key identically in all three normalizers.

entity-filing's norm, the brief's norm, and the bridge's normName each build
person-identity keys; any divergence between them lets the same person key
differently across proposal, suppression, and apply — a silent duplicate or a
suppression bypass. The two Python copies are compared directly; the JS copy
is driven through the same osascript eval harness as test_section_span.py.
"""

import json
import os
import subprocess
import textwrap
import unittest
from pathlib import Path

from helpers import load

ef = load("entity-filing.py", "entity_filing")
mb = load("dt-morning-brief.py", "dt_morning_brief")

BRIDGE = (Path(__file__).resolve().parents[2] / "stow" / "devonthink" /
          ".local" / "bin" / "entity-dt-bridge.js")

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
      return JSON.stringify(cases.map(function (s) { return normName(s) }))
    }
""")

BATTERY = [
    "Straße",
    "STRASSE",
    "strasse",
    "Jos\u00e9 Quill",
    "Jose\u0301 Quill",
    "  Zora   Quill ",
    "İpek Vance",
    "ﬁnn Marsh",
    "נוֹעָה כהן",
    "",
]


def bridge_norm_all(strings):
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "normalizer-parity"
    tmp.mkdir(parents=True, exist_ok=True)
    harness = tmp / "harness.js"
    harness.write_text(HARNESS)
    payload = tmp / "cases.json"
    payload.write_text(json.dumps(strings))
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(harness), str(BRIDGE),
         str(payload)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


class NormalizerParity(unittest.TestCase):
    def test_python_copies_agree(self):
        for s in BATTERY:
            self.assertEqual(ef.norm(s), mb.norm(s), repr(s))

    def test_bridge_agrees_with_python(self):
        js = bridge_norm_all(BATTERY)
        for s, j in zip(BATTERY, js):
            self.assertEqual(ef.norm(s), j, repr(s))

    def test_sharp_s_and_uppercase_collide(self):
        self.assertEqual(ef.norm("Straße"), ef.norm("STRASSE"))
        self.assertEqual(ef.norm("Straße"), "strasse")

    def test_composed_and_decomposed_collide(self):
        self.assertEqual(ef.norm("Jos\u00e9 Quill"), ef.norm("Jose\u0301 Quill"))


if __name__ == "__main__":
    unittest.main()
