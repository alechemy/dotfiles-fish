#!/usr/bin/env python3
"""Test headphone audio routing without changing live devices."""

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "stow/btd700-audio/.local/bin/btd700-audio-watcher"
loader = importlib.machinery.SourceFileLoader("btd700_audio", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
audio = importlib.util.module_from_spec(spec)
loader.exec_module(audio)

WEBCAM = "HD Pro Webcam C920"
ANTLION = "Antlion USB Microphone"


class FakeDevices:
    def __init__(self, names, selected):
        self.names = set(names)
        self.selected = selected
        self.selections = []
        self.fail_select = False

    def available(self):
        return self.names

    def current(self):
        return self.selected

    def select(self, name):
        if self.fail_select:
            raise RuntimeError("selection failed")
        self.selected = name
        self.selections.append(name)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.log_patch = patch.object(audio, "log")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.outputs = FakeDevices(
            [audio.BTD_OUTPUT, *audio.FALLBACK_OUTPUTS], audio.FALLBACK_OUTPUTS[0]
        )
        self.inputs = FakeDevices([WEBCAM, ANTLION], ANTLION)
        self.watcher = audio.Watcher(None, self.outputs, self.inputs)

    def test_connection_selects_dongle_and_webcam(self):
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.assertEqual(self.outputs.selected, audio.BTD_OUTPUT)
        self.assertEqual(self.inputs.selected, WEBCAM)
        self.assertIsNone(self.watcher.pending_action)

    def test_disconnection_selects_existing_output_fallback_and_antlion(self):
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.watcher.receive_state(audio.STATE_DISCONNECTED)
        self.assertEqual(self.outputs.selected, audio.FALLBACK_OUTPUTS[0])
        self.assertEqual(self.inputs.selected, ANTLION)

    def test_removing_connected_dongle_restores_antlion(self):
        self.watcher.device = 42
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.outputs.names.remove(audio.BTD_OUTPUT)
        self.watcher.device_removed(42)
        self.assertEqual(self.outputs.selected, audio.FALLBACK_OUTPUTS[0])
        self.assertEqual(self.inputs.selected, ANTLION)

    def test_disconnected_startup_restores_antlion_without_changing_other_output(self):
        self.outputs.selected = "DELL U4025QW"
        self.inputs.selected = WEBCAM
        self.watcher.receive_state(audio.STATE_DISCONNECTED)
        self.assertEqual(self.outputs.selected, "DELL U4025QW")
        self.assertEqual(self.outputs.selections, [])
        self.assertEqual(self.inputs.selected, ANTLION)

    def test_disconnected_startup_switches_away_from_dongle(self):
        self.outputs.selected = audio.BTD_OUTPUT
        self.inputs.selected = WEBCAM
        self.watcher.receive_state(audio.STATE_DISCONNECTED)
        self.assertEqual(self.outputs.selected, audio.FALLBACK_OUTPUTS[0])
        self.assertEqual(self.inputs.selected, ANTLION)

    def test_missing_webcam_does_not_block_output_and_is_retried(self):
        self.inputs.names.remove(WEBCAM)
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.assertEqual(self.outputs.selected, audio.BTD_OUTPUT)
        self.assertEqual(self.inputs.selected, ANTLION)
        self.assertIsNotNone(self.watcher.pending_action)
        self.inputs.names.add(WEBCAM)
        self.watcher.apply_pending_action()
        self.assertEqual(self.inputs.selected, WEBCAM)
        self.assertEqual(self.outputs.selections, [audio.BTD_OUTPUT])
        self.assertIsNone(self.watcher.pending_action)

    def test_output_failure_does_not_block_microphone_selection(self):
        self.outputs.fail_select = True
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.assertEqual(self.inputs.selected, WEBCAM)
        self.assertIsNotNone(self.watcher.pending_action)
        self.outputs.fail_select = False
        self.watcher.apply_pending_action()
        self.assertEqual(self.outputs.selected, audio.BTD_OUTPUT)
        self.assertEqual(self.inputs.selections, [WEBCAM])

    def test_input_failure_does_not_block_output_and_is_retried(self):
        self.inputs.fail_select = True
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.assertEqual(self.outputs.selected, audio.BTD_OUTPUT)
        self.assertIsNotNone(self.watcher.pending_action)
        self.inputs.fail_select = False
        self.watcher.apply_pending_action()
        self.assertEqual(self.inputs.selected, WEBCAM)
        self.assertIsNone(self.watcher.pending_action)

    def test_new_state_replaces_pending_connection(self):
        self.inputs.names.remove(WEBCAM)
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.watcher.receive_state(audio.STATE_DISCONNECTED)
        self.inputs.names.add(WEBCAM)
        self.assertEqual(self.inputs.selected, ANTLION)
        self.assertIsNone(self.watcher.pending_action)

    def test_repeated_state_does_not_override_manual_selection(self):
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.inputs.selected = ANTLION
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.assertEqual(self.inputs.selected, ANTLION)
        self.assertEqual(self.inputs.selections, [WEBCAM])

    def test_missing_antlion_keeps_current_input_and_retries(self):
        self.watcher.receive_state(audio.STATE_CONNECTED)
        self.inputs.names.remove(ANTLION)
        self.watcher.receive_state(audio.STATE_DISCONNECTED)
        self.assertEqual(self.outputs.selected, audio.FALLBACK_OUTPUTS[0])
        self.assertEqual(self.inputs.selected, WEBCAM)
        self.assertIsNotNone(self.watcher.pending_action)
        self.inputs.names.add(ANTLION)
        self.watcher.apply_pending_action()
        self.assertEqual(self.inputs.selected, ANTLION)


class DeviceCommandTests(unittest.TestCase):
    def test_commands_use_configured_device_type(self):
        for device_type in ("input", "output"):
            with self.subTest(device_type=device_type):
                devices = audio.AudioDevices("/fake/SwitchAudioSource", device_type)
                result = subprocess.CompletedProcess([], 0, "Device\n", "")
                with patch.object(audio.subprocess, "run", return_value=result) as run:
                    self.assertEqual(devices.available(), {"Device"})
                    self.assertEqual(devices.current(), "Device")
                    devices.select("Device")
                self.assertEqual(
                    [call.args[0] for call in run.call_args_list],
                    [
                        ["/fake/SwitchAudioSource", "-a", "-t", device_type],
                        ["/fake/SwitchAudioSource", "-c", "-t", device_type],
                        ["/fake/SwitchAudioSource", "-s", "Device", "-t", device_type],
                    ],
                )


if __name__ == "__main__":
    unittest.main()
