#!/usr/bin/env python3
"""Exercise Herdr and Pi in an isolated PTY without model calls or live sessions."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import termios
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    plugin = parser.add_mutually_exclusive_group()
    plugin.add_argument("--hunk-plugin-root", type=Path)
    plugin.add_argument("--hunk", action="store_true")
    parser.add_argument("--worktrunk", action="store_true")
    args = parser.parse_args()
    if args.worktrunk and not args.hunk_plugin_root:
        args.hunk = True
    if args.hunk:
        result = subprocess.run(["herdr", "plugin", "list", "--json"],
                                check=True, capture_output=True, text=True, timeout=10)
        plugins = json.loads(result.stdout)["result"]["plugins"]
        args.hunk_plugin_root = next((Path(p["plugin_root"]) for p in plugins
                                     if p["plugin_id"] == "jhochenbaum.hunkdiff"), None)
        if args.hunk_plugin_root is None:
            parser.error("Run scripts/install-herdr-hunk-diff.sh first.")
    hunk_daemon = None
    with tempfile.TemporaryDirectory(prefix="hd-", dir="/tmp") as directory:
        root = Path(directory)
        agent = root / ".pi/agent"
        agent.mkdir(parents=True)
        shutil.copy2(ROOT / "stow/pi/.pi/agent/keybindings.json", agent / "keybindings.json")
        (agent / "settings.json").write_text(json.dumps({
            "tuiMode": "fullscreen", "enableInstallTelemetry": False,
            "defaultProjectTrust": "never",
        }))
        config = root / "config.toml"
        shutil.copy2(ROOT / "stow/herdr/_seed/.config/herdr/config.toml", config)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("PI_", "HERDR_", "WORKTRUNK_", "GIT_"))
               and key != "AI_AGENT"}
        env.update(HOME=str(root), XDG_CONFIG_HOME=str(root / ".config"),
                   PI_CODING_AGENT_DIR=str(agent), PI_OFFLINE="1",
                   HERDR_CONFIG_PATH=str(config), TERM="xterm-256color")
        if args.hunk_plugin_root:
            env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            subprocess.run(["git", "init", "-q", str(root)], env=env, check=True)
            (root / ".gitignore").write_text("*\n!fixture.py\n")
            fixture = root / "fixture.py"
            fixture.write_text("value = 1\n")
            subprocess.run(["git", "-C", str(root), "add", "fixture.py"], env=env, check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Fixture", "-c",
                            "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                            "commit", "-qm", "fixture"], env=env, check=True)
            fixture.write_text("value = 2\n")
            config_dir = root / ".config/herdr/plugins/config/jhochenbaum.hunkdiff"
            configure = ["node", str(ROOT / "scripts/configure-herdr-hunk.mjs"),
                         str(args.hunk_plugin_root), str(config_dir)]
            subprocess.run(configure, env=env, check=True, capture_output=True)
            original = config.read_bytes()
            plugin_config = config_dir / "config.toml"
            plugin_config.write_text(plugin_config.read_text() + "\n# User preference.\n")
            preferences = plugin_config.read_bytes()
            subprocess.run(configure, env=env, check=True, capture_output=True)
            assert config.read_bytes() == original, "Repeated setup changed Herdr bindings"
            assert plugin_config.read_bytes() == preferences, "Setup overwrote plugin preferences"
            subprocess.run(["herdr", "config", "check"], env=env, check=True, capture_output=True)
            subprocess.run(["herdr", "plugin", "link", str(args.hunk_plugin_root)],
                           env=env, check=True, capture_output=True)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                env["HUNK_MCP_PORT"] = str(probe.getsockname()[1])
        if args.worktrunk:
            helper_dir = root / ".agents/skills/worktrunk"
            helper_dir.mkdir(parents=True)
            shutil.copy2(ROOT / "stow/agents/.agents/skills/worktrunk/wt_pi.py", helper_dir)
            binary_dir = root / ".local/bin"
            binary_dir.mkdir(parents=True)
            shutil.copy2(ROOT / "stow/bin/.local/bin/wt-pi", binary_dir)
            env.update(PATH=f"{binary_dir}:{env['PATH']}",
                       WORKTRUNK_WORKTREE_PATH=str(root / "trees/{{ branch | sanitize }}"),
                       WORKTRUNK_SYSTEM_CONFIG_PATH=os.devnull)
            subprocess.run(["bash", str(ROOT / "scripts/setup-worktrunk.sh")], env=env,
                           capture_output=True, check=True, timeout=10)
            (agent / "extensions").mkdir(exist_ok=True)
            shutil.copy2(ROOT / "stow/worktrunk/.pi/agent/extensions/worktrunk.ts", agent / "extensions")
        subprocess.run(["herdr", "integration", "install", "pi"], env=env,
                       capture_output=True, check=True, timeout=10)
        bridge = ROOT / "stow/herdr/.pi/agent/extensions/herdr-ui-state.ts"
        if bridge.exists():
            shutil.copy2(bridge, agent / "extensions/herdr-ui-state.ts")
        session_id = str(uuid.uuid4())
        session = agent / "sessions/fixture.jsonl"
        session.parent.mkdir()
        session.write_text("\n".join(json.dumps(entry) for entry in [
            {"type": "session", "version": 3, "id": session_id,
             "timestamp": "2026-01-01T00:00:00.000Z", "cwd": str(root)},
            {"type": "message", "id": "abc12345", "parentId": None,
             "timestamp": "2026-01-01T00:00:01.000Z", "message": {
                 "role": "assistant", "content": [{"type": "text", "text": "Synthetic fixture."}],
                 "api": "openai-responses", "provider": "openai", "model": "gpt-4.1",
                 "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                           "totalTokens": 0, "cost": {"input": 0, "output": 0, "cacheRead": 0,
                                                     "cacheWrite": 0, "total": 0}},
                 "stopReason": "stop", "timestamp": 1767225601000}},
        ]) + "\n")
        (agent / "extensions/smoke.ts").write_text('''
import { writeFileSync } from "node:fs";
import { basename, join } from "node:path";
export default function (pi) {
  pi.on("session_start", (_event, ctx) => {
    writeFileSync(join(process.env.HOME, "ready.json"), JSON.stringify({
      mode: ctx.mode, sessionId: ctx.sessionManager.getSessionId(),
    }));
  });
  pi.on("input", (event, ctx) => {
    writeFileSync(join(process.env.HOME, "input.json"), JSON.stringify({ text: event.text }));
    writeFileSync(join(process.env.HOME, `input-${basename(ctx.cwd)}.json`), JSON.stringify({ text: event.text }));
    return { action: "handled" };
  });
  pi.registerCommand("smoke-confirm", {
    description: "Exercise confirmation without performing an action",
    handler: async (_args, ctx) => {
      writeFileSync(join(process.env.HOME, "waiting.json"), "true");
      const approved = await ctx.ui.confirm("Synthetic confirmation", "No action will run.");
      writeFileSync(join(process.env.HOME, "confirm.json"), JSON.stringify({ approved }));
    },
  });
}
''')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 160, 0, 0))
        client = subprocess.Popen(["herdr", "--session", "smoke"], env=env, cwd=root,
                                  stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)

        def drain(seconds=0.1):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.02)[0]:
                    try:
                        os.read(master, 65536)
                    except OSError:
                        return

        def api(*args):
            result = subprocess.run(["herdr", "--session", "smoke", *args], env=env,
                                    capture_output=True, text=True, timeout=10)
            if result.returncode:
                raise RuntimeError(f"Herdr command failed: {args[0:2]}")
            if args[:2] in (("pane", "run"), ("pane", "send-keys")):
                return None
            return json.loads(result.stdout)["result"]

        def wait(predicate, description, seconds=15):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                drain()
                if predicate():
                    return
            raise AssertionError(f"Timed out: {description}")

        def key(data):
            os.write(master, data)
            drain(0.3)

        try:
            if args.hunk_plugin_root:
                hunk_daemon = subprocess.Popen(["hunk", "daemon", "serve"], env=env, cwd=root,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server_socket = root / ".config/herdr/sessions/smoke/herdr.sock"
            wait(server_socket.exists, "server socket")
            snapshot = api("workspace", "list")
            workspace = snapshot["workspaces"][0]["workspace_id"]
            pane = api("pane", "list", "--workspace", workspace)["panes"][0]["pane_id"]
            api("pane", "run", pane, f"pi --offline --no-skills --no-context-files --session {session}")
            wait(lambda: (root / "ready.json").exists(), "Pi interactive startup")
            assert json.loads((root / "ready.json").read_text())["mode"] == "tui"
            key(b"first\rsecond")
            assert not (root / "input.json").exists(), "Enter submitted instead of inserting a newline"
            ghostty = (ROOT / "stow/ghostty/.config/ghostty/config").read_text()
            shift_enter = next(line.split("=text:", 1)[1] for line in ghostty.splitlines()
                               if line.startswith("keybind = shift+enter=text:"))
            key(shift_enter.encode().decode("unicode_escape").encode() + b"third")
            assert not (root / "input.json").exists(), "Shift+Enter submitted instead of inserting a newline"
            key(b"\x1b[13;9u")
            wait(lambda: (root / "input.json").exists(), "Cmd+Enter submission")
            assert json.loads((root / "input.json").read_text())["text"] == "first\nsecond\nthird"
            (root / "input.json").unlink()
            key(b"control-submit\x13")
            wait(lambda: (root / "input.json").exists(), "Ctrl+S submission")
            assert json.loads((root / "input.json").read_text())["text"] == "control-submit"
            if args.hunk_plugin_root:
                (root / "input.json").unlink()
                key(b"\x02f")
                wait(lambda: len(api("pane", "list", "--workspace", workspace)["panes"]) == 2,
                     "Hunk review shortcut")
                hunk_pane = next(p["pane_id"] for p in api("pane", "list", "--workspace", workspace)["panes"]
                                 if p["pane_id"] != pane)

                def hunk(*command):
                    return subprocess.run(["hunk", "session", *command, "--repo", str(root), "--json"],
                                          env=env, capture_output=True, text=True, timeout=10)

                wait(lambda: hunk("get").returncode == 0, "Homebrew Hunk session")
                result = hunk("navigate", "--file", "fixture.py", "--new-line", "1")
                assert result.returncode == 0, result.stderr
                drain(1)
                key(b"c")
                key(b"Please keep this synthetic value unchanged.\x13")

                def comments():
                    result = hunk("comment", "list", "--type", "user")
                    assert result.returncode == 0, result.stderr
                    return json.loads(result.stdout)["comments"]

                wait(lambda: len(comments()) == 1, "human inline comment")
                assert not (root / "input.json").exists(), "Comment was sent without an explicit action"
                api("agent", "focus", pane)
                drain(0.5)
            key(b"/smoke-confirm\x1b[13;9u")
            wait(lambda: (root / "waiting.json").exists(), "confirmation opened")
            drain(1)
            assert not (root / "confirm.json").exists(), "Confirmation resolved without input"
            state = api("pane", "get", pane)["pane"]["agent_status"]
            assert state == "blocked", f"Confirmation reported {state}, not blocked"
            if args.hunk_plugin_root:
                key(b"\x02F")
                drain(1)
                assert len(comments()) == 1, "Blocked delivery cleared the comment"
                assert not (root / "input.json").exists(), "Feedback entered a confirmation dialog"
                assert not (root / "confirm.json").exists(), "Feedback answered a confirmation dialog"
            key(b"\x1b")
            wait(lambda: (root / "confirm.json").exists(), "confirmation cancellation")
            assert json.loads((root / "confirm.json").read_text())["approved"] is False
            wait(lambda: api("pane", "get", pane)["pane"]["agent_status"] in ("idle", "done"),
                 "blocked state cleared")
            if args.hunk_plugin_root:
                key(b"\x02F")
                wait(lambda: (root / "input.json").exists(), "Hunk feedback submitted to Pi")
                text = json.loads((root / "input.json").read_text())["text"]
                assert "Please keep this synthetic value unchanged." in text
                assert "fixture.py" in text
                wait(lambda: not comments(), "delivered comment cleared")
                (root / "input.json").unlink()
                key(b"\x02F")
                drain(1)
                assert not (root / "input.json").exists(), "Feedback was delivered twice"
                api("pane", "close", hunk_pane)
                print("Hunk smoke passed: existing binary, review key, user comment, blocked retention and Pi round trip.")
            (root / "waiting.json").unlink()
            (root / "confirm.json").unlink()
            key(b"/smoke-confirm\x1b[13;9u")
            wait(lambda: (root / "waiting.json").exists(), "second confirmation opened")
            assert not (root / "confirm.json").exists()
            key(b"\r")
            wait(lambda: (root / "confirm.json").exists(), "explicit confirmation")
            assert json.loads((root / "confirm.json").read_text())["approved"] is True
            if args.worktrunk:
                from worktrunk_smoke import exercise_worktrunk
                exercise_worktrunk(root, env, workspace, pane, api, wait, key, drain)
            key(b"\x1b[116;9u")
            wait(lambda: len(api("pane", "list", "--workspace", workspace)["panes"]) == 2,
                 "Cmd+T split")
            key(b"\x1b[116;10u")
            wait(lambda: len(api("tab", "list", "--workspace", workspace)["tabs"]) == 2,
                 "Cmd+Shift+T tab")
            key(b"\x02q")
            client.wait(timeout=10)
            assert len(api("pane", "list", "--workspace", workspace)["panes"]) == 3
            subprocess.run(["herdr", "--session", "smoke", "session", "stop", "smoke"],
                           env=env, capture_output=True, check=True, timeout=10)
            (root / "ready.json").unlink()
            os.close(master)
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 160, 0, 0))
            client = subprocess.Popen(["herdr", "--session", "smoke"], env=env, cwd=root,
                                      stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            wait(lambda: (root / "ready.json").exists(), "native Pi session restore", seconds=30)
            assert json.loads((root / "ready.json").read_text())["sessionId"] == session_id
            print("Herdr smoke passed: Pi input, confirmation, blocked state, splits, tabs, detach and native restore.")
        finally:
            subprocess.run(["herdr", "--session", "smoke", "session", "stop", "smoke"],
                           env=env, capture_output=True, timeout=10)
            if client.poll() is None:
                client.terminate()
                client.wait(timeout=10)
            os.close(master)
            if hunk_daemon is not None:
                hunk_daemon.terminate()
                hunk_daemon.wait(timeout=10)


if __name__ == "__main__":
    main()
