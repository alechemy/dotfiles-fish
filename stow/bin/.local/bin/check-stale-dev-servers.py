#!/usr/bin/python3
"""Notify when dev-server-like processes have run too long with too much RAM."""

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

MIN_RSS_KB = 1024 * 1024            # 1 GB
MIN_AGE_HOURS = 4
RENOTIFY_AFTER_SEC = 24 * 3600
PATTERN = re.compile(
    r"jetty|gradle|webpack|next[- ]dev|vite|rails server|bin/spring|java.*-jar",
    re.IGNORECASE,
)

HOME = Path.home()
STATE = HOME / ".local/state/check-stale-dev-servers/last.json"


def run_silent(argv, **kw):
    try:
        return subprocess.run(argv, check=False, capture_output=True, timeout=5, **kw)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def parse_etime(s):
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(p) for p in s.split(":")]
    parts = [0] * (3 - len(parts)) + parts
    h, m, sec = parts
    return (days * 86400 + h * 3600 + m * 60 + sec) / 3600.0


def find_matches():
    out = subprocess.check_output(["ps", "-axo", "pid=,rss=,etime=,command="], text=True)
    hits = []
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid, rss, etime, cmd = parts
        try:
            pid_i, rss_i, age_h = int(pid), int(rss), parse_etime(etime)
        except ValueError:
            continue
        if rss_i < MIN_RSS_KB or age_h < MIN_AGE_HOURS or not PATTERN.search(cmd):
            continue
        hits.append((pid_i, rss_i, age_h, cmd))
    return hits


def should_notify(hits):
    fingerprint = hashlib.sha1(",".join(str(h[0]) for h in sorted(hits)).encode()).hexdigest()
    now = time.time()
    if STATE.exists():
        prev = json.loads(STATE.read_text())
        if prev.get("fp") == fingerprint and now - prev.get("ts", 0) < RENOTIFY_AFTER_SEC:
            return False
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"fp": fingerprint, "ts": now}))
    return True


def notify(hits):
    lines = [
        f"pid {pid}  {rss/1024/1024:.1f}GB  {age:.0f}h  {cmd.split('/')[-1][:60]}"
        for pid, rss, age, cmd in hits
    ]
    body = "\\n".join(l.replace('"', '\\"') for l in lines)
    subprocess.run(
        ["/usr/bin/osascript", "-e",
         f'display notification "{body}" with title "Stale dev servers ({len(hits)})" sound name "Pop"'],
        check=False,
    )


def main():
    run_silent([str(HOME / ".local/bin/pipeline-record-run"), "com.user.check-stale-dev-servers"])
    gate = run_silent([str(HOME / ".local/bin/should-run-background-job")])
    if gate is not None and gate.returncode != 0:
        return 0
    hits = find_matches()
    if hits and should_notify(hits):
        notify(hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
