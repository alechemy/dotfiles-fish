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

    def test_shared_skill_uses_explicit_configuration(self):
        skill = (ROOT / "stow/agents/.agents/skills/agent-browser/SKILL.md").read_text()
        self.assertIn("name: agent-browser\n", skill)
        self.assertIn("agent-browser skills get core", skill)
        self.assertIn('--config "$HOME/.config/agent-browser/config.json"', skill)
        self.assertIn("Never use `close --all`", skill)


if __name__ == "__main__":
    unittest.main()
