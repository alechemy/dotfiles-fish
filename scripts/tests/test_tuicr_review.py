#!/usr/bin/env python3
"""Exercise isolated review snapshots, comment receipts, and cmux ownership."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "stow/agents/.agents/skills/code-review/tuicr_review.py"
spec = importlib.util.spec_from_file_location("tuicr_review", HELPER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        environment = patch.dict(os.environ, {"HOME": str(self.home), "PI_SESSION_ID": "pi-fixture"})
        environment.start()
        self.addCleanup(environment.stop)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.file("sample file.txt", "original\n")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=env, text=True,
                                       stderr=subprocess.PIPE)

    def file(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Fixture")

    def prepare(self, mode="worktree", **options):
        args = argparse.Namespace(repo=str(self.repo), mode=mode, base=None, head=None,
                                  path=[], include_untracked=False)
        for key, value in options.items():
            setattr(args, key, value)
        before = self.git("status", "--porcelain=v1", "--untracked-files=all")
        index = (self.repo / ".git/index").read_bytes()
        result = review.prepare(args)
        self.assertEqual(before, self.git("status", "--porcelain=v1", "--untracked-files=all"))
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())
        directory = review.review_directory(result["review"])
        state = review.load_state(directory)
        self.assertEqual(review.private_git(directory, "remote"), b"")
        self.assertEqual(list((directory / "snapshot").iterdir()), [directory / "snapshot/.git"])
        return directory, state


class SnapshotTests(Fixture):
    def test_staged_unstaged_worktree_and_since_remain_distinct(self):
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "working\n")
        for mode, old, new in (("staged", "original", "staged"), ("unstaged", "staged", "working"),
                               ("worktree", "original", "working"), ("since", "original", "working")):
            with self.subTest(mode=mode):
                directory, state = self.prepare(mode, **({"base": self.base} if mode == "since" else {}))
                text = (directory / "review.patch").read_text()
                self.assertIn("-" + old, text)
                self.assertIn("+" + new, text)
                review.fresh(directory, state)
                self.assertEqual(review.viewer_command(directory, state),
                                 ["--no-update-check", "--revisions", state["snapshot"]["base"] + ".." + state["snapshot"]["head"]])

    def test_committed_review_ignores_dirty_tree_and_later_head(self):
        self.file("sample file.txt", "committed\n")
        self.commit()
        directory, state = self.prepare("endpoints", base=self.base, head="HEAD")
        self.file("sample file.txt", "later\n")
        self.commit()
        self.file("sample file.txt", "dirty\n")
        review.fresh(directory, state)
        self.assertNotIn(b"dirty", (directory / "review.patch").read_bytes())

    def test_content_and_head_drift_are_detected(self):
        self.file("sample file.txt", "first\n")
        directory, state = self.prepare()
        status = self.git("status", "--short")
        self.file("sample file.txt", "second\n")
        self.assertEqual(status, self.git("status", "--short"))
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            review.fresh(directory, state)
        self.commit()
        with self.assertRaisesRegex(review.ReviewError, "HEAD changed"):
            review.fresh(directory, state)

    def test_untracked_and_literal_path_scope(self):
        self.file("[literal].txt", "selected\n")
        self.file("l.txt", "excluded\n")
        with self.assertRaisesRegex(review.ReviewError, "empty"):
            self.prepare()
        directory, state = self.prepare(include_untracked=True, path=["[literal].txt"])
        text = (directory / "review.patch").read_text()
        self.assertIn("+selected", text)
        self.assertNotIn("excluded", text)
        self.file("[literal].txt", "changed\n")
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            review.fresh(directory, state)

    def test_root_and_unborn(self):
        directory, _ = self.prepare("root", head=self.base)
        self.assertIn(b"+original", (directory / "review.patch").read_bytes())
        self.git("checkout", "--orphan", "unborn")
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "working\n")
        for mode, expected in (("worktree", b"+working"), ("staged", b"+staged"), ("unstaged", b"-staged")):
            directory, _ = self.prepare(mode)
            self.assertIn(expected, (directory / "review.patch").read_bytes())

    def test_empty_net_change_is_not_intermediate_index_change(self):
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "original\n")
        with self.assertRaisesRegex(review.ReviewError, "empty"):
            self.prepare()

    def test_rename_deletion_binary_symlink_crlf_and_unicode(self):
        self.file("deleted.txt", "delete\n")
        self.file("café.txt", "before\n")
        self.commit()
        self.git("mv", "sample file.txt", "renamed.txt")
        (self.repo / "deleted.txt").unlink()
        self.file("café.txt", "after\n")
        (self.repo / "binary.dat").write_bytes(b"\0\1\2")
        (self.repo / "link").symlink_to("missing-target")
        (self.repo / "crlf.txt").write_bytes(b"line\r\n")
        directory, state = self.prepare(include_untracked=True)
        contents = (directory / "review.patch").read_bytes()
        for expected in (b"rename from sample file.txt", b"-delete", b"GIT binary patch", b"missing-target", b"line\r\n"):
            self.assertIn(expected, contents)
        review.fresh(directory, state)
        self.assertEqual(review.private_git(directory, "show", state["snapshot"]["head"] + ":binary.dat"), b"\0\1\2")
        self.assertEqual(review.private_git(directory, "show", state["snapshot"]["head"] + ":crlf.txt"), b"line\r\n")

    def test_textconv_and_ignore_rules_do_not_change_snapshot(self):
        marker = self.home / "driver-ran"
        self.git("config", "diff.fixture.textconv", f"touch {marker}")
        self.file(".gitattributes", "*.txt diff=fixture\n")
        self.file(".tuicrignore", "*.txt\n")
        self.file("sample file.txt", "changed\n")
        directory, state = self.prepare(include_untracked=True)
        self.assertEqual(state["viewer"], "tuicr")
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "snapshot/.tuicrignore").exists())

    def test_private_owner_bound_state_and_tampered_patch(self):
        self.file("sample file.txt", "changed\n")
        directory, state = self.prepare()
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in ("review.json", "review.patch"):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {"PI_SESSION_ID": "other"}):
            with self.assertRaisesRegex(review.ReviewError, "another Pi session"):
                review.load_state(directory)
        (directory / "review.patch").write_text("changed patch")
        with self.assertRaisesRegex(review.ReviewError, "saved patch changed"):
            review.fresh(directory, state)

    def test_trailing_space_repo_and_quoted_paths(self):
        renamed = self.repo.with_name("repo with trailing space ")
        self.repo.rename(renamed)
        self.repo = renamed
        self.file('tab\tquote".txt', "changed\n")
        directory, state = self.prepare(include_untracked=True)
        self.assertEqual(state["repo"], str(renamed))
        review.fresh(directory, state)


class ViewerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.file("sample file.txt", "changed\nsecond\n")
        self.directory, self.state = self.prepare()
        self.state["tuicr_binary"] = "/synthetic/tuicr"
        self.state["launch"] = {"workspace": "workspace", "surface": "viewer", "caller": "pi", "status": "ready"}
        self.path = self.directory / "tuicr-home/reviews/sessions/fixture.json"
        self.path.parent.mkdir(parents=True)
        self.state["session"] = {"path": str(self.path), "id": "session"}
        self.record = {"id": "session", "version": "1.3", "repo_path": str(self.directory / "snapshot"),
                       "base_commit": self.state["snapshot"]["head"], "diff_source": "commit_range",
                       "commit_range": [self.state["snapshot"]["head"]],
                       "files": {"sample file.txt": {}, f"Commit Message ({self.state['snapshot']['head'][:7]})": {}},
                       "pr_session_key": None}
        self.persist_record()
        review.save(self.path.parent.parent / "active_sessions.json",
                    review.encoded({"version": "1.0", "sessions": [{"pid": 4123, "path": str(self.path)}]}))
        review.save(self.directory / "process.json", review.encoded(
            {"pid": 4123, "started": "synthetic-start", "workspace": "workspace", "surface": "viewer"}))
        review.save_state(self.directory, self.state)
        self.comments = []
        self.calls = []
        self.apply_error = False
        self.after_apply = None
        for replacement in (patch.object(review, "tuicr", side_effect=self.tuicr),
                            patch.object(review, "surface_exists", return_value=True),
                            patch.object(review, "process_started", return_value="synthetic-start"),
                            patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "workspace", "CMUX_SURFACE_ID": "pi"})):
            replacement.start()
            self.addCleanup(replacement.stop)

    def persist_record(self):
        review.save(self.path, review.encoded(self.record))

    def tuicr(self, directory, state, *args, data=None):
        self.calls.append((args, data))
        if args[0] == "list":
            return [{"kind": "local", "active": True, "path": str(self.path)}]
        if args[0] == "comments":
            return self.comments.copy()
        if args[0] == "add":
            comment = {"id": "note-" + str(len(self.comments)), "path": data.get("file"),
                       "side": data.get("side"), "start_line": data.get("start_line"),
                       "end_line": data.get("end_line"), "content": data["content"].strip(),
                       "author": data["username"], "comment_type": data["type"], "lifecycle_state": "local_draft"}
            self.comments.append(comment)
            if self.after_apply:
                self.after_apply()
            if self.apply_error:
                raise review.ReviewError("Lost response")
            return comment
        self.fail(f"Unexpected tuicr operation: {args}")

    def finding(self, **changes):
        result = {"id": "R1", "axis": "Correctness", "priority": "P1", "scope": "line",
                  "path": "sample file.txt", "side": "new", "start_line": 1, "end_line": 2,
                  "title": "Handle the fixture.", "body": "This fixture demonstrates the failure."}
        result.update(changes)
        if result["scope"] != "line":
            for key in ("side", "start_line", "end_line"):
                result.pop(key)
        if result["scope"] == "review":
            result.pop("path")
        return result

    def send(self, *findings):
        return review.import_findings(self.directory, self.state, {"findings": list(findings)})

    def test_import_is_idempotent_with_real_ranges_and_manual_navigation(self):
        finding = self.finding()
        for _ in range(2):
            self.assertEqual(self.send(finding)["inline"], ["R1"])
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.comments[0]["end_line"], 2)
        shown = review.show_finding(self.directory, self.state, "R1")
        self.assertIn(":summary", shown["navigation"])
        self.assertEqual(shown["comment"], "note-0")

    def test_vendor_trimmed_content_reconciles_with_trailing_whitespace(self):
        for finding in (self.finding(body="Evidence. \n"),
                        self.finding(id="R2", reference="Standard.\n\t")):
            self.send(finding)
            self.send(finding)
        self.assertEqual(len(self.comments), 2)
        self.assertEqual(self.state["pending"], [])

    def test_old_side_file_and_review_comments_and_missing_anchors(self):
        result = self.send(self.finding(side="old", end_line=1), self.finding(id="R2", scope="file"),
                           self.finding(id="R3", scope="review"), self.finding(id="R4", start_line=99, end_line=99))
        self.assertEqual(result["inline"], ["R1"])
        self.assertEqual(result["attached"], ["R1", "R2", "R3"])
        self.assertEqual(result["report_only"], ["R4"])
        self.assertEqual(self.comments[0]["side"], "old")

    def test_invalid_batch_does_not_write(self):
        for invalid in (self.finding(id="R2", start_line=-1), self.finding()):
            with self.assertRaises(review.ReviewError):
                self.send(self.finding(), invalid)
        self.assertEqual(self.comments, [])

    def test_lost_receipt_reconciles_without_duplicate(self):
        self.apply_error = True
        with self.assertRaisesRegex(review.ReviewError, "Lost response"):
            self.send(self.finding())
        self.state = review.load_state(self.directory)
        self.apply_error = False
        self.send(self.finding())
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.state["pending"], [])

    def test_unacknowledged_write_is_not_retried(self):
        self.state["pending"] = ["R1"]
        with self.assertRaisesRegex(review.ReviewError, "may still complete"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_duplicate_edited_and_revised_findings_are_not_overwritten(self):
        self.send(self.finding())
        with self.assertRaisesRegex(review.ReviewError, "existing finding ID changed"):
            self.send(self.finding(title="Revised"))
        self.comments.append(self.comments[0].copy())
        with self.assertRaisesRegex(review.ReviewError, "Duplicate"):
            self.send(self.finding())
        self.comments.pop()
        self.comments[0]["content"] = "Human edit"
        with self.assertRaisesRegex(review.ReviewError, "removed or edited"):
            self.send(self.finding())

    def test_source_race_retains_receipt_and_reports_failure(self):
        self.after_apply = lambda: self.file("sample file.txt", "raced\n")
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            self.send(self.finding())
        self.assertEqual(review.load_state(self.directory)["imports"], {"R1": "note-0"})

    def test_changed_session_comparison_and_inventory_fail_closed(self):
        for key, value in (("id", "other"), ("commit_range", ["other"]), ("files", {}), ("version", "2")):
            with self.subTest(key=key):
                original = self.record[key]
                self.record[key] = value
                self.persist_record()
                with self.assertRaises(review.ReviewError):
                    self.send(self.finding())
                self.record[key] = original
        self.assertEqual(self.comments, [])

    def test_reused_pid_and_wrong_process_fail_closed(self):
        with patch.object(review, "process_started", return_value="different"):
            with self.assertRaisesRegex(review.ReviewError, "process no longer matches"):
                self.send(self.finding())
        review.save(self.path.parent.parent / "active_sessions.json",
                    review.encoded({"version": "1.0", "sessions": [{"pid": 999, "path": str(self.path)}]}))
        with self.assertRaisesRegex(review.ReviewError, "launched process"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_reuse_and_uncertain_launch_never_create_another_pane(self):
        with patch.object(review, "cmux") as client:
            self.assertTrue(review.open_viewer(self.directory, self.state)["reused"])
            self.state.pop("session")
            self.state["launch"].pop("surface")
            with self.assertRaisesRegex(review.ReviewError, "incomplete"):
                review.open_viewer(self.directory, self.state)
        client.assert_not_called()

    def test_human_comments_are_read_without_clearing_or_requiring_live_pane(self):
        self.send(self.finding())
        human = {"id": "human", "author": "user", "content": "Please explain."}
        self.comments.append(human)
        with patch.object(review, "surface_exists", return_value=False):
            self.assertEqual(review.human_comments(self.directory, self.state)["comments"], [human])
        self.assertEqual(len(self.comments), 2)


class CommandTests(unittest.TestCase):
    def test_output_limit_stops_a_producer_before_it_finishes(self):
        with patch.object(review, "LIMIT", 128):
            with self.assertRaisesRegex(review.ReviewError, "32 MiB helper limit"):
                review.run([sys.executable, "-c", "import os,time; os.write(1,b'x'*129); time.sleep(5)"], timeout=0.5)

    def test_timeout_and_stdin(self):
        with self.assertRaisesRegex(review.ReviewError, "could not complete"):
            review.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
        self.assertEqual(review.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                    data=b"fixture"), b"fixture")


class PatchParsingTests(unittest.TestCase):
    def test_context_and_no_newline_marker(self):
        files = review.patch_files('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -2,2 +2,2 @@\n context\n-old\n+new\n\\ No newline at end of file\n')
        self.assertEqual(files["x"]["lines"], {"old": {2: "context", 3: "old"}, "new": {2: "context", 3: "new"}})

    def test_binary_add_delete_paths(self):
        files = review.patch_files('diff --git a/add b/add\nnew file mode 100644\nGIT binary patch\n'
                                   'diff --git a/gone b/gone\ndeleted file mode 100644\nGIT binary patch\n')
        self.assertIsNone(files["add"]["old_path"])
        self.assertIsNone(files["gone"]["new_path"])

    def test_unicode_line_separator_is_not_a_patch_newline(self):
        files = review.patch_files('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\u2028text\n+new\u2028text\n')
        self.assertEqual(files["x"]["lines"]["new"], {1: "new\u2028text"})


if __name__ == "__main__":
    unittest.main()
