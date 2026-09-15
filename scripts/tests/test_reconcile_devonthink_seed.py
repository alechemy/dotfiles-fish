import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
REL = Path("Library/Application Support/DEVONthink")


class ReconcileDevonthinkSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("reconcile-devonthink-seed.sh", "seed-devonthink-config.sh",
                     "normalize-devonthink-plist.py"):
            shutil.copy2(ROOT / "scripts" / name, self.repo / "scripts" / name)
        self.seed = self.repo / "stow/devonthink/_seed" / REL
        self.seed.mkdir(parents=True)
        (self.seed / "CustomMetaData.plist").write_bytes(plistlib.dumps([
            {"identifier": "FixtureField", "index": 1}]))
        (self.seed / "SmartRules.plist").write_bytes(plistlib.dumps([{"name": "Fixture rule"}]))
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        self.pgrep = fake_bin / "pgrep"
        self.pgrep.write_text("#!/bin/sh\nexit 1\n")
        self.pgrep.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PIPELINE_MANUAL="1",
                        PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])

    def run_script(self, name="reconcile-devonthink-seed.sh", *args):
        return subprocess.run(["/bin/bash", str(self.repo / "scripts" / name), *args],
                              env=self.env, capture_output=True, text=True, timeout=20)

    def test_apply_without_targets_includes_missing_and_differing_destinations(self):
        live = self.home / REL
        live.mkdir(parents=True)
        prior = plistlib.dumps([{"name": "Old fictional rule"}])
        (live / "SmartRules.plist").write_bytes(prior)
        report = self.run_script()
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("1 differ, 1 missing", report.stdout)
        applied = self.run_script("reconcile-devonthink-seed.sh", "--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertIn("2 applied", applied.stdout)
        self.assertEqual((live / "SmartRules.plist").read_bytes(),
                         (self.seed / "SmartRules.plist").read_bytes())
        self.assertTrue((live / "CustomMetaData.plist").exists())
        backups = list((self.home / ".local/state/devonthink/seed-backups").rglob("SmartRules.plist"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), prior)
        self.assertIn("0 differ, 0 missing", self.run_script().stdout)

    def test_invalid_metadata_fails_visibly_in_both_scripts(self):
        for target in ("seed", "live"):
            for value in (b"not a plist", plistlib.dumps({})):
                with self.subTest(target=target, value=value):
                    seed = self.seed / "CustomMetaData.plist"
                    original_seed = seed.read_bytes()
                    live = self.home / REL / "CustomMetaData.plist"
                    live.parent.mkdir(parents=True, exist_ok=True)
                    invalid = seed if target == "seed" else live
                    invalid.write_bytes(value)
                    for name in ("seed-devonthink-config.sh", "reconcile-devonthink-seed.sh"):
                        result = self.run_script(name)
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertNotIn("up to date", result.stdout)
                        self.assertEqual(invalid.read_bytes(), value)
                    seed.write_bytes(original_seed)
                    if live.exists():
                        live.unlink()

    def test_running_app_prevents_schema_mutation(self):
        self.pgrep.write_text("#!/bin/sh\nexit 0\n")
        result = self.run_script("seed-devonthink-config.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / REL / "CustomMetaData.plist").exists())
        result = self.run_script("reconcile-devonthink-seed.sh", "--apply")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
