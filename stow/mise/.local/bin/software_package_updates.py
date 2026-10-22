#!/usr/bin/python3
"""Update unpinned Pi packages and allowlisted uv tools."""

import ctypes
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from software_updates import CheckError, command, json_command, read_json

UV_TOOLS = {"osxphotos": ["osxphotos", "--version"], "qmk": ["qmk", "--version"]}
RENAME_SWAP = 2
NPM_NAME = r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*"
STABLE = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def stable_version(value):
    match = re.fullmatch(STABLE, value) if isinstance(value, str) else None
    return tuple(map(int, match.groups())) if match else None


def pi_packages(home):
    agent = home / ".pi/agent"
    settings = read_json(agent / "settings.json")
    if not isinstance(settings, dict) or not isinstance(settings.get("packages"), list):
        raise CheckError("missing Pi package declarations")
    rows, identities = [], set()
    for entry in settings["packages"]:
        source = entry.get("source") if isinstance(entry, dict) else entry
        if not isinstance(source, str) or not source:
            raise CheckError("invalid Pi package declaration")
        if source.startswith("npm:"):
            match = re.fullmatch(rf"npm:({NPM_NAME})(?:@([^\s]+))?", source)
            if not match:
                raise CheckError("invalid npm package declaration")
            name, version = match.groups()
            path = agent / "npm/node_modules" / name
            automatic = version is None
            identity = name
        else:
            if not source.startswith(("./", "../", "/", "~/")):
                raise CheckError("unsupported Pi package source")
            path = Path(source.replace("~/", str(home) + "/", 1)) if source.startswith("~/") else Path(source)
            if not path.is_absolute():
                path = agent / path
            name = read_json(path / "package.json").get("name")
            if not isinstance(name, str) or not re.fullmatch(NPM_NAME, name):
                name = None
            automatic = False
            identity = str(path.resolve())
        if identity in identities:
            raise CheckError("duplicate Pi package declaration")
        identities.add(identity)
        rows.append(dict(name=name, path=path, automatic=automatic, declaration=entry,
                         public_registry=source.startswith("npm:") or name == "pi-subagents"))
    return rows


def npm_environment(job, directory):
    env = {key: value for key, value in job.env.items()
           if not key.lower().startswith("npm_config_") and key not in ("NODE_OPTIONS", "NODE_PATH")}
    config = directory / "empty.npmrc"
    config.write_text("")
    env.update(NPM_CONFIG_USERCONFIG=os.devnull, NPM_CONFIG_GLOBALCONFIG=str(config),
               NPM_CONFIG_REGISTRY="https://registry.npmjs.org", NPM_CONFIG_IGNORE_SCRIPTS="true")
    return env


def exchange_directories(left, right):
    if sys.platform != "darwin":
        raise CheckError("atomic Pi activation requires macOS")
    function = ctypes.CDLL(None, use_errno=True).renamex_np
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(os.fsencode(left), os.fsencode(right), RENAME_SWAP) != 0:
        raise CheckError("atomic Pi activation failed")


def validate_pi(root, versions):
    for name, version in versions.items():
        path = root / "node_modules" / name / "package.json"
        if path.is_symlink():
            raise CheckError("symlinked package manifest")
        manifest = read_json(path)
        if (not isinstance(manifest, dict) or manifest.get("name") != name
                or manifest.get("version") != version or not isinstance(manifest.get("pi"), dict)):
            raise CheckError("installed package manifest mismatch")
        if any(key.startswith("@earendil-works/pi-") for key in manifest.get("dependencies", {})):
            raise CheckError("package bundles host-provided Pi dependencies")


def npm_state(root):
    return {name: (root / name).read_bytes() if (root / name).is_file() else None
            for name in ("package.json", "package-lock.json", "npm-shrinkwrap.json")}


def update_pi(job):
    agent = job.home / ".pi/agent"
    live = agent / "npm"
    results = []
    try:
        declarations = pi_packages(job.home)
        selected = [row for row in declarations if row["automatic"]]
        if not selected:
            job.state["packages"] = []
            return job.finish([])
        if live.is_symlink() or (live.exists() and not live.is_dir()):
            raise CheckError("Pi npm root must be a regular directory")
        with tempfile.TemporaryDirectory(prefix=".npm-update-", dir=agent) as temporary:
            directory = Path(temporary)
            env = npm_environment(job, directory)
            original = npm_state(live)
            targets, changed = {}, []
            for row in selected:
                name = row["name"]
                manifest = read_json(row["path"] / "package.json")
                if not isinstance(manifest, dict) or (manifest and manifest.get("name") != name):
                    raise CheckError("installed Pi package identity mismatch")
                installed = manifest.get("version")
                tags = json_command(["npm", "view", name, "dist-tags", "--json",
                                     "--registry=https://registry.npmjs.org"], directory, env, timeout=30)
                latest = tags.get("latest") if isinstance(tags, dict) else None
                version = stable_version(latest)
                if version is None:
                    raise CheckError("npm latest tag is not a stable version")
                current = stable_version(installed)
                if installed is not None and current is None:
                    raise CheckError("installed Pi package is on an unsupported release channel")
                target = latest if current is None or version > current else installed
                targets[name] = target
                results.append(dict(name=name, before=installed, after=target,
                                    status="update-available" if installed != target else "current"))
                if installed != target:
                    changed.append(name + "@" + target)
            job.state["packages"] = results
            if not changed:
                return job.finish([])
            candidate = directory / "candidate"
            if live.exists():
                shutil.copytree(live, candidate, symlinks=True)
            else:
                candidate.mkdir()
            if (candidate / ".npmrc").exists() or (candidate / ".npmrc").is_symlink():
                raise CheckError("Pi npm project configuration requires review")
            command(["npm", "install", *changed, "--prefix", str(candidate), "--ignore-scripts",
                     "--legacy-peer-deps", "--save-exact", "--no-audit", "--no-fund",
                     "--registry=https://registry.npmjs.org"], directory, env, timeout=600)
            validate_pi(candidate, targets)
            if pi_packages(job.home) != declarations or npm_state(live) != original:
                raise CheckError("Pi package declarations or installation changed during staging")
            backup = job.directory / "pi-previous"
            if live.exists():
                exchange_directories(live, candidate)
                try:
                    if backup.exists():
                        shutil.rmtree(backup)
                    candidate.rename(backup)
                except OSError:
                    exchange_directories(live, candidate)
                    raise
                job.state["backup"] = str(backup)
            else:
                candidate.rename(live)
            for result in results:
                result["status"] = "updated" if result["before"] != result["after"] else "current"
            job.log("INFO", "Activated stable Pi package updates; previous installation retained.")
        result = job.finish([])
        job.notify("reload:" + ",".join(changed),
                   "Pi extensions were updated. Run /reload in each open Pi session to load the new versions.",
                   level="INFO")
        return result
    except (CheckError, OSError, ValueError, TypeError) as error:
        job.state["packages"] = results
        job.log("ERROR", str(error) if isinstance(error, CheckError) else "Pi package update failed.")
        return job.finish(["pi-packages"])


def uv_installed(home, env):
    output = command(["uv", "tool", "list", "--color", "never", "--no-config"], home, env, timeout=30)
    return dict(re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*) v([^\s]+)", output, re.M))


def update_uv(job):
    installed = uv_installed(job.home, job.env)
    results, failures = [], []
    for name, probe in UV_TOOLS.items():
        if name not in installed:
            continue
        result = dict(name=name, before=installed[name])
        try:
            command(["uv", "tool", "upgrade", name, "--prerelease", "disallow", "--no-config",
                     "--color", "never", "--no-progress"], job.home, job.env, timeout=600)
            after = uv_installed(job.home, job.env).get(name)
            if after is None or "-" in after:
                raise CheckError("uv tool missing or on a prerelease")
            command([str(job.home / ".local/bin" / probe[0]), *probe[1:]], job.home, job.env, timeout=60)
            result.update(after=after, status="verified")
        except CheckError as error:
            result.update(status="failed", reason=str(error))
            failures.append(name)
        results.append(result)
        job.log("INFO", f"{name}: {result['status']}")
    job.state["tools"] = results
    return job.finish(failures)
