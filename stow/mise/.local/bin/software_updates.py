#!/usr/bin/python3
"""Run global Mise updates and record scheduled software maintenance outcomes."""

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

DAY = 86400
RUNTIMES = {"node": "24", "python": "3.14", "java": "26", "pnpm": "11"}
PROBES = {"node": ["node", "--version"], "python": ["python3", "--version"],
          "java": ["java", "-version"], "pnpm": ["pnpm", "--version"],
          "npm:defuddle": ["defuddle", "--version"],
          "npm:eas-cli": ["eas", "--version"],
          "npm:@slidev/cli": ["slidev", "--version"],
          "npm:@mermaid-js/mermaid-cli": ["mmdc", "--version"],
          "npm:@posthog/cli": ["posthog-cli", "--version"]}


class CheckError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age(timestamp):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def audit_due(last_success, clock=None):
    clock = clock or datetime.now().astimezone()
    scheduled = clock.replace(hour=10, minute=15, second=0, microsecond=0)
    scheduled -= timedelta(days=(clock.weekday() + 1) % 7)
    if scheduled > clock:
        scheduled -= timedelta(days=7)
    try:
        return datetime.fromisoformat(last_success) < scheduled
    except (ValueError, TypeError):
        return True


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def environment(home):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MISE_", "__MISE", "UV_"))}
    env.update(HOME=str(home), MISE_GLOBAL_CONFIG_FILE=str(home / ".config/mise/config.toml"),
               MISE_YES="1", MISE_COLOR="0", MISE_ENV="",
               HOMEBREW_NO_AUTO_UPDATE="1", HOMEBREW_NO_ANALYTICS="1",
               MAS_NO_AUTO_INDEX="1", NO_COLOR="1", COPILOT_AUTO_UPDATE="false",
               NO_UPDATE_NOTIFIER="1")
    env["PATH"] = os.pathsep.join([str(home / ".local/share/mise/shims"),
                                   "/opt/homebrew/bin", "/usr/local/bin",
                                   "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    return env


def command(args, home, env, timeout=180, accepted=(0,)):
    with tempfile.TemporaryFile() as stdout:
        try:
            proc = subprocess.Popen(args, cwd=home, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise CheckError("command timed out")
        except OSError:
            raise CheckError("command unavailable")
        if code not in accepted:
            raise CheckError("command failed")
        if stdout.tell() > 8 * 1024 * 1024:
            raise CheckError("command output exceeded limit")
        stdout.seek(0)
        return stdout.read().decode("utf-8", errors="replace")


def json_command(args, home, env, **kwargs):
    try:
        return json.loads(command(args, home, env, **kwargs))
    except ValueError:
        raise CheckError("invalid command data")


def safe_name(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9@:/_.+ -]{1,120}", value):
        return value
    return "unknown"


def safe_version(value):
    if isinstance(value, str) and len(value) <= 80 and re.fullmatch(r"(?:v?\d[\w.,+*-]*|latest|lts)", value):
        return value
    return None


class Job:
    def __init__(self, home, name, interval, force=False):
        self.home, self.name, self.interval, self.force = home, name, interval, force
        self.directory = home / ".local/state/software-updates"
        self.path = self.directory / (name + ".json")
        self.env = environment(home)
        self.state = {}
        self.lock = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.directory / (self.name + ".lock")).open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.state = read_json(self.path)
        if not isinstance(self.state, dict):
            self.state = {}
        due = audit_due(self.state.get("last_success")) if self.name == "audit" else age(self.state.get("last_success")) >= self.interval
        if not self.force and not due:
            return False
        self.state.update(last_attempt=now(), status="running", failures=[])
        save_json(self.path, self.state)
        if not self.force:
            try:
                command([str(self.home / ".local/bin/should-run-background-job")],
                        self.home, self.env, timeout=15)
            except CheckError as error:
                if str(error) != "command failed":
                    self.finish(["power-gate"])
                    raise
                self.state.update(status="deferred", deferred_at=now())
                save_json(self.path, self.state)
                self.log("INFO", "Deferred by the power gate.")
                if age(self.state.get("last_success")) > self.interval * 2:
                    self.notify("overdue", "Software maintenance is overdue and deferred by the power gate.")
                return False
        return self

    def log(self, level, message):
        line = f"{now()} {level} [{self.name}] {message[:1500]}\n"
        path = self.directory / (self.name + ".log")
        if path.exists() and path.stat().st_size > 128 * 1024:
            os.replace(path, path.with_suffix(".log.1"))
        with path.open("a") as stream:
            stream.write(line)
        helper = self.home / ".local/bin/pipeline-log"
        if helper.is_file():
            try:
                command([str(helper), "software-" + self.name, level, message[:1500]],
                        self.home, self.env, timeout=10)
            except CheckError:
                pass

    def notify(self, signature, message):
        if self.state.get("notified") == signature:
            return
        self.log("ERROR", message)
        try:
            if (self.home / ".local/bin/pipeline-log").is_file():
                self.state["notified"] = signature
                save_json(self.path, self.state)
                return
            command(["/usr/bin/osascript", "-e",
                     'on run argv\ndisplay notification (item 1 of argv) with title "Software updates"\nend run',
                     message], self.home, self.env, timeout=10)
        except CheckError:
            pass
        self.state["notified"] = signature
        save_json(self.path, self.state)

    def finish(self, failures):
        self.state.update(status="failed" if failures else "success", failures=failures,
                          last_completion=now())
        if not failures:
            self.state.update(last_success=now(), notified=None)
        save_json(self.path, self.state)
        if failures:
            self.notify(",".join(failures), "Software maintenance failed. Check ~/.local/state/software-updates/.")
        self.log("INFO", "Completed with failures." if failures else "Completed successfully.")
        return int(bool(failures))

    def __exit__(self, kind, value, traceback):
        if kind is not None and self.state.get("status") == "running":
            self.finish(["interrupted"])
        if self.lock:
            self.lock.close()


def global_tools(home, env):
    config = home / ".config/mise/config.toml"
    configs = json_command(["mise", "config", "ls", "--json"], home, env)
    if not config.is_file() or not isinstance(configs, list) or not configs:
        raise CheckError("missing global configuration")
    if any(not isinstance(row, dict) or Path(row.get("path", "")).absolute() != config
           for row in configs):
        raise CheckError("non-global configuration selected")
    tools = json_command(["mise", "ls", "--global", "--json"], home, env)
    if not isinstance(tools, dict) or not tools:
        raise CheckError("empty global tool selection")
    selected = {}
    for tool, records in tools.items():
        active = [record for record in records if record.get("active") or not record.get("installed")]
        if len(active) != 1 or safe_name(tool) == "unknown":
            raise CheckError("ambiguous global tool selection")
        record = active[0]
        if Path(record.get("source", {}).get("path", "")).absolute() != config:
            raise CheckError("non-global tool source")
        selected[tool] = record
    return selected


def policy(tool, requested):
    if re.fullmatch(r"v?\d+(?:\.\d+){2,}(?:[-+][\w.-]+)?", requested):
        return "pin"
    if tool in RUNTIMES:
        return "range" if requested == RUNTIMES[tool] else "held"
    if requested == "latest" or re.fullmatch(r"\d+(?:\.\d+)?", requested):
        return "range"
    return "held"


def verify_tool(tool, record, home, env):
    if not record.get("installed"):
        raise CheckError("tool not installed")
    if tool in PROBES:
        binary = PROBES[tool][0]
        resolved = command(["mise", "which", binary], home, env).strip()
        install = Path(record["install_path"]).resolve()
        try:
            Path(resolved).resolve().relative_to(install)
        except ValueError:
            raise CheckError("executable resolves outside managed installation")
        command(["mise", "exec", "--"] + PROBES[tool], home, env, timeout=60)
    elif tool.startswith("npm:"):
        raise CheckError("npm tool has no executable probe")


def check_node_globals(record, tools, home, env):
    root = Path(record["install_path"]) / "lib/node_modules"
    managed = {tool[4:] for tool in tools if tool.startswith("npm:")}
    unmanaged = []
    for manifest in list(root.glob("*/package.json")) + list(root.glob("@*/*/package.json")):
        name = read_json(manifest).get("name")
        if name not in managed | {"npm", "corepack"}:
            unmanaged.append(name)
    if unmanaged and json_command(["mise", "outdated", "node", "--json"], home, env):
        raise CheckError("unmanaged Node globals require migration before a runtime upgrade")


def update_mise(job):
    tools = global_tools(job.home, job.env)
    failures, results = [], []
    order = sorted(tools, key=lambda tool: (0 if tool == "node" else 1 if tool in RUNTIMES else 2, tool))
    node_ok = True
    for tool in order:
        record = tools[tool]
        mode = policy(tool, record.get("requested_version", ""))
        result = {"tool": tool, "before": safe_version(record.get("version")) if record.get("installed") else None,
                  "policy": mode}
        try:
            if mode == "held":
                raise CheckError("unsupported range requires review")
            if (tool.startswith("npm:") or tool == "pnpm") and not node_ok:
                raise CheckError("Node verification failed")
            if mode == "range":
                if tool == "node":
                    check_node_globals(record, tools, job.home, job.env)
                command(["mise", "upgrade", "--quiet", "--yes", "--no-prune", tool],
                        job.home, job.env, timeout=600)
            current = global_tools(job.home, job.env)[tool]
            verify_tool(tool, current, job.home, job.env)
            result.update(after=safe_version(current.get("version")), status="verified")
        except (CheckError, KeyError) as error:
            failures.append(tool)
            result["status"] = "failed"
            result["reason"] = str(error) if isinstance(error, CheckError) else "incomplete tool record"
            if tool == "node":
                node_ok = False
        results.append(result)
        job.log("INFO", f"{tool}: {result['status']}")
    job.state["tools"] = results
    job.state["previous_versions_retained"] = True
    return job.finish(failures)


def run_homebrew(job):
    updater = job.home / "Library/Application Support/com.github.domt4.homebrew-autoupdate/brew_autoupdate"
    env = dict(os.environ, HOME=str(job.home))
    try:
        command([str(updater)], job.home, env, timeout=3600)
        return job.finish([])
    except CheckError:
        return job.finish(["homebrew"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=("mise", "audit", "homebrew"))
    parser.add_argument("--force", action="store_true", help="Bypass power and recency gates")
    args = parser.parse_args(argv)
    try:
        with Job(Path.home(), args.job, 7 * DAY if args.job == "audit" else 20 * 3600,
                 args.force) as job:
            if not job:
                return 0
            if args.job == "mise":
                return update_mise(job)
            if args.job == "homebrew":
                return run_homebrew(job)
            from software_update_audit import audit
            return audit(job)
    except (CheckError, OSError, ValueError, KeyError, TypeError):
        print("Software maintenance failed. Check ~/.local/state/software-updates/.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.modules["software_updates"] = sys.modules[__name__]
    sys.exit(main())
