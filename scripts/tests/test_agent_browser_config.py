import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class AgentBrowserConfigTests(unittest.TestCase):
    def test_homebrew_owns_cli_and_browser(self):
        brewfile = (ROOT / "Brewfile").read_text()
        self.assertIn('brew "agent-browser"\n', brewfile)
        self.assertIn('cask "google-chrome"\n', brewfile)

    def test_defaults_do_not_attach_or_persist_authentication(self):
        config = json.loads(
            (ROOT / "stow/agent-browser/.config/agent-browser/config.json").read_text()
        )
        self.assertEqual(config, {
            "executablePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "headed": False,
            "contentBoundaries": True,
            "maxOutput": 16000,
            "noWebmcp": True,
            "restoreSave": "never",
            "idleTimeout": "20m",
        })

    def test_upstream_extension_replaces_shared_skill(self):
        fragment = json.loads(
            (ROOT / "stow/pi/.pi/agent/settings.fragment.json").read_text()
        )
        self.assertIn("npm:pi-agent-browser-native", fragment["packages"])
        self.assertFalse(
            (ROOT / "stow/agents/.agents/skills/agent-browser/SKILL.md").exists()
        )

    def test_shell_and_policy_select_explicit_configuration(self):
        environment = (ROOT / "stow/fish/.config/fish/conf.d/env.fish").read_text()
        self.assertIn(
            'set -gx AGENT_BROWSER_CONFIG "$HOME/.config/agent-browser/config.json"',
            environment,
        )
        instructions = (ROOT / "stow/agents/.agents/AGENTS.md").read_text()
        self.assertIn("Use `pi-agent-browser-native` tools", instructions)
        self.assertIn("never use `close --all`", instructions)
        self.assertIn("requires explicit authorization", instructions)


if __name__ == "__main__":
    unittest.main()
