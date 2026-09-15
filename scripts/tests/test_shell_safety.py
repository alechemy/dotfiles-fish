#!/usr/bin/env python3
"""Isolated Fish tests. Only listeners created by these tests receive signals."""

import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
FUNCTION = ROOT / "stow/fish/.config/fish/functions/ports.fish"
ABBRS = ROOT / "stow/fish/.config/fish/conf.d/abbrs.fish"
COMPLETIONS = ROOT / "stow/fish/.config/fish/completions/ports.fish"
FISH = shutil.which("fish")
LSOF = shutil.which("lsof")

LISTENER = r"""
import json, signal, socket, sys
mode = sys.argv[1]
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
sock.bind(('127.0.0.1', int(sys.argv[2])))
sock.listen()
sockets = [sock]
if mode == 'dual':
    extra = socket.socket(socket.AF_INET6)
    extra.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    extra.bind(('::1', sock.getsockname()[1]))
    extra.listen()
    sockets.append(extra)
if mode == 'resist':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
print(json.dumps({'port': sock.getsockname()[1]}), flush=True)
while True:
    signal.pause()
"""


@unittest.skipUnless(FISH, "Fish is required")
class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.env = {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"), "XDG_CACHE_HOME": str(self.home / "cache"),
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "LC_ALL": "C", "TERM": "dumb",
        }

    def fish(self, script, interactive=False):
        return subprocess.run(
            [FISH, "--no-config", *(["-i"] if interactive else []), "-c", script],
            env=self.env, text=True, capture_output=True, timeout=15,
        )

    def ports(self, *args):
        return self.fish(f"source {shlex.quote(str(FUNCTION))}; ports " + shlex.join(args))

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def mocks(self, pids="424242\n", ps=None):
        self.stub("lsof", "printf '%s' " + shlex.quote(pids))
        self.stub("ps", "printf '%s\\n' " + shlex.quote(
            ps if ps is not None else f"{os.getuid()} Tue Sep 15 12:00:00 2026 S fixture-listener"
        ))
        self.stub("kill", 'printf "%s\\n" "$*" >> "$HOME/signals"')
        self.stub("sleep", "exit 0\n")

    def signals(self):
        path = self.home / "signals"
        return path.read_text().splitlines() if path.exists() else []

    def test_invalid_input_never_queries_or_signals(self):
        self.stub("lsof", 'touch "$HOME/queried"; exit 3')
        self.stub("kill", 'touch "$HOME/signaled"; exit 3')
        invalid = [(), ("wat",), ("ls", "80"), ("--help", "80"), ("kill",),
                   ("kill", "80", "--force", "extra"), ("kill", "80", "--FORCE"),
                   ("pid", "80", "--force"), ("show", "80", "extra")]
        invalid += [(action, port) for action in ("show", "pid", "kill")
                    for port in ("0", "65536", "-1", "1.0", "1e2", "1,2", "", "abc", "99999999999999999")]
        for args in invalid:
            with self.subTest(args=args):
                result = self.ports(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.home / "queried").exists())
        self.assertFalse((self.home / "signaled").exists())

    def test_no_listener(self):
        self.mocks()
        self.stub("lsof", "exit 1")
        result = self.ports("kill", "12345", "--force")
        self.assertIn("no process listening", result.stderr)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.signals(), [])

    def test_multiple_owners_are_ambiguous(self):
        self.mocks("424242\n424243\n424242\n")
        result = self.ports("kill", "12345", "--force")
        self.assertIn("multiple listener owners", result.stderr)
        self.assertEqual(self.signals(), [])

    def test_pid_output_is_deduplicated(self):
        self.mocks("424242\n424242\n")
        result = self.ports("pid", "00080")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "424242\n")

    def test_unverifiable_listener_query(self):
        for output in ("424242\nwarning: incomplete results\n", "0\n", "-1\n", "abc\n"):
            with self.subTest(output=output):
                self.mocks(output)
                self.assertNotEqual(self.ports("kill", "80").returncode, 0)
                self.assertEqual(self.signals(), [])
        self.stub("lsof", "printf '424242\\n'; exit 1")
        self.assertNotEqual(self.ports("kill", "80").returncode, 0)
        self.assertEqual(self.signals(), [])

    def test_foreign_or_unverifiable_identity(self):
        for identity in ("not an identity", f"{os.getuid() + 1} Tue Sep 15 12:00:00 2026 S fixture"):
            with self.subTest(identity=identity):
                self.mocks(ps=identity)
                self.assertNotEqual(self.ports("kill", "80", "--force").returncode, 0)
                self.assertEqual(self.signals(), [])

    def test_identity_change_before_term_blocks_signal(self):
        self.mocks()
        self.stub("ps", f"""if test -e "$HOME/identity-read"; then
printf '{os.getuid()} Tue Sep 15 12:01:00 2026 S replacement\\n'
else
printf '{os.getuid()} Tue Sep 15 12:00:00 2026 S original\\n'
touch "$HOME/identity-read"
fi
""")
        result = self.ports("kill", "80", "--force")
        self.assertIn("identity changed", result.stderr)
        self.assertEqual(self.signals(), [])

    def test_replacement_port_owner_never_receives_force(self):
        self.mocks()
        self.stub("lsof", """if test -e "$HOME/signals"; then
printf '424243\\n'
else
printf '424242\\n'
fi
""")
        result = self.ports("kill", "80", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.signals(), ["-TERM 424242"])

    def test_reused_pid_during_wait_never_receives_force(self):
        self.mocks()
        self.stub("ps", f"""if test -e "$HOME/signals"; then
printf '{os.getuid()} Tue Sep 15 12:01:00 2026 S replacement\\n'
else
printf '{os.getuid()} Tue Sep 15 12:00:00 2026 S original\\n'
fi
""")
        result = self.ports("kill", "80", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.signals(), ["-TERM 424242"])

    def test_failed_signal_never_escalates(self):
        self.mocks()
        self.stub("kill", 'printf "%s\\n" "$*" >> "$HOME/signals"; exit 1')
        result = self.ports("kill", "80", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.signals(), ["-TERM 424242"])

    def test_identity_query_failure_during_wait_stops(self):
        self.mocks()
        self.stub("ps", f"""if test -e "$HOME/signals"; then
printf 'unverifiable\\n'; exit 2
else
printf '{os.getuid()} Tue Sep 15 12:00:00 2026 S original\\n'
fi
""")
        result = self.ports("kill", "80", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.signals(), ["-TERM 424242"])

    def test_metadata_is_bounded_and_does_not_query_arguments(self):
        self.mocks(ps=f"{os.getuid()} Tue Sep 15 12:00:00 2026 S " + "x" * 1000)
        result = self.ports("kill", "80")
        self.assertLess(len(result.stderr.splitlines()[0]), 230)
        self.assertEqual(self.signals(), ["-TERM 424242"])
        self.assertNotIn("args=", FUNCTION.read_text())

    def test_abbreviations_fresh_and_existing_shell(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                before = "abbr -a copilot 'copilot --allow-all'; abbr -a unpop 'git reset --merge';" if existing else ""
                script = before + f"source {shlex.quote(str(ABBRS))}; " + """
                    abbr --query copilot; and exit 10
                    abbr --query unpop; and exit 11
                    abbr --query killport; or exit 12
                    abbr --show
                """
                result = self.fish(script, interactive=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("killport 'ports kill'", result.stdout)
                self.assertNotIn("allow-all", result.stdout)
                self.assertNotIn("unpop", result.stdout)

    def test_copilot_space_expansion_without_launch(self):
        # A real reader expands abbreviations on space. Ctrl-X captures its buffer
        # and exits without submitting that buffer as a command.
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.stub("copilot", 'touch "$HOME/copilot-launched"')
                master, slave = pty.openpty()
                before = "abbr -a copilot 'copilot --allow-all'; " if existing else ""
                init = before + f"source {shlex.quote(str(ABBRS))}; " + r"""
                    set -g fish_greeting
                    function fish_prompt; printf 'READY>'; end
                    bind \cx 'printf "\nBUFFER:%s\n" (commandline); commandline ""; exit'
                """
                def attach_terminal():
                    os.setsid()
                    fcntl.ioctl(0, termios.TIOCSCTTY, 0)

                proc = subprocess.Popen(
                    [FISH, "--no-config", "-i", "--init-command", init],
                    env=self.env, stdin=slave, stdout=slave, stderr=slave,
                    preexec_fn=attach_terminal,
                )
                os.close(slave)
                output = b""
                sent = False
                deadline = time.monotonic() + 5
                try:
                    while time.monotonic() < deadline:
                        ready, _, _ = select.select([master], [], [], 0.1)
                        if ready:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            if not data:
                                break
                            output += data
                            if b"READY>" in output and not sent:
                                os.write(master, b"copilot \x18")
                                sent = True
                        if proc.poll() is not None:
                            break
                    self.assertTrue(sent, output)
                    self.assertIn(b"BUFFER:copilot ", output)
                    self.assertNotIn(b"BUFFER:copilot --allow-all", output)
                    self.assertFalse((self.home / "copilot-launched").exists())
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=5)
                    os.close(master)

    def test_force_completion_only_for_kill(self):
        source = f"source {shlex.quote(str(COMPLETIONS))}; "
        self.assertIn("--force", self.fish(source + "complete -C 'ports kill 80 --'").stdout)
        self.assertNotIn("--force", self.fish(source + "complete -C 'ports show 80 --'").stdout)

    def listener(self, mode="graceful", port=0):
        proc = subprocess.Popen(
            ["/usr/bin/python3", "-u", "-c", LISTENER, mode, str(port)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(cleanup)
        line = proc.stdout.readline()
        if not line:
            self.fail("Disposable listener could not start: " + proc.stderr.read())
        return proc, json.loads(line)["port"]

    @unittest.skipUnless(LSOF, "lsof is required")
    def test_disposable_graceful_and_unrelated_listener(self):
        target, port = self.listener()
        other, _ = self.listener()
        result = self.ports("kill", str(port))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.wait(timeout=2), 0)
        self.assertIsNone(other.poll())

    @unittest.skipUnless(LSOF, "lsof is required")
    def test_disposable_term_resistance_and_explicit_force(self):
        target, port = self.listener("resist")
        other, _ = self.listener()
        result = self.ports("kill", str(port))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("no escalation", result.stderr)
        self.assertIsNone(target.poll())
        self.assertIsNone(other.poll())
        started = time.monotonic()
        forced = self.ports("kill", str(port), "--force")
        self.assertGreaterEqual(time.monotonic() - started, 2.8)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(target.wait(timeout=2), -9)
        self.assertIsNone(other.poll())

    @unittest.skipUnless(LSOF, "lsof is required")
    def test_disposable_ipv4_ipv6_single_owner(self):
        target, port = self.listener("dual")
        result = self.ports("pid", str(port))
        self.assertEqual(result.stdout, f"{target.pid}\n")
        result = self.ports("kill", str(port))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.wait(timeout=2), 0)

    @unittest.skipUnless(LSOF, "lsof is required")
    def test_disposable_shared_port_owners_are_preserved(self):
        first, port = self.listener()
        second, _ = self.listener(port=port)
        result = self.ports("kill", str(port), "--force")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("multiple listener owners", result.stderr)
        self.assertIsNone(first.poll())
        self.assertIsNone(second.poll())


if __name__ == "__main__":
    unittest.main()
