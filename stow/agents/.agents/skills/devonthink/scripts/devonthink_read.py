#!/usr/bin/env python3
"""Bounded, response-projecting client for three official DEVONthink MCP reads."""

from __future__ import annotations

import argparse
import json
import math
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

DEFAULT_SERVER = Path(
    "/Applications/DEVONthink.app/Contents/Library/LoginItems/"
    "DEVONthink MCP.app/Contents/MacOS/DEVONthink MCP"
)
PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION}
DEFAULT_TIMEOUT = 20.0
MAX_QUERY_LENGTH = 4096
MAX_RESPONSE_CHARS = 256_000
MAX_FRAME_BYTES = 2_097_152
MAX_TRANSPORT_BYTES = 8_388_608
MAX_QUEUED_FRAMES = 8
MAX_JSON_DEPTH = 64
MAX_SEARCH_RESULTS = 100
MAX_METADATA_RECORDS = 50
SEARCH_RESULT_FIELDS = ("uuid", "name")
SORT_FIELDS = (
    "score", "name", "title", "kind", "size", "length", "rating", "label",
    "wordcount", "charactercount", "added", "additionDate", "created",
    "creationDate", "modified", "modificationDate", "opened", "accessDate",
    "due", "dueDate", "location", "path", "comment", "tags", "unsorted",
)


class ClientError(RuntimeError):
    """A safe, user-facing MCP client failure."""


class StdioMCPClient:
    """Minimal JSON-lines MCP client with no tool-discovery or generic-call API."""

    def __init__(self, executable: Path, timeout: float) -> None:
        self.executable = executable
        self.timeout = timeout
        self.process: subprocess.Popen[bytes] | None = None
        self.messages: queue.Queue[bytes | None] = queue.Queue(MAX_QUEUED_FRAMES)
        self.reader_failed = threading.Event()
        self.reader: threading.Thread | None = None

    def __enter__(self) -> "StdioMCPClient":
        try:
            self.process = subprocess.Popen(
                [str(self.executable), "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ClientError("could not start the DEVONthink MCP server") from exc

        assert self.process.stdout is not None
        self.reader = threading.Thread(target=self._read_messages, daemon=True)
        self.reader.start()
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "devonthink-read", "version": "1.0"},
                    },
                }
            )
            response = self._receive(1)
            if "error" in response:
                raise ClientError("DEVONthink MCP initialization failed")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ClientError("DEVONthink MCP returned an invalid initialization result")
            protocol = result.get("protocolVersion")
            if not isinstance(protocol, str) or protocol not in SUPPORTED_PROTOCOL_VERSIONS:
                raise ClientError("DEVONthink MCP negotiated an unsupported protocol version")
            capabilities = result.get("capabilities")
            if not isinstance(capabilities, dict) or not isinstance(
                capabilities.get("tools"), dict
            ):
                raise ClientError("DEVONthink MCP returned invalid tool capabilities")
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                }
            )
            return self
        except Exception:
            self._stop()
            raise

    def __exit__(self, *_: object) -> None:
        self._stop()

    def _stop(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            if self.reader is not None:
                self.reader.join(timeout=1)
            if self.process.stdout is not None and (
                self.reader is None or not self.reader.is_alive()
            ):
                self.process.stdout.close()
            self.process = None

    def _read_messages(self) -> None:
        """Queue bounded raw frames; never parse or report backend content here."""
        assert self.process is not None and self.process.stdout is not None
        stream = self.process.stdout
        total = 0
        try:
            while True:
                line = stream.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    self.messages.put_nowait(None)
                    return
                total += len(line)
                if len(line) > MAX_FRAME_BYTES or total > MAX_TRANSPORT_BYTES:
                    raise ValueError
                if not line.endswith(b"\n"):
                    raise ValueError
                self.messages.put_nowait(line)
        except (OSError, ValueError, queue.Full):
            self.reader_failed.set()
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        try:
            self.process.stdin.write(
                (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ClientError("DEVONthink MCP closed before completing the request") from exc

    def _receive(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            if self.reader_failed.is_set():
                raise ClientError("DEVONthink MCP transport failed or exceeded its bounds")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientError("timed out waiting for DEVONthink MCP")
            try:
                frame = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ClientError("timed out waiting for DEVONthink MCP") from exc
            if self.reader_failed.is_set():
                raise ClientError("DEVONthink MCP transport failed or exceeded its bounds")
            if frame is None:
                raise ClientError("DEVONthink MCP exited before completing the request")
            message = parse_json(frame)
            if not isinstance(message, dict):
                raise ClientError("DEVONthink MCP returned an invalid JSON-RPC response")
            if "method" in message:
                self._handle_server_message(message)
                continue
            if message.get("id") != request_id:
                continue
            has_result = "result" in message
            has_error = "error" in message
            if message.get("jsonrpc") != "2.0" or has_result == has_error:
                raise ClientError("DEVONthink MCP returned an invalid JSON-RPC response")
            return message

    def _handle_server_message(self, message: dict[str, Any]) -> None:
        if "id" not in message:
            return
        response: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": message.get("id"),
        }
        if message.get("jsonrpc") == "2.0" and message.get("method") == "ping":
            response["result"] = {}
        else:
            response["error"] = {
                "code": -32601,
                "message": "Client method not supported",
            }
        self._send(response)

    def call_read_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        arguments = validated_tool_arguments(name, arguments)
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        response = self._receive(2)
        if "error" in response:
            raise ClientError("DEVONthink MCP rejected the request")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ClientError("DEVONthink MCP returned an invalid result")
        if result.get("isError", False) is not False:
            raise ClientError("DEVONthink MCP tool call failed")
        content = result.get("content")
        if (
            not isinstance(content, list)
            or len(content) != 1
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
            or not isinstance(content[0].get("text"), str)
        ):
            raise ClientError("DEVONthink MCP returned an unsupported result format")
        text = content[0]["text"]
        if len(text) > MAX_RESPONSE_CHARS:
            raise ClientError(
                f"DEVONthink MCP response exceeds {MAX_RESPONSE_CHARS} characters"
            )
        payload = projected_tool_result(name, arguments, parse_json(text))
        serialized_output(payload)
        return payload


def parse_json(value: bytes | str) -> Any:
    """Parse bounded UTF-8 JSON, rejecting excessive nesting and nonfinite numbers."""
    try:
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        payload = json.loads(value)
        pending = [(payload, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > MAX_JSON_DEPTH:
                raise ValueError
            if isinstance(item, dict):
                for key in item:
                    key.encode("utf-8")
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError
        return payload
    except (ValueError, RecursionError) as exc:
        raise ClientError("DEVONthink MCP returned invalid or non-JSON content") from exc


def projected_tool_result(name: str, arguments: dict[str, Any], payload: Any) -> Any:
    """Validate vendor 4.4 envelopes and release only operation-owned fields."""
    invalid = "DEVONthink MCP returned an unsupported result shape"
    if name == "list_custom_metadata_fields":
        if not isinstance(payload, list):
            raise ClientError(invalid)
        fields = []
        for field in payload:
            if (
                not isinstance(field, dict)
                or not isinstance(field.get("identifier"), str)
                or not isinstance(field.get("type"), str)
                or not isinstance(field.get("disabled"), bool)
                or (field["disabled"] and not arguments["include_disabled"])
            ):
                raise ClientError(invalid)
            projected = {key: field[key] for key in ("identifier", "type", "disabled")}
            if "title" in field:
                if not isinstance(field["title"], str):
                    raise ClientError(invalid)
                projected["title"] = field["title"]
            if "values" in field:
                if (
                    field["type"] != "set"
                    or not isinstance(field["values"], list)
                    or any(not isinstance(value, str) for value in field["values"])
                ):
                    raise ClientError(invalid)
                projected["values"] = field["values"]
            fields.append(projected)
        return fields

    wrapped = isinstance(payload, dict) and isinstance(payload.get("results"), list)
    rows = payload["results"] if wrapped else payload
    if name == "get_record_custom_metadata":
        if isinstance(payload, dict) and not wrapped and len(arguments["uuids"]) == 1:
            return payload
        limit = len(arguments["uuids"])
    elif name == "search_records":
        limit = arguments["limit"]
    else:
        raise ClientError("refusing non-allowlisted DEVONthink operation")
    if not isinstance(rows, list):
        raise ClientError(invalid)
    if len(rows) > limit:
        raise ClientError("DEVONthink MCP returned too many records")
    projected_rows = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ClientError(invalid)
        record_uuid = canonical_uuid(row.get("uuid"))
        if name == "search_records":
            if not isinstance(row.get("name"), str):
                raise ClientError(invalid)
            projected_rows.append({"uuid": record_uuid, "name": row["name"]})
        else:
            if record_uuid not in arguments["uuids"] or record_uuid in seen:
                raise ClientError("DEVONthink MCP returned unexpected record UUIDs")
            if not isinstance(row.get("metadata"), dict):
                raise ClientError(invalid)
            seen.add(record_uuid)
            projected_rows.append({"uuid": record_uuid, "metadata": row["metadata"]})
    return {"results": projected_rows} if wrapped else projected_rows


def serialized_output(payload: Any) -> str:
    """Bound the complete stdout JSON, including indentation and its final newline."""
    output = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if len(output) > MAX_RESPONSE_CHARS:
        raise ClientError(f"DEVONthink MCP output exceeds {MAX_RESPONSE_CHARS} characters")
    return output


def canonical_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ClientError("UUID values must be strings")
    try:
        return str(uuid.UUID(value)).upper()
    except ValueError as exc:
        raise ClientError("invalid UUID") from exc


def valid_uuid(value: str) -> str:
    try:
        return canonical_uuid(value)
    except ClientError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc


def bounded_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_SEARCH_RESULTS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_SEARCH_RESULTS}")
    return parsed


def nonnegative_offset(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClientError(f"{name} must be an integer")
    return value


def validated_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ClientError("DEVONthink tool arguments must be an object")

    if name == "list_custom_metadata_fields":
        if set(arguments) != {"include_disabled"}:
            raise ClientError("invalid metadata-field arguments")
        include_disabled = arguments["include_disabled"]
        if not isinstance(include_disabled, bool):
            raise ClientError("include_disabled must be boolean")
        return {"include_disabled": include_disabled}

    if name == "search_records":
        allowed = {
            "query",
            "database_uuid",
            "group_uuid",
            "limit",
            "offset",
            "sort",
            "fields",
        }
        required = {"query", "limit", "offset", "sort", "fields"}
        if set(arguments) - allowed or not required <= set(arguments):
            raise ClientError("invalid search arguments")
        query = arguments["query"]
        if not isinstance(query, str) or not query.strip():
            raise ClientError("search query must not be empty")
        query = query.strip()
        if len(query) > MAX_QUERY_LENGTH:
            raise ClientError(f"search query exceeds {MAX_QUERY_LENGTH} characters")
        limit = _integer(arguments["limit"], "limit")
        if not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ClientError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
        offset = _integer(arguments["offset"], "offset")
        if offset < 0:
            raise ClientError("offset must be non-negative")
        sort = arguments["sort"]
        if sort not in SORT_FIELDS:
            raise ClientError("unsupported sort field")
        if arguments["fields"] != list(SEARCH_RESULT_FIELDS):
            raise ClientError("search result fields are fixed to uuid and name")
        validated: dict[str, Any] = {
            "query": query,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "fields": list(SEARCH_RESULT_FIELDS),
        }
        for key in ("database_uuid", "group_uuid"):
            if key in arguments:
                validated[key] = canonical_uuid(arguments[key])
        return validated

    if name == "get_record_custom_metadata":
        if set(arguments) - {"uuids", "database_uuid"} or "uuids" not in arguments:
            raise ClientError("invalid custom-metadata arguments")
        uuids = arguments["uuids"]
        if not isinstance(uuids, list) or not 1 <= len(uuids) <= MAX_METADATA_RECORDS:
            raise ClientError(
                f"between 1 and {MAX_METADATA_RECORDS} record UUIDs are required"
            )
        validated = {"uuids": [canonical_uuid(value) for value in uuids]}
        if "database_uuid" in arguments:
            validated["database_uuid"] = canonical_uuid(arguments["database_uuid"])
        return validated

    raise ClientError("refusing non-allowlisted DEVONthink operation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only access to three demonstrated DEVONthink operations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fields = subparsers.add_parser("fields", help="list custom metadata field definitions")
    fields.add_argument("--include-disabled", action="store_true")

    search = subparsers.add_parser("search", help="search records")
    search.add_argument("query")
    search.add_argument("--database-uuid", type=valid_uuid)
    search.add_argument("--group-uuid", type=valid_uuid)
    search.add_argument("--limit", type=bounded_limit, default=20)
    search.add_argument("--offset", type=nonnegative_offset, default=0)
    search.add_argument("--sort", choices=SORT_FIELDS, default="score")

    metadata = subparsers.add_parser("metadata", help="read record custom metadata")
    metadata.add_argument("uuid", nargs="+", type=valid_uuid)
    metadata.add_argument("--database-uuid", type=valid_uuid)

    return parser


def request_for(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    if args.command == "fields":
        return "list_custom_metadata_fields", {"include_disabled": args.include_disabled}

    if args.command == "search":
        arguments: dict[str, Any] = {
            "query": args.query,
            "limit": args.limit,
            "offset": args.offset,
            "sort": args.sort,
            "fields": list(SEARCH_RESULT_FIELDS),
        }
        for key in ("database_uuid", "group_uuid"):
            value = getattr(args, key)
            if value:
                arguments[key] = value
        return "search_records", arguments

    arguments = {"uuids": args.uuid}
    if args.database_uuid:
        arguments["database_uuid"] = args.database_uuid
    return "get_record_custom_metadata", arguments


def main(
    argv: list[str] | None = None,
    *,
    executable: Path = DEFAULT_SERVER,
    timeout: float = DEFAULT_TIMEOUT,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        tool, arguments = request_for(args)
        arguments = validated_tool_arguments(tool, arguments)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ClientError("timeout must be a finite positive number")
        with StdioMCPClient(executable, timeout) as client:
            payload = client.call_read_tool(tool, arguments)
        sys.stdout.write(serialized_output(payload))
        return 0
    except ClientError as exc:
        print(f"devonthink-read: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print("devonthink-read: could not complete the request", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
