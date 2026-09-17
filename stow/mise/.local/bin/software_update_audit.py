#!/usr/bin/python3
"""Read installed versions and update metadata without installing software."""

import ipaddress
import json
from pathlib import Path
import platform
import plistlib
import re
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.parsers.expat import ExpatError

from software_updates import (CheckError, DAY, age, command, global_tools, json_command,
                              now, policy, read_json, safe_name, safe_version, save_json)

BUNDLES = {"microsoft-teams": "Microsoft Teams.app", "nordvpn": "NordVPN.app",
           "tailscale-app": "Tailscale.app", "karabiner-elements": "Karabiner-Elements.app"}
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


def numeric(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    parts = tuple(int(part) for part in value.split("."))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def compare(left, right):
    a, b = numeric(left), numeric(right)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def classify(actual, available, receipt=None, pinned=False, disabled=False):
    if disabled:
        return "disabled"
    if pinned:
        return "intentional-pin"
    if actual and re.search(r"(?i)(beta|alpha|rc|nightly|preview)", actual):
        return "alternate-channel"
    comparison = compare(actual, available)
    if comparison is None:
        return "unknown"
    if comparison < 0:
        return "outdated"
    if comparison > 0:
        return "ahead-of-catalog"
    return "stale-receipt" if receipt and compare(receipt, actual) != 0 else "current"


def app_info(path):
    try:
        with (path / "Contents/Info.plist").open("rb") as stream:
            data = plistlib.load(stream)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, plistlib.InvalidFileException, ExpatError):
        return {}


def app_inventory(home):
    apps = {}
    for directory in (Path("/Applications"), home / "Applications"):
        for path in sorted(directory.glob("*.app")):
            apps[path] = app_info(path)
        for path in sorted((directory / "Utilities").glob("*.app")):
            apps[path] = app_info(path)
    return apps


def cask_apps(cask, apps):
    names = {BUNDLES.get(cask["token"])}
    names.update(name + ".app" for name in cask.get("name", []))
    for artifact in cask.get("artifacts", []):
        if "app" in artifact:
            names.add(Path(artifact["app"][0]).name)
            if isinstance(artifact.get("target"), str):
                names.add(Path(artifact["target"]).name)
    return [path for path in apps if path.name in names]


def brew_rows(info, outdated, apps, cli_versions=None):
    rows, owned = [], set()
    candidates = {row["name"] for kind in ("formulae", "casks") for row in outdated[kind]}
    for formula in info["formulae"]:
        versions = [safe_version(item.get("version")) for item in formula.get("installed", [])]
        status = "outdated" if formula["name"] in candidates else "current"
        if formula.get("pinned"):
            status = "intentional-pin"
        if formula.get("disabled"):
            status = "disabled"
        rows.append(dict(category="homebrew-formula", name=safe_name(formula["name"]),
                         installed=versions, available=safe_version(formula.get("versions", {}).get("stable")),
                         status=status, owner="homebrew"))
    for cask in info["casks"]:
        paths = cask_apps(cask, apps)
        owned.update(paths)
        actuals = [apps[path].get("CFBundleShortVersionString") for path in paths]
        if not actuals:
            actuals = [(cli_versions or {}).get(cask["token"])]
        receipt = str(cask.get("installed") or "").split(",")[0]
        available = str(cask.get("version") or "").split(",")[0]
        for actual in actuals:
            rows.append(dict(category="homebrew-cask", name=safe_name(cask["token"]),
                             installed=safe_version(actual), receipt=safe_version(receipt),
                             available=safe_version(available),
                             status=classify(actual, available, receipt, cask.get("pinned"), cask.get("disabled")),
                             owner="homebrew" if cask["token"] == "microsoft-teams" or not cask.get("auto_updates")
                             else "native-updater", candidate=cask["token"] in candidates))
    return rows, owned


def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise CheckError("unsupported update feed")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise CheckError("nonpublic update feed")
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def feed_version(data, os_version):
    root = ET.fromstring(data)
    versions = []
    for item in root.findall(".//item"):
        if item.findtext(SPARKLE + "channel"):
            continue
        hardware = item.findtext(SPARKLE + "hardwareRequirements")
        if hardware and hardware != platform.machine():
            continue
        minimum = item.findtext(SPARKLE + "minimumSystemVersion")
        maximum = item.findtext(SPARKLE + "maximumSystemVersion")
        if minimum and compare(os_version, minimum) not in (0, 1):
            continue
        if maximum and compare(os_version, maximum) not in (-1, 0):
            continue
        enclosure = item.find("enclosure")
        version = item.findtext(SPARKLE + "shortVersionString")
        if not version and enclosure is not None:
            version = enclosure.get(SPARKLE + "shortVersionString")
        if numeric(version) is not None:
            versions.append(version)
    return max(versions, key=numeric) if versions else None


def independent_row(path, info):
    actual = safe_version(info.get("CFBundleShortVersionString"))
    row = dict(category="independent-app", name=safe_name(path.stem), installed=actual,
               available=None, status="unknown", owner="native-updater")
    if (path / "Contents/_MASReceipt/receipt").is_file():
        row.update(owner="app-store", note="App Store receipt present; see App Store audit.")
        return row
    if actual and re.search(r"(?i)(beta|alpha|rc|preview|nightly)", actual):
        row["status"] = "alternate-channel"
        return row
    url = info.get("SUFeedURL")
    if not isinstance(url, str):
        row["note"] = "No supported public update metadata."
        return row
    try:
        public_url(url)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicRedirect())
        with opener.open(url, timeout=10) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise CheckError("oversized feed")
        available = feed_version(data, platform.mac_ver()[0])
        row.update(available=safe_version(available), status=classify(actual, available))
        row["note"] = "Public Sparkle stable feed; native updater decides rollout and license eligibility."
    except (CheckError, OSError, ValueError, ET.ParseError):
        row["note"] = "Public update feed unavailable or unsupported."
    return row


def mise_rows(home, env):
    tools = global_tools(home, env)
    within = json_command(["mise", "outdated", "--json"], home, env)
    latest = json_command(["mise", "outdated", "--bump", "--json"], home, env)
    rows = []
    for tool, record in tools.items():
        mode = policy(tool, record.get("requested_version", ""))
        rows.append(dict(category="mise", name=tool, owner="mise" if mode == "range" else "review",
                         installed=safe_version(record.get("version")) if record.get("installed") else None,
                         requested=safe_version(record.get("requested_version")),
                         available=safe_version(latest.get(tool, {}).get("latest")),
                         status="missing" if not record.get("installed") else "intentional-pin" if mode == "pin" else "review-required" if mode == "held"
                         else "outdated" if tool in within else "current-in-range"))
    return rows


def npm_rows(home, env):
    data = json_command(["mise", "ls", "--json"], home, env)
    active_root = Path(command(["npm", "root", "-g"], home, env).strip()).resolve()
    roots = {active_root}
    runtimes = {}
    for record in data.get("node", []):
        if record.get("installed") and record.get("install_path"):
            root = (Path(record["install_path"]) / "lib/node_modules").resolve()
            roots.add(root)
            runtimes[root] = safe_version(record.get("version"))
    rows, versions = [], {}
    for root in sorted(roots):
        manifests = list(root.glob("*/package.json")) + list(root.glob("@*/*/package.json"))
        for manifest in manifests:
            package = read_json(manifest)
            name = safe_name(package.get("name"))
            if name == "npm":
                continue
            if name not in versions:
                try:
                    versions[name] = json_command(["npm", "view", name, "version", "--json",
                                                   "--registry=https://registry.npmjs.org"], home, env, timeout=30)
                except CheckError:
                    versions[name] = None
            available = versions[name]
            bundled = name == "corepack" and root == active_root
            rows.append(dict(category="npm-global" if root == active_root else "npm-retained-global",
                             name=name, owner="node-bundled" if bundled else "unmanaged",
                             installed=safe_version(package.get("version")), available=safe_version(available),
                             status="runtime-bundled" if bundled else classify(package.get("version"), available),
                             active_runtime=root == active_root, node_version=runtimes.get(root)))
    return rows


def uv_rows(home, env):
    installed = command(["uv", "tool", "list", "--color", "never"], home, env)
    outdated = command(["uv", "tool", "list", "--outdated", "--color", "never", "--no-progress"], home, env)
    latest = dict(re.findall(r"^([\w.-]+) v[^\s]+ \[latest: ([\w.+-]+)\]", outdated, re.M))
    rows = []
    for name, version in re.findall(r"^([\w.-]+) v([\w.+-]+)", installed, re.M):
        rows.append(dict(category="uv", name=name, installed=version, available=latest.get(name),
                         owner="review" if name == "agent-reader" else "uv",
                         status="review-required" if name == "agent-reader" else "outdated" if name in latest else "current"))
    return rows


def reviewed_packages(home, env):
    packages = {
        "@upstash/context7-pi": home / ".pi/agent/npm/node_modules/@upstash/context7-pi/package.json",
        "pi-web-access": home / ".pi/agent/npm/node_modules/pi-web-access/package.json",
        "pi-subagents": home / ".pi/agent/local/copilot-delegation/node_modules/pi-subagents/package.json",
    }
    rows = []
    for name, manifest in packages.items():
        package = read_json(manifest)
        if package.get("name") != name:
            rows.append(dict(category="reviewed-extension", name=name, installed=None,
                             available=None, owner="review", status="unknown"))
            continue
        tags = json_command(["npm", "view", name, "dist-tags", "--json"], home, env, timeout=30)
        rows.append(dict(category="reviewed-extension", name=name,
                         installed=safe_version(package.get("version")),
                         available=safe_version(tags.get("latest")),
                         channels={safe_name(key): safe_version(value) for key, value in tags.items()},
                         owner="review", status="review-required"))
    return rows


def json_records(text):
    if not text.strip():
        return []
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result]
    except ValueError:
        try:
            return [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError:
            raise CheckError("invalid App Store data")


def mas_rows(home, env):
    installed = json_records(command(["mas", "list", "--json"], home, env))
    outdated = json_records(command(["mas", "outdated", "--json", "--inaccurate"], home, env))
    pending = {str(item.get("adamID", item.get("id"))): safe_version(item.get("newVersion"))
               for item in outdated}
    return [dict(category="app-store", name=safe_name(item.get("name")),
                 installed=safe_version(item.get("version")), owner="app-store",
                 available=pending.get(str(item.get("adamID", item.get("id")))),
                 status="update-candidate" if str(item.get("adamID", item.get("id"))) in pending
                 else "no-update-reported", note="Read-only catalog check; account eligibility is not verified.")
            for item in installed]


def job_status(directory, name, interval):
    data = read_json(directory / (name + ".json"))
    return dict(name=name, last_attempt=data.get("last_attempt"), last_success=data.get("last_success"),
                status=data.get("status", "unknown"), failures=data.get("failures", []),
                deferred_at=data.get("deferred_at"), overdue=age(data.get("last_success")) > interval * 2)


def audit(job):
    rows, failures, sources = [], [], {}
    apps = app_inventory(job.home)
    owned = set()
    try:
        info = json_command(["brew", "info", "--json=v2", "--installed"], job.home, job.env)
        outdated = json_command(["brew", "outdated", "--greedy", "--json=v2"], job.home, job.env)
        versions = {}
        try:
            output = command(["copilot", "--version"], job.home, job.env, timeout=20)
            match = re.search(r"\b\d+\.\d+\.\d+\b", output)
            if match:
                versions["copilot-cli"] = match.group()
        except CheckError:
            pass
        result, owned = brew_rows(info, outdated, apps, versions)
        rows.extend(result)
        sources["homebrew"] = "checked"
    except (CheckError, KeyError, TypeError):
        failures.append("homebrew-check")
        sources["homebrew"] = "unknown"
    for name, check in (("mise", mise_rows), ("npm", npm_rows), ("uv", uv_rows),
                        ("reviewed-packages", reviewed_packages), ("app-store", mas_rows)):
        try:
            rows.extend(check(job.home, job.env))
            sources[name] = "checked"
        except (CheckError, KeyError, TypeError):
            failures.append(name + "-check")
            sources[name] = "unknown"
    try:
        automatic = command(["/usr/bin/defaults", "read", "/Library/Preferences/com.apple.commerce", "AutoUpdate"],
                            job.home, job.env, timeout=10).strip()
        sources["app-store-automatic-updates"] = {"1": "enabled", "0": "disabled"}.get(automatic, "unknown")
        if automatic == "0":
            failures.append("app-store-automatic-updates-disabled")
    except CheckError:
        sources["app-store-automatic-updates"] = "unknown"
    for path, info in apps.items():
        if path not in owned:
            rows.append(independent_row(path, info))
    sources["independent-apps"] = "checked where public metadata is supported; otherwise unknown"
    jobs = [job_status(job.directory, name, interval) for name, interval in
            (("mise", DAY), ("homebrew", DAY), ("audit", 7 * DAY))]
    problems = [item["name"] for item in jobs if item["name"] != "audit" and
                (item["overdue"] or item["status"] == "failed")]
    jobs[-1].update(status="failed" if failures else "success", failures=failures)
    if not failures:
        jobs[-1].update(last_success=now(), overdue=False)
    report = dict(generated_at=now(), sources=sources, jobs=jobs, software=rows, failures=failures)
    save_json(job.directory / "report.json", report)
    lines = ["# Software update audit", "", "Generated at " + report["generated_at"] + ".", "",
             "## Jobs", "", "| Job | Last attempt | Last success | Status | Overdue |",
             "| --- | --- | --- | --- | --- |"]
    for item in jobs:
        lines.append(f"| {item['name']} | {item['last_attempt'] or 'unknown'} | {item['last_success'] or 'unknown'} | {item['status']} | {item['overdue']} |")
    lines.extend(["", "## Software", "", "| Category | Software | Installed | Available | Status | Owner |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for row in rows:
        installed = row.get("installed") or "unknown"
        if isinstance(installed, list):
            installed = ", ".join(value or "unknown" for value in installed)
        lines.append(f"| {row['category']} | {row['name']} | {installed} | {row.get('available') or 'unknown'} | {row['status']} | {row['owner']} |")
    lines.extend(["", "Failed checks: " + (", ".join(failures) or "none") + ".",
                  "", "Unknown and candidate results require the supported native updater or App Store. No software was installed.",
                  "The JSON report includes source coverage, receipts, channel notes and runtime ownership.", ""])
    report_path = job.directory / "report.md"
    report_path.write_text("\n".join(lines))
    report_path.chmod(0o600)
    result = job.finish(failures)
    if problems:
        job.notify("overdue:" + ",".join(problems), "An update job is overdue or failed. Check ~/.local/state/software-updates/report.md.")
    return result
