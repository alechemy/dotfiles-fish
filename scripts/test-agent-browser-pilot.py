#!/usr/bin/env python3
"""Opt-in fictional checks using separately approved private dependencies."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import plistlib
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time

RESOURCES = Path(__file__).resolve().parent / "browser-pilot"


def environment(root, node, pi_root):
    env = {"PATH": f"{node.parent}:/usr/bin:/bin", "PILOT_PI_ROOT": str(pi_root),
           "MCP_UI_VIEWER": "none", "MCP_OUTPUT_GUARD": "1", "PI_OFFLINE": "1",
           "PI_MCP_ADAPTER_TEST_AUTH_STORE": "memory", "PI_MCP_ADAPTER_DISABLE_AUTH_CACHE": "1",
           "PI_MCP_ADAPTER_DISABLE_KEYRING_RECOVERY": "1", "JITI_FS_CACHE": "0",
           "CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS": "1", "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
           "npm_config_update_notifier": "false"}
    for key, name in {"HOME": "home", "PI_CODING_AGENT_DIR": "agent", "TMPDIR": "tmp",
                      "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
                      "XDG_STATE_HOME": "state", "npm_config_cache": "npm-cache"}.items():
        path = root / name
        path.mkdir(mode=0o700)
        env[key] = str(path)
    for key in ("npm_config_userconfig", "npm_config_globalconfig"):
        path = root / key
        path.write_text("")
        env[key] = str(path)
    return env


def configuration(root):
    server = {"command": "/usr/bin/python3", "args": [str(root / "synthetic-mcp.py"), str(root / "journal.jsonl")],
              "cwd": str(root), "lifecycle": "lazy", "protocolVersion": "legacy", "auth": False, "oauth": False,
              "directTools": False, "exposeResources": False, "includeTools": ["allowed_echo", "blocked_echo"],
              "excludeTools": ["blocked_echo"], "approveTools": True, "debug": False, "trace": False}
    return {"imports": [], "settings": {"hostConfigDiscovery": "off", "agentPluginPaths": [], "scriptMode": False,
            "sampling": False, "samplingAutoApprove": False, "elicitation": False, "autoAuth": False,
            "directTools": False, "approveTools": True, "idleTimeout": 1, "requestTimeoutMs": 5000,
            "trace": {"enabled": False}}, "mcpServers": {"pilot": server, "disabled": {**server, "disabled": True}}}


def validate_configuration(config):
    for server in config["mcpServers"].values():
        allowlist = server.get("includeTools")
        if not isinstance(allowlist, list) or not allowlist or not all(
            isinstance(name, str) and name and set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-")
            for name in allowlist
        ):
            raise ValueError("Pilot requires a nonempty exact-name allowlist")


def tree_digest(root):
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Package source symlink requires review")
        if path.is_file():
            entries.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(root).as_posix() + "\n")
    return hashlib.sha256("".join(entries).encode()).hexdigest()


def validate_inputs(node, pi_root, adapter):
    for path in (node, pi_root, adapter):
        if not path.is_absolute() or not path.exists():
            raise ValueError("All executable/source paths must be existing absolute paths")
    for root, name, version in ((pi_root, "@earendil-works/pi-coding-agent", "0.85.1"),
                                 (adapter, "pi-mcp-adapter", "2.32.1")):
        manifest = json.loads((root / "package.json").read_text())
        if (manifest["name"], manifest["version"]) != (name, version):
            raise ValueError("Reviewed package version mismatch")
    provenance = json.loads((RESOURCES / "provenance.json").read_text())
    if hashlib.sha256((RESOURCES / "package-lock.json").read_bytes()).hexdigest() != provenance["lockSha256"]:
        raise ValueError("Reviewed dependency lock mismatch")
    if hashlib.sha256((pi_root / "dist/core/extensions/loader.js").read_bytes()).hexdigest() != provenance["piLoaderSha256"]:
        raise ValueError("Reviewed Pi loader mismatch")
    for name, expected in provenance["piImportFiles"].items():
        if hashlib.sha256((pi_root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Reviewed Pi transitive entrypoint mismatch")
    modules = adapter.parent
    actual = set()
    for path in modules.iterdir():
        if path.name.startswith("."):
            continue
        if path.name.startswith("@"):
            actual.update(f"{path.name}/{child.name}" for child in path.iterdir())
        else:
            actual.add(path.name)
    if actual != set(provenance["installedPackages"]):
        raise ValueError("Private dependency inventory mismatch")
    for name, expected in provenance["installedPackages"].items():
        if tree_digest(modules / name) != expected["treeSha256"]:
            raise ValueError(f"Reviewed source mismatch: {name}")


def stop_group(child):
    forced = False
    for attempt in range(100):
        child.poll()
        try:
            os.killpg(child.pid, 0)
            os.killpg(child.pid, signal.SIGTERM if attempt < 50 else signal.SIGKILL)
        except ProcessLookupError:
            child.wait(timeout=10)
            return forced
        forced = True
        child.poll()
        time.sleep(0.1)
    raise RuntimeError("Owned process group survived cleanup")


def run(node, pi_root, adapter, mode):
    with tempfile.TemporaryDirectory(prefix="pi-browser-run-") as temporary:
        root = Path(temporary)
        env = environment(root, node, pi_root)
        env["PILOT_MODE"] = mode
        version = subprocess.run([str(node), "--version"], env=env, cwd=root, check=True, capture_output=True, text=True)
        if version.stdout.strip() != "v24.18.0":
            raise ValueError("Reviewed Node version mismatch")
        config = configuration(root)
        validate_configuration(config)
        (root / "config.json").write_text(json.dumps(config))
        ambient = {"mcpServers": {"ambient": {**config["mcpServers"]["pilot"], "lifecycle": "eager"}}}
        (root / ".mcp.json").write_text(json.dumps(ambient))
        (root / "home/.mcp.json").write_text(json.dumps(ambient))
        (root / "agent/mcp-cache.json").write_text('{"version":1,"servers":{}}')
        for name in ("synthetic-mcp.py", "load.mjs"):
            shutil.copyfile(RESOURCES / name, root / name)
        (root / "check.ts").write_text((RESOURCES / "check.ts").read_text().replace("__ADAPTER__", str(adapter)))
        with (root / "diagnostics.log").open("w") as log:
            child = subprocess.Popen([str(node), str(root / "load.mjs")], cwd=root, env=env,
                                     stdout=log, stderr=log, start_new_session=True)
            try:
                code = child.wait(timeout=120)
            finally:
                forced = stop_group(child)
        if code or forced:
            # Only this owned fictional run can be copied for private diagnosis.
            failed = Path(tempfile.mkdtemp(prefix="pi-browser-failure-"))
            for name in ("diagnostics.log", "loader-errors.json"):
                if (root / name).exists():
                    shutil.copyfile(root / name, failed / name)
            raise RuntimeError(f"Fictional {mode} failed; private diagnostics: {failed}")
        rows = [json.loads(line) for line in (root / "journal.jsonl").read_text().splitlines()]
        for pid in {row["pid"] for row in rows}:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            raise RuntimeError("Owned fixture process survived shutdown")
        result = json.loads((root / "summary.json").read_text())
    if root.exists():
        raise RuntimeError("Owned runtime cleanup failed")
    return {"mode": mode, **result, "ownedRuntimeRemoved": True}


BROWSER_TOOLS = ["list_pages", "navigate_page", "take_snapshot", "click", "fill", "take_screenshot",
                 "list_console_messages", "get_console_message", "list_network_requests", "get_network_request",
                 "performance_start_trace", "performance_stop_trace"]


def validate_browser(server, app):
    provenance = json.loads((RESOURCES / "provenance.json").read_text())
    if hashlib.sha256((RESOURCES / "browser-catalog.json").read_bytes()).hexdigest() != provenance["browserCatalog"]["sha256"]:
        raise ValueError("Reviewed browser catalog expectations mismatch")
    if not server.is_absolute() or tree_digest(server) != provenance["chromeServer"]["treeSha256"]:
        raise ValueError("Reviewed Chrome server source mismatch")
    if (not app.is_absolute() or app.name != "Google Chrome.app" or app.is_symlink()
            or not app.parent.name.startswith("pi-browser-app-")
            or app.parent.parent.resolve() != Path(tempfile.gettempdir()).resolve()):
        raise ValueError("Expected private signed Chrome app")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (info["CFBundleIdentifier"], info["CFBundleShortVersionString"]) != ("com.google.Chrome", "152.0.7977.83"):
        raise ValueError("Reviewed browser identity mismatch")
    executable = app / "Contents/MacOS/Google Chrome"
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, check=True, timeout=120)
    identity = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(app)], capture_output=True, text=True, check=True, timeout=30).stderr
    for line in provenance["signedBrowser"]["signatureIdentity"]:
        if line not in identity.splitlines():
            raise ValueError("Reviewed browser signing identity mismatch")
    assessment = subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app)],
                                capture_output=True, text=True, check=True, timeout=120).stderr
    if "source=Notarized Developer ID" not in assessment.splitlines():
        raise ValueError("Browser notarization mismatch")
    arch = subprocess.run(["/usr/bin/lipo", "-archs", str(executable)], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    if arch != "x86_64 arm64":
        raise ValueError("Browser architecture mismatch")
    return executable


def browser_configuration(root, node, server, executable, port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Expected owned loopback listener port")
    config = configuration(root)
    config["mcpServers"] = {"pilot": {**config["mcpServers"]["pilot"], "command": str(node),
        "args": [str(server / "build/src/bin/chrome-devtools-mcp.js"), "--headless", "--isolated",
                 f"--executable-path={executable}", "--viewport=1280x720", "--no-usage-statistics",
                 "--no-performance-crux", "--no-category-emulation", "--redact-network-headers",
                 f"--allowed-url-pattern=http://127.0.0.1:{port}/*"],
        "includeTools": BROWSER_TOOLS[:], "excludeTools": [], "approveTools": False}}
    config["settings"]["requestTimeoutMs"] = 30000
    config["settings"]["idleTimeout"] = 300
    validate_configuration(config)
    return config


class OwnedProcesses:
    def __init__(self, pid):
        self.pid = pid
        self.identities = {}
        self.ambiguous = False

    @staticmethod
    def identity(pid):
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 1) or (result.returncode == 1 and result.stderr):
            raise RuntimeError("Owned process metadata query failed")
        identity = result.stdout.strip()
        if result.returncode == 0 and not identity:
            raise RuntimeError("Owned process birth identity missing")
        return identity

    def capture(self):
        pending = [self.pid, *self.identities]
        seen = set()
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            identity = self.identity(pid)
            if not identity:
                continue
            if pid in self.identities and self.identities[pid] != identity:
                self.ambiguous = True
                continue
            self.identities[pid] = identity
            result = subprocess.run(["/usr/bin/pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5)
            if result.returncode not in (0, 1):
                raise RuntimeError("Owned descendant query failed")
            pending.extend(int(value) for value in result.stdout.split())

    def same_process(self, pid):
        identity = self.identity(pid)
        if identity and identity != self.identities[pid]:
            self.ambiguous = True
        return identity == self.identities[pid]

    def alive(self):
        return [pid for pid in self.identities if self.same_process(pid)]

    def stop(self):
        forced = False
        for attempt in range(150):
            alive = self.alive()
            if not alive:
                if self.ambiguous:
                    raise RuntimeError("Owned process identity became ambiguous")
                return forced
            if attempt >= 50:
                for pid in reversed(alive):
                    if not self.same_process(pid):
                        continue
                    try:
                        os.kill(pid, signal.SIGTERM if attempt < 100 else signal.SIGKILL)
                        forced = True
                    except ProcessLookupError:
                        pass
            time.sleep(0.1)
        raise RuntimeError("Owned descendants survived cleanup")


def stop_browser_processes(child, owned):
    forced = False
    errors = []
    for cleanup in (lambda: stop_group(child) if child is not None else False,
                    lambda: owned.stop() if owned is not None else False):
        try:
            forced = cleanup() or forced
        except Exception as error:
            errors.append(error)
    if errors:
        raise RuntimeError("Could not verify all owned browser processes exited") from errors[0]
    return forced


def run_browser(node, pi_root, adapter, server, executable):
    root = Path(tempfile.mkdtemp(prefix="pi-browser-run-")).resolve()
    env = environment(root, node, pi_root)
    listeners = []
    threads = []
    counts = {"blocked": 0, "data": 0, "redirect": 0, "fictionalAuthorization": 0}
    child = None
    owned = None
    cleanup_ok = False
    try:
        version = subprocess.run([str(node), "--version"], env=env, cwd=root, capture_output=True, text=True, check=True, timeout=10)
        if version.stdout.strip() != "v24.18.0":
            raise ValueError("Reviewed Node version mismatch")

        class Fixture(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.server is listeners[0]:
                    counts["blocked"] += 1
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Fictional blocked origin")
                elif self.path == "/redirect":
                    counts["redirect"] += 1
                    self.send_response(302)
                    self.send_header("Location", blocked + "/redirect-target")
                    self.end_headers()
                elif self.path == "/data":
                    counts["data"] += 1
                    counts["fictionalAuthorization"] += self.headers.get("Authorization") == "Bearer fictional-header-value"
                    self.send_response(200)
                    self.send_header("Set-Cookie", "pilot=fictional-cookie-value; SameSite=Strict")
                    self.end_headers()
                    self.wfile.write(b"fictional-response-body")
                elif self.path == "/":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write((RESOURCES / "fixture.html").read_text().replace("__BLOCKED__", blocked).encode())
                else:
                    self.send_response(404)
                    self.end_headers()

        for _ in range(2):
            listener = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
            listeners.append(listener)
            thread = threading.Thread(target=listener.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        blocked = f"http://127.0.0.1:{listeners[0].server_port}"
        config = browser_configuration(root, node, server, executable, listeners[1].server_port)
        (root / "config.json").write_text(json.dumps(config))
        (root / "fixture.json").write_text(json.dumps({"url": f"http://127.0.0.1:{listeners[1].server_port}/",
                                                      "blocked": blocked, "executable": str(executable)}))
        (root / "agent/mcp-cache.json").write_text('{"version":1,"servers":{}}')
        for name in ("load.mjs", "browser-assertions.mjs", "browser-catalog.json"):
            shutil.copyfile(RESOURCES / name, root / name)
        (root / "check.ts").write_text((RESOURCES / "browser-check.ts").read_text().replace("__ADAPTER__", str(adapter)))
        with (root / "diagnostics.log").open("w") as log:
            child = subprocess.Popen([str(node), str(root / "load.mjs")], cwd=root, env=env,
                                     stdout=log, stderr=log, start_new_session=True)
            owned = OwnedProcesses(child.pid)
            deadline = time.monotonic() + 180
            while child.poll() is None:
                owned.capture()
                if time.monotonic() > deadline or log.tell() > 2_000_000:
                    raise RuntimeError("Finite browser run exceeded time/output bound")
                time.sleep(0.1)
            if child.returncode:
                raise RuntimeError("Finite browser checks failed")
        result = json.loads((root / "summary.json").read_text())
        if counts["blocked"] or not counts["data"] or not counts["redirect"] or counts["data"] != counts["fictionalAuthorization"]:
            raise RuntimeError("Fictional request assertions failed")
    except BaseException as error:
        failed = Path(tempfile.mkdtemp(prefix="pi-browser-failure-"))
        for name in ("diagnostics.log", "loader-errors.json", "last-result.json", "catalog-0.json", "catalog-1.json"):
            if (root / name).exists():
                shutil.copyfile(root / name, failed / name)
        raise RuntimeError(f"Browser run failed; private diagnostics: {failed}") from error
    finally:
        try:
            forced = stop_browser_processes(child, owned)
            cleanup_ok = True
        finally:
            for listener in listeners:
                listener.shutdown()
                listener.server_close()
            for thread in threads:
                thread.join(timeout=5)
            if cleanup_ok:
                shutil.rmtree(root)
            else:
                raise RuntimeError(f"Cleanup incomplete; owned runtime retained: {root}")
    if forced or root.exists():
        raise RuntimeError("Browser checks required forced cleanup")
    return {"mode": "browser", **result, "blockedOriginRequests": counts["blocked"],
            "ownedProcessesExited": len(owned.identities), "ownedRuntimeRemoved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--pi-root", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--chrome-server", type=Path)
    parser.add_argument("--chrome-app", type=Path)
    args = parser.parse_args()
    if bool(args.chrome_server) != bool(args.chrome_app):
        parser.error("Both private Chrome source and app paths are required")
    os.umask(0o077)
    validate_inputs(args.node, args.pi_root, args.adapter)
    for mode in ("seams", "registration"):
        print(json.dumps(run(args.node, args.pi_root, args.adapter, mode)))
    if args.chrome_server:
        executable = validate_browser(args.chrome_server, args.chrome_app)
        print(json.dumps(run_browser(args.node, args.pi_root, args.adapter, args.chrome_server, executable)))


if __name__ == "__main__":
    main()
