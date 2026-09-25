#!/usr/bin/env python3
"""Test cmux-aware gaps without touching desktop state."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("cmux_gaps", REPO / "stow/aerospace/.local/bin/aerospace-cmux-gaps.py")
cmux = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cmux)


def pane(x, y=0, width=100, height=100):
    return {"surface_count": 1, "pixel_frame": {"x": x, "y": y, "width": width, "height": height}}


class ColumnTests(unittest.TestCase):
    def test_three_columns(self):
        self.assertEqual(cmux.column_count([pane(0), pane(100), pane(200)]), 3)

    def test_three_rows_are_one_column(self):
        self.assertEqual(cmux.column_count([pane(0), pane(0, 100), pane(0, 200)]), 1)

    def test_four_panes_in_two_columns(self):
        self.assertEqual(cmux.column_count([pane(0), pane(100), pane(0, 100), pane(100, 100)]), 2)

    def test_nested_splits(self):
        self.assertEqual(cmux.column_count([pane(0, height=200), pane(100), pane(200), pane(100, 100, width=200)]), 3)

    def test_tabs_do_not_count_as_columns(self):
        item = pane(0)
        item["surface_count"] = 5
        self.assertEqual(cmux.column_count([item]), 1)

    def test_overlapping_frames_do_not_count_twice(self):
        self.assertEqual(cmux.column_count([pane(0), pane(0), pane(0)]), 1)

    def test_empty_dock_and_missing_geometry_are_ignored(self):
        self.assertEqual(cmux.column_count([pane(0), {"dock_scope": "global", "surface_count": 0}, {"surface_count": 1}]), 1)

    def test_invalid_frames_are_ignored(self):
        self.assertEqual(cmux.column_count([pane(0, width=0), pane(0, width=-1), pane(float("nan")), pane(0, height="bad")]), 0)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.windows = {"windows": [
            {"id": "window-a", "selected_workspace_id": "workspace-a"},
            {"id": "window-b", "selected_workspace_id": "workspace-b"},
        ]}
        self.terminals = {"terminals": [
            {"window_id": "window-a", "window_number": 10},
            {"window_id": "window-b", "window_number": 20},
        ]}
        self.panes = {"a": [pane(0), pane(100), pane(200)], "b": [pane(0)]}
        self.calls = []

    def query(self, *args):
        self.calls.append(args)
        if args == ("rpc", "window.list"):
            return self.windows
        if args == ("debug-terminals",):
            return self.terminals
        params = json.loads(args[2])
        self.assertEqual(args[:2], ("rpc", "pane.list"))
        suffix = params["window_id"][-1]
        self.assertEqual(params, {"window_id": f"window-{suffix}", "workspace_id": f"workspace-{suffix}"})
        return {"panes": self.panes[suffix]}

    def test_matches_native_window_id_and_explicit_selected_workspace(self):
        with patch.object(cmux, "query", side_effect=self.query):
            self.assertEqual(cmux.window_columns(), {"10": 3, "20": 1})

    def test_two_columns_are_retained(self):
        self.panes["a"] = [pane(0), pane(100)]
        with patch.object(cmux, "query", side_effect=self.query):
            self.assertEqual(cmux.window_columns(), {"10": 2, "20": 1})

    def test_missing_geometry_counts_as_one_window(self):
        self.panes["a"] = [{"surface_count": 1}]
        with patch.object(cmux, "query", side_effect=self.query):
            self.assertEqual(cmux.window_columns(), {"10": 1, "20": 1})

    def test_conflicting_window_identity_does_not_expand(self):
        self.terminals["terminals"].append({"window_id": "window-b", "window_number": 10})
        with patch.object(cmux, "query", side_effect=self.query):
            self.assertEqual(cmux.window_columns(), {"20": 1})

    def test_workspace_switch_during_sample_does_not_expand(self):
        def changing_query(*args):
            result = self.query(*args)
            if "pane.list" in args:
                self.windows = {"windows": [{"id": "window-a", "selected_workspace_id": "other"}]}
            return result
        with patch.object(cmux, "query", side_effect=changing_query):
            with self.assertRaises(ValueError):
                cmux.window_columns()

    def test_rpc_uses_timeout_and_ignores_caller_workspace(self):
        result = subprocess.CompletedProcess([], 0, '{"panes": []}', "")
        with patch.object(cmux.subprocess, "run", return_value=result) as run:
            self.assertEqual(cmux.query("rpc", "pane.list", "{}"), {"panes": []})
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertNotIn("CMUX_WORKSPACE_ID", run.call_args.kwargs["env"])
        self.assertIn("uuids", run.call_args.args[0])


class FailureTests(unittest.TestCase):
    def test_unavailable_or_invalid_api_uses_ordinary_gaps(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired("cmux", 2),
                      ValueError(), KeyError("panes"), TypeError()):
            with self.subTest(error=type(error)), patch.object(cmux, "needs_full_width", side_effect=error):
                self.assertEqual(cmux.main(["1", "10"]), 1)

    def test_invalid_arguments_do_not_read_cache(self):
        with patch.object(cmux, "needs_full_width") as lookup:
            for args in ([], ["0"], ["10"], ["bad", "10"], ["0", "10"],
                         ["1", "10", "20"], ["1", "-10"]):
                with self.subTest(args=args):
                    self.assertEqual(cmux.main(args), 1)
        lookup.assert_not_called()

    def test_portable_events_do_not_run_gap_worker(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cmux, "STATE", Path(directory)), \
                patch.object(cmux.subprocess, "run") as run:
            (Path(directory) / "display-mode").write_text("portable")
            cmux.recompute()
        run.assert_not_called()


class CacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.cache = self.state / "cmux-columns.json"
        self.start_patch("STATE", self.state)
        self.boot = self.start_patch("boot_id", return_value="boot-a")
        self.sample = self.start_patch("window_columns", return_value={"10": 3})
        self.recompute = self.start_patch("recompute")
        sleep = patch.object(cmux.time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        monotonic = patch.object(cmux.time, "monotonic", return_value=100)
        monotonic.start()
        self.addCleanup(monotonic.stop)

    def start_patch(self, name, *args, **kwargs):
        patcher = patch.object(cmux, name, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def test_two_cmux_columns_plus_another_window_use_full_width(self):
        self.sample.return_value = {"10": 2}
        cmux.refresh()
        self.assertEqual(cmux.main(["2", "10"]), 0)
        self.assertEqual(cmux.main(["1", "10"]), 1)

    def test_columns_from_multiple_cmux_windows_are_combined(self):
        self.sample.return_value = {"10": 2, "20": 1, "30": 4}
        cmux.refresh()
        self.assertEqual(cmux.main(["2", "10", "20"]), 0)
        self.assertEqual(cmux.main(["2", "20", "40"]), 1)
        self.assertEqual(cmux.main(["1", "10", "10"]), 1)

    def test_refresh_and_lookup_do_not_need_socket_access_from_aerospace(self):
        cmux.refresh()
        with patch.object(cmux, "query") as query:
            self.assertEqual(cmux.main(["1", "10"]), 0)
            self.assertEqual(cmux.main(["1", "20"]), 1)
        query.assert_not_called()
        self.sample.assert_called_once()
        self.recompute.assert_called_once()
        self.boot.return_value = "boot-b"
        self.assertEqual(cmux.main(["1", "10"]), 1)
        self.sample.return_value = {}
        cmux.refresh()
        self.assertEqual(cmux.main(["1", "10"]), 1)

    def test_shrinking_keeps_columns_until_two_second_settle(self):
        widths = []
        self.sample.return_value = {"10": 2}
        self.recompute.side_effect = lambda: widths.append(cmux.main(["2", "10"]))
        cmux.refresh()
        self.assertEqual(widths, [0])
        self.assertNotIn(call(2), self.sleep.call_args_list)
        widths.clear()
        self.sample.return_value = {"10": 1}
        cmux.refresh()
        self.assertEqual(widths, [0, 1])
        self.assertIn(call(2), self.sleep.call_args_list)

    def test_three_to_two_columns_only_restores_single_window_gaps(self):
        cmux.refresh()
        widths = []
        self.recompute.side_effect = lambda: widths.append((cmux.main(["1", "10"]), cmux.main(["2", "10"])))
        self.sample.return_value = {"10": 2}
        cmux.refresh()
        self.assertEqual(widths, [(0, 0), (1, 0)])

    def test_return_to_wide_cancels_pending_shrink(self):
        cmux.refresh()
        self.sample.return_value = {}

        def switch_back(delay):
            if delay == 2:
                self.sample.return_value = {"10": 3}
                cmux.refresh()

        self.sleep.side_effect = switch_back
        cmux.refresh()
        self.assertEqual(cmux.main(["1", "10"]), 0)
        self.assertEqual(self.sample.call_count, 3)

    def test_another_narrow_workspace_restarts_shrink_timer(self):
        cmux.refresh()
        self.sample.return_value = {}
        waits = []

        def switch_again(delay):
            if delay == 2:
                waits.append(cmux.main(["1", "10"]))
                if len(waits) == 1:
                    cmux.refresh()

        self.sleep.side_effect = switch_again
        cmux.refresh()
        self.assertEqual(waits, [0, 0])
        self.assertEqual(cmux.main(["1", "10"]), 1)
        self.assertEqual(self.sample.call_count, 4)

    def test_settle_resamples_before_shrinking(self):
        self.sample.side_effect = [{"10": 3}, {}, {"10": 3}]
        cmux.refresh()
        cmux.refresh()
        self.assertEqual(cmux.main(["1", "10"]), 0)

    def test_expansion_in_another_window_does_not_wait_for_pending_shrink(self):
        widths = []
        self.recompute.side_effect = lambda: widths.append((cmux.main(["1", "10"]), cmux.main(["1", "20"])))
        cmux.refresh()
        self.sample.return_value = {"20": 3}
        cmux.refresh()
        self.assertEqual(widths, [(0, 1), (0, 0), (1, 0)])

    def test_closed_single_column_windows_are_removed_without_delay(self):
        self.sample.return_value = {"10": 1}
        cmux.refresh()
        self.sleep.reset_mock()
        self.sample.return_value = {}
        cmux.refresh()
        self.assertEqual(json.loads(self.cache.read_text())["window_columns"], {})
        self.sleep.assert_called_once_with(0.2)

    def test_old_boot_cache_does_not_delay_shrinking(self):
        self.cache.write_text(json.dumps({"boot_id": "boot-b", "window_columns": {"10": 3}}))
        self.sample.return_value = {}
        cmux.refresh()
        self.assertEqual(cmux.main(["1", "10"]), 1)
        self.sleep.assert_called_once_with(0.2)

    def test_failed_refresh_falls_back_after_settling(self):
        cmux.refresh()
        self.sample.side_effect = ValueError
        widths = []
        self.recompute.side_effect = lambda: widths.append(cmux.main(["1", "10"]))
        cmux.refresh()
        self.assertEqual(json.loads(self.cache.read_text())["window_columns"], {})
        self.assertEqual(widths, [0, 1])

    def test_missing_legacy_and_invalid_cache_use_ordinary_gaps(self):
        self.assertEqual(cmux.main(["2", "10"]), 1)
        for snapshot in ("bad json", "[]", '{"boot_id":"boot-a","wide_windows":[10]}',
                         *[json.dumps({"boot_id": "boot-a", "window_columns": columns})
                           for columns in ([], {"10": 0}, {"10": -1}, {"10": "3"}, {"10": True}, {"bad": 3})]):
            with self.subTest(snapshot=snapshot):
                self.cache.write_text(snapshot)
                self.assertEqual(cmux.main(["2", "10"]), 1)
        self.cache.write_text('{"boot_id":"boot-a","wide_windows":[10]}')
        self.sample.return_value = {"10": 2}
        cmux.refresh()
        self.assertEqual(cmux.main(["2", "10"]), 0)
        self.sleep.assert_called_once_with(0.2)

class SizingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        state = patch.object(cmux, "STATE", self.state)
        state.start()
        self.addCleanup(state.stop)
        boot = patch.object(cmux, "boot_id", return_value="boot-a")
        boot.start()
        self.addCleanup(boot.stop)
        self.windows = [self.window(10, "cmux"), self.window(20, "Finder")]
        self.focused = "1"
        self.resizes = []
        self.cache_columns({"10": 2})

    def window(self, number, app):
        return {"window-id": number, "app-name": app, "window-layout": "h_tiles",
                "workspace-root-container-layout": "h_tiles", "window-is-fullscreen": False}

    def cache_columns(self, columns):
        (self.state / "cmux-columns.json").write_text(json.dumps({
            "boot_id": "boot-a", "window_columns": columns,
        }))

    def command(self, *args):
        if args[0] == "list-workspaces":
            return self.focused
        if args[0] == "list-windows":
            return json.dumps(self.windows)
        if args[0] == "resize":
            self.resizes.append(args)
            return ""
        self.fail(f"Unexpected AeroSpace command: {args}")

    def resize(self, gap=0):
        with patch.object(cmux, "aerospace", side_effect=self.command):
            cmux.resize_pair("1", 3360, gap)

    def test_two_cmux_columns_give_companion_one_third(self):
        self.resize()
        self.assertEqual(self.resizes, [("resize", "--window-id", "20", "width", "1120")])

    def test_return_to_one_column_restores_equal_widths(self):
        self.resize()
        self.cache_columns({"10": 1})
        self.resize(gap=566)
        self.assertEqual(self.resizes[-1], ("resize", "--window-id", "20", "width", "1114"))
        self.resize(gap=566)
        self.assertEqual(len(self.resizes), 2)

    def test_unchanged_layout_does_not_override_manual_resize(self):
        self.resize()
        self.resize()
        self.assertEqual(len(self.resizes), 1)

    def test_ordinary_pair_is_not_balanced_without_previous_ownership(self):
        self.cache_columns({"10": 1})
        self.resize(gap=566)
        self.assertEqual(self.resizes, [])

    def test_column_count_change_updates_proportions(self):
        self.resize()
        self.cache_columns({"10": 3})
        self.resize()
        self.assertEqual(self.resizes[-1], ("resize", "--window-id", "20", "width", "840"))

    def test_two_cmux_windows_use_both_column_counts(self):
        self.windows[1]["app-name"] = "cmux"
        self.cache_columns({"10": 2, "20": 3})
        self.resize()
        self.assertEqual(self.resizes, [("resize", "--window-id", "10", "width", "1344")])

    def test_floating_windows_are_excluded(self):
        floating = self.window(30, "Things")
        floating["window-layout"] = "floating"
        self.windows.append(floating)
        self.resize()
        self.assertEqual(len(self.resizes), 1)

    def test_stacked_accordion_nested_and_fullscreen_layouts_are_untouched(self):
        for field, value in (("window-layout", "v_tiles"), ("window-layout", "h_accordion"),
                             ("workspace-root-container-layout", "v_tiles"),
                             ("window-is-fullscreen", True)):
            with self.subTest(field=field, value=value):
                self.windows = [self.window(10, "cmux"), self.window(20, "Finder")]
                self.windows[0][field] = value
                self.resize()
        self.assertEqual(self.resizes, [])

    def test_one_or_three_tiled_windows_are_untouched(self):
        self.windows.append(self.window(30, "Finder"))
        self.resize()
        self.windows = self.windows[:1]
        self.resize()
        self.assertEqual(self.resizes, [])

    def test_inactive_workspace_is_untouched(self):
        self.focused = "2"
        self.resize()
        self.assertEqual(self.resizes, [])

    def test_focus_change_before_resize_is_untouched(self):
        def command(*args):
            result = self.command(*args)
            if args[0] == "list-windows":
                self.focused = "2"
            return result
        with patch.object(cmux, "aerospace", side_effect=command):
            cmux.resize_pair("1", 3360, 0)
        self.assertEqual(self.resizes, [])

    def test_topology_change_before_resize_is_untouched(self):
        samples = 0

        def command(*args):
            nonlocal samples
            if args[0] == "list-windows":
                samples += 1
                if samples == 2:
                    self.windows.append(self.window(30, "Finder"))
            return self.command(*args)

        with patch.object(cmux, "aerospace", side_effect=command):
            cmux.resize_pair("1", 3360, 0)
        self.assertEqual(self.resizes, [])

    def test_other_workspace_does_not_lose_resize_ownership(self):
        self.resize()
        self.focused = "2"
        self.resize()
        self.focused = "1"
        self.cache_columns({"10": 1})
        self.resize(gap=566)
        self.assertEqual(len(self.resizes), 2)

    def test_resize_cli_dispatches_without_cmux_queries(self):
        with patch.object(cmux, "resize_pair") as resize, patch.object(cmux, "query") as query:
            self.assertEqual(cmux.main(["--resize", "1", "3360", "0"]), 0)
        resize.assert_called_once_with("1", 3360, 0)
        query.assert_not_called()

    def test_changed_pair_does_not_inherit_restore(self):
        self.resize()
        self.windows = [self.window(30, "cmux"), self.window(40, "Finder")]
        self.cache_columns({"30": 1})
        self.resize(gap=566)
        self.assertEqual(len(self.resizes), 1)

    def test_gap_and_cache_disagreement_waits_for_next_pass(self):
        self.resize(gap=566)
        self.assertEqual(self.resizes, [])

    def test_failed_resize_is_retried(self):
        def command(*args):
            if args[0] == "resize":
                raise subprocess.CalledProcessError(1, "aerospace")
            return self.command(*args)
        with patch.object(cmux, "aerospace", side_effect=command):
            with self.assertRaises(subprocess.CalledProcessError):
                cmux.resize_pair("1", 3360, 0)
        self.resize()
        self.assertEqual(len(self.resizes), 1)

    def test_old_boot_does_not_restore_previous_ownership(self):
        self.resize()
        self.cache_columns({"10": 1})
        with patch.object(cmux, "boot_id", return_value="boot-b"):
            self.resize(gap=566)
        self.assertEqual(len(self.resizes), 1)


class WorkerTests(unittest.TestCase):
    def run_worker(self, tree, columns=None, suppressed=False, expected_width=None, managed=False):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            state = home / ".cache/aerospace-gaps"
            state.mkdir(parents=True)
            (state / "display-mode").write_text("docked")
            if columns is not None:
                (state / "cmux-columns.json").write_text(json.dumps({"boot_id": "boot-a", "window_columns": columns}))
            if suppressed:
                (state / "suppressed-workspace").write_text("1")
            scripts = home / ".dotfiles/scripts"
            scripts.mkdir(parents=True)
            (scripts / "aerospace-gaps-lib.sh").symlink_to(REPO / "scripts/aerospace-gaps-lib.sh")
            source = home / ".dotfiles/stow/aerospace/.aerospace.toml"
            source.parent.mkdir(parents=True)
            source.write_text((REPO / "stow/aerospace/.aerospace.toml").read_text())
            runtime = home / ".aerospace.toml"
            runtime.write_text(source.read_text())
            if managed:
                runtime.write_text(source.read_text().replace('"DELL U4025QW" = 600', '"DELL U4025QW" = 0'))
                (state / "cmux-sizing.json").write_text(json.dumps({"boot_id": "boot-a", "workspaces": {
                    "1": {"windows": [10, 20], "columns": [2, 1], "screen_width": 3360, "gap": 0},
                }}))
            helper = home / ".local/bin/aerospace-cmux-gaps.py"
            helper.parent.mkdir(parents=True)
            helper.write_text(
                "import json,os,pathlib,runpy,sys\n"
                "if sys.argv[1] != '--resize':\n"
                "    pathlib.Path.home().joinpath('called').write_text(' '.join(sys.argv[1:]))\n"
                f"module = runpy.run_path({str(Path(SPEC.origin))!r})\n"
                "module['main'].__globals__['boot_id'] = lambda: 'boot-a'\n"
                "def aerospace(*args):\n"
                "    if args[0] == 'list-workspaces': return '1'\n"
                "    if args[0] == 'list-windows':\n"
                "        return json.dumps([{'window-id': int(number), 'app-name': app, 'window-layout': layout,\n"
                "            'workspace-root-container-layout': 'h_tiles', 'window-is-fullscreen': False}\n"
                "            for ws,number,app,layout in (line.split('|') for line in os.environ['TREE'].splitlines()) if ws == '1'])\n"
                "    if args[0] == 'resize':\n"
                "        pathlib.Path.home().joinpath('resized').write_text(json.dumps(args))\n"
                "        return ''\n"
                "    raise AssertionError(args)\n"
                "module['main'].__globals__['aerospace'] = aerospace\n"
                "sys.exit(module['main'](sys.argv[1:]))\n"
            )
            command = r'''
                aerospace() {
                    case "$1" in
                        list-monitors) printf '[{"monitor-name":"DELL U4025QW"}]' ;;
                        list-workspaces) printf '1\n' ;;
                        list-windows)
                            if [ "$2" = --all ]; then printf '%s\n' "$TREE";
                            else printf '%s\n' "$TREE" | awk -F'|' '$1 == 1 {print $4}'; fi ;;
                        reload-config) printf 'reload\n' >> "$HOME/reloads" ;;
                        *) return 1 ;;
                    esac
                }
                osascript() { printf '3360\n'; }
                sleep() { :; }
                . "$WORKER"
            '''
            result = subprocess.run(["/bin/bash", "-c", command], env={
                **os.environ, "HOME": str(home), "TREE": tree,
                "WORKER": str(REPO / "scripts/aerospace-auto-gaps.sh"),
            }, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            gap = int(re.search(r'outer.left = .*? = (\d+)', runtime.read_text())[1])
            self.assertEqual(gap, int(re.search(r'outer.right = .*? = (\d+)', runtime.read_text())[1]))
            called = (home / "called").read_text() if (home / "called").exists() else None
            if expected_width is None:
                self.assertFalse((home / "resized").exists())
            else:
                resized = json.loads((home / "resized").read_text())
                self.assertEqual(resized, ["resize", "--window-id", "20", "width", str(expected_width)])
            return gap, called

    def test_single_three_column_cmux_window_expands_to_zero_gaps(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles", {"10": 3}), (0, "1 10"))

    def test_two_cmux_columns_with_other_tiled_window_expands(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|h_tiles", {"10": 2}, expected_width=1120), (0, "2 10"))

    def test_returning_to_one_column_restores_gap_and_equal_widths(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|h_tiles", {"10": 1},
                                         expected_width=1114, managed=True), (566, "2 10"))

    def test_unchanged_pair_is_not_resized_by_worker(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|h_tiles", {"10": 2},
                                         managed=True), (0, "2 10"))

    def test_multiple_cmux_windows_combine_columns(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|cmux|h_tiles", {"10": 2, "20": 1}, expected_width=1120), (0, "2 10 20"))

    def test_two_columns_alone_or_unavailable_cmux_use_regular_preset(self):
        for columns in ({"10": 2}, None):
            with self.subTest(columns=columns):
                self.assertEqual(self.run_worker("1|10|cmux|h_tiles", columns), (566, "1 10"))

    def test_one_cmux_column_and_one_other_window_keep_regular_preset(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|h_tiles", {"10": 1}), (566, "2 10"))

    def test_floating_and_other_workspace_cmux_do_not_count(self):
        gap, called = self.run_worker("1|10|cmux|floating\n2|20|cmux|h_tiles\n1|30|Finder|h_tiles", {"10": 3, "20": 3})
        self.assertGreater(gap, 0)
        self.assertIsNone(called)

    def test_other_workspace_columns_do_not_expand_selected_cmux(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n2|20|cmux|h_tiles", {"10": 2, "20": 3}), (566, "1 10"))

    def test_floating_companion_does_not_add_to_total(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|floating", {"10": 2}), (566, "1 10"))

    def test_manual_override_wins(self):
        self.assertEqual(self.run_worker("1|10|cmux|h_tiles\n1|20|Finder|h_tiles", {"10": 2}, suppressed=True), (600, None))


if __name__ == "__main__":
    unittest.main()
