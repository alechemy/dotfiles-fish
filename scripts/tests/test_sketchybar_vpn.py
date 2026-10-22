import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "stow/sketchybar/.config/sketchybar/sketchybarrc"
STATUS = CONFIG.parent / "plugins/vpn.sh"


class SketchybarVpnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.plugins = self.root / "plugins"
        (self.plugins / "lib").mkdir(parents=True)
        (self.plugins / "lib/display_mode.sh").write_text(
            'display_mode() { printf "%s\\n" "$DISPLAY_MODE"; }\n')
        self.overlay = self.plugins / "vpn_work.sh"
        self.calls = self.root / "calls.jsonl"
        self.stub("sketchybar", '''#!/usr/bin/python3
import json, os, sys
with open(os.environ["CALL_LOG"], "a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
''')
        self.stub("ifconfig", '#!/bin/sh\nprintf "%s\\n" "$IFCONFIG_OUTPUT"\n')
        self.stub("aerospace", '#!/bin/sh\nprintf "1\\n"\n')
        self.env = {
            "HOME": str(self.root),
            "PATH": str(self.bin) + ":/usr/bin:/bin",
            "CONFIG_DIR": str(self.root),
            "CALL_LOG": str(self.calls),
            "NAME": "vpn",
            "DISPLAY_MODE": "compact",
        }

    def stub(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o700)

    def run_script(self, path):
        result = subprocess.run(["/bin/bash", str(path)], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_bar_stays_above_native_menu_for_clicks(self):
        calls = self.run_script(CONFIG)
        settings = next(call for call in calls if call[0] == "--bar")
        self.assertIn("topmost=on", settings)

    def test_idle_work_toggle_remains_visible_on_laptop(self):
        self.overlay.touch()
        self.assertEqual(self.run_script(STATUS), [
            ["--set", "vpn", "icon=🔴", "label=", "drawing=on"]])

    def test_idle_work_toggle_remains_visible_when_docked(self):
        self.overlay.touch()
        self.env["DISPLAY_MODE"] = "spacious"
        self.assertIn("drawing=on", self.run_script(STATUS)[0])

    def test_non_work_laptop_hides_idle_indicator(self):
        self.assertEqual(self.run_script(STATUS), [["--set", "vpn", "drawing=off"]])

    def test_non_work_docked_keeps_idle_indicator(self):
        self.env["DISPLAY_MODE"] = "spacious"
        self.assertIn("drawing=on", self.run_script(STATUS)[0])

    def test_active_overlay_owns_indicator(self):
        self.overlay.write_text(
            'sketchybar --set "$NAME" icon=connected label=Proxy drawing=on\nexit 0\n')
        self.assertEqual(self.run_script(STATUS), [
            ["--set", "vpn", "icon=connected", "label=Proxy", "drawing=on"]])

    def test_nordvpn_status_survives_inactive_work_overlay(self):
        self.overlay.touch()
        self.env["IFCONFIG_OUTPUT"] = "inet 10.5.0.2"
        self.assertEqual(self.run_script(STATUS), [
            ["--set", "vpn", "icon=🌐", "label=NordVPN", "drawing=on"]])


if __name__ == "__main__":
    unittest.main()
