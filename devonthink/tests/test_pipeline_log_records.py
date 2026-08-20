"""One log call must produce one notification, however many lines it spans.

A failure whose message carried several lines (a captured subprocess stderr, a
traceback) used to reach dt-watchdog as one failure *per line*: a single failed
`mise upgrade` raised four notifications, none of which carried the `caused by:`
line that explained it — that line has no level token, so the scanner's failure
pattern never matched it.

Both writers now fold continuations under a leading TAB and the scanner reads
records rather than lines. The shell functions here are extracted from the
shipped scripts rather than restated, so a change to either side fails the test
instead of quietly drifting past it.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "stow" / "devonthink" / ".local" / "bin"
PIPELINE_LOG = BIN / "pipeline-log"
WATCHDOG = BIN / "dt-watchdog.sh"
UPDATE_NPM = REPO / "stow" / "mise" / ".local" / "bin" / "update-npm-tools.sh"

LOG_REL = Path("Library") / "Logs" / "devonthink-pipeline.log"
PIPE_BUF = 4096

WATCHDOG_FUNCS = ("surface_line", "absorb_continuation", "flush_pending", "scan_log")
WATCHDOG_VARS = ("FAILURE_PATTERN", "MANUAL_MARKER", "MAX_NOTIFY_PER_LOG",
                 "CAUSE_PATTERN", "MAX_CAUSE_CHARS", "PENDING_LINE",
                 "PENDING_CAUSE", "SCAN_COUNT")


def extract_function(path, name):
    """Return a shell function's source, located by its opening line."""
    lines = path.read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}() {{"))
    end = next(j for j in range(start + 1, len(lines)) if lines[j] == "}")
    return "\n".join(lines[start:end + 1])


def extract_assignments(path, names):
    pattern = re.compile(r"^(" + "|".join(names) + ")=")
    return "\n".join(l for l in path.read_text().split("\n") if pattern.match(l))


def shell_value(path, name):
    return int(extract_assignments(path, (name,)).split("=", 1)[1])


MAX_RECORD_CHARS = shell_value(PIPELINE_LOG, "MAX_RECORD_CHARS")
MAX_NOTIFY_PER_LOG = shell_value(WATCHDOG, "MAX_NOTIFY_PER_LOG")
MAX_ERR_LINES = shell_value(UPDATE_NPM, "MAX_ERR_LINES")


def write_record(home, component, level, message, *rest):
    subprocess.run(
        [str(PIPELINE_LOG), component, level, message, *rest],
        env={**os.environ, "HOME": str(home), "PIPELINE_MANUAL": "0"},
        capture_output=True, text=True, check=True,
    )


def read_log(home):
    return (Path(home) / LOG_REL).read_text().rstrip("\n").split("\n")


def records(lines):
    return [l for l in lines if not l.startswith("\t")]


def continuations(lines):
    return [l for l in lines if l.startswith("\t")]


def scan(log_text):
    """Run the shipped watchdog scan over log_text; return notification bodies."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "state").mkdir()
        (d / "state" / "notified.txt").touch()
        (d / "state" / "test.log.offset").write_text("0")
        (d / "test.log").write_text(log_text)
        script = "\n".join([
            f'SCAN_STATE_DIR="{d}/state"',
            'NOTIFIED_FILE="$SCAN_STATE_DIR/notified.txt"',
            extract_assignments(WATCHDOG, WATCHDOG_VARS),
            "log() { :; }",
            'notify() { printf "NOTIFY>>%s\\n" "$1"; }',
            *[extract_function(WATCHDOG, n) for n in WATCHDOG_FUNCS],
            f'scan_log "{d}/test.log"',
        ])
        out = subprocess.run(["/bin/bash", "-c", script],
                             capture_output=True, text=True, check=True)
    return [l[len("NOTIFY>>"):] for l in out.stdout.split("\n")
            if l.startswith("NOTIFY>>")]


def sanitize(raw):
    script = "\n".join([
        extract_assignments(UPDATE_NPM, ("MAX_ERR_LINES",)),
        extract_function(UPDATE_NPM, "sanitize_output"),
        "sanitize_output",
    ])
    out = subprocess.run(["/bin/bash", "-c", script], input=raw,
                         capture_output=True, text=True, check=True)
    return out.stdout.rstrip("\n").split("\n") if out.stdout.strip() else []


def failure(component, head, *conts):
    lines = [f"2026-08-20T03:15:08 ERROR [{component}] {head}"]
    lines.extend(f"\t{c}" for c in conts)
    return lines


class ShellWriterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / LOG_REL.parent).mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def test_multiline_message_is_one_record(self):
        write_record(self.home, "npm-tools-update", "ERROR",
                     "upgrade failed:\nmise ERROR could not resolve\n"
                     "  caused by: trust downgrade\nmise ERROR Version: 1.2.3")
        lines = read_log(self.home)
        self.assertEqual(len(records(lines)), 1)
        self.assertEqual(len(continuations(lines)), 3)

    def test_first_line_heads_the_record(self):
        write_record(self.home, "comp", "ERROR", "head line\nsecond line")
        head = records(read_log(self.home))[0]
        self.assertTrue(head.endswith("[comp] head line"))

    def test_blank_continuations_are_dropped(self):
        write_record(self.home, "comp", "ERROR", "head\n\n   \nreal detail\n\n")
        self.assertEqual(continuations(read_log(self.home)), ["\treal detail"])

    def test_record_suffix_stays_on_the_head_line(self):
        write_record(self.home, "comp", "ERROR", "head\ndetail", "Some Note", "UUID-1")
        head = records(read_log(self.home))[0]
        self.assertIn('(record="Some Note"|uuid=UUID-1)', head)
        self.assertNotIn("uuid=", continuations(read_log(self.home))[0])

    def test_single_line_message_is_unchanged(self):
        write_record(self.home, "comp", "INFO", "nothing special")
        lines = read_log(self.home)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("[comp] nothing special"))

    def test_oversized_record_is_truncated_within_pipe_buf(self):
        write_record(self.home, "comp", "ERROR", "head\n" + ("x" * 9000))
        lines = read_log(self.home)
        self.assertEqual(lines[-1], "\t[truncated]")
        blob = (self.home / LOG_REL).read_bytes()
        self.assertLess(len(blob), PIPE_BUF)
        self.assertLessEqual(len(lines[0]) + len(lines[1]), MAX_RECORD_CHARS + 1)


class PythonWriterParityTest(unittest.TestCase):
    """pipeline_log.py writes the same log; a traceback must fold identically."""

    def run_logger(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / LOG_REL.parent).mkdir(parents=True)
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(BIN)!r})\n"
            "from pipeline_log import setup\n"
            "log = setup('py-test', manual=False, echo_stdout=False)\n"
            + body
        )
        subprocess.run(["/usr/bin/python3", "-c", code],
                       env={**os.environ, "HOME": str(home)},
                       capture_output=True, text=True, check=True)
        return read_log(home)

    def test_traceback_folds_into_one_record(self):
        lines = self.run_logger(
            "try:\n"
            "    raise ValueError('boom')\n"
            "except ValueError:\n"
            "    log.exception('import failed')\n"
        )
        self.assertEqual(len(records(lines)), 1)
        self.assertTrue(records(lines)[0].endswith("[py-test] import failed"))
        self.assertTrue(any("Traceback" in c for c in continuations(lines)))
        self.assertTrue(all(c.startswith("\t") for c in continuations(lines)))

    def test_a_folded_traceback_raises_one_notification(self):
        lines = self.run_logger(
            "try:\n"
            "    raise RuntimeError('ERROR: inner detail')\n"
            "except RuntimeError:\n"
            "    log.exception('ingest failed')\n"
        )
        self.assertEqual(len(scan("\n".join(lines) + "\n")), 1)

    def test_oversized_message_is_truncated(self):
        lines = self.run_logger("log.error('y' * 9000)\n")
        self.assertEqual(lines[-1], "\t[truncated]")
        self.assertLessEqual(len(lines[0]), MAX_RECORD_CHARS)


class WatchdogGroupingTest(unittest.TestCase):
    def test_multiline_failure_raises_one_notification(self):
        log = failure("npm-tools-update", "mise upgrade npm:@slidev/cli failed:",
                      "mise ERROR Failed to install npm:@slidev/cli@latest",
                      "  caused by: trust downgrade for cytoscape@3.34.1",
                      "mise ERROR Version: 2026.8.9 macos-arm64",
                      "mise ERROR Run with --verbose or MISE_VERBOSE=1")
        self.assertEqual(len(scan("\n".join(log) + "\n")), 1)

    def test_continuation_lines_never_notify_on_their_own(self):
        """The four-notification bug: each `mise ERROR …` line matched alone."""
        log = failure("comp", "outer failure",
                      "mise ERROR Version: 2026.8.9",
                      "mise ERROR Run with --verbose",
                      "WARNING: something else")
        notes = scan("\n".join(log) + "\n")
        self.assertEqual(len(notes), 1)
        self.assertNotIn("Version", notes[0])

    def test_cause_line_is_lifted_onto_the_notification(self):
        log = failure("comp", "upgrade failed:",
                      "  caused by: trust downgrade for cytoscape@3.34.1")
        notes = scan("\n".join(log) + "\n")
        self.assertIn("caused by: trust downgrade for cytoscape@3.34.1", notes[0])

    def test_first_cause_wins_when_several_are_present(self):
        log = failure("comp", "failed:", "caused by: first reason",
                      "caused by: second reason")
        notes = scan("\n".join(log) + "\n")
        self.assertIn("first reason", notes[0])
        self.assertNotIn("second reason", notes[0])

    def test_failure_without_a_cause_still_notifies(self):
        log = failure("comp", "plain failure", "some unremarkable detail")
        notes = scan("\n".join(log) + "\n")
        self.assertEqual(len(notes), 1)
        self.assertIn("plain failure", notes[0])

    def test_single_line_failure_still_notifies(self):
        self.assertEqual(
            len(scan("2026-08-20T03:15:08 ERROR [comp] flat failure\n")), 1)

    def test_manual_component_is_suppressed_with_its_continuations(self):
        log = (failure("comp/manual", "hand-run failure", "caused by: nothing")
               + failure("comp", "real failure", "caused by: something"))
        notes = scan("\n".join(log) + "\n")
        self.assertEqual(len(notes), 1)
        self.assertIn("real failure", notes[0])

    def test_informational_records_are_ignored(self):
        self.assertEqual(scan("2026-08-20T03:15:08 INFO [comp] all good\n"), [])

    def test_overflow_summary_counts_records_not_lines(self):
        extra = 3
        log = []
        for i in range(MAX_NOTIFY_PER_LOG + extra):
            log += failure("comp", f"distinct failure {'a' * i}",
                           "mise ERROR Version: 2026.8.9",
                           "mise ERROR Run with --verbose")
        notes = scan("\n".join(log) + "\n")
        self.assertEqual(len(notes), MAX_NOTIFY_PER_LOG + 1)
        self.assertIn(f"{extra} further failure(s)", notes[-1])

    def test_orphan_continuation_before_any_record_is_ignored(self):
        log = ["\tcaused by: tail of a record scanned last time",
               "2026-08-20T03:15:08 ERROR [comp] fresh failure"]
        notes = scan("\n".join(log) + "\n")
        self.assertEqual(len(notes), 1)
        self.assertNotIn("tail of a record", notes[0])


class SanitizerTest(unittest.TestCase):
    """mise redraws progress in place; a burst must not crowd out the error."""

    def burst(self, frames=300):
        return "".join(f"mise npm:x [1/3] resolving {i}/712 pkgs\r"
                       for i in range(frames))

    def test_redraw_burst_collapses_to_the_final_frame(self):
        self.assertEqual(sanitize(self.burst() + "done\n"), ["done"])

    def test_error_survives_a_long_redraw_burst(self):
        raw = (self.burst() + "mise ERROR Failed to install\n"
               "  caused by: trust downgrade for cytoscape@3.34.1\n")
        out = sanitize(raw)
        self.assertEqual(out[0], "mise ERROR Failed to install")
        self.assertIn("caused by: trust downgrade for cytoscape@3.34.1", out[1])

    def test_blank_and_trailing_whitespace_are_stripped(self):
        self.assertEqual(sanitize("a   \n\n   \nb\t\n"), ["a", "b"])

    def test_line_budget_marks_truncation(self):
        out = sanitize("\n".join(f"line {i}" for i in range(MAX_ERR_LINES + 5)))
        self.assertEqual(len(out), MAX_ERR_LINES + 1)
        self.assertEqual(out[-1], "[output truncated]")

    def test_output_within_budget_is_unmarked(self):
        out = sanitize("\n".join(f"line {i}" for i in range(MAX_ERR_LINES)))
        self.assertEqual(len(out), MAX_ERR_LINES)
        self.assertNotIn("[output truncated]", out)


if __name__ == "__main__":
    unittest.main()
