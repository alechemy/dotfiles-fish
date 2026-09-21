#!/usr/bin/env python3
"""Pi settings merge contract, using copied scripts and a disposable HOME."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]


class MergePiSettingsTests(unittest.TestCase):
    kind = "settings"
    mode = 0o644
    args = []

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.target = self.home / f".pi/agent/{self.kind}.json"
        self.target.parent.mkdir(parents=True)
        self.script = self.root / "repo/scripts/merge-pi-settings.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(REPO / "scripts/merge-pi-settings.sh", self.script)
        self.fragment = self.root / f"repo/stow/pi/.pi/agent/{self.kind}.fragment.json"
        self.fragment.parent.mkdir(parents=True)
        self.fragment.write_text('{"theme":"dark"}')
        self.target.write_text('{"runtimeOwned":{"keep":true},"theme":"light"}')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}")

    def run_merge(self):
        return subprocess.run(["/bin/bash", str(self.script), *self.args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def assert_failed_unchanged(self, original):
        result = self.run_merge()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(f"Merged Pi {self.kind}", result.stdout)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(list(self.target.parent.glob(f".{self.kind}.json.*")), [])

    def test_missing_target_creates_settings_then_noops(self):
        self.target.unlink()
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.target.read_text()), {"theme": "dark"})
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), self.mode)
        before = self.target.stat()
        content = self.target.read_bytes()
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), content)
        self.assertEqual(self.target.stat().st_ino, before.st_ino)
        self.assertEqual(self.target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_recursive_merge_preserves_runtime_and_replaces_arrays(self):
        self.target.write_text(json.dumps({"lastChangelogVersion": "fixture",
            "nested": {"keep": True, "managed": "old"},
            "packages": ["npm:removed", "npm:kept"], "enabledModels": ["old", "kept"]}))
        self.fragment.write_text(json.dumps({"nested": {"managed": "new"},
            "packages": ["npm:kept"], "enabledModels": ["kept", "new"]}))
        before = self.target.stat().st_ino
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.target.read_text()), {
            "lastChangelogVersion": "fixture", "nested": {"keep": True, "managed": "new"},
            "packages": ["npm:kept"], "enabledModels": ["kept", "new"]})
        self.assertNotEqual(self.target.stat().st_ino, before)
        self.fragment.write_text('{"packages":[],"enabledModels":[]}')
        self.assertEqual(self.run_merge().returncode, 0)
        self.assertEqual(json.loads(self.target.read_text())["packages"], [])
        self.assertEqual(json.loads(self.target.read_text())["enabledModels"], [])

    def test_invalid_json_inputs_fail_closed(self):
        for path in (self.target, self.fragment):
            for invalid in ('', ' \n', '{', '{}\n{}', '{}\nnull', '[]', 'null', 'true', '42', '"text"'):
                with self.subTest(input=path.name, invalid=invalid):
                    self.target.write_text('{"runtimeOwned":true}')
                    self.fragment.write_text('{"theme":"dark"}')
                    path.write_text(invalid)
                    self.assert_failed_unchanged(self.target.read_bytes())

    def test_missing_fragment_fails_closed(self):
        self.fragment.unlink()
        self.assert_failed_unchanged(self.target.read_bytes())

    def test_symlinks_including_dangling_are_rejected(self):
        for path in (self.target, self.fragment):
            for dangling in (False, True):
                with self.subTest(input=path.name, dangling=dangling):
                    original = self.target.read_bytes()
                    saved = path.read_bytes()
                    referent = self.root / "referent"
                    if not dangling:
                        referent.write_bytes(saved)
                    path.unlink()
                    path.symlink_to(referent)
                    result = self.run_merge()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(path.is_symlink())
                    if not dangling:
                        self.assertEqual(referent.read_bytes(), saved)
                        referent.unlink()
                    else:
                        self.assertFalse(referent.exists())
                    path.unlink()
                    path.write_bytes(saved)
                    self.assertEqual(self.target.read_bytes(), original)

    @unittest.skipIf(os.geteuid() == 0, "root can read mode-000 files")
    def test_unreadable_inputs_fail_closed(self):
        for path in (self.target, self.fragment):
            with self.subTest(input=path.name):
                original = self.target.read_bytes()
                path.chmod(0)
                try:
                    result = self.run_merge()
                finally:
                    path.chmod(0o600)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.target.read_bytes(), original)

    def test_directories_and_fifos_are_rejected_without_blocking(self):
        for path in (self.target, self.fragment):
            for kind in ("directory", "fifo"):
                with self.subTest(input=path.name, kind=kind):
                    original = self.target.read_bytes()
                    saved = path.read_bytes()
                    path.unlink()
                    if kind == "directory":
                        path.mkdir()
                    else:
                        os.mkfifo(path)
                    try:
                        result = self.run_merge()
                        self.assertNotEqual(result.returncode, 0)
                        self.assertTrue(path.is_dir() if kind == "directory" else stat.S_ISFIFO(path.stat().st_mode))
                    finally:
                        if path.is_dir():
                            path.rmdir()
                        else:
                            path.unlink()
                        path.write_bytes(saved)
                    self.assertEqual(self.target.read_bytes(), original)

    def test_chmod_and_mv_failures_preserve_target_and_clean_temp(self):
        for command in ("chmod", "mv"):
            with self.subTest(command=command):
                stub = self.bin / command
                stub.write_text('#!/bin/sh\nexit 1\n')
                stub.chmod(0o755)
                self.assert_failed_unchanged(self.target.read_bytes())
                stub.unlink()

    def test_mv_uses_same_directory_complete_file(self):
        stub = self.bin / "mv"
        stub.write_text('''#!/bin/sh
[ "$(dirname "$1")" = "$(dirname "$2")" ] || exit 20
jq -e '.theme == "dark" and .runtimeOwned.keep == true' "$1" >/dev/null || exit 21
[ -f "$2" ] || exit 22
exec /bin/mv "$@"
''')
        stub.chmod(0o755)
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tracked_fragment_matches_approved_snapshot(self):
        actual = json.loads((REPO / "stow/pi/.pi/agent/settings.fragment.json").read_text())
        self.assertEqual(actual, {
            "theme": "dark", "defaultProvider": "openai-codex", "defaultModel": "gpt-6-astra",
            "defaultThinkingLevel": "high", "defaultProjectTrust": "ask",
            "enableInstallTelemetry": False, "hideThinkingBlock": True,
            "tuiMode": "fullscreen", "fullscreenExitOutput": "transcript",
            "packages": ["npm:@upstash/context7-pi@0.1.2", "./local/copilot-delegation/node_modules/pi-subagents", "npm:pi-web-access@0.27.0"],
            "enabledModels": ["openai-codex/gpt-5.6-sol", "github-copilot/claude-opus-5",
                              "omlx/Qwen3.8-27B-oQ8e-mtp", "openai-codex/gpt-6-astra",
                              "github-copilot/claude-fable-5.1", "github-copilot/gemini-3.8-flash"],
            "subagents": self.approved_subagent_settings()})

    @staticmethod
    def approved_subagent_settings():
        providers = {}
        for provider in ("github-copilot", "openai-codex"):
            providers[provider] = {
                role: {"model": f"{provider}/{model}"}
                for role, model in {
                    "delegate": "gpt-5.6-luna", "local-editor": "gpt-5.6-luna",
                    "oracle": "gpt-6-astra", "researcher": "gpt-5.6-luna", "reviewer": "gpt-5.6-sol",
                    "scout": "gpt-5.6-luna", "worker": "gpt-5.6-luna",
                }.items()
            }
        local_model = "omlx/Qwen3.8-27B-oQ8e-mtp"
        providers["github-copilot"]["local-editor"] = {
            "model": local_model, "fallbackModels": [], "thinking": False,
            "defaultContext": "fresh", "tools": ["read", "grep", "find", "ls", "edit", "write"],
            "extensions": [], "output": False,
            "description": "Local editor for small, well-specified changes under a Copilot root. Give it explicit files or a narrow source area and acceptance criteria; the parent reviews and validates the result.",
        }
        providers["github-copilot"]["scout"] = {
            "model": local_model, "fallbackModels": [], "thinking": False,
            "defaultContext": "fresh", "tools": ["read", "grep", "find", "ls"],
            "extensions": [], "output": False,
            "description": "Local scout for short, mechanical retrieval under a Copilot root. Use it for one narrow source area or question, such as tracing one caller chain, locating tests, or extracting configuration facts. Do not use it for merge-conflict analysis, cross-cutting synthesis, architecture, or broad repository reconstruction; keep those tasks on Copilot. Return a short answer with file and line references.",
        }
        return {
            "worktreeProvider": "worktrunk",
            "modelScope": {
                "allow": ["inherit-provider"], "enforce": True, "strict": True,
                "localDelegation": {"rootProvider": "github-copilot", "agents": {
                    "scout": [local_model], "local-editor": [local_model]}},
            },
            "agentOverridesByProvider": providers,
        }

    def test_managed_subagent_routing_preserves_unrelated_runtime_settings(self):
        self.target.write_text(json.dumps({"subagents": {
            "runtimeOwned": {"keep": True}, "modelScope": {"enforce": False},
            "agentOverridesByProvider": {"github-copilot": {
                "scout": {"model": "github-copilot/old", "fallbackModels": ["github-copilot/old"]},
            }},
        }}))
        self.fragment.write_text(json.dumps({"subagents": self.approved_subagent_settings()}))
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.approved_subagent_settings()
        expected["runtimeOwned"] = {"keep": True}
        self.assertEqual(json.loads(self.target.read_text())["subagents"], expected)


class MergePiModelsTests(MergePiSettingsTests):
    kind = "models"
    mode = 0o600
    args = ["--models"]

    def test_tracked_fragment_matches_approved_snapshot(self):
        actual = json.loads((REPO / "stow/pi/.pi/agent/models.fragment.json").read_text())
        self.assertEqual(actual, {"providers": {
            "github-copilot": {"modelOverrides": {"gpt-5.6-sol": {"contextWindow": 272000}}},
            "omlx": {"modelOverrides": {"Qwen3.8-27B-oQ8e-mtp": {
                "reasoning": True, "compat": {
                    "supportsDeveloperRole": False, "thinkingFormat": "qwen-chat-template"},
            }}},
        }})

    def test_tracked_overrides_apply_to_matching_custom_models(self):
        original = {"providers": {"local": {
            "apiKey": "fixture-credential", "baseUrl": "http://127.0.0.1:8000/v1",
            "models": [
                {"id": "target", "reasoning": False, "maxTokens": 32768,
                 "compat": {"supportsStore": False}},
                {"id": "other", "reasoning": False},
            ],
            "modelOverrides": {"other": {"reasoning": True}},
        }}}
        override = {"reasoning": True, "compat": {"thinkingFormat": "qwen-chat-template"}}
        self.target.write_text(json.dumps(original))
        self.fragment.write_text(json.dumps({"providers": {"local": {
            "modelOverrides": {"target": override, "missing": {"reasoning": True}},
        }}}))
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = original["providers"]["local"]
        expected["modelOverrides"].update({"target": override, "missing": {"reasoning": True}})
        expected["models"][0]["reasoning"] = True
        expected["models"][0]["compat"]["thinkingFormat"] = "qwen-chat-template"
        self.assertEqual(json.loads(self.target.read_text()), original)
        before = self.target.stat()
        self.assertEqual(self.run_merge().returncode, 0)
        self.assertEqual(self.target.stat().st_ino, before.st_ino)

    def test_identical_models_content_restores_private_permissions(self):
        self.assertEqual(self.run_merge().returncode, 0)
        self.target.chmod(0o644)
        before = self.target.stat()
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(self.target.stat().st_ino, before.st_ino)
        self.assertEqual(self.target.stat().st_mtime_ns, before.st_mtime_ns)

    def test_override_preserves_local_provider_and_other_model_fields(self):
        original = {"providers": {
            "omlx": {"apiKey": "fixture-local-credential", "models": [{"id": "fixture"}]},
            "github-copilot": {"modelOverrides": {
                "gpt-5.6-sol": {"contextWindow": 1050000, "maxTokens": 32000},
                "other": {"contextWindow": 128000}}}}}
        self.target.write_text(json.dumps(original))
        shutil.copy2(REPO / "stow/pi/.pi/agent/models.fragment.json", self.fragment)
        result = self.run_merge()
        self.assertEqual(result.returncode, 0, result.stderr)
        original["providers"]["github-copilot"]["modelOverrides"]["gpt-5.6-sol"]["contextWindow"] = 272000
        original["providers"]["omlx"]["modelOverrides"] = {
            "Qwen3.8-27B-oQ8e-mtp": {
                "reasoning": True, "compat": {
                    "supportsDeveloperRole": False, "thinkingFormat": "qwen-chat-template"},
            },
        }
        self.assertEqual(json.loads(self.target.read_text()), original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
