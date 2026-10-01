#!/usr/bin/env python3
"""Apply the reviewed TypeBox peer declaration to the two pinned Pi packages."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

PACKAGES = (
    ("local/provider-local-delegation/node_modules/pi-subagents", "pi-subagents", "0.65.0",
     "6cf6be693b51463b000a16c5bbdced421de47ef7e5be406dbd018743341492b5",
     "3eea4cdff22d2f3542ffc0ceb3621b70c10ea4d767d8b41f776caf4ca48a9a4c"),
    ("npm/node_modules/pi-web-access", "pi-web-access", "0.27.0",
     "820c77279eaa539e187191fa01deb931250be14a588667c31b96904f04b9bcb1",
     "23d647f7a9d77fb04b222b59af54f06b761c05493c934d8d7677d921d8d2a2d8"),
)


def patched_manifest(original):
    manifest = json.loads(original)
    del manifest["dependencies"]["typebox"]
    manifest["peerDependencies"]["typebox"] = "*"
    return (json.dumps(manifest, indent=2) + "\n").encode()


def prepare(agent_dir):
    updates = []
    for relative, name, version, digest, corrected_digest in PACKAGES:
        path = agent_dir / relative / "package.json"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Expected a regular manifest: {path}")
        original = path.read_bytes()
        manifest = json.loads(original)
        if manifest.get("name") != name or manifest.get("version") != version:
            raise ValueError(f"Package identity changed: {path}")
        actual_digest = hashlib.sha256(original).hexdigest()
        if actual_digest == corrected_digest:
            continue
        if actual_digest != digest:
            raise ValueError(f"Unreviewed manifest changes: {path}")
        backup = path.with_name("package.json.before-host-peers")
        if backup.exists() or backup.is_symlink():
            if backup.is_symlink() or not backup.is_file() or backup.read_bytes() != original:
                raise ValueError(f"Conflicting backup: {backup}")
        replacement = patched_manifest(original)
        if hashlib.sha256(replacement).hexdigest() != corrected_digest:
            raise ValueError(f"Correction digest mismatch: {path}")
        updates.append((path, backup, original, replacement))
    return updates


def apply(agent_dir, check=False):
    updates = prepare(agent_dir)
    if check:
        return len(updates)
    for path, backup, original, replacement in updates:
        if not backup.exists():
            with backup.open("xb") as stream:
                stream.write(original)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(replacement)
                os.fchmod(stream.fileno(), path.stat().st_mode & 0o777)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return len(updates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", type=Path, default=Path.home() / ".pi/agent")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        count = apply(args.agent_dir, args.check)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(f"{count} manifests {'need correction' if args.check else 'corrected'}.")
    return int(args.check and count > 0)


if __name__ == "__main__":
    raise SystemExit(main())
