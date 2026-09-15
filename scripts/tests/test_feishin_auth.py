"""Exercise the real fallback auth block with fictional data and stubbed I/O."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from urllib.parse import parse_qs


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "stow/sketchybar/.config/sketchybar/plugins/feishin.sh"


class FeishinAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.cache = self.root / "auth.json"
        self.marker = self.root / "must-not-exist"
        self.requests = self.root / "requests.jsonl"
        self.password = 'fictional "password" \\ &+=% 雪\nsecond line\n\n'  # betterleaks:allow (fictional encoding fixture)
        self.username = 'fixture "user" \\ &+=% 雪\n'
        self.token = 'fixture-token &+=% \\"\n$(touch ' + str(self.marker) + ')\n'
        self.salt = 'fixture-salt &+=% \\"\n`touch ' + str(self.marker) + '`\n'
        self.response = {"subsonicToken": self.token, "subsonicSalt": self.salt,
                         "unneeded": "must not be cached"}
        self.fixture = self.root / "fixture.json"
        self.write_fixture()
        stub = """#!/usr/bin/python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FIXTURE_ROOT"])
fixture = json.loads((root / "fixture.json").read_text())
name = pathlib.Path(sys.argv[0]).name
if name == "security":
    (root / "keychain-called").touch()
    sys.stdout.write(fixture["password"] + "\\n")
elif name == "curl":
    args = sys.argv[1:]
    data = sys.stdin.read()
    with (root / "requests.jsonl").open("a") as f:
        f.write(json.dumps({"args": args, "data": data}) + "\\n")
    if "https://fixture.invalid/auth/login" in args:
        print(json.dumps(fixture["response"]))
    elif "https://fixture.invalid/rest/getNowPlaying" in args:
        print('{"subsonic-response":{"nowPlaying":{}}}')
    else:
        sys.exit(90)
"""
        for name in ("security", "curl", "sketchybar"):
            target = self.bin / name
            target.write_text(stub)
            target.chmod(0o755)
        source = PLUGIN.read_text()
        start = source.index("# Keep only valid token fields.")
        end = source.index('\nif [ -n "$CURRENT_SONG"', start)
        self.script = self.root / "auth.sh"
        self.script.write_text(source[start:end])
        self.env = {"HOME": str(self.root), "PATH": str(self.bin) + ":/opt/homebrew/bin:/usr/bin:/bin",
                    "FIXTURE_ROOT": str(self.root), "AUTH_CACHE": str(self.cache),
                    "AUTH_MAX_AGE": "300", "USERNAME": self.username, "NAME": "fixture",
                    "NAVIDROME_URL": "https://fixture.invalid"}
        self.assertIsNotNone(shutil.which("jq", path=self.env["PATH"]))

    def write_fixture(self):
        self.fixture.write_text(json.dumps({"password": self.password, "response": self.response}))

    def run_auth(self):
        result = subprocess.run(["/bin/bash", str(self.script)], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists(), "cache material executed a command")
        return [json.loads(line) for line in self.requests.read_text().splitlines()]

    def assert_form(self, request):
        self.assertEqual(parse_qs(request["data"].strip()), {
            "u": [self.username], "t": [self.token], "s": [self.salt],
            "v": ["1.8.0"], "c": ["SketchyBar"], "f": ["json"]})
        args = " ".join(request["args"])
        for secret in (self.password, self.token, self.salt):
            self.assertNotIn(secret, args)

    def test_special_characters_round_trip_and_private_cache(self):
        requests = self.run_auth()
        self.assertEqual(json.loads(requests[0]["data"]),
                         {"username": self.username, "password": self.password})
        self.assertNotIn(self.password, " ".join(requests[0]["args"]))
        self.assert_form(requests[1])
        self.assertEqual(json.loads(self.cache.read_text()),
                         {"subsonicToken": self.token, "subsonicSalt": self.salt})
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)
        (self.root / "keychain-called").unlink()
        self.requests.unlink()
        requests = self.run_auth()
        self.assertEqual(len(requests), 1)
        self.assert_form(requests[0])
        self.assertFalse((self.root / "keychain-called").exists())

    def test_legacy_shell_cache_is_not_executed(self):
        self.cache.write_text(f'SUBSONIC_TOKEN=$(touch "{self.marker}")\nSUBSONIC_SALT=fake\n')
        self.cache.chmod(0o644)
        self.assertEqual(len(self.run_auth()), 2)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_invalid_cache_shapes_force_authentication(self):
        for value in (None, [], "shell text", {}, {"subsonicToken": "token"},
                      {"subsonicToken": [], "subsonicSalt": "salt"},
                      {"subsonicToken": "token", "subsonicSalt": ""}):
            with self.subTest(value=value):
                self.cache.write_text(json.dumps(value))
                self.cache.chmod(0o600)
                self.requests.unlink(missing_ok=True)
                self.assertEqual(len(self.run_auth()), 2)

    def test_expired_regular_cache_forces_authentication(self):
        self.cache.write_text(json.dumps(self.response))
        self.cache.chmod(0o600)
        os.utime(self.cache, (1, 1))
        self.assertEqual(len(self.run_auth()), 2)

    def test_cache_symlink_is_replaced_without_following_it(self):
        outside = self.root / "outside.json"
        old = json.dumps({"subsonicToken": "old", "subsonicSalt": "old"})
        outside.write_text(old)
        os.utime(outside, (1, 1))
        self.cache.symlink_to(outside)
        self.assertEqual(len(self.run_auth()), 2)
        self.assertEqual(outside.read_text(), old)
        self.assertFalse(self.cache.is_symlink())

    def test_permissive_valid_json_cache_is_replaced_privately(self):
        self.cache.write_text(json.dumps(self.response))
        self.cache.chmod(0o644)
        self.assertEqual(len(self.run_auth()), 2)
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)

    def test_cache_directory_symlink_is_replaced_without_writing_to_target(self):
        outside = self.root / "outside-dir"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("fictional unrelated data")
        self.cache.symlink_to(outside, target_is_directory=True)
        self.assertEqual(len(self.run_auth()), 2)
        self.assertFalse(self.cache.is_symlink())
        self.assertEqual(self.cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "fictional unrelated data")

    def test_actual_cache_directory_never_receives_token_files(self):
        self.cache.mkdir()
        self.assertEqual(len(self.run_auth()), 2)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(list(self.root.glob("auth.json.*")), [])

    def test_missing_auth_salt_stops_before_now_playing_and_cache_write(self):
        self.response = {"subsonicToken": "fixture-token"}
        self.write_fixture()
        self.assertEqual(len(self.run_auth()), 1)
        self.assertFalse(self.cache.exists())


if __name__ == "__main__":
    unittest.main()
