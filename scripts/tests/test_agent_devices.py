import importlib.machinery
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
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
        self.live = {10: self.identity(10), 20: self.identity(20)}

    def identity(self, pid, start=1):
        return {"pid": pid, "start": start, "boot": "synthetic-boot"}

    def get(self, pid):
        return self.live.get(pid)

    def alive(self, identity):
        return self.get(identity["pid"]) == identity

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


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.identities = Identities()
        self.devices = ad.Devices(self.identities)
        self.identities.live[30] = self.identities.identity(30)
        self.entry = {"platform": "android", "device": "test_avd", "target": "emulator-5554",
                      "generation": self.identities.live[30]}

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
        child = Mock()
        def wait():
            ad.signal.getsignal(ad.signal.SIGALRM)(ad.signal.SIGALRM, None)
            return 7

        child.wait.side_effect = wait
        child.poll.return_value = 7
        args = Mock(platform="ios", device=UDID, command=["--", "test-command"])
        with patch.object(ad.subprocess, "Popen", return_value=child) as popen:
            self.assertEqual(ad.run_with_device(manager, args), 7)
        self.assertEqual(popen.call_args.kwargs["env"]["AGENT_DEVICE_TARGET"], UDID)
        manager.protect_job.assert_called_once_with("synthetic", child.pid)
        manager.renew.assert_called_once_with("synthetic")
        manager.release.assert_called_once_with("synthetic")

    def test_wrapper_cancels_its_job_before_releasing_device(self):
        manager = Mock()
        manager.acquire.return_value = {"lease": "synthetic", "target": UDID}
        child = Mock(pid=4321)
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        child.poll.return_value = None
        args = Mock(platform="ios", device=UDID, command=["test-command"])
        calls = []
        manager.release.side_effect = lambda token: calls.append("release")
        with patch.object(ad.subprocess, "Popen", return_value=child), \
                patch.object(ad.os, "killpg", side_effect=lambda pid, sig: calls.append((pid, sig))):
            with self.assertRaises(KeyboardInterrupt):
                ad.run_with_device(manager, args)
        self.assertEqual(calls, [(4321, ad.signal.SIGTERM), "release"])

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
