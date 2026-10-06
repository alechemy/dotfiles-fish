import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class SyntheticIsolation(unittest.TestCase):
    def test_home_config_state_and_credentials_are_isolated_before_import(self):
        directory = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_home = root / "previous-home"
            config = fake_home / ".config/dt-pipeline/entities.conf"
            state = fake_home / ".local/state/devonthink/entity-filing-state.json"
            config.parent.mkdir(parents=True)
            state.parent.mkdir(parents=True)
            config.write_text("SELF_NAME=Fictional fixture\n")
            state.write_text('{"fictional":true}')
            runner = root / "run_synthetic.py"
            runner.write_text((directory / "run_synthetic.py").read_text())
            output = root / "sandbox-path.txt"
            probe = '''import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, DIRECTORY)
from helpers import load
assert Path.home() != Path(PREVIOUS_HOME)
assert "OMLX_API_KEY" not in os.environ
assert "THINGS_AUTH_TOKEN" not in os.environ
assert os.environ["PIPELINE_MANUAL"] == "1"
opened = []
def audit(event, args):
    if event == "open" and isinstance(args[0], str) and args[0].startswith(PREVIOUS_HOME):
        opened.append(args[0])
sys.addaudithook(audit)
ef = load("entity-filing.py", "runner_isolation_filing")
assert ef.CONFIG_FILE.startswith(str(Path.home()))
assert ef.STATE_FILE.startswith(str(Path.home()))
assert ef.load_config()["SELF_NAME"] == ""
assert ef.load_state()["processed"] == {}
assert opened == [], [str(Path(path).relative_to(PREVIOUS_HOME)) for path in opened]
Path(OUTPUT).write_text(str(Path.home()))
class Probe(unittest.TestCase):
    def test_isolated(self):
        self.assertFalse(opened)
'''.replace("DIRECTORY", repr(str(directory))).replace("PREVIOUS_HOME", repr(str(fake_home))).replace("OUTPUT", repr(str(output)))
            (root / "test_probe_isolation.py").write_text(probe)
            for name in ("test_calendar_canary.py", "test_contacts_canary.py"):
                (root / name).write_text('raise AssertionError("Live canary was imported")\n')
            environment = dict(os.environ, HOME=str(fake_home), OMLX_API_KEY="fictional-provider-value", THINGS_AUTH_TOKEN="fictional-reminder-value", PIPELINE_MANUAL="1")
            result = subprocess.run(["/usr/bin/python3", str(runner)], env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(Path(output.read_text()).exists())
            (root / "test_probe_isolation.py").write_text(probe.replace("self.assertFalse(opened)", "self.fail('fictional failure')"))
            failed = subprocess.run(["/usr/bin/python3", str(runner)], env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(failed.returncode, 1)
            self.assertFalse(Path(output.read_text()).exists())
            self.assertEqual(config.read_text(), "SELF_NAME=Fictional fixture\n")
            self.assertEqual(state.read_text(), '{"fictional":true}')
