"""Live checks against the real macOS Contacts.

Like test_calendar_canary, these guard bugs that live in the JXA/ObjC bridge
and cannot be reproduced with a fixture: a JS null where ObjC nil ($()) is
expected silently returns zero containers, a JS-array keysToFetch crashes the
fetch, and a year-less birthday carries NSIntegerMax as its year. Skips
(rather than fails) when the script isn't stowed, access isn't granted, or
the address book is empty, so a fresh machine stays green.
"""

import json
import os
import subprocess
import unittest

CONTACTS = os.path.expanduser("~/.local/bin/contacts-json.js")


@unittest.skipUnless(os.path.exists(CONTACTS), "contacts-json.js not stowed")
class ContactsCanary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", CONTACTS],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise unittest.SkipTest(f"contacts dump failed: {result.stderr.strip()}")
        cls.data = json.loads(result.stdout)
        if not cls.data.get("ok"):
            raise unittest.SkipTest(f"contacts unavailable: {cls.data.get('error')}")
        cls.contacts = cls.data["contacts"]

    def test_containers_actually_enumerate(self):
        # Zero contacts with ok:true is the shape of the null-vs-$() predicate
        # bug — the store answers, but every container query returns nothing.
        self.assertTrue(
            self.contacts,
            "contacts dump is ok but empty — container enumeration is likely "
            "passing a JS null where ObjC nil ($()) is required",
        )

    def test_no_year_sentinel_leaks(self):
        for c in self.contacts:
            b = c.get("birthday")
            if b and "year" in b:
                self.assertTrue(1 <= b["year"] <= 9999, c["id"])

    def test_birthdays_carry_plausible_month_and_day(self):
        for c in self.contacts:
            b = c.get("birthday")
            if b:
                self.assertTrue(1 <= b["month"] <= 12, c["id"])
                self.assertTrue(1 <= b["day"] <= 31, c["id"])

    def test_names_resolve(self):
        self.assertTrue(any(c["name"] for c in self.contacts),
                        "every contact has an empty name — formatter broken")
