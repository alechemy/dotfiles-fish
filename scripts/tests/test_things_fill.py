#!/usr/bin/env python3
"""Things argument and identity guards without database or application access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "stow/agents/.agents/skills/things/things_fill.py"
spec = importlib.util.spec_from_file_location("things_fill", SOURCE)
things = importlib.util.module_from_spec(spec)
spec.loader.exec_module(things)


class ThingsFillTests(unittest.TestCase):
    def test_unknown_options_rejected_before_any_state_access(self):
        for args in (["missing.json", "--dryrun"], ["missing.json", "--dry"],
                     ["missing.json", "--other"], ["one.json", "two.json"]):
            with self.subTest(args=args), patch("builtins.open") as opened, \
                    patch.object(things, "find_db") as db, \
                    patch.object(things, "auth_token") as token, \
                    patch.object(things, "fill") as fill:
                with self.assertRaises(SystemExit) as error:
                    things.main(args)
                self.assertEqual(error.exception.code, 2)
                for mock in (opened, db, token, fill):
                    mock.assert_not_called()

    def test_duplicate_titles_rejected_before_side_effects(self):
        for heading in ("First", "Second"):
            todos = [{"title": "Same", "heading": "First"},
                     {"title": "Same", "heading": heading}]
            with self.subTest(heading=heading), \
                    patch.object(things, "resolve_project") as project, \
                    patch.object(things, "ensure_running") as running:
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    things.fill("project", todos)
                project.assert_not_called()
                running.assert_not_called()
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "spec.json"
                source.write_text(json.dumps({"project": "project", "todos": todos}))
                with patch.object(things, "auth_token") as token, \
                        patch.object(things, "fill") as fill:
                    with self.assertRaises(SystemExit) as error:
                        things.main([str(source)])
                    self.assertEqual(error.exception.code, 2)
                    token.assert_not_called()
                    fill.assert_not_called()

    def test_dry_run_reads_state_without_auth_or_app_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "spec.json"
            source.write_text(json.dumps({"project": "project", "todos": [{"title": "New"}]}))
            with patch.object(things, "resolve_project", return_value="project"), \
                    patch.object(things, "existing_titles", return_value=set()), \
                    patch.object(things, "auth_token") as token, \
                    patch.object(things, "ensure_running") as running, \
                    patch.object(things, "_open") as opened:
                things.main([str(source), "--dry-run"])
                for mock in (token, running, opened):
                    mock.assert_not_called()

    def test_unique_titles_retain_write_and_idempotency_contract(self):
        with patch.object(things, "resolve_project", return_value="project"), \
                patch.object(things, "existing_titles", return_value={"Present"}), \
                patch.object(things, "heading_ids", return_value={}), \
                patch.object(things, "ensure_running"), \
                patch.object(things, "add_todo") as add:
            things.fill("project", [{"title": "Present"}])
            add.assert_not_called()


if __name__ == "__main__":
    unittest.main()
