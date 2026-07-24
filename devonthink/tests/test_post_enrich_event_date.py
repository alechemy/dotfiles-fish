"""A future EventDate must never pick the daily note a record links into.

Enrich: AI Metadata extracts eventDate from document content, and content can
carry tomorrow's date relative to the local capture moment — a research
artifact generated in UTC and captured late evening local time is saturated
with the UTC day. Post-Enrich & Archive stamps the bullet's time of day from
the record's creation date, so trusting a future EventDate for day selection
files a creation-timed bullet into a daily note for a day that hasn't happened
yet. The pinning design assumes an upload trails its event, never leads it:
the hasValidEventDate computation must reject an EventDate after the record's
creation day.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = (REPO / "stow" / "devonthink" / "Library" / "Application Scripts"
          / "com.devon-technologies.think" / "Smart Rules"
          / "post-enrich-and-archive.applescript")

CONTINUATION = re.compile(r"¬\s*\n\s*")


class FutureEventDateGuard(unittest.TestCase):
    def test_flag_rejects_event_date_after_creation_day(self):
        text = CONTINUATION.sub(" ", SCRIPT.read_text())
        condition = re.search(
            r'if eventDate is not ""(.*?)then\s*\n\s*set hasValidEventDate to true',
            text, re.S)
        self.assertIsNotNone(
            condition, "hasValidEventDate condition block not found")
        self.assertIn(
            "eventDate ≤ creationDay", condition.group(1),
            "hasValidEventDate no longer rejects an EventDate after the "
            "creation day — a future-dated extraction would pin a "
            "creation-timed bullet to a day that hasn't happened")


if __name__ == "__main__":
    unittest.main()
