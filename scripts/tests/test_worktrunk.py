#!/usr/bin/env python3
"""Worktrunk integration contracts in disposable repositories and homes."""

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "stow/agents/.agents/skills/worktrunk/wt_pi.py"
spec = importlib.util.spec_from_file_location("wt_pi", SOURCE)
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class WorktrunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.repo = self.root / "repo with spaces"
        self.home.mkdir()
        self.repo.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("GIT_", "PI_", "HERDR_", "WORKTRUNK_"))
               and key not in ("AI_AGENT", "OMP_PROFILE")}
        env.update(HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   WORKTRUNK_SYSTEM_CONFIG_PATH=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "-qb", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "hooks"))
        (self.repo / "fixture").write_text("original\n")
        self.git("add", "fixture")
        self.git("commit", "-qm", "fixture")
        self.config = self.home / ".config/worktrunk/config.toml"
        self.config.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "stow/worktrunk/_seed/.config/worktrunk/config.toml", self.config)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True, check=True, timeout=15).stdout.strip()

    def wt(self, *args, cwd=None, check=True):
        return subprocess.run(["wt", *args], cwd=cwd or self.repo, capture_output=True,
                              text=True, check=check, timeout=30)

    def create(self, branch="feature/test"):
        result = self.wt("switch", "--create", branch, "--no-cd", "--format=json")
        return Path(json.loads(result.stdout)["path"]).resolve()

    @contextmanager
    def cwd(self, path):
        previous = Path.cwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(previous)

    def own(self, target):
        _, _, gitdir = workflow.repository(target)
        workflow.write_json(gitdir / "wt-pi/task.json", {"version": 1, "path": str(target)})

    def test_sibling_allocation_leaves_existing_changes_alone(self):
        (self.repo / "fixture").write_text("local work\n")
        target = self.create()
        self.assertEqual(target, self.repo.parent / "repo with spaces.feature-test")
        self.assertEqual((target / "fixture").read_text(), "original\n")
        self.assertEqual((self.repo / "fixture").read_text(), "local work\n")
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def test_merge_refuses_dirty_or_diverged_and_keeps_tree(self):
        target = self.create()
        original = self.git("rev-parse", "main")
        (target / "fixture").write_text("changed\n")
        self.assertNotEqual(self.wt("merge", cwd=target, check=False).returncode, 0)
        self.assertEqual(self.git("rev-parse", "main"), original)
        self.git("add", "fixture", cwd=target)
        self.git("commit", "-qm", "change", cwd=target)
        feature = self.git("rev-parse", "HEAD", cwd=target)
        self.git("branch", "dependent", feature)
        self.git("config", "rebase.updateRefs", "true")
        self.git("commit", "--allow-empty", "-qm", "diverged")
        self.assertNotEqual(self.wt("merge", cwd=target, check=False).returncode, 0)
        self.assertEqual(self.git("rev-parse", "dependent"), feature)
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=target), feature)
        self.assertTrue(target.exists())

    def test_fast_forward_keeps_worktree_and_git_commit_hooks(self):
        target = self.create()
        hook = self.root / "hooks/pre-commit"
        hook.parent.mkdir()
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        (target / "fixture").write_text("changed\n")
        self.git("add", "fixture", cwd=target)
        result = self.wt("step", "commit", cwd=target, check=False)
        self.assertNotEqual(result.returncode, 0)
        hook.unlink()
        self.git("commit", "-qm", "change", cwd=target)
        self.wt("merge", cwd=target)
        self.assertTrue(target.exists())
        self.assertEqual(self.git("rev-parse", "main"), self.git("rev-parse", "HEAD", cwd=target))

    def test_approvals_fail_closed_without_running_project_commands(self):
        (self.repo / ".config").mkdir()
        (self.repo / ".config/wt.toml").write_text('[pre-start]\nfixture = "touch unexpected"\n')
        with self.assertRaises(workflow.WorkflowError):
            workflow.require_approved(self.repo)
        self.assertFalse((self.repo / "unexpected").exists())
        self.assertFalse((self.config.parent / "approvals.toml").exists())

    def test_activity_aggregates_and_prunes_without_private_text(self):
        target = self.create()
        a, b = "a" * 32, "b" * 32
        workflow.activity(target, token=a, pid=os.getpid(), status="working")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "🤖")
        workflow.activity(target, token=b, pid=os.getpid(), status="blocked")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "❗")
        workflow.activity(target, token=a, pid=os.getpid(), status="clear")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "❗")
        workflow.activity(target, token=b, pid=os.getpid(), status="idle")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "💬")
        with patch.object(workflow, "process_started", return_value=None):
            self.assertEqual(workflow.activity(target), 0)
        self.assertIsNone(workflow.marker(target, "feature/test"))

    def test_detached_shutdown_and_dead_sessions_clear_owned_markers(self):
        target = self.create()
        token = "a" * 32
        workflow.activity(target, token=token, pid=os.getpid(), status="working")
        self.git("checkout", "--detach", cwd=target)
        workflow.activity(target, token=token, pid=os.getpid(), status="clear")
        self.assertIsNone(workflow.marker(target, "feature/test"))
        self.git("checkout", "feature/test", cwd=target)
        workflow.activity(target, token=token, pid=os.getpid(), status="idle")
        self.git("checkout", "--detach", cwd=target)
        with patch.object(workflow, "process_started", return_value=None):
            self.assertEqual(workflow.activity(target), 0)
        self.assertIsNone(workflow.marker(target, "feature/test"))

    def test_detached_shutdown_preserves_other_live_session_marker(self):
        target = self.create()
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working")
        workflow.activity(target, token="b" * 32, pid=os.getpid(), status="working")
        self.git("checkout", "--detach", cwd=target)
        self.assertEqual(workflow.activity(target, token="a" * 32, pid=os.getpid(), status="clear"), 1)
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "🤖")
        self.assertEqual(workflow.activity(target, token="b" * 32, pid=os.getpid(), status="clear"), 0)
        self.assertIsNone(workflow.marker(target, "feature/test"))

    def test_concurrent_activity_writers_preserve_all_sessions(self):
        target = self.create()
        children = [subprocess.Popen([sys.executable, str(SOURCE), "_activity", f"{index:032x}",
                                      str(os.getpid()), "blocked" if index == 0 else "idle"],
                                     cwd=target, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for index in range(4)]
        try:
            for child in children:
                _, error = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, error)
            self.assertEqual(workflow.activity(target), 4)
            self.assertEqual(workflow.marker(target, "feature/test")["marker"], "❗")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

    def test_sleep_does_not_expire_a_live_session_but_pid_reuse_does(self):
        target = self.create()
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working")
        with patch.object(workflow.time, "time", return_value=time.time() + 3600):
            self.assertEqual(workflow.activity(target), 1)
        with patch.object(workflow, "process_started", return_value="another process start"):
            self.assertEqual(workflow.activity(target), 0)
        self.assertIsNone(workflow.marker(target, "feature/test"))

    def test_clears_only_owned_marker_after_branch_switch(self):
        target = self.create()
        token = "a" * 32
        workflow.activity(target, token=token, pid=os.getpid(), status="working")
        self.git("checkout", "-b", "another", cwd=target)
        workflow.activity(target, token=token, pid=os.getpid(), status="clear")
        self.assertIsNone(workflow.marker(target, "feature/test"))
        self.assertIsNone(workflow.marker(target, "another"))
        workflow.activity(target, token="b" * 32, pid=os.getpid(), status="idle")
        self.assertEqual(workflow.marker(target, "another")["marker"], "💬")

    def test_explicit_cwd_ignores_inherited_git_location(self):
        target = self.create()
        with patch.dict(os.environ, {"GIT_DIR": str(self.repo / ".git"), "GIT_WORK_TREE": str(self.repo)}):
            self.assertEqual(workflow.repository(target)[0], target)
            workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "💬")
        self.assertIsNone(workflow.marker(self.repo, "main"))

    def test_manual_markers_are_preserved(self):
        target = self.create()
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working")
        self.wt("config", "state", "marker", "set", "reviewed", cwd=target)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="clear")
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "reviewed")

    def test_foreign_worktrees_and_subagent_namespace_are_not_adopted(self):
        target = self.create()
        with self.assertRaises(workflow.WorkflowError):
            workflow.task_state(target)
        with self.assertRaises(workflow.WorkflowError):
            workflow.validate_branch(self.repo, "pi-subagents/fixture")

    def bound_task(self):
        target = self.create()
        _, _, gitdir = workflow.repository(target)
        workflow.write_json(gitdir / "wt-pi/task.json", {"version": 1, "path": str(target),
                                                       "tab_id": "task", "pane_id": "original"})
        return target

    def test_mixed_tabs_refused_before_focus_or_agent_lookup(self):
        target = self.bound_task()
        panes = [{"tab_id": "task", "pane_id": "original"},
                 {"tab_id": "other", "pane_id": "foreign"}]
        with patch.object(workflow, "task_panes", return_value=panes), \
                patch.object(workflow, "herdr") as api:
            with self.assertRaisesRegex(workflow.WorkflowError, "outside"):
                workflow.open_task(target, "feature/test")
            api.assert_not_called()

    def test_simultaneous_agents_refused_before_focus_or_reuse(self):
        target = self.bound_task()
        panes = [{"tab_id": "task", "pane_id": "original"},
                 {"tab_id": "task", "pane_id": "foreign"}]
        for agents in ([{"pane_id": "foreign"}],
                       [{"pane_id": "original"}, {"pane_id": "foreign"}]):
            with self.subTest(agents=agents), \
                    patch.object(workflow, "task_panes", return_value=panes), \
                    patch.object(workflow, "herdr", return_value={"agents": agents}) as api:
                with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                    workflow.open_task(target, "feature/test")
                api.assert_called_once_with("agent", "list")

    def test_extra_native_sessions_refused_before_reuse(self):
        target = self.bound_task()
        with patch.object(workflow, "task_panes", return_value=[{"tab_id": "task", "pane_id": "original"}]), \
                patch.object(workflow, "herdr", return_value={"agents": [{"pane_id": "original"}]}) as api, \
                patch.object(workflow, "activity", return_value=2):
            with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                workflow.open_task(target, "feature/test")
            api.assert_called_once_with("agent", "list")

    def test_external_record_not_exempt_when_owned_pane_has_no_record(self):
        target = self.bound_task()
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working")
        with patch.object(workflow, "task_panes", return_value=[{"tab_id": "task", "pane_id": "original"}]), \
                patch.object(workflow, "herdr", return_value={"agents": [{"pane_id": "original"}]}) as api:
            with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                workflow.open_task(target, "feature/test")
            api.assert_called_once_with("agent", "list")

    def test_only_matching_native_pane_identity_is_exempt(self):
        target = self.bound_task()
        with patch.dict(os.environ, {"HERDR_PANE_ID": "original"}):
            workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working")
        self.assertEqual(workflow.activity(target, owned_pane="original"), 0)
        self.assertEqual(workflow.activity(target, owned_pane="foreign"), 1)
        self.assertEqual(workflow.activity(target), 1)
        # A live legacy record with no pane identity must fail closed too.
        _, _, gitdir = workflow.repository(target)
        path = gitdir / "wt-pi/activity.json"
        state = workflow.read_json(path)
        state["sessions"]["a" * 32].pop("pane_id")
        workflow.write_json(path, state)
        self.assertEqual(workflow.activity(target, owned_pane="original"), 1)

    def test_multiple_native_sessions_in_owned_pane_block_reuse(self):
        target = self.bound_task()
        with patch.dict(os.environ, {"HERDR_PANE_ID": "original"}):
            for token in ("a" * 32, "b" * 32):
                workflow.activity(target, token=token, pid=os.getpid(), status="working")
        self.assertEqual(workflow.activity(target), 2)
        self.assertEqual(workflow.activity(target, owned_pane="original"), 1)
        with patch.object(workflow, "task_panes", return_value=[{"tab_id": "task", "pane_id": "original"}]), \
                patch.object(workflow, "herdr", return_value={"agents": [{"pane_id": "original"}]}) as api:
            with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                workflow.open_task(target, "feature/test")
            api.assert_called_once_with("agent", "list")

    def test_owned_agent_reused_after_all_checks(self):
        target = self.bound_task()
        with patch.object(workflow, "task_panes", return_value=[{"tab_id": "task", "pane_id": "original"}]), \
                patch.object(workflow, "herdr", return_value={"agents": [{"pane_id": "original"}]}) as api, \
                patch.object(workflow, "activity", return_value=0) as active:
            self.assertEqual(workflow.open_task(target, "feature/test")["action"], "reused")
            active.assert_called_once_with(target, owned_pane="original")
            self.assertEqual(api.call_args_list, [unittest.mock.call("agent", "list"),
                                                unittest.mock.call("tab", "focus", "task")])

    def test_failed_launch_retains_the_tab_binding_for_recovery(self):
        target = self.create()
        self.own(target)
        responses = [
            {"pane": {"workspace_id": "workspace", "cwd": str(self.repo)}},
            {"tab": {"tab_id": "task"}, "root_pane": {"pane_id": "original"}},
            workflow.WorkflowError("Synthetic Pi start failure"),
        ]
        with patch.object(workflow, "task_panes", return_value=[]), \
                patch.object(workflow, "herdr", side_effect=responses):
            with self.assertRaisesRegex(workflow.WorkflowError, "Synthetic"):
                workflow.open_task(target, "feature/test", focus=False)
        self.assertEqual(workflow.task_state(target)["pane_id"], "original")
        self.assertEqual(workflow.task_state(target)["tab_id"], "task")
        self.assertTrue(target.exists())

    def test_agents_cannot_use_human_launcher(self):
        for key in ("PI_CODING_AGENT", "PI_SESSION_ID", "AI_AGENT"):
            with self.subTest(key=key), patch.dict(os.environ, {key: "fixture"}):
                with self.assertRaisesRegex(workflow.WorkflowError, "Subagents"):
                    workflow.human_launcher()

    def test_removal_checks_ignored_files_and_retains_unmerged_branch(self):
        target = self.create()
        self.own(target)
        (target / ".gitignore").write_text("private.local\n")
        self.git("add", ".gitignore", cwd=target)
        self.git("commit", "-qm", "ignore", cwd=target)
        (target / "private.local").write_text("synthetic private data")
        args = type("Args", (), {"branch": "feature/test", "discard_ignored": False})()
        with self.cwd(self.repo), patch.object(workflow, "require_herdr"), patch.object(workflow, "check_remove"):
            with self.assertRaisesRegex(workflow.WorkflowError, "ignored"):
                workflow.remove(args)
            self.assertTrue(target.exists())
            args.discard_ignored = True
            workflow.remove(args)
        self.assertFalse(target.exists())
        self.git("rev-parse", "--verify", "refs/heads/feature/test")

    def test_live_panes_block_removal(self):
        target = self.create()
        with patch.object(workflow, "require_herdr"), patch.object(workflow, "task_panes", return_value=[{}]):
            with self.assertRaisesRegex(workflow.WorkflowError, "Herdr panes"):
                workflow.check_remove(target)
        self.assertTrue(target.exists())

    def test_real_detached_process_blocks_removal(self):
        target = self.create()
        process = subprocess.Popen(["sleep", "30"], cwd=target, start_new_session=True)
        try:
            with patch.object(workflow, "require_herdr"), patch.object(workflow, "task_panes", return_value=[]):
                with self.assertRaisesRegex(workflow.WorkflowError, "process"):
                    workflow.check_remove(target)
        finally:
            process.terminate()
            process.wait(timeout=5)

    def test_setup_preserves_config_and_rejects_links(self):
        before = self.config.stat().st_mtime_ns
        subprocess.run(["bash", str(ROOT / "scripts/setup-worktrunk.sh")], check=True, capture_output=True)
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.config.unlink()
        self.config.symlink_to(self.root / "missing")
        result = subprocess.run(["bash", str(ROOT / "scripts/setup-worktrunk.sh")], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.config.is_symlink())
        self.assertFalse((self.root / "missing").exists())

    def test_setup_seeds_private_regular_config(self):
        self.config.unlink()
        subprocess.run(["bash", str(ROOT / "scripts/setup-worktrunk.sh")], check=True, capture_output=True)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.config.is_symlink())

    def test_setup_refuses_linked_dotfiles_before_any_bootstrap_work(self):
        target = self.create()
        (target / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/setup.sh", target / "scripts/setup.sh")
        result = subprocess.run(["bash", str(target / "scripts/setup.sh")], cwd=target,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("primary dotfiles checkout", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_hooks_skip_linked_trees_even_with_a_legacy_worker(self):
        target = self.create()
        (target / "scripts").mkdir()
        worker = target / "scripts/restow-changed.sh"
        worker.write_text('#!/bin/sh\ntouch "$HOME/unsafe-restow"\n')
        worker.chmod(0o755)
        for name in ("post-merge", "post-rewrite", "post-commit"):
            with self.subTest(hook=name):
                args = ["rebase"] if name == "post-rewrite" else []
                result = subprocess.run(["bash", str(ROOT / "scripts/git-hooks" / name), *args],
                                        cwd=target, input="", capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.home / "unsafe-restow").exists())

    def test_fish_wrapper_changes_directory_and_handles_missing_binary(self):
        target = self.create()
        script = ROOT / "stow/fish/.config/fish/conf.d/worktrunk.fish"
        result = subprocess.run(["fish", "--no-config", "-ic", f'source "{script}"; wt switch feature/test; pwd'],
                                cwd=self.repo, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.splitlines()[-1]).resolve(), target)
        result = subprocess.run(["fish", "--no-config", "-ic", f'set -gx PATH /usr/bin /bin; source "{script}"'],
                                cwd=self.repo, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
