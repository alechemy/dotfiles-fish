#!/usr/bin/env python3
"""Exercise the catalog-digest exception against Betterleaks with fictional inputs."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
CATALOG = Path("scripts/browser-pilot/browser-catalog.json")


class BetterleaksConfigTests(unittest.TestCase):
    def test_only_exact_catalog_digest_lines_are_filtered(self):
        source = json.loads((REPO / CATALOG).read_text())
        digests = {name: values["press_key"] for name, values in source.items()}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temporary,
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
            templates = root / "templates"
            templates.mkdir()
            subprocess.run(["git", "init", "--quiet", f"--template={templates}"], cwd=root, env=env,
                           capture_output=True, check=True, timeout=10)
            catalog = root / CATALOG
            catalog.parent.mkdir(parents=True)
            data = {name: {"press_key": value} for name, value in digests.items()}
            report = root / "report.json"
            for allowed in (True, False):
                if not allowed:
                    data["mutation"] = {"press_key": hashlib.sha256(b"fictional negative control").hexdigest()}
                    data["wrong_field"] = {"api_key": next(iter(digests.values()))}
                    (root / "other.json").write_text(json.dumps(
                        {name: {"press_key": value} for name, value in digests.items()}, indent=2))
                catalog.write_text(json.dumps(data, indent=2))
                subprocess.run(["git", "add", "--", str(CATALOG), *([] if allowed else ["other.json"])],
                               cwd=root, env=env, capture_output=True, check=True, timeout=10)
                result = subprocess.run([
                    "betterleaks", "git", "--staged", "--no-banner", "--redact=85",
                    "--config", str(REPO / ".betterleaks.toml"), "--report-format=json",
                    "--report-path", str(report), ".",
                ], cwd=root, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0 if allowed else 1)
                findings = json.loads(report.read_text()) or []
                actual = sorted((row["RuleID"], row["File"]) for row in findings)
                expected = [] if allowed else sorted([
                    ("generic-api-key", str(CATALOG)), ("generic-api-key", str(CATALOG)),
                    ("generic-api-key", "other.json"), ("generic-api-key", "other.json"),
                ])
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
