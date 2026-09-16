#!/usr/bin/python3
"""Lazy, stdlib-only private NAS topology. Importing this module reads no config."""

import argparse
import ipaddress
import json
import os
import posixpath
import re
import shlex
import sys
import unicodedata
from urllib.parse import quote


class ConfigError(ValueError):
    pass


class MissingConfig(ConfigError):
    pass


def _keys(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ConfigError(f"{name}: expected exactly {', '.join(sorted(keys))}")


def _text(value, name):
    if not isinstance(value, str) or not value or any(unicodedata.category(c).startswith("C") or unicodedata.category(c) in {"Zl", "Zp"} for c in value):
        raise ConfigError(f"{name}: expected a nonempty string without control characters")
    return value


def _address(value, name):
    _text(value, name)
    if "%" in value:
        raise ConfigError(f"{name}: scoped IPv6 addresses are unsupported")
    return value


def _host(value, name):
    _address(value, name)
    try:
        ipaddress.ip_address(value)
        return
    except ValueError:
        pass
    if len(value) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                   for label in value.rstrip(".").split(".")):
        raise ConfigError(f"{name}: invalid host")
    if re.fullmatch(r"[0-9.]+", value):
        raise ConfigError(f"{name}: invalid IP address")


def _user(value, name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", _text(value, name)):
        raise ConfigError(f"{name}: invalid user")


def _path(value, name):
    _text(value, name)
    if not value.startswith("/") or value.startswith("//") or ".." in value.split("/") or posixpath.normpath(value) == "/":
        raise ConfigError(f"{name}: expected an absolute non-root path without '..'")


def validate(config, *, worker=False):
    _keys(config, ("version", "remote") if worker else
          ("version", "mount", "ssh_hosts", "local_library_root", "remote"), "NAS config")
    if type(config["version"]) is not int or config["version"] != 1:
        raise ConfigError("NAS config: unsupported version")
    _keys(config["remote"], ("library_root", "inbox", "python", "rip", "streamrip_config"), "remote")
    for key, value in config["remote"].items():
        _path(value, "remote." + key)
    if worker:
        return config
    _path(config["local_library_root"], "local_library_root")
    mount = config["mount"]
    _keys(mount, ("host", "user", "shares", "home_gateway"), "mount")
    _host(mount["host"], "mount.host")
    _user(mount["user"], "mount.user")
    gateway = _address(mount["home_gateway"], "mount.home_gateway")
    try:
        ipaddress.ip_address(gateway)
    except ValueError:
        raise ConfigError("mount.home_gateway: expected an IP address") from None
    shares = mount["shares"]
    if not isinstance(shares, list) or not shares:
        raise ConfigError("mount.shares: expected a nonempty list")
    for share in shares:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", _text(share, "mount.shares")) or share in (".", ".."):
            raise ConfigError("mount.shares: invalid share name")
    if len(set(shares)) != len(shares):
        raise ConfigError("mount.shares: duplicate share")
    hosts = config["ssh_hosts"]
    if not isinstance(hosts, list) or not hosts:
        raise ConfigError("ssh_hosts: expected a nonempty ordered list")
    for host in hosts:
        parts = _text(host, "ssh_hosts").split("@")
        if len(parts) != 2:
            raise ConfigError("ssh_hosts: expected user@host")
        _user(parts[0], "ssh_hosts user")
        address = _address(parts[1], "ssh_hosts host")
        if address.startswith("[") and address.endswith("]"):
            try:
                ipaddress.IPv6Address(address[1:-1])
            except ValueError:
                raise ConfigError("ssh_hosts: invalid IPv6 address") from None
        else:
            _host(address, "ssh_hosts host")
            if ":" in address:
                raise ConfigError("ssh_hosts: bracket IPv6 addresses")
    if len(set(hosts)) != len(hosts):
        raise ConfigError("ssh_hosts: duplicate host")
    return config


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("NAS config: duplicate JSON key")
        result[key] = value
    return result


def load(path=None, *, worker=False):
    """Validate the whole selected file on demand. No import-time reads or cache."""
    path = path if path is not None else os.environ.get("MUSIC_NAS_CONFIG", "~/.config/music/nas.json")
    if not path:
        raise ConfigError("MUSIC_NAS_CONFIG is empty")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = f.read(65537)
        if len(data) > 65536:
            raise ConfigError("NAS config exceeds 64 KiB")
        config = json.loads(data, object_pairs_hook=_unique_object)
    except FileNotFoundError:
        raise MissingConfig("NAS config missing; create ~/.config/music/nas.json or set MUSIC_NAS_CONFIG") from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ConfigError("NAS config is unreadable or invalid JSON") from None
    return validate(config, worker=worker)


def library_root(override=None):
    """An explicit command path does not require NAS configuration."""
    return os.path.abspath(os.path.expanduser(override)) if override else load()["local_library_root"]


def remote_path(path, config=None):
    config = load() if config is None else config
    root = os.path.abspath(config["local_library_root"])
    path = os.path.abspath(path)
    if os.path.commonpath((root, path)) != root:
        raise ConfigError("cannot map a path outside local_library_root")
    return posixpath.normpath(posixpath.join(config["remote"]["library_root"], os.path.relpath(path, root)))


def get(config, field):
    if field == "mount.urls":
        mount = config["mount"]
        host = mount["host"]
        if ":" in host:
            host = "[" + host + "]"
        return [f"smb://{mount['user']}@{host}/{quote(share, safe='')}" for share in mount["shares"]]
    value = config
    for key in field.split("."):
        if not isinstance(value, dict) or key not in value:
            raise ConfigError("unknown NAS field")
        value = value[key]
    if not isinstance(value, (str, list)):
        raise ConfigError("get expects a scalar string or list")
    return value if isinstance(value, list) else [value]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help="read a projected worker config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    getter = sub.add_parser("get")
    getter.add_argument("field")
    mapper = sub.add_parser("map-path")
    mapper.add_argument("path")
    project = sub.add_parser("project-worker")
    project.add_argument("output", help="new mode-600 file; existing files are never overwritten")
    quoter = sub.add_parser("quote")
    quoter.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "quote":
            print(" ".join(shlex.quote(arg) for arg in args.args))
            return 0
        config = load(worker=args.worker)
        if args.command == "get":
            print("\n".join(get(config, args.field)))
        elif args.command == "map-path":
            if args.worker:
                raise ConfigError("map-path requires full local config")
            print(remote_path(args.path, config))
        elif args.command == "project-worker":
            if args.worker:
                raise ConfigError("projection requires full local config")
            projected = {"version": 1, "remote": config["remote"]}
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
                json.dump(projected, f, indent=2)
                f.write("\n")
    except MissingConfig as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except (ConfigError, OSError) as e:
        # OSError may contain private paths; report the class, not its payload.
        print(f"ERROR: {e if isinstance(e, ConfigError) else type(e).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
