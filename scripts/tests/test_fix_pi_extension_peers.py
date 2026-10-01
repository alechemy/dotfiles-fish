import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "fix_pi_extension_peers", Path(__file__).resolve().parents[1] / "fix-pi-extension-peers.py")
peers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(peers)


class ExtensionPeersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = []
        packages = []
        for relative, name, version, _, _ in peers.PACKAGES:
            manifest = {
                "name": name, "version": version,
                "dependencies": {"typebox": "1.1.38", "fixture": "1.0.0"},
                "peerDependencies": {"@earendil-works/pi-ai": "*"},
            }
            original = (json.dumps(manifest, indent=2) + "\n").encode()
            path = self.root / relative / "package.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(original)
            self.paths.append(path)
            packages.append((relative, name, version, hashlib.sha256(original).hexdigest(),
                             hashlib.sha256(peers.patched_manifest(original)).hexdigest()))
        self.patch = patch.object(peers, "PACKAGES", tuple(packages))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_correction_preserves_other_fields_and_original_bytes(self):
        originals = [path.read_bytes() for path in self.paths]
        self.assertEqual(peers.apply(self.root, check=True), 2)
        self.assertEqual([path.read_bytes() for path in self.paths], originals)
        self.assertEqual(peers.apply(self.root), 2)
        for path, original in zip(self.paths, originals):
            manifest = json.loads(path.read_bytes())
            self.assertNotIn("typebox", manifest["dependencies"])
            self.assertEqual(manifest["dependencies"], {"fixture": "1.0.0"})
            self.assertEqual(manifest["peerDependencies"]["typebox"], "*")
            self.assertEqual(path.with_name("package.json.before-host-peers").read_bytes(), original)
        stats = [path.stat().st_mtime_ns for path in self.paths]
        self.assertEqual(peers.apply(self.root), 0)
        self.assertEqual(peers.apply(self.root, check=True), 0)
        self.assertEqual([path.stat().st_mtime_ns for path in self.paths], stats)

    def test_drift_in_second_package_leaves_both_untouched(self):
        self.paths[1].write_bytes(self.paths[1].read_bytes() + b"\n")
        originals = [path.read_bytes() for path in self.paths]
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            peers.apply(self.root)
        self.assertEqual([path.read_bytes() for path in self.paths], originals)
        self.assertFalse(self.paths[0].with_name("package.json.before-host-peers").exists())

    def test_symlinked_manifest_is_rejected(self):
        path = self.paths[1]
        target = self.root / "manifest.json"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular manifest"):
            peers.apply(self.root)
        self.assertIn("typebox", json.loads(self.paths[0].read_bytes())["dependencies"])

    def test_existing_conflicting_backup_is_preserved(self):
        backup = self.paths[1].with_name("package.json.before-host-peers")
        backup.write_bytes(b"unrelated")
        with self.assertRaisesRegex(ValueError, "Conflicting backup"):
            peers.apply(self.root)
        self.assertEqual(backup.read_bytes(), b"unrelated")
        self.assertIn("typebox", json.loads(self.paths[0].read_bytes())["dependencies"])


if __name__ == "__main__":
    unittest.main()
