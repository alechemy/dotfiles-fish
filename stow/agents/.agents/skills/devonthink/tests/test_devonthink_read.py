#!/usr/bin/env python3
"""Synthetic protocol tests for the focused DEVONthink read client."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / "scripts" / "devonthink_read.py"
SPEC = importlib.util.spec_from_file_location("devonthink_read", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
FAKE_SERVER = r"""#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ["FAKE_MCP_LOG"]
mode = os.environ.get("FAKE_MCP_MODE", "ok")
payload_override = os.environ.get("FAKE_MCP_PAYLOAD")


def record(message):
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(message, separators=(",", ":")) + "\n")


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def request_client(request_id, method):
    emit({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})
    response = json.loads(sys.stdin.readline())
    record(response)


for line in sys.stdin:
    message = json.loads(line)
    record(message)
    request_id = message.get("id")
    if message.get("method") == "initialize":
        if mode == "hang_initialize":
            continue
        if mode == "ping_initialize":
            request_client(request_id, "ping")
        if mode == "unsupported_request":
            request_client(request_id, "roots/list")
        if mode == "malformed_before_initialize":
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
        if mode == "invalid_utf8":
            sys.stdout.buffer.write(b"\xff\n")
            sys.stdout.buffer.flush()
            continue
        if mode == "oversized_frame":
            sys.stdout.write("x" * (2_097_152 + 1))
            sys.stdout.flush()
            continue
        if mode == "deep_frame":
            sys.stdout.write("[" * 2000 + "0" + "]" * 2000 + "\n")
            sys.stdout.flush()
            continue
        if mode == "null_initialize":
            result = None
        else:
            result = {
                "protocolVersion": (
                    [] if mode == "nonstring_protocol"
                    else "1900-01-01" if mode == "bad_protocol" else "2025-03-26"
                ),
                "capabilities": (
                    []
                    if mode == "bad_capabilities"
                    else {} if mode == "missing_tools" else {"tools": {}}
                ),
                "serverInfo": {"name": "synthetic", "version": "1"},
            }
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    elif message.get("method") == "tools/call":
        if mode == "hang":
            continue
        if mode == "ping_tool":
            request_client(request_id, "ping")
        name = message["params"]["name"]
        if mode == "rpc_error":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "synthetic RPC rejection"},
            }
            emit(response)
            continue
        if mode == "invalid_rpc":
            emit({"id": request_id, "result": {}})
            continue
        if mode == "error":
            result = {
                "isError": True,
                "content": [{"type": "text", "text": "synthetic rejection"}],
            }
        elif mode == "oversized":
            result = {
                "isError": False,
                "content": [
                    {"type": "text", "text": json.dumps({"value": "x" * 256_001})}
                ],
            }
        elif mode == "non_json":
            result = {
                "isError": False,
                "content": [{"type": "text", "text": "not JSON"}],
            }
        else:
            payloads = {
                "list_custom_metadata_fields": [
                    {"identifier": "FictionalField", "type": "string", "disabled": False}
                ],
                "search_records": [
                    {
                        "uuid": "33333333-3333-4333-8333-333333333333",
                        "name": "Synthetic Record",
                    }
                ],
                "get_record_custom_metadata": {
                    "results": [
                        {
                            "uuid": "11111111-1111-4111-8111-111111111111",
                            "metadata": {"FictionalField": "value"},
                        }
                    ]
                },
            }
            result = {
                "isError": False,
                "content": [{"type": "text", "text": payload_override or json.dumps(payloads[name])}],
            }
        response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    else:
        continue
    emit(response)
"""


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.log = root / "requests.jsonl"
        self.server = root / "fake-devonthink-mcp"
        self.server.write_text(textwrap.dedent(FAKE_SERVER))
        self.server.chmod(0o755)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_client(
        self, *args: str, mode: str = "ok", timeout: float = 2.0
    ) -> types.SimpleNamespace:
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {
            "FAKE_MCP_LOG": str(self.log),
            "FAKE_MCP_MODE": mode,
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, environment))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            stack.enter_context(contextlib.redirect_stderr(stderr))
            try:
                returncode = MODULE.main(
                    list(args), executable=self.server, timeout=timeout
                )
            except SystemExit as exc:
                returncode = int(exc.code)
        return types.SimpleNamespace(
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def requests(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def tool_call(self) -> dict:
        return next(item for item in self.requests() if item.get("method") == "tools/call")

    def test_fields_uses_only_the_allowlisted_tool(self) -> None:
        result = self.run_client("fields")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["identifier"], "FictionalField")
        self.assertEqual(
            [item.get("method") for item in self.requests()],
            ["initialize", "notifications/initialized", "tools/call"],
        )
        call = self.tool_call()["params"]
        self.assertEqual(call["name"], "list_custom_metadata_fields")
        self.assertEqual(call["arguments"], {"include_disabled": False})

    def test_server_pings_with_colliding_ids_are_answered(self) -> None:
        for mode, request_id in (("ping_initialize", 1), ("ping_tool", 2)):
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                result = self.run_client("fields", mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                response = next(
                    item
                    for item in self.requests()
                    if item.get("id") == request_id
                    and item.get("result") == {}
                    and "method" not in item
                )
                self.assertEqual(response["jsonrpc"], "2.0")

    def test_unsupported_server_request_is_safely_rejected(self) -> None:
        result = self.run_client("fields", mode="unsupported_request")
        self.assertEqual(result.returncode, 0, result.stderr)
        response = next(
            item
            for item in self.requests()
            if item.get("error", {}).get("code") == -32601
        )
        self.assertEqual(response["error"]["message"], "Client method not supported")

    def test_initialize_result_and_protocol_are_validated(self) -> None:
        cases = (
            ("null_initialize", "invalid initialization result"),
            ("bad_protocol", "unsupported protocol version"),
            ("nonstring_protocol", "unsupported protocol version"),
            ("bad_capabilities", "invalid tool capabilities"),
            ("missing_tools", "invalid tool capabilities"),
        )
        for mode, expected in cases:
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                result = self.run_client("fields", mode=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_malformed_frame_fails_safely(self) -> None:
        result = self.run_client("fields", mode="malformed_before_initialize")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("not-json", result.stderr)

    def test_search_bounds_and_forwards_explicit_read_options(self) -> None:
        result = self.run_client(
            "search",
            "kind:markdown fictional",
            "--database-uuid",
            UUID_A,
            "--group-uuid",
            UUID_B,
            "--limit",
            "7",
            "--offset",
            "2",
            "--sort",
            "modified",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["name"], "Synthetic Record")
        call = self.tool_call()["params"]
        self.assertEqual(call["name"], "search_records")
        self.assertEqual(
            call["arguments"],
            {
                "query": "kind:markdown fictional",
                "database_uuid": UUID_A.upper(),
                "group_uuid": UUID_B.upper(),
                "limit": 7,
                "offset": 2,
                "sort": "modified",
                "fields": ["uuid", "name"],
            },
        )

    def test_metadata_accepts_a_bounded_uuid_batch(self) -> None:
        result = self.run_client(
            "metadata", UUID_A, UUID_B, "--database-uuid", UUID_A
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["results"][0]["metadata"]["FictionalField"],
            "value",
        )
        call = self.tool_call()["params"]
        self.assertEqual(call["name"], "get_record_custom_metadata")
        self.assertEqual(call["arguments"]["uuids"], [UUID_A.upper(), UUID_B.upper()])

    def test_invalid_limit_never_starts_the_server(self) -> None:
        result = self.run_client("search", "fictional", "--limit", "101")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_server_error_is_nonzero_without_structured_output(self) -> None:
        result = self.run_client("fields", mode="error")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("tool call failed", result.stderr)
        self.assertNotIn("synthetic rejection", result.stderr)

    def test_timeout_is_nonzero(self) -> None:
        result = self.run_client("fields", mode="hang", timeout=0.1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out", result.stderr)

    def test_oversized_response_is_rejected(self) -> None:
        result = self.run_client("fields", mode="oversized")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("exceeds 256000 characters", result.stderr)

    def test_initialize_timeout_is_nonzero(self) -> None:
        result = self.run_client("fields", mode="hang_initialize", timeout=0.1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out", result.stderr)

    def test_invalid_rpc_and_content_responses_are_rejected(self) -> None:
        cases = (
            ("rpc_error", "rejected the request"),
            ("invalid_rpc", "invalid JSON-RPC response"),
            ("non_json", "non-JSON content"),
        )
        for mode, expected in cases:
            with self.subTest(mode=mode):
                self.log.unlink(missing_ok=True)
                result = self.run_client("fields", mode=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)

    def test_invalid_cli_arguments_never_start_the_server(self) -> None:
        cases = (
            ("search", " "),
            ("search", "x" * 4097),
            ("search", "fictional", "--offset", "-1"),
            ("metadata", "not-a-uuid"),
            ("metadata", *([UUID_A] * 51)),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments[:3]):
                self.log.unlink(missing_ok=True)
                result = self.run_client(*arguments)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.log.exists())

    def test_imported_callers_cannot_expand_read_tool_arguments(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        search = {
            "query": "fictional",
            "limit": 1,
            "offset": 0,
            "sort": "score",
            "fields": ["content"],
        }
        with self.assertRaises(MODULE.ClientError):
            client.call_read_tool("search_records", search)
        with self.assertRaises(MODULE.ClientError):
            client.call_read_tool(
                "list_custom_metadata_fields",
                {"include_disabled": False, "extra": True},
            )
        with self.assertRaises(MODULE.ClientError):
            client.call_read_tool(
                "get_record_custom_metadata",
                {"uuids": [UUID_A] * 51},
            )


    def run_payload(self, payload, *args: str):
        with mock.patch.dict(os.environ, {"FAKE_MCP_PAYLOAD": json.dumps(payload)}):
            return self.run_client(*args)

    def test_search_projects_both_vendor_shapes(self) -> None:
        row = {"uuid": UUID_A, "name": "Fictional record", "content": "PRIVATE"}
        for payload in ([row], {"results": [row], "debug": "PRIVATE"}):
            with self.subTest(wrapped=isinstance(payload, dict)):
                result = self.run_payload(payload, "search", "fictional")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = [{"uuid": UUID_A, "name": "Fictional record"}]
                if isinstance(payload, dict):
                    expected = {"results": expected}
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertNotIn("PRIVATE", result.stdout + result.stderr)

    def test_search_rejects_unknown_shapes_invalid_rows_and_over_limit(self) -> None:
        row = {"uuid": UUID_A, "name": "Fictional record"}
        for payload in (None, {}, {"records": [row]}, [None],
                        [{"uuid": UUID_A}], [{"uuid": "PRIVATE", "name": "x"}],
                        [{"uuid": UUID_A, "name": None}], [row, row]):
            with self.subTest(payload=payload):
                result = self.run_payload(payload, "search", "fictional", "--limit", "1")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("PRIVATE", result.stderr)

    def test_metadata_projects_vendor_batch_shapes_and_json_values(self) -> None:
        metadata = {"mdfictional": {"nested": [True, None, 1, 1.5, "value"]}}
        row = {"uuid": UUID_A, "metadata": metadata, "content": "PRIVATE"}
        for payload in ([row], {"results": [row], "debug": "PRIVATE"}):
            result = self.run_payload(payload, "metadata", UUID_A, UUID_B)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = [{"uuid": UUID_A, "metadata": metadata}]
            if isinstance(payload, dict):
                expected = {"results": expected}
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertNotIn("PRIVATE", result.stdout + result.stderr)

    def test_metadata_singleton_preserves_bare_dictionary(self) -> None:
        payload = {"mdfictional": ["value", 2, False, None]}
        result = self.run_payload(payload, "metadata", UUID_A)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), payload)

    def test_metadata_rejects_unrequested_duplicate_and_malformed_rows(self) -> None:
        row = {"uuid": UUID_A, "metadata": {}}
        for payload in ([{"uuid": "33333333-3333-4333-8333-333333333333", "metadata": {}}],
                        [row, row], [row, row, row], [{"uuid": UUID_A}],
                        [{"uuid": UUID_A, "metadata": []}], {"records": [row]},
                        {"mdfictional": "value"}, None):
            with self.subTest(payload=payload):
                result = self.run_payload(payload, "metadata", UUID_A, UUID_B)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")

    def test_fields_project_defined_properties_and_optional_title_values(self) -> None:
        payload = [
            {"identifier": "mdfictional", "type": "string", "disabled": False,
             "extra": "PRIVATE"},
            {"identifier": "mdchoice", "title": "Fictional choice", "type": "set",
             "disabled": False, "values": ["A", "B"], "extra": "PRIVATE"},
        ]
        result = self.run_payload(payload, "fields")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            {key: value for key, value in field.items() if key != "extra"}
            for field in payload
        ])

    def test_fields_reject_unknown_or_invalid_shapes(self) -> None:
        field = {"identifier": "mdfictional", "type": "string", "disabled": False}
        for payload in ({"fields": [field]}, [None], [{}],
                        [{**field, "disabled": 0}], [{**field, "title": None}],
                        [{**field, "type": []}], [{**field, "values": [1]}],
                        [{**field, "disabled": True}]):
            with self.subTest(payload=payload):
                result = self.run_payload(payload, "fields")
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
        result = self.run_payload([{**field, "disabled": True}], "fields", "--include-disabled")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_final_stdout_cap_includes_indentation_and_newline(self) -> None:
        payload = {"mdfictional": [0] * 38_000}
        self.assertLess(len(json.dumps(payload)), MODULE.MAX_RESPONSE_CHARS)
        self.assertGreater(len(json.dumps(payload, indent=2)), MODULE.MAX_RESPONSE_CHARS)
        result = self.run_payload(payload, "metadata", UUID_A)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("256000", result.stderr)

    def test_reader_failures_are_safe_and_prompt(self) -> None:
        for mode in ("invalid_utf8", "oversized_frame", "deep_frame"):
            with self.subTest(mode=mode):
                result = self.run_client("fields", mode=mode)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertNotIn("timed out", result.stderr)

    def test_raw_frames_and_queue_are_bounded_before_json_parsing(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        client.process = types.SimpleNamespace(stdout=io.BytesIO(b"{}\n" * 100))
        with mock.patch.object(MODULE.json, "loads") as loads:
            client._read_messages()
        loads.assert_not_called()
        self.assertEqual(client.messages.qsize(), MODULE.MAX_QUEUED_FRAMES)
        with self.assertRaises(MODULE.ClientError):
            client._receive(1)

    def test_total_transport_bytes_are_bounded(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        client.process = types.SimpleNamespace(stdout=io.BytesIO(b"{}\n" * 4))
        with mock.patch.object(MODULE, "MAX_TRANSPORT_BYTES", 10):
            client._read_messages()
        with self.assertRaises(MODULE.ClientError):
            client._receive(1)

    def test_reader_oserror_does_not_escape_thread(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        stream = mock.Mock()
        stream.readline.side_effect = OSError("PRIVATE")
        client.process = types.SimpleNamespace(stdout=stream)
        client._read_messages()
        with self.assertRaises(MODULE.ClientError) as raised:
            client._receive(1)
        self.assertNotIn("PRIVATE", str(raised.exception))

    def test_excessive_json_depth_and_nonfinite_values_fail_safely(self) -> None:
        nested = "value"
        for _ in range(70):
            nested = [nested]
        for payload in ({"mdfictional": nested}, {"mdfictional": float("nan")},
                        {"mdfictional": float("inf")}, {"mdfictional": "\ud800"},
                        {"\ud800": "fictional"}):
            result = self.run_payload(payload, "metadata", UUID_A)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)

    def test_mixed_content_and_malformed_error_flags_are_rejected(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        content = [{"type": "text", "text": "[]"}]
        results = [
            {"content": content + [{"type": "image", "data": "PRIVATE"}]},
            {"content": content * 2},
            {"content": [{"type": "text", "text": None}]},
            {"content": content, "isError": "PRIVATE"},
            {"content": content, "isError": 0},
        ]
        for result in results:
            with self.subTest(result=result), mock.patch.object(client, "_send"), \
                    mock.patch.object(client, "_receive", return_value={"result": result}):
                with self.assertRaises(MODULE.ClientError) as raised:
                    client.call_read_tool("list_custom_metadata_fields", {"include_disabled": False})
                self.assertNotIn("PRIVATE", str(raised.exception))

    def test_stdout_cap_boundary_counts_unicode_and_newline(self) -> None:
        payload = {"mdfictional": "é\n"}
        output = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with mock.patch.object(MODULE, "MAX_RESPONSE_CHARS", len(output)):
            self.assertEqual(MODULE.serialized_output(payload), output)
        with mock.patch.object(MODULE, "MAX_RESPONSE_CHARS", len(output) - 1):
            with self.assertRaises(MODULE.ClientError):
                MODULE.serialized_output(payload)

    def test_oversized_raw_frame_is_never_queued_or_parsed(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        stream = io.BytesIO(b"x" * (MODULE.MAX_FRAME_BYTES + 10))
        client.process = types.SimpleNamespace(stdout=stream)
        with mock.patch.object(MODULE.json, "loads") as loads:
            client._read_messages()
        loads.assert_not_called()
        self.assertEqual(stream.tell(), MODULE.MAX_FRAME_BYTES + 1)
        self.assertIsNone(client.messages.get_nowait())
        self.assertTrue(client.reader_failed.is_set())

    def test_server_start_error_does_not_echo_exception(self) -> None:
        with mock.patch.object(MODULE.subprocess, "Popen", side_effect=OSError("PRIVATE")):
            result = self.run_client("fields")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("PRIVATE", result.stderr)

    def test_timeout_stops_process_and_reader(self) -> None:
        with mock.patch.dict(os.environ, {"FAKE_MCP_LOG": str(self.log), "FAKE_MCP_MODE": "hang"}):
            client = MODULE.StdioMCPClient(self.server, 2)
            with self.assertRaises(MODULE.ClientError):
                with client:
                    process = client.process
                    client.timeout = 0.1
                    client.call_read_tool("list_custom_metadata_fields", {"include_disabled": False})
        self.assertIsNotNone(process.poll())
        self.assertIsNone(client.process)
        self.assertFalse(client.reader.is_alive())
        self.assertTrue(process.stdout.closed)

    def test_cleanup_does_not_close_a_pipe_still_owned_by_the_reader(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        process = mock.Mock()
        client.process = process
        client.reader = mock.Mock()
        client.reader.is_alive.return_value = True
        client._stop()
        client.reader.join.assert_called_once_with(timeout=1)
        process.stdout.close.assert_not_called()
        self.assertIsNone(client.process)

    def test_client_has_no_generic_tool_escape_hatch(self) -> None:
        client = MODULE.StdioMCPClient(Path("/unused"), 1)
        with self.assertRaises(MODULE.ClientError):
            client.call_read_tool("create_record", {})


if __name__ == "__main__":
    unittest.main()
