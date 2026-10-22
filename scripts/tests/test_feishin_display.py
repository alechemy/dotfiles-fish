import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "stow/sketchybar/.config/sketchybar/plugins/feishin.sh"


class FeishinDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        lib = self.root / "plugins/lib"
        lib.mkdir(parents=True)
        (lib / "display_mode.sh").write_text(
            'display_mode() { printf "%s\\n" "$DISPLAY_MODE"; }\n')
        self.calls = self.root / "calls.jsonl"
        self.stub("sketchybar", '''#!/usr/bin/python3
import json, os, sys
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
''')
        self.stub("pgrep", '''#!/bin/sh
[ "$APP_RUNNING" = 1 ] && [ "$2" = Feishin ]
''')
        self.env = {
            "HOME": str(self.root),
            "PATH": str(self.bin) + ":/opt/homebrew/bin:/usr/bin:/bin",
            "CONFIG_DIR": str(self.root),
            "CALL_LOG": str(self.calls),
            "NAME": "feishin",
            "DISPLAY_MODE": "compact",
            "APP_RUNNING": "0",
        }

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def run_plugin(self):
        commands = '''
function /opt/homebrew/bin/nowplaying-cli() {
    printf '%s\\n' '{"artist":"Fixture artist","title":"Fixture track","playbackRate":1}'
}
source "$1"
'''
        result = subprocess.run(["/bin/bash", "-c", commands, "fixture", str(PLUGIN)],
                                env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_laptop_hides_nothing_playing(self):
        calls = self.run_plugin()
        self.assertEqual(len(calls), 1)
        self.assertIn("drawing=off", calls[0])

    def test_external_display_keeps_nothing_playing(self):
        self.env["DISPLAY_MODE"] = "spacious"
        calls = self.run_plugin()
        self.assertIn("drawing=on", calls[0])
        self.assertIn("label= Nothing playing", calls[0])

    def test_playback_restores_item_after_hiding(self):
        self.run_plugin()
        self.calls.unlink()
        self.env["APP_RUNNING"] = "1"
        calls = self.run_plugin()
        self.assertIn(["--set", "feishin", "drawing=on"], calls)
        self.assertIn("label= Fixture track", calls[-1])

    def test_docking_restores_idle_item_after_hiding(self):
        self.run_plugin()
        self.calls.unlink()
        self.env["DISPLAY_MODE"] = "spacious"
        self.assertIn("drawing=on", self.run_plugin()[0])


if __name__ == "__main__":
    unittest.main()
