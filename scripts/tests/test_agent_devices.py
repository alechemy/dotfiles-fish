import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "stow/bin/.local/bin/agent-device"
loader = importlib.machinery.SourceFileLoader("agent_device", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
ad = importlib.util.module_from_spec(spec)
loader.exec_module(ad)
UDID = "11111111-1111-4111-8111-111111111111"


class Identities:
    def __init__(self):
        self.boot = "synthetic-boot"
        self.live = {10: self.identity(10), 20: self.identity(20)}

    def identity(self, pid, start=1):
        return {"pid": pid, "start": start, "boot": "synthetic-boot"}

    def get(self, pid):
        return self.live.get(pid)

    def alive(self, identity):
        return self.get(identity["pid"]) == identity

    def group(self, pgid):
        return [self.live[pgid]] if pgid in self.live else []

    def executable(self, pid):
        return getattr(self, "device_executable", "/sdk/qemu-system-aarch64-headless")


class Devices:
    def __init__(self, identities):
        self.identities = identities
        self.running = None
        self.boots = 0
        self.stops = 0
        self.fail_stop = False
        self.fail_ready = False

    def identity(self, platform, device):
        return self.running

    def prepare(self, platform, device):
        return device if platform == "ios" else "emulator-5554"

    def boot(self, platform, device, target):
        self.boots += 1
        self.running = self.identities.identity(30, self.boots)
        self.identities.live[30] = self.running
        return self.running

    def ready(self, entry):
        if self.fail_ready:
            raise ad.DeviceError("Not ready")

    def stop(self, entry):
        if self.fail_stop:
            raise ad.DeviceError("Shutdown failed")
        if self.identities.alive(entry["generation"]):
            self.stops += 1
            self.identities.live.pop(30, None)
            self.running = None
            return "shutdown"
        return "forgot-replaced-or-stopped-device"


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ad.Store(Path(self.tmp.name) / "state.json")
        self.identities = Identities()
        self.devices = Devices(self.identities)
        self.now = 1000
        self.manager = ad.Manager(self.store, self.devices, self.identities, lambda: self.now)

    def acquire(self, pid=10, session="session-a"):
        return self.manager.acquire("ios", UDID, pid, session)["lease"]

    def state(self):
        return json.loads(self.store.path.read_text())["devices"]

    def test_shares_one_boot_and_stops_only_after_last_release(self):
        a = self.acquire()
        b = self.acquire(20, "session-b")
        self.assertEqual(self.devices.boots, 1)
        self.manager.release(a)
        self.assertEqual(self.devices.stops, 0)
        self.manager.release(b)
        self.assertEqual(self.devices.stops, 1)
        self.assertEqual(self.state(), {})
        self.manager.release(b)
        self.assertEqual(self.devices.stops, 1)

    def test_concurrent_acquisition_boots_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pid: self.acquire(pid, str(pid)), (10, 20)))
        self.assertEqual(self.devices.boots, 1)
        self.assertEqual(len(set(results)), 2)
        self.manager.release(results[0])
        self.assertEqual(self.devices.stops, 0)
        self.manager.release(results[1])
        self.assertEqual(self.devices.stops, 1)

    def test_expiry_cleans_up_even_if_pi_is_still_open(self):
        self.acquire()
        self.now += ad.TTL
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_renewal_extends_lease(self):
        lease = self.acquire()
        self.now += ad.TTL - 1
        self.manager.renew(lease)
        self.now += 2
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        self.now += ad.TTL
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_dead_owner_gets_grace_period(self):
        self.acquire()
        del self.identities.live[10]
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        self.now += ad.ORPHAN_GRACE - 1
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        self.now += 1
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_reused_owner_pid_is_not_live_owner(self):
        self.acquire()
        self.identities.live[10] = self.identities.identity(10, 2)
        self.manager.sweep()
        self.now += ad.ORPHAN_GRACE
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_rebooted_device_is_not_shut_down(self):
        lease = self.acquire()
        replacement = self.identities.identity(30, 2)
        self.identities.live[30] = replacement
        self.devices.running = replacement
        self.manager.release(lease)
        self.assertEqual(self.devices.stops, 0)
        self.assertEqual(self.state(), {})
        with self.assertRaisesRegex(ad.DeviceError, "unmanaged"):
            self.acquire()

    def test_preserves_preexisting_unmanaged_device(self):
        self.devices.running = self.identities.identity(30)
        with self.assertRaisesRegex(ad.DeviceError, "unmanaged"):
            self.acquire()
        self.assertEqual(self.devices.boots, 0)
        self.assertEqual(self.devices.stops, 0)

    def test_failed_boot_does_not_adopt_racing_unmanaged_device(self):
        def failed_boot(*_args):
            self.devices.running = self.identities.identity(30, 99)
            self.identities.live[30] = self.devices.running
            raise ad.DeviceError("Boot lost the race")

        with patch.object(self.devices, "boot", side_effect=failed_boot):
            with self.assertRaisesRegex(ad.DeviceError, "lost the race"):
                self.acquire()
        entry = next(iter(self.state().values()))
        self.assertIsNone(entry["generation"])
        self.assertEqual(entry["leases"], {})
        self.assertEqual(self.manager.sweep()[0]["action"], "unconfirmed-startup-needs-inspection")
        self.assertEqual(self.devices.stops, 0)
        with self.assertRaisesRegex(ad.DeviceError, "interrupted startup"):
            self.acquire()

    def test_expired_owner_cannot_renew_a_stopped_device(self):
        lease = self.acquire()
        self.now += ad.TTL
        self.manager.sweep()
        with self.assertRaisesRegex(ad.DeviceError, "absent"):
            self.manager.renew(lease)

    def test_manual_hold_survives_session_exit_and_owner_death(self):
        lease = self.acquire()
        self.manager.hold(lease, 60)
        self.manager.release(session="session-a", owner_pid=10)
        del self.identities.live[10]
        self.now += ad.TTL
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        with self.assertRaisesRegex(ad.DeviceError, "automatically renewed"):
            self.manager.renew(lease)
        self.now += ad.TTL
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_hold_duration_is_bounded(self):
        lease = self.acquire()
        for minutes in (0, 121):
            with self.assertRaises(ad.DeviceError):
                self.manager.hold(lease, minutes)

    def test_session_exit_only_releases_that_session_and_process(self):
        self.acquire()
        self.acquire(20, "session-a")
        self.acquire(10, "session-b")
        self.manager.release(session="session-a", owner_pid=10)
        leases = next(iter(self.state().values()))["leases"]
        self.assertEqual(len(leases), 2)
        self.assertEqual(self.devices.stops, 0)

    def test_active_job_survives_expiry_and_wrapper_crash(self):
        lease = self.acquire()
        self.identities.live[40] = self.identities.identity(40)
        self.manager.protect_job(lease, 40)
        del self.identities.live[10]
        self.now += ad.TTL * 2
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        del self.identities.live[40]
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_legacy_job_record_protects_workers_after_leader_exit(self):
        lease = self.acquire()
        with self.store.locked() as data:
            _, _, record = self.manager.locate(data, lease)
            record["job"] = self.identities.identity(40)
            self.store.save(data)
        self.now += ad.TTL * 2
        with patch.object(self.identities, "group", return_value=[self.identities.identity(41)]):
            self.manager.release(lease)
            self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        with patch.object(self.identities, "group", return_value=[]):
            self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_group_verification_failure_retains_registered_protection(self):
        lease = self.acquire()
        self.identities.live[40] = self.identities.identity(40)
        self.manager.protect_job(lease, 40)
        before = self.store.path.read_bytes()
        with patch.object(self.identities, "group", side_effect=ad.DeviceError("cannot verify")):
            with self.assertRaisesRegex(ad.DeviceError, "cannot verify"):
                self.manager.release(lease)
            self.now += ad.TTL * 2
            with self.assertRaisesRegex(ad.DeviceError, "cannot verify"):
                self.manager.sweep()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.devices.stops, 0)

    def test_watchdog_protects_workers_after_wrapper_and_leader_exit(self):
        lease = self.acquire()
        self.identities.live[40] = self.identities.identity(40)
        self.identities.live[41] = self.identities.identity(41)
        with patch.object(self.identities, "group", return_value=[self.identities.live[40],
                                                                 self.identities.live[41]]):
            self.manager.protect_job(lease, 40)
        del self.identities.live[10]
        del self.identities.live[40]
        self.now += ad.TTL * 2
        with patch.object(self.identities, "group", return_value=[self.identities.live[41]]):
            self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)
        with patch.object(self.identities, "group", return_value=[]):
            self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)

    def test_release_and_watchdog_protect_workers_after_leader_exit(self):
        for session_release in (False, True):
            with self.subTest(session_release=session_release):
                lease = self.acquire()
                self.identities.live[40] = self.identities.identity(40)
                self.identities.live[41] = self.identities.identity(41)
                with patch.object(self.identities, "group", return_value=[self.identities.live[40],
                                                                         self.identities.live[41]]):
                    self.manager.protect_job(lease, 40)
                del self.identities.live[40]
                with patch.object(self.identities, "group", return_value=[self.identities.live[41]]):
                    if session_release:
                        self.manager.release(session="session-a", owner_pid=10)
                    else:
                        self.manager.release(lease)
                    self.now += ad.TTL * 2
                    self.identities.live.pop(10, None)
                    self.manager.sweep()
                    self.assertEqual(self.devices.stops, int(session_release))
                    self.assertIn(lease, next(iter(self.state().values()))["leases"])
                with patch.object(self.identities, "group", return_value=[]):
                    self.manager.sweep()
                self.assertEqual(self.devices.stops, 1 + int(session_release))
                self.identities.live[10] = self.identities.identity(10)

    def test_shutdown_failure_is_retained_for_retry(self):
        lease = self.acquire()
        self.devices.fail_stop = True
        result = self.manager.release(lease)
        self.assertEqual(result[0]["action"], "shutdown-retry")
        self.assertTrue(self.state())
        self.devices.fail_stop = False
        self.manager.sweep()
        self.assertEqual(self.devices.stops, 1)
        self.assertEqual(self.state(), {})

    def test_ready_failure_releases_ownership_and_device(self):
        self.devices.fail_ready = True
        with self.assertRaisesRegex(ad.DeviceError, "Not ready"):
            self.acquire()
        self.assertEqual(self.devices.stops, 1)
        self.assertEqual(self.state(), {})

    def test_no_state_or_empty_state_does_not_query_devices(self):
        with patch.object(self.devices, "identity") as identity, patch.object(self.devices, "stop") as stop:
            self.manager.sweep()
            with self.store.locked() as data:
                self.store.save(data)
            self.manager.sweep()
            identity.assert_not_called()
            stop.assert_not_called()

    def test_live_leases_do_not_query_device_process_table(self):
        self.acquire()
        with patch.object(self.devices, "identity") as identity, patch.object(self.devices, "stop") as stop:
            before = self.store.path.stat().st_mtime_ns
            self.manager.sweep()
            self.assertEqual(self.store.path.stat().st_mtime_ns, before)
            identity.assert_not_called()
            stop.assert_not_called()

    def test_busy_watchdog_skips_instead_of_waiting(self):
        self.acquire()
        with self.store.locked():
            self.assertEqual(self.manager.sweep(), [])
        self.assertEqual(self.devices.stops, 0)

    def test_corrupt_state_fails_closed(self):
        self.acquire()
        self.store.path.write_text('{"version":1,"devices":{"bad":{}}}')
        with self.assertRaises(ad.DeviceError):
            self.manager.sweep()
        self.assertEqual(self.devices.stops, 0)

    def test_android_target_is_recorded(self):
        result = self.manager.acquire("android", "test_avd", 10, "session-a")
        self.assertEqual(result["target"], "emulator-5554")
        self.manager.release(result["lease"])
        self.assertEqual(self.devices.stops, 1)

    def test_ambiguous_interrupted_startup_is_preserved(self):
        self.acquire()
        with self.store.locked() as data:
            entry = next(iter(data["devices"].values()))
            entry["generation"] = None
            entry["leases"] = {}
            self.store.save(data)
        result = self.manager.sweep()
        self.assertEqual(result[0]["action"], "unconfirmed-startup-needs-inspection")
        self.assertEqual(self.devices.stops, 0)
        with self.assertRaisesRegex(ad.DeviceError, "interrupted startup"):
            self.acquire()


class ProcessGroupTests(unittest.TestCase):
    def setUp(self):
        self.identities = ad.ProcessIdentity.__new__(ad.ProcessIdentity)
        self.identities.libproc = Mock()

    def test_zero_result_with_errno_is_not_an_empty_group(self):
        def fail(*_args):
            ad.ctypes.set_errno(ad.errno.ENOMEM)
            return 0

        self.identities.libproc.proc_listpids.side_effect = fail
        with self.assertRaisesRegex(ad.DeviceError, "retaining device protection"):
            self.identities.group(40)

    def test_empty_group_does_not_inherit_stale_errno(self):
        ad.ctypes.set_errno(ad.errno.ENOMEM)
        self.identities.libproc.proc_listpids.return_value = 0
        self.assertEqual(self.identities.group(40), [])


class JobCleanupTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.leader = self.identities.identity(40)
        self.worker = self.identities.identity(41)
        self.identities.live.update({40: self.leader, 41: self.worker})
        self.group = {"pgid": 40, "boot": self.identities.boot,
                      "members": [self.leader, self.worker]}
        self.now = 0
        self.signals = []
        self.addCleanup(patch.stopall)
        patch.object(ad.time, "monotonic", side_effect=lambda: self.now).start()
        patch.object(ad.time, "sleep", side_effect=self.advance).start()
        patch.object(ad.os, "getpgid", return_value=40).start()
        patch.object(self.identities, "group", side_effect=lambda _: [
            item for pid, item in self.identities.live.items() if pid in (40, 41)]).start()
        patch.object(ad.os, "killpg", side_effect=self.signal_group).start()

    def advance(self, delay):
        self.now += delay

    def signal_group(self, pgid, number):
        self.signals.append((pgid, number))
        self.identities.live.pop(40, None)
        if number == ad.signal.SIGKILL:
            self.identities.live.pop(41, None)

    def test_escalates_after_leader_exits_but_worker_ignores_term(self):
        ad.cleanup_job(self.identities, self.group)
        self.assertEqual(self.signals, [(40, ad.signal.SIGTERM), (40, ad.signal.SIGKILL)])
        self.assertLessEqual(self.now, 16)
        self.assertNotIn(41, self.identities.live)

    def test_cleans_workers_when_leader_is_already_gone(self):
        del self.identities.live[40]
        ad.cleanup_job(self.identities, self.group)
        self.assertEqual(self.signals, [(40, ad.signal.SIGTERM), (40, ad.signal.SIGKILL)])

    def test_preserves_reused_unconfirmed_group(self):
        self.identities.live[40] = self.identities.identity(40, 2)
        del self.identities.live[41]
        with self.assertRaisesRegex(ad.DeviceError, "unconfirmed"):
            ad.cleanup_job(self.identities, self.group)
        self.assertEqual(self.signals, [])

    def test_preserves_replacement_between_term_and_kill(self):
        def replace_group(pgid, number):
            self.signals.append((pgid, number))
            self.identities.live[40] = self.identities.identity(40, 2)
            del self.identities.live[41]

        ad.os.killpg.side_effect = replace_group
        with self.assertRaisesRegex(ad.DeviceError, "unconfirmed"):
            ad.cleanup_job(self.identities, self.group)
        self.assertEqual(self.signals, [(40, ad.signal.SIGTERM)])

    def test_surviving_workers_timeout_is_bounded(self):
        ad.os.killpg.side_effect = lambda pid, number: self.signals.append((pid, number))
        with self.assertRaisesRegex(ad.DeviceError, "surviv"):
            ad.cleanup_job(self.identities, self.group)
        self.assertLessEqual(self.now, 16)
        self.assertIn(41, self.identities.live)

    def test_old_boot_does_not_signal_current_processes(self):
        self.group["boot"] = "old-boot"
        ad.cleanup_job(self.identities, self.group)
        self.assertEqual(self.signals, [])


@unittest.skipUnless(sys.platform == "darwin", "uses macOS process identities")
class SyntheticJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identities = ad.ProcessIdentity()
        self.backend = Mock()
        self.backend.identity.return_value = None
        self.backend.prepare.return_value = UDID
        self.backend.boot.return_value = self.identities.get(os.getpid())
        self.backend.stop.return_value = "synthetic-shutdown"
        self.manager = ad.Manager(ad.Store(self.root / "state.json"), self.backend, self.identities)
        self.worker = None
        self.addCleanup(self.stop_worker)
        patch.object(ad, "JOB_TERM_GRACE", 0.2).start()
        patch.object(ad, "JOB_KILL_GRACE", 2).start()
        self.addCleanup(patch.stopall)

    def stop_worker(self):
        if self.worker and self.identities.alive(self.worker):
            os.kill(self.worker["pid"], ad.signal.SIGKILL)

    def args(self, code, *extra):
        return Mock(platform="ios", device=UDID, command=[sys.executable, "-c", code, *extra])

    def wait_for_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists(), "task-owned child did not reach its checkpoint")

    def run_worker_case(self, cancel):
        ready = self.root / "worker.json"
        code = '''import json, os, signal, sys, time
pid = os.fork()
if pid == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(sys.argv[1], "w") as stream:
        json.dump(os.getpid(), stream)
    while True:
        time.sleep(1)
while not os.path.exists(sys.argv[1]):
    time.sleep(0.01)
if sys.argv[2] == "exit":
    os._exit(0)
while not os.path.exists(sys.argv[1] + ".armed"):
    time.sleep(0.01)
time.sleep(0.05)
os.kill(os.getppid(), signal.SIGTERM)
while True:
    time.sleep(1)
'''
        original_wait = ad.wait_job_unreaped

        def wait(pid):
            self.wait_for_file(ready)
            self.worker = self.identities.get(json.loads(ready.read_text()))
            self.assertIsNotNone(self.worker)
            if cancel:
                Path(str(ready) + ".armed").touch()
            original_wait(pid)

        def stop(_entry):
            self.assertFalse(self.identities.alive(self.worker), "device released with surviving worker")
            return "synthetic-shutdown"

        self.backend.stop.side_effect = stop
        with patch.object(ad, "wait_job_unreaped", side_effect=wait):
            if cancel:
                with self.assertRaises(KeyboardInterrupt):
                    ad.run_with_device(self.manager, self.args(code, str(ready), "wait"))
            else:
                self.assertEqual(ad.run_with_device(self.manager, self.args(code, str(ready), "exit")), 0)
        self.backend.stop.assert_called_once()
        self.assertFalse(self.identities.alive(self.worker))
        self.assertEqual(self.manager.status(), {"devices": []})

    def test_cancellation_kills_term_ignoring_worker_before_synthetic_release(self):
        self.run_worker_case(cancel=True)

    def test_leader_exit_kills_surviving_worker_before_synthetic_release(self):
        self.run_worker_case(cancel=False)

    def test_gated_command_preserves_argv_environment_and_exit_code(self):
        marker = self.root / "command.json"
        literal = "spaces ; $HOME $(not-a-command)"
        code = '''import json, os, sys
with open(sys.argv[1], "w") as stream:
    json.dump([sys.argv[2], os.environ["AGENT_DEVICE_TARGET"],
               os.environ["AGENT_DEVICE_LEASE"]], stream)
sys.exit(7)
'''
        previous = ad.signal.signal(ad.signal.SIGCHLD, ad.signal.SIG_IGN)
        try:
            self.assertEqual(ad.run_with_device(self.manager, self.args(code, str(marker), literal)), 7)
            self.assertEqual(ad.signal.getsignal(ad.signal.SIGCHLD), ad.signal.SIG_IGN)
        finally:
            ad.signal.signal(ad.signal.SIGCHLD, previous)
        saved = json.loads(marker.read_text())
        self.assertEqual(saved[:2], [literal, UDID])
        self.assertRegex(saved[2], r"^[a-f0-9]{32}$")
        self.backend.stop.assert_called_once()

    def test_gated_native_command_preserves_default_sigpipe(self):
        command = ["/bin/sh", "-c", "kill -PIPE $$; exit 0"]
        direct = subprocess.run(command, check=False)
        self.assertEqual(direct.returncode, -ad.signal.SIGPIPE)
        args = Mock(platform="ios", device=UDID, command=command)
        self.assertEqual(ad.run_with_device(self.manager, args), 128 + ad.signal.SIGPIPE)
        self.backend.stop.assert_called_once()

    def test_registration_failure_never_executes_user_command(self):
        marker = self.root / "must-not-run"
        args = self.args("from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker))
        with patch.object(self.manager, "protect_job", side_effect=ad.DeviceError("registration failed")):
            with self.assertRaisesRegex(ad.DeviceError, "registration failed"):
                ad.run_with_device(self.manager, args)
        self.assertFalse(marker.exists())
        self.backend.stop.assert_called_once()

    def test_wrapper_sigkill_before_ack_never_executes_user_command(self):
        pid_file = self.root / "launcher-pid"
        marker = self.root / "must-not-run"
        code = '''import importlib.machinery, os, signal, sys
from pathlib import Path
from unittest.mock import Mock
ad = importlib.machinery.SourceFileLoader("owned_device_fixture", sys.argv[1]).load_module()
manager = Mock()
manager.acquire.return_value = {"lease": "synthetic", "target": "synthetic"}
manager.identities = ad.ProcessIdentity()
def lose_wrapper(token, pid):
    Path(sys.argv[2]).write_text(str(pid))
    os.kill(os.getpid(), signal.SIGKILL)
manager.protect_job.side_effect = lose_wrapper
args = Mock(platform="ios", device="synthetic", command=[sys.executable, "-c",
    "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", sys.argv[3]])
ad.run_with_device(manager, args)
'''
        wrapper = subprocess.Popen([sys.executable, "-c", code, str(SOURCE), str(pid_file), str(marker)])
        try:
            self.assertEqual(wrapper.wait(timeout=5), -ad.signal.SIGKILL)
            self.wait_for_file(pid_file)
            pid = int(pid_file.read_text())
            self.worker = self.identities.get(pid)
            deadline = time.monotonic() + 5
            while self.worker and self.identities.alive(self.worker) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(self.worker and self.identities.alive(self.worker))
            self.assertFalse(marker.exists())
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=5)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.devices = ad.Devices(self.identities)
        self.identities.live[30] = self.identities.identity(30)
        self.entry = {"platform": "android", "device": "test_avd", "target": "emulator-5554",
                      "generation": self.identities.live[30]}
        self.wait_job = patch.object(ad, "wait_job_unreaped").start()
        patch.object(ad.os, "write", return_value=1).start()
        self.addCleanup(patch.stopall)

    def test_android_identity_matches_exact_avd(self):
        with patch.object(ad, "processes", return_value=[
                (30, "qemu-system -avd test_avd"),
                (40, "qemu-system -avd test_avd_other")]):
            self.assertEqual(self.devices.identity("android", "test_avd"), self.identities.live[30])

    def test_ios_identity_matches_bootstrap_path(self):
        self.identities.device_executable = "/usr/libexec/launchd_sim"
        with patch.object(ad, "processes", return_value=[
                (30, "launchd_sim /Devices/" + UDID + "/data/var/run/launchd_bootstrap.plist")]):
            self.assertEqual(self.devices.identity("ios", UDID), self.identities.live[30])

    def test_wrong_android_serial_is_not_killed(self):
        with patch.object(self.devices, "identity", return_value=self.entry["generation"]), \
                patch.object(ad, "command", return_value="different_avd\nOK\n") as command:
            with self.assertRaisesRegex(ad.DeviceError, "different AVD"):
                self.devices.stop(self.entry)
            self.assertEqual(command.call_count, 1)

    def test_replaced_generation_does_not_run_shutdown_commands(self):
        self.identities.live[30] = self.identities.identity(30, 2)
        with patch.object(ad, "command") as command:
            self.assertEqual(self.devices.stop(self.entry), "forgot-replaced-or-stopped-device")
            command.assert_not_called()

    def test_wrapper_releases_lease_and_exports_target_after_command_failure(self):
        manager = Mock()
        manager.acquire.return_value = {"lease": "synthetic", "target": UDID}
        manager.protect_job.return_value = {"members": []}
        child = Mock()
        def wait(timeout=None):
            ad.signal.getsignal(ad.signal.SIGALRM)(ad.signal.SIGALRM, None)
            return 7

        child.wait.side_effect = wait
        child.poll.return_value = 7
        args = Mock(platform="ios", device=UDID, command=["--", "test-command"])
        with patch.object(ad.subprocess, "Popen", return_value=child) as popen, \
                patch.object(ad, "cleanup_job"):
            self.assertEqual(ad.run_with_device(manager, args), 7)
        self.assertEqual(popen.call_args.kwargs["env"]["AGENT_DEVICE_TARGET"], UDID)
        manager.protect_job.assert_called_once_with("synthetic", child.pid)
        manager.renew.assert_called_once_with("synthetic")
        manager.release.assert_called_once_with("synthetic")

    def test_wrapper_cleans_group_even_when_leader_already_exited(self):
        manager = Mock()
        manager.acquire.return_value = {"lease": "synthetic", "target": UDID}
        manager.protect_job.return_value = {"members": []}
        child = Mock(pid=4321)
        child.wait.return_value = 0
        child.poll.return_value = 0
        args = Mock(platform="ios", device=UDID, command=["test-command"])
        with patch.object(ad.subprocess, "Popen", return_value=child), \
                patch.object(ad, "cleanup_job") as cleanup:
            ad.run_with_device(manager, args)
        cleanup.assert_called_once_with(manager.identities, manager.protect_job.return_value)
        manager.release.assert_called_once_with("synthetic")

    def test_wrapper_cancels_its_job_before_releasing_device(self):
        manager = Mock()
        manager.acquire.return_value = {"lease": "synthetic", "target": UDID}
        manager.protect_job.return_value = {"members": []}
        self.wait_job.side_effect = KeyboardInterrupt()
        child = Mock(pid=4321)
        child.poll.return_value = None
        args = Mock(platform="ios", device=UDID, command=["test-command"])
        calls = []
        child.wait.side_effect = lambda **kwargs: calls.append("reap") or 0
        manager.release.side_effect = lambda token: calls.append("release")
        with patch.object(ad.subprocess, "Popen", return_value=child), \
                patch.object(ad, "cleanup_job", side_effect=lambda *_: calls.append("cleanup")):
            with self.assertRaises(KeyboardInterrupt):
                ad.run_with_device(manager, args)
        self.assertEqual(calls, ["cleanup", "reap", "release"])

    def test_failed_command_start_still_releases_device(self):
        manager = Mock()
        manager.acquire.return_value = {"lease": "synthetic", "target": UDID}
        args = Mock(platform="ios", device=UDID, command=["missing-command"])
        with patch.object(ad.subprocess, "Popen", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                ad.run_with_device(manager, args)
        manager.release.assert_called_once_with("synthetic")

    def test_watchdog_is_short_lived_low_priority_and_five_minute_interval(self):
        path = REPO / "stow/bin/Library/LaunchAgents/com.user.agent-device-watchdog.plist.template"
        with path.open("rb") as stream:
            data = plistlib.load(stream)
        self.assertEqual(data["StartInterval"], 300)
        self.assertEqual(data["ProcessType"], "Background")
        self.assertTrue(data["LowPriorityIO"])
        self.assertNotIn("KeepAlive", data)
        self.assertEqual(data["ProgramArguments"][0], "/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
