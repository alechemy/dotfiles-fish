import unittest

from helpers import load

aw = load("awake-seconds", "awake_seconds")


def pmset(*entries):
    """Render (timestamp, domain) pairs as pmset -g log lines, with the
    surrounding noise a real log carries."""
    lines = [
        "PM configuration log:",
        "Time stamp                Domain          Message",
        "2026-07-19 08:00:00 -0500 Assertions      \tPID 123 Created "
        "MAINTENANCE InternalPreventSleep",
        "2026-07-19 08:00:01 -0500 Wake Requests   \t[process=powerd "
        "request=Maintenance deltaSecs=7200]",
    ]
    for ts, domain in entries:
        lines.append(f"{ts} {domain:<15} \tEntering state details 21 secs")
    return lines


def epoch(hms, day=19, offset="-0500"):
    return aw.parse_events(
        [f"2026-07-{day:02d} {hms} {offset} Wake \tprobe"])[0][0]


class ParseEventsTest(unittest.TestCase):
    def test_only_sleep_wake_domains_parse(self):
        events = aw.parse_events(pmset(
            ("2026-07-19 22:00:00 -0500", "Sleep"),
            ("2026-07-20 06:00:00 -0500", "Wake"),
        ))
        self.assertEqual(len(events), 2)
        self.assertEqual([asleep for _, asleep in events], [True, False])

    def test_darkwake_counts_as_asleep(self):
        events = aw.parse_events(pmset(("2026-07-20 04:00:00 -0500", "DarkWake")))
        self.assertEqual([asleep for _, asleep in events], [True])

    def test_start_counts_as_awake(self):
        events = aw.parse_events(pmset(("2026-07-20 09:00:00 -0500", "Start")))
        self.assertEqual([asleep for _, asleep in events], [False])

    def test_garbage_and_malformed_timestamps_ignored(self):
        events = aw.parse_events([
            "not a log line",
            "2026-99-99 22:00:00 -0500 Sleep \tbad date",
            "2026-07-19 22:00:00 -0500 SleepService \tnot a sleep domain",
        ])
        self.assertEqual(events, [])

    def test_prefix_domains_do_not_match(self):
        # "Wake Requests" is logged seconds after each dark wake re-sleeps;
        # matching its first word would fabricate an awake period per nap.
        events = aw.parse_events([
            "2026-07-19 23:11:53 -0500 Wake Requests       \t[process="
            "mDNSResponder request=Maintenance deltaSecs=7200]",
            "2026-07-19 23:11:54 -0500 Sleep/Wake failure  \tdetails",
        ])
        self.assertEqual(events, [])

    def test_events_sorted_by_absolute_time_across_offsets(self):
        # 06:30 -0500 is 11:30Z; 12:40 +0100 is 11:40Z — later despite the
        # earlier-looking local ordering a timezone hop produces.
        events = aw.parse_events(pmset(
            ("2026-07-20 12:40:00 +0100", "Wake"),
            ("2026-07-20 06:30:00 -0500", "Sleep"),
        ))
        self.assertEqual([asleep for _, asleep in events], [True, False])


class AwakeSecondsTest(unittest.TestCase):
    def window(self, entries, start_hms, end_hms, start_day=19, end_day=20):
        events = aw.parse_events(pmset(*entries))
        return aw.awake_seconds(
            events, epoch(start_hms, start_day), epoch(end_hms, end_day))

    def test_no_events_counts_whole_window_awake(self):
        self.assertEqual(
            self.window([], "10:00:00", "11:00:00", end_day=19), 3600)

    def test_sleep_covered_gap_counts_only_awake_edges(self):
        awake = self.window([
            ("2026-07-19 22:10:00 -0500", "Sleep"),
            ("2026-07-20 06:50:00 -0500", "Wake"),
        ], "22:00:00", "07:00:00")
        self.assertEqual(awake, 600 + 600)

    def test_darkwake_does_not_end_sleep(self):
        awake = self.window([
            ("2026-07-19 22:10:00 -0500", "Sleep"),
            ("2026-07-20 04:00:00 -0500", "DarkWake"),
            ("2026-07-20 04:01:00 -0500", "Sleep"),
            ("2026-07-20 06:50:00 -0500", "Wake"),
        ], "22:00:00", "07:00:00")
        self.assertEqual(awake, 600 + 600)

    def test_window_starting_mid_sleep_infers_state_from_prior_event(self):
        awake = self.window([
            ("2026-07-19 20:00:00 -0500", "Sleep"),
            ("2026-07-20 06:50:00 -0500", "Wake"),
        ], "22:00:00", "07:00:00")
        self.assertEqual(awake, 600)

    def test_boot_after_sleep_counts_awake_from_start_event(self):
        awake = self.window([
            ("2026-07-19 22:10:00 -0500", "Sleep"),
            ("2026-07-20 06:50:00 -0500", "Start"),
        ], "22:00:00", "07:00:00")
        self.assertEqual(awake, 600 + 600)

    def test_events_outside_window_only_set_initial_state(self):
        awake = self.window([
            ("2026-07-19 20:00:00 -0500", "Wake"),
            ("2026-07-20 08:00:00 -0500", "Sleep"),
        ], "22:00:00", "07:00:00")
        self.assertEqual(awake, 9 * 3600)

    def test_empty_or_inverted_window_is_zero(self):
        events = aw.parse_events(pmset())
        t = epoch("10:00:00")
        self.assertEqual(aw.awake_seconds(events, t, t), 0)
        self.assertEqual(aw.awake_seconds(events, t, t - 60), 0)


if __name__ == "__main__":
    unittest.main()
