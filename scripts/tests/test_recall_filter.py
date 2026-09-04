#!/usr/bin/env python3
"""Synthetic normalized JSON tests for the shared recall filter."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "stow/agents/.agents/skills/recall/recall-filter.py"
WORKSPACE = "/tmp/fictional-widget"
DATE = "2026-07-24T18:00:00+00:00"
WINDOW = ["--since", "2026-07-24T00:00:00Z", "--until", "2026-07-25T00:00:00Z"]
FAKE = "sk-proj-" + "fictionalONLY" * 8


def session(**changes):
    result = dict(cli="pi", id="fictional-001", cwd=WORKSPACE,
                  last_activity=DATE, name=FAKE, label=FAKE,
                  path="/private/fictional-transcript.jsonl", extra=FAKE)
    result.update(changes)
    return result


def turn(index=0, **changes):
    result = dict(index=index, timestamp=DATE, prompt="Review widget launch.",
                  blocks=["Widget launch now initializes only the requested widget."],
                  provider="fictional-provider", model="fictional-model", extra=FAKE)
    result.update(changes)
    return result


class RecallFilterTests(unittest.TestCase):
    def run_filter(self, value, operation="transcript", arguments=(), window=WINDOW, raw=False):
        args = [sys.executable, str(HELPER), operation, "--workspace", WORKSPACE, *window]
        if operation == "transcript":
            args += ["--cli", "pi", "--session", "fictional-001"]
        return subprocess.run(args + list(arguments), input=value if raw else json.dumps(value),
                              text=True, capture_output=True, timeout=10)

    def output(self, value, **kwargs):
        result = self.run_filter(value, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn(FAKE, result.stdout)
        return json.loads(result.stdout)

    def transcript(self, *turns, **changes):
        return dict(session=session(**changes), turns=list(turns))

    def test_index_projects_metadata_and_exact_workspace(self):
        value = [session(), session(id="other", cwd=WORKSPACE + "-other"),
                 session(id="child", cwd=WORKSPACE + "/child"), session(id="unknown", cwd=None)]
        out = self.output(value, operation="session-index")
        self.assertEqual(out["matched"], 1)
        self.assertEqual(set(out["sessions"][0]), {"cli", "id", "last_activity"})
        self.assertNotIn("private", json.dumps(out))

    def test_index_dates_unknown_and_all_history(self):
        value = [session(), session(id="old", last_activity="2025-01-01T00:00:00Z"),
                 session(id="future", last_activity="2026-07-25T00:00:00Z"),
                 session(id="unknown", last_activity=None), session(id="bad", last_activity="oops")]
        out = self.output(value, operation="session-index")
        self.assertEqual(out["matched"], 1)
        self.assertEqual(out["unknown_dates"], 2)
        out = self.output(value, operation="session-index", window=["--all-history"])
        self.assertEqual(out["matched"], 5)
        self.assertEqual(out["unknown_dates"], 2)

    def test_extreme_date_is_counted_as_unknown(self):
        out = self.output([session(last_activity="0001-01-01T00:00:00+02:00")], operation="session-index")
        self.assertEqual(out["unknown_dates"], 1)
        self.assertEqual(out["matched"], 0)

    def test_index_pagination_and_cli_selection(self):
        value = [session(id=f"fictional-{i:03}", cli=cli) for i, cli in enumerate(["pi", "claude", "copilot"])]
        out = self.output(value, operation="session-index", arguments=["--limit", "2"])
        self.assertTrue(out["truncated"])
        self.assertEqual(out["next_offset"], 2)
        page = self.output(value, operation="session-index", arguments=["--offset", "2", "--limit", "2"])
        self.assertEqual(len(page["sessions"]), 1)
        self.assertIsNone(page["next_offset"])
        out = self.output(value, operation="session-index", arguments=["--cli", "copilot"])
        self.assertEqual(out["matched"], 1)

    def test_transcript_default_is_metadata_only_even_when_matching(self):
        out = self.output(self.transcript(turn()), arguments=["--topic", "widget"])
        self.assertEqual(out["matched"], 1)
        self.assertEqual(set(out["turns"][0]), {"index", "timestamp"})
        for forbidden in ("prompt", "blocks", "name", "label", "path", "provider", "model", "initializes"):
            self.assertNotIn(forbidden, json.dumps(out))

    def test_exact_session_workspace_and_cli_are_required(self):
        for changes in ({"id": "fictional-001-long"}, {"cwd": WORKSPACE + "/child"}, {"cli": "claude"}):
            with self.subTest(changes=changes):
                result = self.run_filter(self.transcript(turn(), **changes))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(FAKE, result.stderr)

    def test_turn_dates_not_session_activity_control_content(self):
        value = self.transcript(turn(), turn(1, timestamp=None),
                                turn(2, timestamp="2026-07-25T00:00:00Z"),
                                turn(3, timestamp="2026-07-24T01:00:00+02:00"),
                                last_activity="2026-08-01T00:00:00Z")
        out = self.output(value)
        self.assertEqual([t["index"] for t in out["turns"]], [0])
        self.assertEqual(out["unknown_dates"], 1)
        out = self.output(value, window=["--all-history"])
        self.assertEqual(out["matched"], 4)

    def test_content_requires_opt_in_and_start_turn(self):
        result = self.run_filter(self.transcript(turn()), arguments=["--include-content"])
        self.assertNotEqual(result.returncode, 0)
        out = self.output(self.transcript(turn()), arguments=["--start-turn", "0"])
        self.assertNotIn("text", out["turns"][0])
        out = self.output(self.transcript(turn()), arguments=["--include-content", "--start-turn", "0"])
        self.assertIn("initializes", out["turns"][0]["text"])

    def test_topic_excerpts_exclude_unrelated_lines_and_dumps(self):
        value = self.transcript(turn(blocks=["Widget decision: use lazy initialization.\nUnrelated private prose.\n"
                                            "```python\nwidget_raw_code()\n```\n"
                                            '{"widget_config": "fictional-dump"}\n'
                                            "    widget_indented_dump\n"
                                            "tool result: widget raw tool dump\n"
                                            "Widget unstructured dump continuation\n\n"
                                            "Widget verification remains open."]))
        out = self.output(value, arguments=["--include-content", "--start-turn", "0", "--topic", "widget"])
        text = out["turns"][0]["text"]
        self.assertIn("lazy initialization", text)
        self.assertIn("verification remains open", text)
        for forbidden in ("Unrelated", "raw_code", "fictional-dump", "indented_dump", "raw tool", "dump continuation"):
            self.assertNotIn(forbidden, text)

    def test_fences_require_matching_character_length_and_empty_suffix(self):
        for opening, interior, closing in (
            ("````", "```", "````"),
            ("~~~~", "~~~", "~~~~~"),
            ("```", "```python", "```"),
            ("~~~", "~~~ trailing text", "~~~"),
            ("```", "~~~", "````"),
        ):
            with self.subTest(opening=opening, interior=interior):
                block = "\n".join([opening, interior, "Widget opaque payload", interior,
                                   closing, "Widget verification remains open."])
                out = self.output(self.transcript(turn(prompt="", blocks=[block])),
                                  arguments=["--include-content", "--start-turn", "0", "--topic", "widget"])
                text = out["turns"][0]["text"]
                self.assertNotIn("opaque", text)
                self.assertIn("verification remains open", text)

    def test_multiline_dump_continuations_are_not_prose(self):
        value = self.transcript(turn(prompt="", blocks=[
            'Widget goal remains lazy initialization.\n\n{\n"widget":\n"Widget opaque JSON value"\n}\n\n'
            'Widget verification remains open.\n\nsettings:\nwidget: |\nWidget opaque config value\n\n'
            'Tool output:\nWidget opaque tool value\n']))
        out = self.output(value, arguments=["--include-content", "--start-turn", "0", "--topic", "widget"])
        text = out["turns"][0]["text"]
        self.assertIn("lazy initialization", text)
        self.assertIn("verification remains open", text)
        self.assertNotIn("opaque", text)

    def test_secrets_are_removed_before_truncation(self):
        secrets = [FAKE, "ghp_" + "a" * 36, "github_pat_" + "b" * 82,
                   "xoxb-123456789-123456789-fictional", "AKIA" + "A" * 16,
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaWN0aW9uYWwifQ.signature"]
        blocks = ["Widget " + value for value in secrets]
        blocks += ["Widget password: fictional-password", "Authorization: Bearer fictional-bearer",
                   "Widget API_KEY=" + "x" * 3000, "Widget token is fictional-token",
                   "-----BEGIN PRIVATE KEY-----\nfictional-private-material\n-----END PRIVATE KEY-----"]
        out = self.output(self.transcript(turn(prompt="", blocks=blocks)),
                          arguments=["--include-content", "--start-turn", "0", "--max-chars", "80"])
        serialized = json.dumps(out)
        for secret in secrets + ["fictional-password", "fictional-bearer", "fictional-token", "fictional-private-material", "xxx"]:
            self.assertNotIn(secret, serialized)

    def test_turn_and_text_bounds_are_visible(self):
        value = self.transcript(*(turn(i, blocks=["Widget " + "long prose " * 50]) for i in range(8)))
        out = self.output(value, arguments=["--include-content", "--start-turn", "2", "--max-turns", "2", "--max-chars", "70"])
        self.assertEqual([t["index"] for t in out["turns"]], [2, 3])
        self.assertEqual(out["next_start_turn"], 4)
        self.assertTrue(out["truncated"])
        self.assertTrue(all(t["text_truncated"] and len(t["text"]) <= 70 for t in out["turns"]))
        for args in (["--max-turns", "6"], ["--max-chars", "2001"], ["--start-turn", "-1"]):
            self.assertNotEqual(self.run_filter(value, arguments=args).returncode, 0)

    def test_invalid_input_is_fail_closed(self):
        for value in ("", "not JSON " + FAKE, "null", "[]", "42", '"text"', "{} {}", '{"session":null,"turns":[]}',
                      json.dumps(self.transcript(None)), json.dumps(self.transcript(turn(blocks=[{}])))):
            with self.subTest(value=value[:10]):
                result = self.run_filter(value, raw=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn(FAKE, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
        for value in ({}, [None], [session(id=FAKE)]):
            self.assertNotEqual(self.run_filter(value, operation="session-index").returncode, 0)

    def test_unknown_fields_cannot_leak(self):
        value = self.transcript(turn())
        value["unknown"] = {"text": FAKE}
        out = self.output(value, arguments=["--include-content", "--start-turn", "0"])
        self.assertNotIn("unknown", out)

    def test_invalid_scope_is_rejected_without_echo(self):
        for window in ([], ["--since", "bad"], ["--since", DATE, "--until", DATE],
                       ["--since", "2026-07-24", "--until", DATE], ["--all-history", "--until", DATE]):
            self.assertNotEqual(self.run_filter(self.transcript(turn()), window=window).returncode, 0)
        result = subprocess.run([sys.executable, str(HELPER), "session-index", "--all-history"],
                                input="[]", text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
