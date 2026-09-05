#!/usr/bin/env python3
"""Opt-in fictional checks using separately approved private dependencies."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
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
    for root, name, version in ((pi_root, "@earendil-works/pi-coding-agent", "0.84.4"),
                                 (adapter, "pi-mcp-adapter", "2.32.1")):
        manifest = json.loads((root / "package.json").read_text())
        if (manifest["name"], manifest["version"]) != (name, version):
            raise ValueError("Reviewed package version mismatch")
    provenance = json.loads((RESOURCES / "provenance.json").read_text())
    if hashlib.sha256((RESOURCES / "package-lock.json").read_bytes()).hexdigest() != provenance["lockSha256"]:
        raise ValueError("Reviewed dependency lock mismatch")
    if hashlib.sha256((pi_root / "dist/core/extensions/loader.js").read_bytes()).hexdigest() != provenance["piLoaderSha256"]:
        raise ValueError("Reviewed Pi loader mismatch")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--pi-root", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    validate_inputs(args.node, args.pi_root, args.adapter)
    for mode in ("seams", "registration"):
        print(json.dumps(run(args.node, args.pi_root, args.adapter, mode)))


if __name__ == "__main__":
    main()
