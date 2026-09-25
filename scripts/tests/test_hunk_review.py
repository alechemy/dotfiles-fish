#!/usr/bin/env python3
"""Exercise review snapshots and viewer routing without touching live sessions."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "stow/agents/.agents/skills/code-review/hunk_review.py"
spec = importlib.util.spec_from_file_location("hunk_review", HELPER)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.environment = patch.dict(os.environ, {"HOME": str(self.home), "PI_SESSION_ID": "pi-fixture"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
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
        result = review.prepare(args)
        directory = review.review_directory(result["review"])
        return directory, review.load_state(directory)


class SnapshotTests(Fixture):
    def test_repository_path_with_trailing_space_is_preserved(self):
        renamed = self.repo.with_name("repo with trailing space ")
        self.repo.rename(renamed)
        self.repo = renamed
        self.file("sample file.txt", "changed\n")
        directory, state = self.prepare()
        self.assertEqual(state["repo"], str(self.repo.resolve()))
        review.fresh(directory, state)

    def test_staged_and_unstaged_remain_distinct(self):
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "working\n")
        directory, state = self.prepare("staged")
        self.assertIn(b"+staged", (directory / "review.patch").read_bytes())
        self.assertNotIn(b"working", (directory / "review.patch").read_bytes())
        review.fresh(directory, state)
        directory, state = self.prepare("unstaged")
        self.assertIn(b"-staged", (directory / "review.patch").read_bytes())
        self.assertIn(b"+working", (directory / "review.patch").read_bytes())

    def test_committed_review_ignores_dirty_tree_and_later_head(self):
        self.file("sample file.txt", "committed\n")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        directory, state = self.prepare("endpoints", base=self.base, head=head)
        self.file("sample file.txt", "later commit\n")
        self.commit()
        self.file("sample file.txt", "dirty\n")
        review.fresh(directory, state)
        self.assertNotIn(b"dirty", (directory / "review.patch").read_bytes())

    def test_mutable_review_detects_changed_content_with_same_status(self):
        self.file("sample file.txt", "first\n")
        directory, state = self.prepare()
        status = self.git("status", "--short")
        self.file("sample file.txt", "second\n")
        self.assertEqual(self.git("status", "--short"), status)
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            review.fresh(directory, state)

    def test_mutable_review_detects_changed_head(self):
        self.file("sample file.txt", "first\n")
        directory, state = self.prepare()
        self.commit()
        with self.assertRaisesRegex(review.ReviewError, "HEAD changed"):
            review.fresh(directory, state)

    def test_untracked_files_are_explicit_and_fingerprinted(self):
        self.file("new file.txt", "new\n")
        with self.assertRaisesRegex(review.ReviewError, "empty"):
            self.prepare()
        directory, state = self.prepare(include_untracked=True)
        self.assertIn(b"+new", (directory / "review.patch").read_bytes())
        self.file("new file.txt", "changed\n")
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            review.fresh(directory, state)

    def test_worktree_launch_pins_head_and_unstaged_launch_does_not(self):
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "working\n")
        directory, state = self.prepare()
        self.assertEqual(review.viewer_command(directory, state)[:2], ["diff", self.base])
        directory, state = self.prepare("unstaged")
        self.assertEqual(review.viewer_command(directory, state), ["diff", "--agent-notes", "--exclude-untracked", "--"])

    def test_path_selection_is_literal(self):
        self.file("[literal].txt", "selected\n")
        self.file("l.txt", "excluded\n")
        directory, state = self.prepare(include_untracked=True, path=["[literal].txt"])
        self.assertIn(b"+selected", (directory / "review.patch").read_bytes())
        self.assertNotIn(b"excluded", (directory / "review.patch").read_bytes())
        review.fresh(directory, state)

    def test_root_commit_is_complete_and_rejects_nonroot(self):
        directory, _ = self.prepare("root", head=self.base)
        self.assertIn(b"+original", (directory / "review.patch").read_bytes())
        self.file("sample file.txt", "second\n")
        self.commit()
        with self.assertRaisesRegex(review.ReviewError, "root commit"):
            self.prepare("root", head="HEAD")

    def test_net_worktree_diff_does_not_show_intermediate_index(self):
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "original\n")
        with self.assertRaisesRegex(review.ReviewError, "empty"):
            self.prepare()

    def test_unborn_worktree_uses_working_files_not_index(self):
        self.git("checkout", "--orphan", "unborn")
        self.file("sample file.txt", "staged\n")
        self.git("add", "--all")
        self.file("sample file.txt", "working\n")
        directory, _ = self.prepare()
        patch_text = (directory / "review.patch").read_text()
        self.assertIn("+working", patch_text)
        self.assertNotIn("staged", patch_text)

    def test_state_and_patch_are_private_and_owner_bound(self):
        self.file("sample file.txt", "changed\n")
        directory, state = self.prepare()
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in ("review.json", "review.patch"):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        with patch.dict(os.environ, {"PI_SESSION_ID": "different-session"}):
            with self.assertRaisesRegex(review.ReviewError, "another Pi session"):
                review.load_state(directory)
        (directory / "review.patch").write_text("changed patch")
        with self.assertRaisesRegex(review.ReviewError, "saved patch changed"):
            review.fresh(directory, state)

    def test_rename_deletion_binary_and_symlink_are_preserved(self):
        self.file("deleted.txt", "delete\n")
        self.commit()
        self.git("mv", "sample file.txt", "renamed.txt")
        (self.repo / "deleted.txt").unlink()
        (self.repo / "binary.dat").write_bytes(b"\0\1\2")
        (self.repo / "link").symlink_to("missing-target")
        directory, state = self.prepare(include_untracked=True)
        contents = (directory / "review.patch").read_bytes()
        for expected in (b"rename from sample file.txt", b"-delete", b"GIT binary patch", b"missing-target"):
            self.assertIn(expected, contents)
        review.fresh(directory, state)

    def test_configured_textconv_selects_snapshot_without_executing_driver(self):
        marker = self.home / "driver-ran"
        self.git("config", "diff.fixture.textconv", f"touch {marker}")
        self.file(".gitattributes", "*.txt diff=fixture\n")
        self.file("sample file.txt", "changed\n")
        directory, state = self.prepare()
        self.assertTrue(state["patch_view"])
        self.assertEqual(review.viewer_command(directory, state)[0], "patch")
        self.assertFalse(marker.exists())

    def test_since_includes_worktree_against_explicit_base(self):
        self.file("sample file.txt", "committed\n")
        self.commit()
        self.file("sample file.txt", "working\n")
        directory, _ = self.prepare("since", base=self.base)
        contents = (directory / "review.patch").read_bytes()
        self.assertIn(b"-original", contents)
        self.assertIn(b"+working", contents)
        self.assertNotIn(b"committed", contents)


class ViewerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.file("sample file.txt", "changed\nsecond\n")
        self.directory, self.state = self.prepare()
        self.state["launch"] = {"workspace": "workspace", "surface": "viewer", "caller": "pi", "status": "ready"}
        self.state["session"] = {"id": "session", "generation": "generation:fixture:1"}
        self.session = {"sessionId": "session", "pid": 4123, "cwd": str(self.repo), "repoRoot": str(self.repo),
                        "inputKind": "vcs", "sourceLabel": str(self.repo),
                        "snapshot": {"state": {"reviewPublication": {"generation": "generation:fixture:1", "stateRevision": 0}}}}
        self.view = {"files": [{"path": "sample file.txt", "patch": (self.directory / "review.patch").read_text()}]}
        self.comments = []
        self.calls = []
        self.apply_error = False
        self.after_apply = None
        review.save(self.directory / "process.json", review.encoded({"pid": 4123, "started": "synthetic-start", "workspace": "workspace", "surface": "viewer"}))
        review.save_state(self.directory, self.state)
        for replacement in (patch.object(review, "hunk", side_effect=self.hunk),
                            patch.object(review, "surface_exists", return_value=True),
                            patch.object(review, "process_started", return_value="synthetic-start"),
                            patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "workspace", "CMUX_SURFACE_ID": "pi"})):
            replacement.start()
            self.addCleanup(replacement.stop)

    def hunk(self, *args, data=None):
        self.calls.append((args, data))
        if args[0] == "get":
            return {"session": json.loads(json.dumps(self.session))}
        if args[0] == "list":
            return {"sessions": [self.session]}
        if args[0] == "review":
            return {"review": self.view}
        if args[:2] == ("comment", "list"):
            return {"comments": self.comments.copy()}
        if args[:2] == ("comment", "apply"):
            applied = []
            for payload in data["comments"]:
                side = "old" if "oldLine" in payload else "new"
                comment = {"commentId": "note-" + str(len(self.comments)), "side": side, "line": payload[side + "Line"],
                           **{key: payload[key] for key in ("filePath", "summary", "rationale", "author")}}
                self.comments.append(comment)
                applied.append(comment)
            if self.after_apply:
                self.after_apply()
            if self.apply_error:
                raise review.ReviewError("Lost response")
            return {"result": {"applied": applied}}
        if args[0] == "navigate":
            return {"result": {"filePath": "sample file.txt"}}
        self.fail(f"Unexpected Hunk operation: {args}")

    def finding(self, **changes):
        result = {"id": "R1", "axis": "Correctness", "priority": "P1", "scope": "line",
                  "path": "sample file.txt", "side": "new", "start_line": 1, "end_line": 2,
                  "title": "Handle the fixture.", "body": "This fixture demonstrates the failure."}
        result.update(changes)
        return result

    def send(self, *findings):
        return review.import_findings(self.directory, self.state, {"findings": list(findings)})

    def test_import_is_idempotent_and_navigates_by_saved_id(self):
        finding = self.finding()
        self.assertEqual(self.send(finding)["inline"], ["R1"])
        self.assertEqual(self.send(finding)["inline"], ["R1"])
        self.assertEqual(len(self.comments), 1)
        result = review.show_finding(self.directory, self.state, "R1")
        self.assertEqual(result["comment"], "note-0")
        self.assertIn((("navigate", "session", "--comment", "note-0"), None), self.calls)
        self.assertIn("Reviewed range: new lines 1-2", self.comments[0]["rationale"])

    def test_old_side_is_not_silently_remapped_to_new(self):
        self.send(self.finding(side="old", end_line=1))
        self.assertEqual(self.comments[0]["side"], "old")
        self.assertEqual(self.comments[0]["line"], 1)

    def test_no_anchor_is_fabricated_for_unrenderable_or_general_findings(self):
        file_note = {key: value for key, value in self.finding(id="R2", scope="file").items()
                     if key not in ("side", "start_line", "end_line")}
        general = {key: value for key, value in self.finding(id="R3", scope="review").items()
                   if key not in ("path", "side", "start_line", "end_line")}
        result = self.send(self.finding(start_line=99, end_line=99), file_note, general)
        self.assertEqual(result["report_only"], ["R1", "R2", "R3"])
        self.assertEqual(self.comments, [])
        self.assertEqual(len(review.load_state(self.directory)["findings"]), 3)

    def test_invalid_range_or_batch_fails_before_mutation(self):
        with self.assertRaises(review.ReviewError):
            self.send(self.finding(), self.finding(id="R2", start_line=-1))
        self.assertEqual(self.comments, [])
        self.assertEqual(self.state["findings"], {})
        with self.assertRaises(review.ReviewError):
            self.send(self.finding(), self.finding())

    def test_lost_apply_receipt_reconciles_without_duplicate_import(self):
        self.apply_error = True
        with self.assertRaisesRegex(review.ReviewError, "Lost response"):
            self.send(self.finding())
        self.state = review.load_state(self.directory)
        self.apply_error = False
        self.send(self.finding())
        self.assertEqual(len(self.comments), 1)
        self.assertEqual(self.state["imports"], {"R1": "note-0"})

    def test_unacknowledged_import_is_not_retried_before_notes_appear(self):
        self.state["pending"] = ["R1"]
        self.state["findings"] = {"R1": self.finding()}
        with self.assertRaisesRegex(review.ReviewError, "may still complete"):
            self.send(self.finding())
        self.assertFalse(any(args[:2] == ("comment", "apply") for args, _ in self.calls))

    def test_reopen_refuses_a_live_pane_or_incomplete_launch(self):
        with self.assertRaisesRegex(review.ReviewError, "Close the old review pane"):
            review.open_viewer(self.directory, self.state, reopen=True)
        self.state.pop("session")
        with self.assertRaisesRegex(review.ReviewError, "incomplete launch"):
            review.open_viewer(self.directory, self.state, reopen=True)

    def test_duplicate_or_edited_comments_are_not_overwritten(self):
        self.send(self.finding())
        self.comments.append(self.comments[0].copy())
        with self.assertRaisesRegex(review.ReviewError, "Duplicate"):
            self.send(self.finding())
        self.comments.pop()
        self.comments[0]["summary"] = "Human edit"
        with self.assertRaisesRegex(review.ReviewError, "removed or edited"):
            self.send(self.finding())
        self.assertEqual(self.comments[0]["summary"], "Human edit")

    def test_changed_finding_id_does_not_replace_existing_evidence(self):
        self.send(self.finding())
        with self.assertRaisesRegex(review.ReviewError, "existing finding ID changed"):
            self.send(self.finding(title="Changed title"))
        self.assertEqual(len(self.comments), 1)

    def test_stale_content_and_changed_generation_prevent_import(self):
        self.session["snapshot"]["state"]["reviewPublication"]["generation"] = "generation:fixture:2"
        with self.assertRaisesRegex(review.ReviewError, "reloaded"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])
        self.session["snapshot"]["state"]["reviewPublication"]["generation"] = "generation:fixture:1"
        self.file("sample file.txt", "newer content\n")
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_binary_file_without_exportable_patch_stays_unanchorable(self):
        (self.repo / "binary.dat").write_bytes(b"\x00\x01")
        self.state["comparison"]["include_untracked"] = True
        snapshot = review.snapshot(self.state["repo"], self.state["comparison"])
        review.save(self.directory / "review.patch", snapshot)
        self.state["patch_sha256"] = review.digest(snapshot)
        self.view["files"].append({"path": "binary.dat", "hunks": []})
        result = self.send(self.finding(), self.finding(id="R2", path="binary.dat"))
        self.assertEqual(result["inline"], ["R1"])
        self.assertEqual(result["report_only"], ["R2"])

    def test_missing_text_patch_prevents_import(self):
        self.view["files"][0]["patch"] = ""
        with self.assertRaisesRegex(review.ReviewError, "complete text patch"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_different_displayed_patch_prevents_import(self):
        self.view["files"][0]["patch"] = self.view["files"][0]["patch"].replace("+changed", "+wrong")
        with self.assertRaisesRegex(review.ReviewError, "displayed diff differs"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_raced_source_change_never_reports_success(self):
        self.after_apply = lambda: self.file("sample file.txt", "raced\n")
        with self.assertRaisesRegex(review.ReviewError, "content changed"):
            self.send(self.finding())
        self.assertEqual(review.load_state(self.directory)["imports"], {"R1": "note-0"})

    def test_reused_open_never_creates_another_pane(self):
        with patch.object(review, "cmux") as client:
            result = review.open_viewer(self.directory, self.state)
        self.assertTrue(result["reused"])
        client.assert_not_called()

    def test_new_open_captures_exact_process_and_surface(self):
        self.state.pop("launch")
        self.state.pop("session")
        response = {"workspace_id": "workspace", "surface_id": "viewer", "type": "terminal"}
        with patch.object(review, "cmux", return_value=response) as client, patch.object(review.shutil, "which", return_value="/hunk"):
            result = review.open_viewer(self.directory, self.state)
        self.assertEqual(result["session"], "session")
        call = client.call_args.args
        self.assertEqual(call[:6], ("new-split", "right", "--workspace", "workspace", "--surface", "pi"))
        self.assertEqual(call[-2:], ("--focus", "false"))

    def test_uncertain_launch_is_not_retried(self):
        self.state.pop("session")
        self.state["launch"].pop("surface")
        with patch.object(review, "cmux") as client:
            with self.assertRaisesRegex(review.ReviewError, "incomplete"):
                review.open_viewer(self.directory, self.state)
        client.assert_not_called()

    def test_reused_process_id_prevents_import(self):
        with patch.object(review, "process_started", return_value="different-start"):
            with self.assertRaisesRegex(review.ReviewError, "process no longer matches"):
                self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_pid_and_terminal_mismatch_prevent_import(self):
        self.session["pid"] = 9999
        with self.assertRaisesRegex(review.ReviewError, "process no longer matches"):
            self.send(self.finding())
        self.assertEqual(self.comments, [])

    def test_patch_sessions_use_exact_id_without_repo_selector(self):
        self.session["inputKind"] = "patch"
        self.session.pop("repoRoot")
        self.session["sourceLabel"] = str(self.directory / "review.patch")
        with patch.object(review, "viewer_command", return_value=["patch", self.session["sourceLabel"]]):
            self.send(self.finding())
        self.assertEqual(len(self.comments), 1)
        self.assertFalse(any("--repo" in args for args, _ in self.calls))


class PatchParsingTests(unittest.TestCase):
    def test_cmux_json_flags_follow_the_command(self):
        with patch.object(review, "cli_json", return_value={}) as client:
            review.cmux("workspace", "create", "--name", "Fixture")
        self.assertEqual(client.call_args.args[0], ["cmux", "workspace", "create", "--name", "Fixture", "--json", "--id-format", "uuids"])

    def test_context_additions_deletions_and_no_newline_marker(self):
        contents = ('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -2,2 +2,2 @@\n context\n-old\n+new\n'
                    '\\ No newline at end of file\n')
        files = review.patch_files(contents)
        self.assertEqual(files["x"]["lines"], {"old": {2: "context", 3: "old"}, "new": {2: "context", 3: "new"}})

    def test_git_quoted_paths_and_deleted_file(self):
        contents = ('diff --git "a/tab\\tfile" "b/tab\\tfile"\n--- "a/tab\\tfile"\n+++ /dev/null\n'
                    '@@ -1 +0,0 @@\n-old\n')
        files = review.patch_files(contents)
        self.assertIn("tab\tfile", files)
        self.assertEqual(files["tab\tfile"]["lines"]["new"], {})

    def test_crlf_source_matches_hunks_lf_export(self):
        source = 'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\r\n+new\r\n'
        self.assertEqual(review.patch_files(source), review.patch_files(source.replace("\r\n", "\n")))

    def test_unicode_line_separators_inside_source_are_not_patch_newlines(self):
        files = review.patch_files('diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\u2028text\n+new\u2028text\n')
        self.assertEqual(files["x"]["lines"]["new"], {1: "new\u2028text"})

    def test_rename_side_aliases(self):
        files = review.patch_files('diff --git a/old name b/new name\nrename from old name\nrename to new name\n')
        self.assertEqual(files["new name"]["old_path"], "old name")


if __name__ == "__main__":
    unittest.main()
