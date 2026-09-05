#!/usr/bin/env python3
"""Fixed fictional stdio exchange for the browser pilot, never a backend client."""

import json
import os
from pathlib import Path
import sys


def main():
    journal = Path(sys.argv[1])

    def record(event, **fields):
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "pid": os.getpid(), **fields}) + "\n")

    def send(message):
        print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)

    record("start")
    try:
        for line in sys.stdin:
            message = json.loads(line)
            method = message.get("method")
            if method == "initialize":
                capabilities = message["params"]["capabilities"]
                record("initialize", capabilities=sorted(capabilities))
                send({"id": message["id"], "result": {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fictional-pilot", "version": "1.0.0"}}})
            elif method == "notifications/initialized":
                for index, request in enumerate(("sampling/createMessage", "elicitation/create")):
                    send({"id": f"probe-{index}", "method": request, "params": {}})
            elif method == "tools/list":
                names = ["allowed_echo", "blocked_echo", "unexpected_tool", "echo-name", "echo_name"]
                send({"id": message["id"], "result": {"tools": [
                    {"name": name, "description": "Fictional test only.", "inputSchema": {
                        "type": "object", "properties": {"large": {"type": "boolean"}}}}
                    for name in names]}})
            elif method == "tools/call":
                name = message["params"]["name"]
                record("call", name=name)
                text = "fictional output\n" * 5000 if message["params"].get("arguments", {}).get("large") else "fictional echo"
                send({"id": message["id"], "result": {"content": [{"type": "text", "text": text}]}})
            elif method == "ping":
                send({"id": message["id"], "result": {}})
            elif method is None and str(message.get("id", "")).startswith("probe-"):
                record("probe-response", code=message.get("error", {}).get("code"))
            elif "id" in message:
                send({"id": message["id"], "error": {"code": -32601, "message": "Fictional method absent"}})
    finally:
        record("exit")


if __name__ == "__main__":
    main()
