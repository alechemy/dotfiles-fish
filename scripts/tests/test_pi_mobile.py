import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "stow/pi-mobile"
LAUNCHER = PACKAGE / ".local/bin/pi-mobile"
loader = importlib.machinery.SourceFileLoader("pi_mobile", str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
mobile = importlib.util.module_from_spec(spec)
loader.exec_module(mobile)


class Executed(Exception):
    pass


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        for path in [".config/tmux/pi-mobile.conf", ".config/pi-mobile/mobile.ts"]:
            destination = self.home / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.touch()
        self.environment = patch.dict(os.environ, {"HOME": str(self.home), "PATH": os.defpath}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.tty = patch.object(sys.stdin, "isatty", return_value=True)
        self.output_tty = patch.object(sys.stdout, "isatty", return_value=True)
        self.tty.start()
        self.output_tty.start()
        self.addCleanup(self.tty.stop)
        self.addCleanup(self.output_tty.stop)

    def execute(self, action):
        with patch.object(mobile.os, "execvpe", side_effect=Executed) as execute:
            with self.assertRaises(Executed):
                mobile.main(action)
        return execute.call_args.args

    def test_attach_creates_or_reuses_without_detaching_other_clients(self):
        executable, args, environment = self.execute([])
        self.assertEqual(executable, "tmux")
        self.assertEqual(args[:3], ["tmux", "-L", "pi-mobile"])
        self.assertIn("new-session", args)
        self.assertIn("-A", args)
        self.assertNotIn("-D", args)
        self.assertNotIn("-d", args)
        self.assertEqual(args[-3:], [sys.executable, str(LAUNCHER.resolve()), "_run"])
        self.assertEqual(environment["HOME"], str(self.home))

    def test_resume_creates_one_window_then_attaches(self):
        with patch.object(mobile.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            _, args, _ = self.execute(["resume"])
        self.assertEqual(len(run.call_args_list), 2)
        self.assertIn("has-session", run.call_args_list[0].args[0])
        self.assertIn("new-window", run.call_args_list[1].args[0])
        self.assertEqual(args[-3:], ["attach-session", "-t", "=mobile"])

    def test_resume_creates_first_session_if_absent(self):
        with patch.object(mobile.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            _, args, _ = self.execute(["resume"])
        self.assertIn("new-session", args)

    def test_failed_window_creation_does_not_attach_or_retry(self):
        results = [subprocess.CompletedProcess([], 0), subprocess.CalledProcessError(1, "tmux")]
        with patch.object(mobile.subprocess, "run", side_effect=results) as run:
            with patch.object(mobile.os, "execvpe") as execute:
                with self.assertRaises(subprocess.CalledProcessError):
                    mobile.main(["resume"])
        self.assertEqual(run.call_count, 2)
        execute.assert_not_called()

    def test_desktop_and_nested_launches_are_refused(self):
        for key in ["CMUX_SURFACE_ID", "TMUX"]:
            with self.subTest(key=key), patch.dict(os.environ, {key: "synthetic"}):
                with patch.object(mobile.os, "execvpe") as execute:
                    with self.assertRaises(ValueError):
                        mobile.main([])
                    execute.assert_not_called()

    def test_noninteractive_launch_is_refused(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(ValueError, "interactive terminal"):
                mobile.main([])

    def test_missing_config_is_refused(self):
        (self.home / ".config/tmux/pi-mobile.conf").unlink()
        with self.assertRaisesRegex(ValueError, "Restow"):
            mobile.main([])

    def test_environment_removes_desktop_identity_without_changing_original(self):
        source = {"CMUX_SURFACE_ID": "synthetic", "CMUX_SOCKET_PATH": "synthetic",
                  "TERM_PROGRAM": "ghostty", "GHOSTTY_RESOURCES_DIR": "synthetic",
                  "SSH_CONNECTION": "synthetic", "TMUX_PANE": "%1", "PATH": "/bin"}
        cleaned = mobile.mobile_environment(source)
        self.assertEqual(cleaned, {"SSH_CONNECTION": "synthetic", "TMUX_PANE": "%1", "PATH": "/bin"})
        self.assertIn("CMUX_SURFACE_ID", source)

    def test_internal_runner_cleans_server_environment_and_preserves_terminal_flags(self):
        with patch.dict(os.environ, {"TMUX": "synthetic", "TMUX_PANE": "%1", "CMUX_SURFACE_ID": "leaked"}):
            attributes = [termios.IXON | termios.ICRNL, 0, 0, 0, 0, 0, []]
            with patch.object(mobile.termios, "tcgetattr", return_value=attributes):
                with patch.object(mobile.termios, "tcsetattr") as set_attributes:
                    executable, args, environment = self.execute(["_run"])
        self.assertEqual(executable, "pi")
        self.assertEqual(args, ["pi", "--extension", str(self.home / ".config/pi-mobile/mobile.ts"), "--resume"])
        self.assertEqual(environment["PI_MOBILE"], "1")
        self.assertNotIn("CMUX_SURFACE_ID", environment)
        self.assertEqual(environment["TMUX_PANE"], "%1")
        self.assertFalse(set_attributes.call_args.args[2][0] & termios.IXON)
        self.assertTrue(set_attributes.call_args.args[2][0] & termios.ICRNL)

    def test_internal_runner_requires_tmux(self):
        with self.assertRaisesRegex(ValueError, "inside tmux"):
            mobile.main(["_run"])

    def test_list_does_not_require_tty_or_create_a_session(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            with patch.object(mobile.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(mobile.main(["list"]), 0)
        self.assertIn("list-windows", run.call_args.args[0])
        self.assertNotIn("new-session", run.call_args.args[0])


@unittest.skipUnless(shutil.which("tmux"), "tmux is required for the isolated integration check")
class TmuxTests(unittest.TestCase):
    def test_isolated_config_and_detached_process(self):
        with tempfile.TemporaryDirectory(prefix="pi-mobile-test-") as temporary:
            home = Path(temporary)
            for relative, source in [
                (".config/tmux/tmux.conf", ROOT / "stow/tmux/.config/tmux/tmux.conf"),
                (".config/tmux/pi-mobile.conf", PACKAGE / ".config/tmux/pi-mobile.conf"),
                (".config/pi-mobile/mobile.ts", PACKAGE / ".config/pi-mobile/mobile.ts"),
                (".local/bin/pi-mobile", LAUNCHER),
            ]:
                target = home / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(source, target)
            (home / ".local/bin/pi-mobile").chmod(0o755)
            fake_pi = home / ".local/bin/pi"
            fake_pi.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, pathlib, sys, termios, time\n"
                "record = {'args': sys.argv[1:], 'cmux': any(k.startswith('CMUX_') for k in os.environ), "
                "'mobile': os.environ.get('PI_MOBILE'), 'ixon': bool(termios.tcgetattr(0)[0] & termios.IXON)}\n"
                "pathlib.Path(os.environ['HOME'], os.environ['TMUX_PANE'][1:] + '.json').write_text(json.dumps(record))\n"
                "time.sleep(60)\n"
            )
            fake_pi.chmod(0o755)
            environment = {"HOME": str(home), "PATH": str(home / ".local/bin") + os.pathsep + os.environ["PATH"],
                           "TERM": "xterm-256color", "CMUX_SURFACE_ID": "synthetic-leak"}
            socket = "pi-mobile-test-" + uuid.uuid4().hex
            base = [shutil.which("tmux"), "-L", socket, "-f", str(home / ".config/tmux/pi-mobile.conf")]

            def tmux(*arguments):
                return subprocess.run(base + list(arguments), env=environment, text=True, capture_output=True,
                                      timeout=10, check=True).stdout.strip()

            try:
                tmux("new-session", "-d", "-s", "mobile", "-c", str(home))
                deadline = time.monotonic() + 5
                while not list(home.glob("*.json")) and time.monotonic() < deadline:
                    time.sleep(0.05)
                records = list(home.glob("*.json"))
                self.assertEqual(len(records), 1)
                record = json.loads(records[0].read_text())
                self.assertFalse(record["cmux"])
                self.assertFalse(record["ixon"])
                self.assertEqual(record["mobile"], "1")
                self.assertEqual(record["args"], ["--extension", str(home / ".config/pi-mobile/mobile.ts"), "--resume"])
                self.assertEqual(tmux("display-message", "-p", "-t", "=mobile:", "#{session_attached}"), "0")
                self.assertEqual(tmux("show-options", "-gv", "history-limit"), "50000")
                self.assertEqual(tmux("show-options", "-gv", "destroy-unattached"), "off")
                self.assertEqual(tmux("show-options", "-gwv", "window-size"), "latest")
                self.assertIn("new-window", tmux("list-keys", "-T", "prefix", "r"))
                tmux("new-window", "-t", "mobile:", "-n", "resume", "-c", str(home))
                deadline = time.monotonic() + 5
                while len(list(home.glob("*.json"))) < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(len(list(home.glob("*.json"))), 2)
                self.assertEqual(len(tmux("list-windows", "-t", "=mobile", "-F", "#{window_id}").splitlines()), 2)
            finally:
                subprocess.run(base + ["kill-server"], env=environment, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
