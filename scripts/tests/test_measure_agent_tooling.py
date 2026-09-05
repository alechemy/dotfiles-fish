#!/usr/bin/env python3
"""Synthetic source-only fixtures for measure-agent-tooling.py."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / "measure-agent-tooling.py"
SPEC = importlib.util.spec_from_file_location("measure_agent_tooling", HELPER)
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def skill(name, description="A fictional skill.", manual=False):
    flag = "disable-model-invocation: true\n" if manual else ""
    return f"---\nname: {name}\ndescription: {description}\n{flag}---\nIgnored body.\n"


class MeasureAgentToolingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.packages = self.root / "node_modules"
        self.shared = self.repo / "stow/agents/.agents/skills"
        self.fragment = self.repo / "stow/pi/.pi/agent/settings.fragment.json"
        self.put(self.fragment, json.dumps({"packages": [f"npm:{n}@{v}" for n, v in measurement.PACKAGES.items()]}))
        for name, version in measurement.PACKAGES.items():
            manifest = {"name": name, "version": version, "pi": {"extensions": ["./index.ts"]}}
            if name != "pi-web-access":
                manifest["pi"]["skills"] = ["./skills"]
                self.put(self.packages / name / "skills" / "one" / "SKILL.md", skill(name.split("/")[-1]))
            self.put(self.packages / name / "package.json", json.dumps(manifest))
        self.put(self.shared / "visible" / "SKILL.md", skill("visible", "A café 🐈."))
        for name in ("recall", "handoff", "simplify-review", "grill-me", "teach"):
            self.put(self.shared / name / "SKILL.md", skill(name, manual=True))
        sources = {
            "@upstash/context7-pi/lib/prompts.ts": 'export const RESOLVE_LIBRARY_ID_DESCRIPTION = "Find";\nexport const QUERY_DOCS_DESCRIPTION = `Docs`;',
            "pi-subagents/src/extension/tool-description.ts": "\n".join(
                f'const {name} = "Guidance";' for name in (
                    "WORKFLOW_RESOURCE_GUIDANCE", "AGENT_SELECTION_GUIDANCE", "WORKFLOW_SCRIPT_PORTABILITY_GUIDANCE",
                    "WORKFLOW_LANES_GUIDANCE", "WORKFLOW_HOST_GUIDANCE", "EXTERNAL_CLI_RUNNER_GUIDANCE"))
                + '\nexport const DEFAULT_SUBAGENT_TOOL_DESCRIPTION = `Delegate ${AGENT_SELECTION_GUIDANCE}`;',
            "pi-subagents/src/runs/background/wait-tool.ts": 'const description = `Wait`;',
            "pi-subagents/src/intercom/native-supervisor-channel.ts": 'export const NATIVE_SUPERVISOR_TOOL_NAME = "subagent_supervisor";\n'
                'function buildParentSupervisorTool() { return { name: NATIVE_SUPERVISOR_TOOL_NAME, label: "Supervisor", '
                'description: "Reply to a child.", parameters: {} }; }',
            "pi-web-access/index.ts": "\n".join(
                f'name: toolNames.{key}, label: "Label", description: `Fetch ${{storedContentSources}}`, parameters: {{}}'
                for key in ("webSearch", "sourceCheck", "fetchContent", "getSearchContent")),
        }
        for relative, text in sources.items():
            self.put(self.packages / relative, text)
        self.hashes = {relative: hashlib.sha256(text.encode()).hexdigest() for relative, text in sources.items()}
        self.addCleanup(patch.stopall)
        patch.object(measurement, "SOURCE_SHA256", self.hashes).start()

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def measure(self):
        return measurement.measure(self.repo, self.packages)

    def test_default_projection_manual_exclusion_and_utf8(self):
        result = self.measure()
        self.assertEqual(result["tool_descriptions"]["count"], 9)
        self.assertEqual(result["advertised_skill_descriptions"]["count"], 3)
        self.assertEqual(result["manual_skills_excluded"], ["grill-me", "handoff", "recall", "simplify-review", "teach"])
        visible = next(row for row in result["skills"] if row["name"] == "visible")
        self.assertEqual((visible["characters"], visible["utf8_bytes"]), (9, 13))
        self.assertEqual(result, self.measure())

    def test_session_start_parent_supervisor_description_is_included(self):
        tools = self.measure()["tools"]
        supervisor = next((row for row in tools if row["name"] == "subagent_supervisor"), None)
        self.assertIsNotNone(supervisor)
        self.assertEqual(supervisor, {"package": "pi-subagents", "name": "subagent_supervisor",
                                      "characters": 17, "utf8_bytes": 17})
        self.assertEqual(sum(row["package"] == "pi-subagents" for row in tools), 3)

    def test_source_drift_fails(self):
        self.put(self.packages / "pi-web-access/index.ts", "throw new Error('never execute');")
        with self.assertRaisesRegex(ValueError, "source drift"):
            self.measure()

    def test_installed_version_mismatch_fails(self):
        path = self.packages / "pi-web-access/package.json"
        manifest = json.loads(path.read_text())
        manifest["version"] = "99.0.0"
        self.put(path, json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            self.measure()

    def test_declared_package_filters_require_review(self):
        fragment = json.loads(self.fragment.read_text())
        fragment["packages"][0] = {"source": fragment["packages"][0], "skills": []}
        self.put(self.fragment, json.dumps(fragment))
        with self.assertRaisesRegex(ValueError, "set/filters changed"):
            self.measure()

    def test_manifest_omitted_skills_does_not_discover_conventional_directory(self):
        self.put(self.packages / "pi-web-access/skills/ignored/SKILL.md", skill("not-discovered"))
        self.assertNotIn("not-discovered", [row["name"] for row in self.measure()["skills"]])

    def test_manifest_patterns_require_review(self):
        path = self.packages / "pi-web-access/package.json"
        manifest = json.loads(path.read_text())
        manifest["pi"]["skills"] = ["./skills", "!./skills/private"]
        self.put(path, json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "manifest/filters require review"):
            self.measure()

    def test_discovery_stops_at_skill_root_and_skips_shared_root_markdown(self):
        self.put(self.shared / "README.md", "Not a skill.")
        self.put(self.shared / "visible/references/SKILL.md", "Do not parse this reference.")
        self.put(self.shared / ".hidden/SKILL.md", "Hidden.")
        self.put(self.shared / "node_modules/example/SKILL.md", "Dependency.")
        self.put(self.packages / "pi-subagents/skills/top.md", skill("package-root"))
        self.put(self.shared / "group/nested/SKILL.md", skill("nested"))
        names = [row["name"] for row in self.measure()["skills"]]
        self.assertEqual(set(names), {"visible", "nested", "package-root", "pi-subagents", "context7-pi"})

    def test_ignore_rules_symlinks_and_duplicate_skills_fail(self):
        for filename in (".gitignore", ".ignore", ".fdignore"):
            with self.subTest(filename=filename):
                ignore = self.shared / filename
                self.put(ignore, "visible/\n")
                try:
                    with self.assertRaisesRegex(ValueError, "ignore rules"):
                        self.measure()
                finally:
                    ignore.unlink()
        link = self.shared / "linked"
        link.symlink_to(self.shared / "visible", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            self.measure()
        link.unlink()
        self.put(self.shared / "duplicate/SKILL.md", skill("visible"))
        with self.assertRaisesRegex(ValueError, "collision"):
            self.measure()

    def test_source_paths_reject_traversal_symlinks_and_missing_files(self):
        for relative in ("../elsewhere", "/etc/passwd", "missing"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                measurement.source_file(self.root, relative)
        (self.root / "link").symlink_to(self.fragment)
        with self.assertRaisesRegex(ValueError, "Symlinked"):
            measurement.source_file(self.root, "link")

    def test_literal_decodes_only_known_strings_and_substitutions(self):
        self.assertEqual(measurement.literal_after('x = `café\\n\\` ${known}`;', "x =", {"known": "ok"}), "café\n` ok")
        for text in ('x = call();', 'x = `${fetch()}`;', 'x = "a" + "b";', 'x = "\\u0041";', 'x = "a"; x = "b";', 'x = `unfinished'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                measurement.literal_after(text, "x =")

    def test_frontmatter_folding_and_literal_newlines(self):
        for header, expected in ((">-", "First line second.\nNext paragraph."), ("|", "First line\nsecond.\n\nNext paragraph.\n")):
            text = skill("example", f"{header}\n  First line\n  second.\n\n  Next paragraph.")
            self.assertEqual(measurement.skill_description(text), ("example", expected, False))

    def test_yaml_nonstring_plain_scalars_require_review(self):
        for value in ("1e3", "0x10", "0o17", ".nan", ".NaN", ".NAN", ".inf", "+.Inf", "-.INF",
                      "-2.5e+4", ".5E-2", "123", "+12", "1.", "true", "false", "null", "~"):
            for field in ("name", "description"):
                with self.subTest(value=value, field=field):
                    text = skill("example", value) if field == "description" else skill(value)
                    with self.assertRaises(ValueError):
                        measurement.skill_description(text)

    def test_block_scalar_descriptions_remain_strings(self):
        for value in ("1e3", "true", "null"):
            with self.subTest(value=value):
                self.assertEqual(measurement.skill_description(skill("example", f">-\n  {value}")),
                                 ("example", value, False))

    def test_unsupported_frontmatter_fails(self):
        for text in (skill("example", '"quoted"'), skill("example", "&alias text"),
                     skill("example", "A line.\n  continuation"), skill("example", "false"),
                     skill("example").replace("description:", "name: duplicate\ndescription:"),
                     skill("example").replace("---\nIgnored", "disable-model-invocation: yes\n---\nIgnored")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                measurement.skill_description(text)


if __name__ == "__main__":
    unittest.main()
