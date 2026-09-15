#!/usr/bin/env python3
"""Run documented recall shell recipes with disposable workspaces and fake input."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_recall_filter import HELPER, ROOT, session, turn

RECALL = ROOT / "stow/agents/.agents/skills/recall/SKILL.md"
HANDOFF = ROOT / "stow/agents/.agents/skills/handoff/SKILL.md"


def recipes(path):
    return re.findall(r"```bash\n(.*?)```", path.read_text(), re.DOTALL)


class RecallRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.work = self.root / "fictional work"
        self.work.mkdir()
        self.env = dict(os.environ, HOME=str(self.home))
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            self.env.pop(key, None)

    def run_bash(self, script, cwd=None):
        return subprocess.run(["bash", "-c", script], cwd=cwd or self.work,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def handoff(self, context, name, workspace, topic="widget-launch"):
        context.mkdir(parents=True, exist_ok=True)
        path = context / name
        path.write_text(f"Workspace: {workspace}\nTopic: {topic}\nUpdated: 2026-07-24T18:00:00Z\n\n"
                        "The fictional widget is ready for a lazy-initialization check.\n")
        return path

    def check_lookup(self, context, workspace, cwd):
        wanted = self.handoff(context, "fictional-widget-launch-handoff.md", workspace)
        other = self.handoff(context, "other-widget-launch-handoff.md", str(workspace) + "-other")
        with other.open("a") as handle:
            handle.write(f"\nQuoted prior handoff:\nWorkspace: {workspace}\nTopic: widget-launch\n")
        self.handoff(context, "other-topic-handoff.md", workspace, topic="other")
        (context / "legacy-handoff.md").write_text("Legacy context without scope headers.\n")
        (context / "symlink-handoff.md").symlink_to(wanted)
        result = self.run_bash(recipes(RECALL)[0], cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(wanted)])
        resolved = self.run_bash(recipes(HANDOFF)[0] + '\nprintf "%s\\n" "$workspace" "$context"', cwd=cwd)
        self.assertEqual(resolved.stdout.splitlines(), [str(workspace), str(context)])

    def test_repo_lookup_uses_root_from_subdirectory(self):
        subprocess.run(["git", "init", "-q", str(self.work)], env=self.env, check=True,
                       capture_output=True)
        child = self.work / "nested"
        child.mkdir()
        self.check_lookup(self.work / ".context", self.work, child)

    def test_nonrepo_lookup_uses_home_and_exact_workspace(self):
        self.check_lookup(self.home / ".context", self.work, self.work)

    def test_lookup_disables_inherited_shell_tracing(self):
        context = self.home / ".context"
        wanted = self.handoff(context, "widget-launch-handoff.md", self.work)
        self.handoff(context, "unrelated-handoff.md", "/tmp/fictional-unrelated-workspace")
        result = self.run_bash("set -x\n" + recipes(RECALL)[0])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), [str(wanted)])
        self.assertNotIn("fictional-unrelated-workspace", result.stderr)
        self.assertNotIn("workspace_header", result.stderr)

    def test_coding_receipt_is_local_scoped_and_preserves_unresolved_work(self):
        context = self.home / ".context"
        wanted = self.handoff(context, "widget-launch-handoff.md", self.work)
        receipt = (
            "\n## Coding-task review receipt\n"
            "Integration target: release/integration\n"
            "Base / merge-base: fixture-base / fixture-merge-base\n"
            "Reviewed HEAD: fixture-head\n"
            "Scope: committed base...HEAD; staged, unstaged, untracked excluded\n"
            "Tested dirty state: .context/fixture-validation.txt\n"
            "Validation: fictional unit suite passed before later edits\n"
            "Unresolved: recheck after rebase; prior evidence is stale\n"
            "Session/run references: fixture-session / fixture-run\n"
            "Service ownership: none; shared services must remain untouched\n"
            "Next action: review and test current state\n")
        with wanted.open("a") as stream:
            stream.write(receipt)
        unrelated = self.handoff(context, "unrelated-handoff.md", "/tmp/other-workspace")
        with unrelated.open("a") as stream:
            stream.write("DO NOT SELECT THIS RECEIPT\n")
        result = self.run_bash(recipes(RECALL)[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(wanted)])
        self.assertIn(receipt, wanted.read_text())
        self.assertNotIn("DO NOT SELECT", result.stdout)
        # Required evidence stays in one owner rather than duplicate recall rules.
        handoff = HANDOFF.read_text()
        recall = RECALL.read_text()
        for contract in ("Noncoding handoffs need no Git fields", "integration target",
                         "merge-base", "reviewed HEAD", "tested dirty state", "Unresolved findings",
                         "session/run references", "Service ownership", "One next action"):
            self.assertIn(contract, handoff)
        self.assertIn("../code-review/references/scope.md", handoff)
        self.assertIn("../handoff/SKILL.md#coding-task-review-receipt", recall)
        self.assertIn("Mark evidence stale after edits or rebases", recall)

    def test_missing_context_is_empty(self):
        result = self.run_bash(recipes(RECALL)[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_all_documented_bash_recipes_parse(self):
        for path in (RECALL, HANDOFF):
            for script in recipes(path):
                result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def wire_fake_reader(self, failing=False):
        installed = self.home / ".agents/skills/recall/recall-filter.py"
        installed.parent.mkdir(parents=True)
        installed.symlink_to(HELPER)
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        reader = fake_bin / "agent-read"
        reader.write_text("#!/bin/bash\n"
                          "printf '%s\\n' 'fictional-raw-upstream-diagnostic' >&2\n"
                          'case "$1" in\n'
                          "list) printf '%s\\n' '" + json.dumps([session()]) + "' ;;\n"
                          "transcript) printf '%s\\n' '" + json.dumps(dict(session=session(), turns=[turn()])) + "' ;;\n"
                          "*) exit 99 ;;\nesac\n" + ("exit 7\n" if failing else ""))
        reader.chmod(0o755)
        self.env["PATH"] = str(fake_bin) + os.pathsep + self.env["PATH"]

    def test_pipelines_suppress_upstream_stderr_and_release_only_metadata(self):
        self.wire_fake_reader()
        for script in recipes(RECALL)[1:]:
            result = self.run_bash(script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            output = json.loads(result.stdout)
            self.assertNotIn("fictional-raw", result.stdout)
            self.assertNotIn("initializes", result.stdout)
            self.assertNotIn("label", result.stdout)
            self.assertEqual(output["matched"], 1)

    def test_pipefail_preserves_upstream_failure(self):
        self.wire_fake_reader(failing=True)
        for script in recipes(RECALL)[1:]:
            result = self.run_bash(script)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertNotIn("fictional-raw", result.stdout)


if __name__ == "__main__":
    unittest.main()
