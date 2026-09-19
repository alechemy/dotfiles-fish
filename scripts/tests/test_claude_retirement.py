import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_jsonc(relative):
    text = (ROOT / relative).read_text()
    text = re.sub(
        r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/',
        lambda match: match[0] if match[0].startswith('"') else " ",
        text,
    )
    text = re.sub(
        r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])',
        lambda match: match[0] if match[0].startswith('"') else "",
        text,
    )
    return json.loads(text)


class ClaudeRetirementTests(unittest.TestCase):
    def test_bootstrap_does_not_install_or_configure_claude(self):
        self.assertNotIn('cask "claude-code', (ROOT / "Brewfile").read_text())
        setup = (ROOT / "scripts/setup.sh").read_text()
        for obsolete in ("merge-claude-mcp", "claude-agent-acp", ".claude.json"):
            self.assertNotIn(obsolete, setup)
        self.assertIn("install-agent-reader.sh", setup)
        self.assertIn("merge-pi-settings.sh", setup)

    def test_claude_only_files_are_removed(self):
        for relative in (
            "stow/claude",
            "stow/bin/.local/bin/claude-restart",
            "scripts/merge-claude-mcp.sh",
            "CLAUDE.md",
            "devonthink/CLAUDE.md",
        ):
            with self.subTest(path=relative):
                path = ROOT / relative
                self.assertFalse(path.exists() or path.is_symlink())
        self.assertTrue((ROOT / "stow/pi/.pi/agent/AGENTS.md").is_file())
        self.assertTrue((ROOT / "devonthink/AGENTS.md").is_file())
        self.assertTrue((ROOT / "stow/agents/.agents/skills/recall/SKILL.md").is_file())

    def test_zed_removes_claude_servers_but_preserves_models(self):
        config = read_jsonc("stow/zed/.config/zed/settings.template.jsonc")
        servers = config["agent_servers"]
        self.assertNotIn("claude-acp", servers)
        self.assertNotIn("Claude Code by Rohan Patra", servers)
        self.assertIn("copilot-acp", servers)
        self.assertTrue(any(
            model["model"].startswith("claude-")
            for model in config["agent"]["favorite_models"]
        ))

    def test_vscode_removes_extension_and_bindings_but_preserves_pi_submit(self):
        self.assertNotIn(
            "anthropic.claude-code", (ROOT / "stow/vscode/extensions.txt").read_text()
        )
        bindings = read_jsonc(
            "stow/vscode/Library/Application Support/VSCodium/User/keybindings.json"
        )
        self.assertFalse(any("claude-vscode" in row["command"] for row in bindings))
        self.assertTrue(any(
            row.get("key") == "cmd+enter"
            and row["command"] == "workbench.action.terminal.sendSequence"
            and row["args"]["text"] == "\x1b[13;9u"
            for row in bindings
        ))

    def test_karabiner_no_longer_invokes_removed_restart_helper(self):
        source = (ROOT / "stow/karabiner/.config/karabiner.edn").read_text()
        self.assertNotIn("claude-restart", source)
        self.assertIn("move-node-to-workspace", source)
        self.assertIn("m1ddc", source)


if __name__ == "__main__":
    unittest.main()
