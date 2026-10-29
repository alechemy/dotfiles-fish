import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
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


class TerminalOutput(io.StringIO):
    def isatty(self):
        return True


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

    def test_new_launches_create_independent_sessions_in_callers_directory(self):
        project = self.home / "project with spaces"
        project.mkdir()
        for action in [[], ["resume"]]:
            with self.subTest(action=action), patch.object(mobile.Path, "cwd", return_value=project):
                with patch.object(mobile.subprocess, "run") as run:
                    executable, args, environment = self.execute(action)
                run.assert_not_called()
                self.assertEqual(executable, "tmux")
                self.assertEqual(args[:3], ["tmux", "-L", "pi-mobile"])
                self.assertIn("new-session", args)
                for option in ["-A", "-D", "-d", "-s", "-t"]:
                    self.assertNotIn(option, args)
                self.assertEqual(args[args.index("-c") + 1], str(project))
                self.assertEqual(args[-3:], [sys.executable, str(LAUNCHER.resolve()), "_run"])
                self.assertEqual(environment["HOME"], str(self.home))

    def session_results(self):
        return [
            subprocess.CompletedProcess([], 0, "$4\n$9\n", ""),
            subprocess.CompletedProcess([], 0, "pi: alpha / First task | /projects/alpha | 1 window(s) | detached\n", ""),
            subprocess.CompletedProcess([], 0, "pi: beta / Second task | /projects/beta | 2 window(s) | attached\n", ""),
        ]

    def test_attach_uses_a_numbered_menu_and_exact_session_id(self):
        with patch.object(mobile.subprocess, "run", side_effect=self.session_results()) as run:
            with patch("builtins.input", side_effect=["bad", "0", "3", "2"]):
                with patch("sys.stdout", new_callable=TerminalOutput) as output:
                    _, args, _ = self.execute(["attach"])
        self.assertEqual(args[-3:], ["attach-session", "-t", "$9"])
        self.assertNotIn("-d", args)
        self.assertIn("1. pi: alpha / First task", output.getvalue())
        self.assertIn("2. pi: beta / Second task", output.getvalue())
        self.assertIn("/projects/beta", output.getvalue())
        self.assertNotIn("$9", output.getvalue())
        self.assertEqual(run.call_args_list[0].args[0][-3:], ["list-sessions", "-F", "#{session_id}"])
        self.assertEqual(run.call_args_list[2].args[0][-2:], ["$9", mobile.SESSION_LABEL])

    def test_attach_can_be_cancelled_without_creating_a_session(self):
        for response in ["", "q", EOFError(), KeyboardInterrupt()]:
            with self.subTest(response=response):
                with patch.object(mobile.subprocess, "run", side_effect=self.session_results()):
                    with patch("builtins.input", side_effect=[response]):
                        with patch.object(mobile.os, "execvpe") as execute, patch("sys.stdout", new_callable=TerminalOutput):
                            self.assertEqual(mobile.main(["attach"]), 0)
                        execute.assert_not_called()

    def test_attach_with_no_server_does_not_create_a_session(self):
        for error in ["no server running on /tmp/test\n", "error connecting to /tmp/test (No such file or directory)\n"]:
            with self.subTest(error=error):
                with patch.object(mobile.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", error)):
                    with patch.object(mobile.os, "execvpe") as execute, patch("sys.stdout", new_callable=TerminalOutput) as output:
                        self.assertEqual(mobile.main(["attach"]), 0)
                    execute.assert_not_called()
                    self.assertIn("No running mobile sessions", output.getvalue())

    def test_session_listing_failure_is_reported(self):
        with patch.object(mobile.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "permission denied")):
            with self.assertRaisesRegex(ValueError, "permission denied"):
                mobile.main(["list"])

    def test_disappeared_sessions_are_skipped_and_labels_are_terminal_safe(self):
        results = [
            subprocess.CompletedProcess([], 0, "$4\n$9\n", ""),
            subprocess.CompletedProcess([], 1, "", "session disappeared"),
            subprocess.CompletedProcess([], 0, "pi: task\n/project\x1b[2J\tattached\n", ""),
        ]
        with patch.object(mobile.subprocess, "run", side_effect=results):
            sessions = mobile.list_mobile_sessions(["tmux"], {})
        self.assertEqual(sessions, [("$9", "pi: task /project [2J attached")])

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
            with patch.object(mobile.subprocess, "run", side_effect=self.session_results()) as run:
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(mobile.main(["list"]), 0)
        self.assertIn("list-sessions", run.call_args_list[0].args[0])
        self.assertIn("First task", output.getvalue())
        self.assertIn("Second task", output.getvalue())


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
                "record = {'args': sys.argv[1:], 'cwd': os.getcwd(), 'cmux': any(k.startswith('CMUX_') for k in os.environ), "
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

            project = home / "project with spaces"
            other_project = home / "other-project"
            project.mkdir()
            other_project.mkdir()
            selected_project = home / "selected #{session_name}, with spaces"
            selected_project.mkdir()
            try:
                first = tmux("new-session", "-d", "-P", "-F", "#{session_id}", "-c", str(project))
                deadline = time.monotonic() + 5
                while not list(home.glob("*.json")) and time.monotonic() < deadline:
                    time.sleep(0.05)
                records = list(home.glob("*.json"))
                self.assertEqual(len(records), 1)
                record = json.loads(records[0].read_text())
                self.assertFalse(record["cmux"])
                self.assertFalse(record["ixon"])
                self.assertEqual(record["mobile"], "1")
                self.assertEqual(record["cwd"], str(project.resolve()))
                self.assertEqual(record["args"], ["--extension", str(home / ".config/pi-mobile/mobile.ts"), "--resume"])
                self.assertEqual(tmux("display-message", "-p", "-t", first, "#{session_attached}"), "0")
                self.assertEqual(tmux("show-options", "-gv", "history-limit"), "50000")
                self.assertEqual(tmux("show-options", "-gv", "destroy-unattached"), "off")
                self.assertEqual(tmux("show-options", "-gwv", "window-size"), "latest")
                binding = tmux("list-keys", "-T", "prefix", "r")
                self.assertIn("new-window", binding)
                self.assertIn('#{pane_current_path}', binding)
                tmux("rename-window", "-t", first + ":", "pi: project / Fictional task")
                first_window = tmux("display-message", "-p", "-t", first, "#{window_id}")
                second = tmux("new-session", "-d", "-P", "-F", "#{session_id}", "-c", str(other_project))
                self.assertNotEqual(first, second)
                for session in [first, second]:
                    self.assertEqual(tmux("display-message", "-p", "-t", session, "#{session_group}"), "")
                binding_args = shlex.split(binding)
                new_window = binding_args[binding_args.index("new-window"):]
                tmux(*new_window, "-t", second + ":")
                deadline = time.monotonic() + 5
                while len(list(home.glob("*.json"))) < 3 and time.monotonic() < deadline:
                    time.sleep(0.05)
                records = [json.loads(path.read_text()) for path in home.glob("*.json")]
                self.assertEqual(len(records), 3)
                self.assertEqual(sum(record["cwd"] == str(other_project.resolve()) for record in records), 2)
                self.assertEqual(tmux("display-message", "-p", "-t", first, "#{window_id}"), first_window)
                self.assertEqual(len(tmux("list-windows", "-t", first, "-F", "#{window_id}").splitlines()), 1)
                self.assertEqual(len(tmux("list-windows", "-t", second, "-F", "#{window_id}").splitlines()), 2)
                sessions = dict(mobile.list_mobile_sessions(base, environment))
                self.assertEqual(set(sessions), {first, second})
                self.assertIn("Fictional task", sessions[first])
                self.assertIn(str(project.resolve()), sessions[first])
                self.assertIn("detached", sessions[first])
                self.assertIn(str(other_project.resolve()), sessions[second])
                tmux("set-option", "-p", "-t", second, "@pi_mobile_cwd", str(selected_project))
                sessions = dict(mobile.list_mobile_sessions(base, environment))
                self.assertIn(str(selected_project), sessions[second])
                tmux(*new_window, "-t", second + ":")
                deadline = time.monotonic() + 5
                while len(list(home.glob("*.json"))) < 4 and time.monotonic() < deadline:
                    time.sleep(0.05)
                records = [json.loads(path.read_text()) for path in home.glob("*.json")]
                self.assertEqual(len(records), 4)
                self.assertEqual(sum(record["cwd"] == str(selected_project.resolve()) for record in records), 1)
                self.assertEqual(tmux("display-message", "-p", "-t", first, "#{window_id}"), first_window)
            finally:
                subprocess.run(base + ["kill-server"], env=environment, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
