#!/usr/bin/env python3
"""The allocator smoke requires a named candidate before touching a loader."""
import json
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "scripts/tests/worktrunk-subagents-smoke.mjs"


class CandidateAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.marker = self.root / "loader-used"
        fake_brew = self.bin / "brew"
        fake_brew.write_text("#!/bin/sh\nprintf invoked > " + shlex.quote(str(self.marker)) + "\nexit 99\n")
        fake_brew.chmod(0o755)
        self.env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "HOME": str(self.home), "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_NOSYSTEM": "1"}
        # A fictional apparent active package must never be a fallback.
        active = self.home / ".pi/agent/local/copilot-delegation/node_modules/pi-subagents/src/runs/shared"
        active.mkdir(parents=True)
        (active / "worktree.ts").write_text('throw new Error("active fallback loaded");\n')

    def run_smoke(self, *args):
        result = subprocess.run(["node", str(SMOKE), *args], cwd=self.root,
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists(), "invalid admission invoked dependency lookup")
        self.assertNotIn("active fallback loaded", result.stderr)
        return result

    def test_no_candidate_and_unknown_or_duplicate_options_fail_before_loading(self):
        for args in ([], ["--candidate"], ["--unknown", "/fixture"],
                     ["--candidate-root"], ["--pi-loader-root", "/fixture"],
                     ["--candidate-root", "/one", "--candidate-root", "/two"]):
            with self.subTest(args=args):
                self.assertIn("Usage:", self.run_smoke(*args).stderr)

    def test_absent_or_nonpackage_candidate_fails_before_loading(self):
        self.run_smoke("--candidate-root", str(self.root / "absent"))
        candidate = self.root / "candidate"
        candidate.mkdir()
        self.run_smoke("--candidate-root", str(candidate))
        (candidate / "package.json").write_text(json.dumps({"name": "not-subagents"}))
        self.assertIn("must be pi-subagents", self.run_smoke("--candidate-root", str(candidate)).stderr)

    def valid_candidate(self):
        candidate = self.root / "candidate"
        shared = candidate / "src/runs/shared"
        shared.mkdir(parents=True)
        (candidate / "package.json").write_text(json.dumps({"name": "pi-subagents", "version": "fixture"}))
        for name in ("worktree.ts", "worktree-cleanup-plan.ts"):
            (shared / name).write_text('throw new Error("candidate must not import in admission test");\n')
        return candidate

    def loader_attempt(self, candidate, *args):
        result = subprocess.run(["node", str(SMOKE), "--candidate-root", str(candidate), *args],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists(), "loader selection invoked brew")
        self.assertNotIn("candidate must not import", result.stderr)
        return result

    def test_normal_loader_discovery_is_code_only_and_never_invokes_brew(self):
        candidate = self.valid_candidate()
        loader = self.root / "pi-package"
        (loader / "dist").mkdir(parents=True)
        (loader / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent"}))
        cli = loader / "dist/cli.js"
        cli.write_text("#!/bin/sh\nexit 99\n")
        cli.chmod(0o755)
        (self.bin / "pi").symlink_to(cli)
        jiti = loader / "node_modules/jiti"
        jiti.mkdir(parents=True)
        # Reaching this code proves discovery without executing Pi or its extension.
        (jiti / "index.js").write_text('throw new Error("fictional loader reached");\n')
        result = self.loader_attempt(candidate)
        self.assertIn("fictional loader reached", result.stderr)
        explicit = self.loader_attempt(candidate, "--pi-loader-root", str(loader))
        self.assertIn("fictional loader reached", explicit.stderr)

    def test_identity_reads_do_not_run_candidate_filters_or_refresh_index(self):
        candidate = self.valid_candidate()
        def git(*args):
            subprocess.run(["git", "-C", str(candidate), *args], env=self.env,
                           capture_output=True, text=True, timeout=10, check=True)
        git("init", "-qb", "main", "--template=")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "commit.gpgsign", "false")
        git("config", "core.hooksPath", str(self.root / "no-hooks"))
        (candidate / ".gitattributes").write_text("*.ts filter=fixture\n")
        git("add", ".")
        git("commit", "-qm", "fictional candidate")
        marker = self.root / "candidate-helper-fired"
        helper = self.root / "candidate-helper"
        helper.write_text("#!/bin/sh\nprintf invoked > " + shlex.quote(str(marker)) + "\nexit 0\n")
        helper.chmod(0o755)
        git("config", "core.fsmonitor", str(helper))
        git("config", "filter.fixture.clean", str(helper))
        (candidate / "src/runs/shared/worktree.ts").write_text("// dirty fictional source\n")
        index = candidate / ".git/index"
        before = index.read_bytes(), index.stat().st_mtime_ns
        loader = self.root / "pi-package"
        jiti = loader / "node_modules/jiti"
        jiti.mkdir(parents=True)
        (loader / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-coding-agent"}))
        (jiti / "index.js").write_text(
            'if (Object.hasOwn(process.env, "SYNTHETIC_PROVIDER_CREDENTIAL")) throw new Error("ambient environment leaked");\n'
            'module.exports = {createJiti: () => ({import: async () => {throw new Error("candidate import boundary reached");}})};\n')
        self.env["SYNTHETIC_PROVIDER_CREDENTIAL"] = "fictional-value"
        result = self.loader_attempt(candidate, "--pi-loader-root", str(loader))
        self.assertIn("candidate import boundary reached", result.stderr)
        self.assertNotIn("ambient environment leaked", result.stderr)
        self.assertFalse(marker.exists(), "identity reads executed candidate configuration")
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)

    def test_missing_loader_never_invokes_brew(self):
        candidate = self.valid_candidate()
        pi = self.bin / "pi"
        pi.write_text("#!/bin/sh\nexit 99\n")
        pi.chmod(0o755)
        result = self.loader_attempt(candidate)
        self.assertIn("pass --pi-loader-root explicitly", result.stderr)
        result = self.loader_attempt(candidate, "--pi-loader-root", str(self.root / "missing-loader"))
        self.assertIn("pass --pi-loader-root explicitly", result.stderr)

    def test_missing_candidate_modules_fail_before_loading(self):
        candidate = self.root / "candidate"
        shared = candidate / "src/runs/shared"
        shared.mkdir(parents=True)
        (candidate / "package.json").write_text(json.dumps({"name": "pi-subagents", "version": "fixture"}))
        (shared / "worktree.ts").write_text('throw new Error("invalid candidate imported");\n')
        result = self.run_smoke("--candidate-root", str(candidate))
        self.assertNotIn("invalid candidate imported", result.stderr)
        self.assertIn("worktree-cleanup-plan.ts", result.stderr)


if __name__ == "__main__":
    unittest.main()
