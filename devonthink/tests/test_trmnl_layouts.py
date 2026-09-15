import html
import json
import subprocess
import unittest
from pathlib import Path


LAYOUTS = Path(__file__).parents[2] / "trmnl/dt-brief/src"


class BriefLayoutTests(unittest.TestCase):
    def test_external_text_is_escaped_in_every_layout(self):
        hostile = '<script title="fixture">&unsafe</script>'
        payload = {
            "date": "2026-09-15", "svg_logo": "",
            "meetings": [{"title": hostile, "time": "10:00am",
                          "people": [{"name": hostile, "role": hostile,
                                      "employer": hostile}], "unmatched": [hostile]}],
            "birthdays": [{"name": hostile, "date": "2026-09-15", "age": 30}],
            "reconnect": [{"name": hostile, "relationship": hostile, "days": 30}],
            "on_this_day": [{"name": hostile, "kind": hostile, "years": 2}],
        }
        code = '''require "json"
require "liquid"
data = JSON.parse(STDIN.read)
template = Liquid::Template.parse(File.read(ARGV[0]))
STDOUT.write(template.render!(data))
'''
        for name, occurrences in (("full", 10), ("half_horizontal", 3),
                                  ("half_vertical", 3), ("quadrant", 1)):
            with self.subTest(layout=name):
                result = subprocess.run(["ruby", "-e", code, str(LAYOUTS / f"{name}.liquid")],
                                        input=json.dumps(payload), capture_output=True,
                                        text=True, timeout=15)
                if "cannot load such file -- liquid" in result.stderr:
                    self.skipTest("Ruby Liquid renderer is not installed")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("<script", result.stdout)
                self.assertEqual(result.stdout.count(html.escape(hostile)), occurrences)


if __name__ == "__main__":
    unittest.main()
