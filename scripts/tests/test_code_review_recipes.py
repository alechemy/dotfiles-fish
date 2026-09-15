#!/usr/bin/env python3
"""Exercise the code-review skill's Git recipes in disposable repositories."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "stow/agents/.agents/skills/code-review"
SCOPE = (SKILL / "references/scope.md").read_text()
RECIPES = dict(re.findall(r"^\| ([^|]+?) \| `([^`]+)` \|$", SCOPE, re.MULTILINE))


class CodeReviewRecipeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(self.work), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("sample file.txt", "original\n")
        self.commit("Initial fixture")
        self.env["head"] = self.git("rev-parse", "HEAD").strip()
        self.env["base"] = self.env["head"]
        self.env["merge_base"] = self.env["head"]

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.work, env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def write(self, path, content):
        (self.work / path).write_text(content)

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "-q", "-m", message)

    def shell(self, command):
        result = subprocess.run(["bash", "-c", command], cwd=self.work,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def recipe(self, mode):
        return self.shell(RECIPES[mode])

    def test_staged_and_unstaged_versions_stay_distinct(self):
        self.write("sample file.txt", "staged-version\n")
        self.git("add", "--", "sample file.txt")
        self.write("sample file.txt", "working-version\n")
        staged = self.recipe("Staged only")
        self.assertIn("+staged-version", staged)
        self.assertNotIn("working-version", staged)
        unstaged = self.recipe("Unstaged only")
        self.assertIn("-staged-version", unstaged)
        self.assertIn("+working-version", unstaged)
        self.env["path"] = "sample file.txt"
        self.assertIn('git show ":$path"', SCOPE)
        self.assertEqual(self.shell('git show ":$path"'), "staged-version\n")

    def test_wip_uses_net_contents_not_both_intermediate_diffs(self):
        self.write("sample file.txt", "staged-version\n")
        self.git("add", "--", "sample file.txt")
        self.write("sample file.txt", "original\n")
        self.assertEqual(self.recipe("All uncommitted tracked changes"), "")
        self.assertNotEqual(self.recipe("Staged only"), "")
        self.assertNotEqual(self.recipe("Unstaged only"), "")

    def test_branch_uses_merge_base_and_excludes_working_tree(self):
        self.git("checkout", "-q", "-b", "feature")
        self.write("feature.txt", "feature-commit\n")
        self.commit("Feature fixture")
        self.env["head"] = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write("main-only.txt", "main-only-change\n")
        self.commit("Main fixture")
        self.env["base"] = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "feature")
        self.write("feature.txt", "working-version\n")
        self.env["merge_base"] = self.git("merge-base", self.env["base"], self.env["head"]).strip()
        branch = self.recipe("Branch or PR")
        self.assertIn("+feature-commit", branch)
        self.assertNotIn("working-version", branch)
        self.assertNotIn("main-only-change", branch)
        self.assertIn("-main-only-change", self.recipe("Exact endpoints"))
        wip = self.recipe("Since baseline including work in progress")
        self.assertIn("+working-version", wip)
        self.assertNotIn("main-only-change", wip)

    def test_path_selection_preserves_spaces_and_excludes_unrelated_files(self):
        self.write("sample file.txt", "selected-change\n")
        self.write("unrelated.txt", "unrelated-change\n")
        self.git("add", "--all")
        self.env["path"] = "sample file.txt"
        diff = self.shell(RECIPES["Staged only"] + ' "$path"')
        self.assertIn("+selected-change", diff)
        self.assertNotIn("unrelated-change", diff)

    def test_empty_tracked_diff_still_has_untracked_inventory(self):
        self.write("new file.txt", "untracked-content\n")
        self.assertEqual(self.recipe("All uncommitted tracked changes"), "")
        command = "git ls-files --others --exclude-standard -z --"
        self.assertIn(f"`{command}`", SCOPE)
        self.assertEqual(self.shell(command), "new file.txt\0")
        self.assertEqual(self.recipe("Staged only"), "")

    def test_deletions_and_rename_sides_remain_visible(self):
        self.write("removed.txt", "removed-content\n")
        self.commit("Deletion fixture")
        self.env["head"] = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "sample file.txt", "renamed file.txt")
        self.git("rm", "removed.txt")
        diff = self.recipe("All uncommitted tracked changes")
        self.assertIn("-removed-content", diff)
        self.assertIn("sample file.txt", diff)
        self.assertIn("renamed file.txt", diff)

    def test_root_commit_recipe_includes_additions(self):
        command = next(c for c in re.findall(r"`([^`]+)`", SCOPE)
                       if c.startswith("git diff-tree --root"))
        self.assertIn("+original", self.shell(command))

    def test_staged_review_supports_a_repository_without_head(self):
        self.git("checkout", "-q", "--orphan", "unborn")
        self.write("sample file.txt", "initial-staged-version\n")
        self.git("add", "--all")
        command = "git diff --no-ext-diff --no-textconv --cached --"
        self.assertIn(f"`{command}`", SCOPE)
        self.assertIn("+initial-staged-version", self.shell(command))

    def test_same_status_does_not_hide_changed_review_content(self):
        self.write("sample file.txt", "first-version\n")
        first_status = self.git("status", "--short")
        first_diff = self.recipe("All uncommitted tracked changes")
        self.write("sample file.txt", "second-version\n")
        self.assertEqual(self.git("status", "--short"), first_status)
        self.assertNotEqual(self.recipe("All uncommitted tracked changes"), first_diff)

    def test_shared_criteria_and_local_references_resolve(self):
        simplify = SKILL.parent / "simplify-review/SKILL.md"
        for path in (SKILL / "SKILL.md", simplify):
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                self.assertTrue((path.parent / target).is_file(), target)
        self.assertIn("../code-review/references/maintainability.md", simplify.read_text())
        self.assertNotIn("disable-model-invocation: true", (SKILL / "SKILL.md").read_text())
        self.assertIn("disable-model-invocation: true", simplify.read_text())


if __name__ == "__main__":
    unittest.main()
