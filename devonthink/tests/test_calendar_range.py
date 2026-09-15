import json
import os
import subprocess
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "stow/devonthink/.local/bin/calendar-events-json.js"


class CalendarRangeTests(unittest.TestCase):
    def test_range_ends_at_civil_midnight_across_both_dst_transitions(self):
        source = SCRIPT.read_text()
        bounds = source[source.index("  const parts ="):source.index("  const start =")]
        for day, hours in (("2026-03-08", 23), ("2026-11-01", 25),
                           ("2026-07-01", 24)):
            with self.subTest(day=day):
                code = (f"const dateStr = {json.dumps(day)}; const endStr = dateStr;\n"
                        + bounds + "\nJSON.stringify([(dayEnd-dayStart)/3600, "
                        "new Date(dayEnd*1000).getHours()])")
                result = subprocess.run(
                    ["/usr/bin/osascript", "-l", "JavaScript", "-e", code],
                    env=dict(os.environ, TZ="America/New_York"),
                    capture_output=True, text=True, check=True, timeout=15)
                self.assertEqual(json.loads(result.stdout), [hours, 0])


if __name__ == "__main__":
    unittest.main()
