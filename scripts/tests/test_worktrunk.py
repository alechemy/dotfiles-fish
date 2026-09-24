#!/usr/bin/env python3
"""Worktrunk integration contracts in disposable repositories and homes."""

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
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

    def own_cmux(self, target, *, session="session-1"):
        _, common, gitdir = workflow.repository(target)
        state = {"version": 2, "backend": "cmux", "path": str(target),
                 "repository": str(common), "branch": "feature/test",
                 "workspace_id": "workspace-1", "surface_id": "surface-1"}
        if session:
            state["session_id"] = session
        workflow.write_json(gitdir / "wt-pi/task.json", state)
        return state

    def cmux_tree(self, target):
        return {"windows": [{"id": "window-1", "workspaces": [{"id": "workspace-1", "panes": [
            {"id": "pane-1", "surfaces": [{"id": "surface-1", "type": "terminal", "cwd": str(target)}]}
        ]}]}]}

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

    def test_cmux_unknown_creation_outcome_cannot_be_retried(self):
        target = self.create()
        state = self.own_cmux(target, session=None)
        state.pop("workspace_id")
        state.pop("surface_id")
        state["launch"] = {"token": "a" * 32, "status": "creating", "requested_at": time.time()}
        workflow.write_task_state(target, state)
        with patch.object(workflow, "cmux") as api:
            with self.assertRaisesRegex(workflow.WorkflowError, "unknown outcome"):
                workflow.open_cmux_task(target, "feature/test")
            api.assert_not_called()

    def test_cmux_creation_retains_returned_workspace_after_binding_failure(self):
        target = self.create()
        state = self.own_cmux(target, session=None)
        state.pop("workspace_id")
        state.pop("surface_id")
        workflow.write_task_state(target, state)
        with patch.object(workflow, "cmux", return_value={"workspace_id": "workspace-created"}) as api, \
                patch.object(workflow, "wait_for_cmux_binding",
                             side_effect=workflow.WorkflowError("Synthetic binding failure")):
            with self.assertRaisesRegex(workflow.WorkflowError, "Synthetic"):
                workflow.create_cmux_task(target, "feature/test", state, focus=False, resume=False)
        command = api.call_args.args
        self.assertEqual(command[:5], ("new-workspace", "--name", "feature/test", "--cwd", str(target)))
        self.assertIn("wt-pi _start", command[command.index("--command") + 1])
        self.assertEqual(command[-2:], ("--focus", "false"))
        saved = workflow.task_state(target)
        self.assertEqual(saved["workspace_id"], "workspace-created")
        self.assertNotIn("surface_id", saved)
        with self.assertRaisesRegex(workflow.WorkflowError, "incomplete"):
            workflow.cmux_bound_surface(saved)

    def test_cmux_parent_preserves_faster_child_binding(self):
        target = self.create()
        state = self.own_cmux(target, session=None)
        state.pop("workspace_id")
        state.pop("surface_id")
        workflow.write_task_state(target, state)

        def create_and_bind(*_args, **_kwargs):
            latest = workflow.task_state(target)
            latest["workspace_id"] = "workspace-created"
            latest["surface_id"] = "surface-created"
            latest["session_id"] = "session-created"
            latest["launch"] = {**latest["launch"], "status": "bound", "pid": os.getpid(),
                                "started": workflow.process_started(os.getpid())}
            workflow.write_task_state(target, latest)
            return {"workspace_id": "workspace-created"}

        with patch.object(workflow, "cmux", side_effect=create_and_bind), \
                patch.object(workflow, "wait_for_cmux_binding", side_effect=lambda root, _workspace: workflow.task_state(root)):
            workflow.create_cmux_task(target, "feature/test", state, focus=False, resume=False)
        saved = workflow.task_state(target)
        self.assertEqual(saved["surface_id"], "surface-created")
        self.assertEqual(saved["session_id"], "session-created")
        self.assertEqual(saved["launch"]["status"], "bound")

    def test_cmux_start_binds_exact_environment_before_exec(self):
        target = self.create()
        state = self.own_cmux(target, session=None)
        state.pop("workspace_id")
        state.pop("surface_id")
        state["launch"] = {"token": "a" * 32, "status": "creating", "requested_at": time.time()}
        workflow.write_task_state(target, state)
        with self.cwd(target), patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "workspace-new",
                                                       "CMUX_SURFACE_ID": "surface-new"}), \
                patch.object(workflow.os, "execvp", side_effect=workflow.WorkflowError("exec stopped")) as execute:
            with self.assertRaisesRegex(workflow.WorkflowError, "exec stopped"):
                workflow.start_cmux_pi("a" * 32)
        saved = workflow.task_state(target)
        self.assertEqual(saved["workspace_id"], "workspace-new")
        self.assertEqual(saved["surface_id"], "surface-new")
        self.assertEqual(saved["launch"]["status"], "bound")
        execute.assert_called_once_with("pi", ["pi", "--name", "feature/test"])

    def test_cmux_reuses_only_the_bound_live_session(self):
        target = self.create()
        state = self.own_cmux(target)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1")
        calls = []
        with patch.object(workflow, "cmux_tree", return_value=self.cmux_tree(target)), \
                patch.object(workflow, "cmux_saved_session_active", return_value=False), \
                patch.object(workflow, "cmux", side_effect=lambda *args, **kwargs: calls.append(args) or {}):
            result = workflow.open_cmux_task(target, "feature/test")
        self.assertEqual(result["action"], "reused")
        self.assertEqual(calls, [("select-workspace", "--workspace", state["workspace_id"])])

    def test_new_cmux_conversation_reports_activity_without_taking_task_ownership(self):
        target = self.create()
        state = self.own_cmux(target)
        comment = {"id": "comment-1", "source": "user", "path": "fixture", "summary": "Change this"}
        for workspace, surface in (("workspace-1", "surface-1"), ("workspace-new", "surface-new")):
            with self.subTest(workspace=workspace), patch.object(workflow, "cmux_tree", return_value=self.cmux_tree(target)):
                workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                                  backend="cmux", session_id="session-new", workspace_id=workspace,
                                  surface_id=surface)
                self.assertEqual(workflow.task_state(target), state)
                self.assertEqual(workflow.marker(target, "feature/test")["marker"], "💬")
                self.assertEqual(workflow.active_records(target)[0]["session_id"], "session-new")
                with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                    workflow.open_cmux_task(target, "feature/test")
                with self.assertRaisesRegex(workflow.WorkflowError, "exactly one verified"):
                    workflow.deliver_feedback(target, {"comments": [comment]})
                with self.assertRaisesRegex(workflow.WorkflowError, "still active"):
                    workflow.check_remove(target, state)
                workflow.activity(target, token="a" * 32, status="clear")
                self.assertIsNone(workflow.marker(target, "feature/test"))

    def test_unbound_task_only_adopts_activity_from_its_recorded_surface(self):
        target = self.create()
        state = self.own_cmux(target, session=None)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                          backend="cmux", session_id="session-foreign", workspace_id="workspace-foreign",
                          surface_id="surface-foreign")
        self.assertEqual(workflow.task_state(target), state)
        workflow.activity(target, token="b" * 32, pid=os.getpid(), status="working",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1")
        self.assertEqual(workflow.task_state(target), {**state, "session_id": "session-1"})
        self.assertEqual(workflow.activity(target), 2)
        self.assertEqual(workflow.marker(target, "feature/test")["marker"], "🤖")

    def test_stale_task_identity_does_not_block_activity_or_rebind(self):
        target = self.create()
        state = self.own_cmux(target)
        _, _, gitdir = workflow.repository(target)
        for field, value in (("branch", "old-branch"), ("path", str(self.repo)),
                             ("repository", str(self.root / "other.git"))):
            for session_id in ("session-1", "session-new"):
                with self.subTest(field=field, session=session_id), patch.object(workflow, "cmux_tree") as tree:
                    stale = {**state, field: value}
                    workflow.write_task_state(target, stale)
                    workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                                      backend="cmux", session_id=session_id, workspace_id="workspace-new",
                                      surface_id="surface-new")
                    tree.assert_not_called()
                    self.assertEqual(workflow.read_json(gitdir / "wt-pi/task.json"), stale)
                    self.assertEqual(workflow.marker(target, "feature/test")["marker"], "💬")
                    self.assertEqual(workflow.active_records(target)[0]["session_id"], session_id)
                    with self.assertRaises(workflow.WorkflowError):
                        workflow.task_state(target)
                    workflow.activity(target, token="a" * 32, status="clear")

    def test_restored_session_cannot_rebind_while_original_surface_exists(self):
        target = self.create()
        state = self.own_cmux(target)
        tree = self.cmux_tree(target)
        tree["windows"][0]["workspaces"][0]["panes"][0]["surfaces"].append(
            {"id": "surface-new", "type": "terminal", "cwd": str(target)})
        with patch.object(workflow, "cmux_tree", return_value=tree):
            with self.assertRaisesRegex(workflow.WorkflowError, "ambiguous cmux ownership"):
                workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                                  backend="cmux", session_id="session-1", workspace_id="workspace-1",
                                  surface_id="surface-new")
        self.assertEqual(workflow.task_state(target), state)

    def test_restored_exact_pi_session_rebinds_new_cmux_ids(self):
        target = self.create()
        self.own_cmux(target)
        restored = {"windows": [{"id": "window-new", "workspaces": [{"id": "workspace-new", "panes": [
            {"id": "pane-new", "surfaces": [{"id": "surface-new", "type": "terminal", "cwd": str(target)}]}
        ]}]}]}
        with patch.object(workflow, "cmux_tree", return_value=restored):
            workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                              backend="cmux", session_id="session-1", workspace_id="workspace-new",
                              surface_id="surface-new")
        state = workflow.task_state(target)
        self.assertEqual(state["workspace_id"], "workspace-new")
        self.assertEqual(state["surface_id"], "surface-new")

    def test_cli_activity_survives_new_conversation_and_stale_task_branch(self):
        target = self.create()
        state = self.own_cmux(target)
        self.git("branch", "-m", "feature/renamed", cwd=target)
        result = subprocess.run([sys.executable, str(SOURCE), "_activity", "a" * 32,
                                 str(os.getpid()), "idle", "--backend", "cmux",
                                 "--session-id", "session-2", "--workspace-id", "workspace-1",
                                 "--surface-id", "surface-2"], cwd=target,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(workflow.marker(target, "feature/renamed")["marker"], "💬")
        _, _, gitdir = workflow.repository(target)
        self.assertEqual(workflow.read_json(gitdir / "wt-pi/task.json"), state)
        with self.assertRaisesRegex(workflow.WorkflowError, "binding no longer matches"):
            workflow.task_state(target)

    def test_foreign_cmux_activity_blocks_reopen_feedback_and_removal(self):
        target = self.create()
        state = self.own_cmux(target)
        for session_id in ("session-1", "session-2"):
            with self.subTest(session_id=session_id):
                workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                                  backend="cmux", session_id=session_id,
                                  workspace_id="workspace-1", surface_id="surface-1")
                workflow.activity(target, token="b" * 32, pid=os.getpid(), status="working",
                                  backend="cmux", session_id="session-2",
                                  workspace_id="workspace-1", surface_id="surface-2")
                with patch.object(workflow, "cmux_tree", return_value=self.cmux_tree(target)):
                    with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                        workflow.open_cmux_task(target, "feature/test")
                comment = {"id": "comment-1", "source": "user", "path": "fixture", "summary": "Change this"}
                with patch.object(workflow.socket, "socket") as receiver:
                    with self.assertRaisesRegex(workflow.WorkflowError, "exactly one verified"):
                        workflow.deliver_feedback(target, {"comments": [comment]})
                    receiver.assert_not_called()
                with self.assertRaisesRegex(workflow.WorkflowError, "Pi sessions are still active"):
                    workflow.check_remove(target, state)
                workflow.activity(target, token="a" * 32, pid=os.getpid(), status="clear")
                self.assertEqual(workflow.marker(target, "feature/test")["marker"], "🤖")
                workflow.activity(target, token="b" * 32, pid=os.getpid(), status="clear")

    def test_cmux_refuses_stale_or_foreign_ownership(self):
        target = self.create()
        self.own_cmux(target)
        with patch.object(workflow, "cmux_tree", return_value={"windows": []}):
            with self.assertRaisesRegex(workflow.WorkflowError, "stale"):
                workflow.open_cmux_task(target, "feature/test")
        workflow.activity(target, token="b" * 32, pid=os.getpid(), status="idle",
                          backend="standalone")
        with patch.object(workflow, "cmux_tree", return_value=self.cmux_tree(target)):
            with self.assertRaisesRegex(workflow.WorkflowError, "Another agent"):
                workflow.open_cmux_task(target, "feature/test")

    def test_cmux_feedback_is_delivered_once_with_a_receipt(self):
        target = self.create()
        self.own_cmux(target)
        socket_path = self.root / "feedback.sock"
        received = []
        ready = threading.Event()

        def receiver():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(socket_path))
                server.listen(1)
                ready.set()
                connection, _ = server.accept()
                with connection:
                    request = json.loads(connection.makefile().readline())
                    received.append(request)
                    connection.sendall((json.dumps({"request_id": request["request_id"],
                                                    "status": "delivered"}) + "\n").encode())

        thread = threading.Thread(target=receiver)
        thread.start()
        ready.wait(5)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1", feedback_socket=str(socket_path),
                          feedback_token="secret")
        comment = {"id": "comment-1", "source": "user", "path": "fixture", "side": "new",
                   "line": 1, "summary": "Change this"}
        first = workflow.deliver_feedback(target, {"comments": [comment]})
        thread.join(5)
        second = workflow.deliver_feedback(target, {"comments": [comment]})
        self.assertEqual(first, {"status": "delivered", "delivered_ids": ["comment-1"]})
        self.assertEqual(second, {"status": "already-delivered", "delivered_ids": ["comment-1"]})
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["session_id"], "session-1")
        self.assertIn("Change this", received[0]["text"])

    def test_edited_comment_with_same_id_is_delivered_again(self):
        target = self.create()
        self.own_cmux(target)
        socket_path = self.root / "feedback-edit.sock"
        received = []

        def serve_once():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                socket_path.unlink(missing_ok=True)
                server.bind(str(socket_path))
                server.listen(1)
                ready.set()
                connection, _ = server.accept()
                with connection:
                    request = json.loads(connection.makefile().readline())
                    received.append(request)
                    connection.sendall((json.dumps({"request_id": request["request_id"],
                                                    "status": "delivered"}) + "\n").encode())

        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1", feedback_socket=str(socket_path),
                          feedback_token="secret")
        original = {"id": "comment-1", "source": "user", "path": "fixture", "summary": "First"}
        edited = {**original, "summary": "Second"}
        for comment in (original, edited):
            ready = threading.Event()
            thread = threading.Thread(target=serve_once)
            thread.start()
            ready.wait(5)
            workflow.deliver_feedback(target, {"comments": [comment]})
            thread.join(5)
        self.assertEqual([request["text"].splitlines()[-1].strip() for request in received], ["First", "Second"])

    def test_uncertain_feedback_blocks_automatic_resend(self):
        target = self.create()
        self.own_cmux(target)
        socket_path = self.root / "feedback-uncertain.sock"
        ready = threading.Event()

        def receiver():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(socket_path))
                server.listen(1)
                ready.set()
                connection, _ = server.accept()
                with connection:
                    connection.makefile().readline()

        thread = threading.Thread(target=receiver)
        thread.start()
        ready.wait(5)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="idle",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1", feedback_socket=str(socket_path),
                          feedback_token="secret")
        comment = {"id": "comment-1", "source": "user", "path": "fixture", "summary": "Change this"}
        with self.assertRaisesRegex(workflow.WorkflowError, "uncertain"):
            workflow.deliver_feedback(target, {"comments": [comment]})
        thread.join(5)
        with self.assertRaisesRegex(workflow.WorkflowError, "previous delivery is uncertain"):
            workflow.deliver_feedback(target, {"comments": [comment]})

    def test_cmux_busy_recipient_refuses_feedback(self):
        target = self.create()
        self.own_cmux(target)
        workflow.activity(target, token="a" * 32, pid=os.getpid(), status="working",
                          backend="cmux", session_id="session-1", workspace_id="workspace-1",
                          surface_id="surface-1", feedback_socket=str(self.root / "missing.sock"),
                          feedback_token="secret")
        comment = {"id": "comment-1", "source": "user", "path": "fixture", "summary": "Change this"}
        with self.assertRaisesRegex(workflow.WorkflowError, "busy"):
            workflow.deliver_feedback(target, {"comments": [comment]})

    def test_agents_cannot_use_human_launcher(self):
        for key in ("PI_CODING_AGENT", "PI_SESSION_ID", "AI_AGENT"):
            with self.subTest(key=key), patch.dict(os.environ, {key: "fixture"}):
                with self.assertRaisesRegex(workflow.WorkflowError, "Subagents"):
                    workflow.human_launcher()

    def test_removal_checks_ignored_files_and_retains_unmerged_branch(self):
        target = self.create()
        self.own_cmux(target)
        (target / ".gitignore").write_text("private.local\n")
        self.git("add", ".gitignore", cwd=target)
        self.git("commit", "-qm", "ignore", cwd=target)
        (target / "private.local").write_text("synthetic private data")
        args = type("Args", (), {"branch": "feature/test", "discard_ignored": False})()
        with self.cwd(self.repo), patch.object(workflow, "check_remove"):
            with self.assertRaisesRegex(workflow.WorkflowError, "ignored"):
                workflow.remove(args)
            self.assertTrue(target.exists())
            args.discard_ignored = True
            workflow.remove(args)
        self.assertFalse(target.exists())
        self.git("rev-parse", "--verify", "refs/heads/feature/test")

    def test_failed_review_tracking_blocks_removal_until_reconciled(self):
        target = self.create()
        state = self.own_cmux(target)
        token = "c" * 32
        payload = {"id": "comment-1", "source": "user", "fingerprint": "invalid"}
        with self.assertRaisesRegex(workflow.WorkflowError, "Invalid Hunk"):
            workflow.review_event(target, "upsert", os.getpid(), payload, token)
        self.assertEqual(workflow.unsent_feedback(target), (0, 1))
        with patch.object(workflow, "require_cmux"), patch.object(workflow, "activity", return_value=0), \
                patch.object(workflow, "cmux_task_surfaces", return_value=[]):
            with self.assertRaisesRegex(workflow.WorkflowError, "Unsent or uncertain"):
                workflow.check_remove(target, state)
        payload["fingerprint"] = "a" * 64
        workflow.review_event(target, "upsert", os.getpid(), payload, "d" * 32)
        self.assertEqual(workflow.unsent_feedback(target), (1, 0))
        workflow.review_event(target, "remove", os.getpid(), {"id": "comment-1"}, "e" * 32)
        self.assertEqual(workflow.unsent_feedback(target), (0, 0))

    def test_cmux_surface_and_unsent_feedback_block_removal(self):
        target = self.create()
        state = self.own_cmux(target)
        with patch.object(workflow, "require_cmux"), patch.object(workflow, "activity", return_value=0), \
                patch.object(workflow, "cmux_task_surfaces", return_value=[("window", "workspace", "pane", "surface")]):
            with self.assertRaisesRegex(workflow.WorkflowError, "cmux surfaces"):
                workflow.check_remove(target, state)
        with patch.object(workflow, "require_cmux"), patch.object(workflow, "activity", return_value=0), \
                patch.object(workflow, "cmux_task_surfaces", return_value=[]), \
                patch.object(workflow, "unsent_feedback", return_value=(1, 0)):
            with self.assertRaisesRegex(workflow.WorkflowError, "Unsent"):
                workflow.check_remove(target, state)

    def test_real_detached_process_blocks_removal(self):
        target = self.create()
        process = subprocess.Popen(["sleep", "30"], cwd=target, start_new_session=True)
        try:
            state = self.own_cmux(target)
            with patch.object(workflow, "require_cmux"), \
                    patch.object(workflow, "activity", return_value=0), \
                    patch.object(workflow, "cmux_task_surfaces", return_value=[]), \
                    patch.object(workflow, "unsent_feedback", return_value=(0, 0)):
                with self.assertRaisesRegex(workflow.WorkflowError, "process"):
                    workflow.check_remove(target, state)
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
