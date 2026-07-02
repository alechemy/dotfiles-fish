#!/usr/bin/python3
"""Mirror Chromium bookmarks into a managed folder inside Safari's bookmarks.

Alfred reads bookmarks only from Safari and Google Chrome, gating the Chrome
source on the app being installed — so a Chromium-only machine can't surface
its bookmarks in Alfred's default results. This bridges the gap: it rebuilds a
single top-level Safari folder (MANAGED_TITLE) from the Chromium profile's
Bookmarks file, leaving every other Safari bookmark untouched, so Alfred
indexes Chromium bookmarks with no keyword and no extra app.

Watches the Chromium Bookmarks file directly via fswatch (event-driven, so it
never polls or wakes on Chromium's other profile writes). Defers while Safari
is running: Safari caches bookmarks in memory and rewrites the file on its own
edits, which would clobber a folder we injected underneath it.

Writing ~/Library/Safari/ is Full-Disk-Access-gated, so the launchd agent runs
this under /usr/bin/python3 (Apple-signed, stable path); grant FDA to that
binary once and it survives interpreter upgrades.
"""

import argparse
import json
import os
import plistlib
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

MANAGED_TITLE = "Chromium"
CHROMIUM_PROFILE = "Default"

HOME = Path.home()
CHROMIUM_BM = HOME / "Library/Application Support/Chromium" / CHROMIUM_PROFILE / "Bookmarks"
SAFARI_BM = HOME / "Library/Safari/Bookmarks.plist"
STATE_DIR = HOME / ".local/state/chromium-bookmarks-sync"
BACKUP = STATE_DIR / "Safari-Bookmarks.firstrun-backup.plist"

NS = uuid.uuid5(uuid.NAMESPACE_URL, "chromium-bookmarks-sync")
MANAGED_UUID = str(uuid.uuid5(NS, "managed-root")).upper()

SETTLE = 1.0
MAX_COALESCE = 10.0


def log(msg):
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} chromium-bookmarks-sync: {msg}", flush=True)


def safari_running():
    return subprocess.run(
        ["pgrep", "-x", "Safari"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0


def _uuid_for(guid, fallback):
    return str(uuid.uuid5(NS, guid or fallback)).upper()


def _convert(node):
    kind = node.get("type")
    if kind == "url":
        return {
            "WebBookmarkType": "WebBookmarkTypeLeaf",
            "URLString": node.get("url", ""),
            "URIDictionary": {"title": node.get("name", "")},
            "WebBookmarkUUID": _uuid_for(node.get("guid"), "url:" + node.get("url", "")),
        }
    if kind == "folder":
        children = [c for c in (_convert(ch) for ch in node.get("children", [])) if c]
        return {
            "WebBookmarkType": "WebBookmarkTypeList",
            "Title": node.get("name", ""),
            "Children": children,
            "WebBookmarkUUID": _uuid_for(node.get("guid"), "folder:" + node.get("name", "")),
        }
    return None


def build_managed(chromium):
    roots = chromium.get("roots", {})
    children = []
    for key in ("bookmark_bar", "other", "synced"):
        children.extend(c for c in (_convert(ch) for ch in roots.get(key, {}).get("children", [])) if c)
    if not children:
        return None
    return {
        "WebBookmarkType": "WebBookmarkTypeList",
        "Title": MANAGED_TITLE,
        "Children": children,
        "WebBookmarkUUID": MANAGED_UUID,
    }


def count_leaves(node):
    if not node:
        return 0
    if node.get("WebBookmarkType") == "WebBookmarkTypeLeaf":
        return 1
    return sum(count_leaves(c) for c in node.get("Children", []))


# Safari annotates nodes it has seen with its own bookkeeping keys (Sync,
# ReadingListNonSync, ...). Compare only the projection this script owns, and
# preserve Safari's extras on rewrite, or every event would rewrite the plist
# and strip them — which Safari then re-adds, ping-ponging forever.
PROJECT_KEYS = ("WebBookmarkType", "Title", "URLString", "WebBookmarkUUID")


def project(node):
    out = {k: node[k] for k in PROJECT_KEYS if k in node}
    uri = node.get("URIDictionary")
    if isinstance(uri, dict) and "title" in uri:
        out["URIDictionary"] = {"title": uri["title"]}
    if "Children" in node:
        out["Children"] = [project(c) for c in node["Children"]]
    return out


def index_by_uuid(node, out):
    u = node.get("WebBookmarkUUID")
    if u:
        out[u] = node
    for c in node.get("Children", []):
        index_by_uuid(c, out)


def merge_safari_keys(node, old_by_uuid):
    old = old_by_uuid.get(node.get("WebBookmarkUUID"))
    if old:
        for k, v in old.items():
            if k != "Children" and k not in node:
                node[k] = v
    for c in node.get("Children", []):
        merge_safari_keys(c, old_by_uuid)


def is_managed(child):
    return (
        isinstance(child, dict)
        and child.get("WebBookmarkType") == "WebBookmarkTypeList"
        and (child.get("WebBookmarkUUID") == MANAGED_UUID or child.get("Title") == MANAGED_TITLE)
    )


def load_safari():
    try:
        raw = SAFARI_BM.read_bytes()
    except PermissionError:
        log("ERROR: cannot read Safari bookmarks — grant Full Disk Access to /usr/bin/python3")
        return None, None
    except FileNotFoundError:
        log(f"ERROR: Safari bookmarks not found at {SAFARI_BM}")
        return None, None
    fmt = plistlib.FMT_BINARY if raw[:6] == b"bplist" else plistlib.FMT_XML
    try:
        return fmt, plistlib.loads(raw)
    except Exception as e:
        log(f"ERROR: Safari bookmarks unparseable (will retry on next event): {e}")
        return None, None


def backup_once():
    if BACKUP.exists():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SAFARI_BM, BACKUP)
    log(f"saved one-time Safari bookmarks backup to {BACKUP}")


def write_safari(data, fmt):
    backup_once()
    tmp = tempfile.NamedTemporaryFile(dir=str(SAFARI_BM.parent), prefix=".bm-sync-", delete=False)
    try:
        plistlib.dump(data, tmp, fmt=fmt)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, str(SAFARI_BM))
    except BaseException:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    os.chmod(SAFARI_BM, 0o600)


def sync(force=False, dry_run=False):
    if not CHROMIUM_BM.exists():
        log(f"Chromium bookmarks not found at {CHROMIUM_BM}")
        return
    if not force and safari_running():
        log("Safari is running; deferring (syncs on next bookmark change or next agent load)")
        return
    try:
        chromium = json.loads(CHROMIUM_BM.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log(f"could not read Chromium bookmarks (will retry on next event): {e}")
        return

    managed = build_managed(chromium)
    fmt, safari = load_safari()
    if safari is None:
        return

    existing = safari.get("Children", [])
    old_managed = [c for c in existing if is_managed(c)]
    others = [c for c in existing if not is_managed(c)]

    if managed is None:
        up_to_date = not old_managed
    else:
        up_to_date = len(old_managed) == 1 and project(old_managed[0]) == project(managed)
    if up_to_date:
        log(f"up to date ({count_leaves(managed)} bookmark(s))")
        return

    n = count_leaves(managed)
    if dry_run:
        verb = "remove empty managed folder" if managed is None else f"write {n} bookmark(s)"
        log(f"[dry-run] would {verb} in Safari folder {MANAGED_TITLE!r}")
        return

    if managed is not None:
        old_by_uuid = {}
        for node in old_managed:
            index_by_uuid(node, old_by_uuid)
        merge_safari_keys(managed, old_by_uuid)

    safari["Children"] = others + ([managed] if managed is not None else [])
    try:
        write_safari(safari, fmt)
    except OSError as e:
        log(f"ERROR: Safari bookmarks write failed (will retry on next event): {e}")
        return
    if managed is None:
        log(f"removed empty managed folder {MANAGED_TITLE!r}")
    else:
        log(f"synced {n} bookmark(s) into Safari folder {MANAGED_TITLE!r}")


def resolve_fswatch():
    for candidate in ("/opt/homebrew/bin/fswatch", "/usr/local/bin/fswatch"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("fswatch")


def watch():
    fswatch = resolve_fswatch()
    if not fswatch:
        # Exit 0: with KeepAlive.SuccessfulExit=false a clean exit leaves the
        # job dormant until the next login instead of respawning every 10s.
        log("ERROR: fswatch not found (brew install fswatch); exiting until next load")
        sys.exit(0)
    if not CHROMIUM_BM.exists():
        log(f"Chromium bookmarks not found at {CHROMIUM_BM}; exiting")
        sys.exit(0)

    log(f"starting; watching {CHROMIUM_BM}")
    sync()

    proc = subprocess.Popen(
        [fswatch, "-0", "--latency", "1", str(CHROMIUM_BM)], stdout=subprocess.PIPE
    )
    fd = proc.stdout.fileno()
    buf = b""
    dirty_since = None
    eof = False
    # Trailing-edge coalescing: mark dirty on each event and sync only after
    # SETTLE seconds of quiet, so the last save in a burst is never dropped.
    # MAX_COALESCE bounds staleness if events somehow never go quiet.
    while not eof:
        timeout = SETTLE if dirty_since is not None else None
        readable, _, _ = select.select([fd], [], [], timeout)
        now = time.monotonic()
        if readable:
            chunk = os.read(fd, 65536)
            if not chunk:
                eof = True
            else:
                buf += chunk
                *events, buf = buf.split(b"\0")
                for ev in events:
                    path = ev.decode("utf-8", "replace")
                    if os.path.basename(path) == "Bookmarks" and dirty_since is None:
                        dirty_since = now
                if dirty_since is not None and now - dirty_since < MAX_COALESCE:
                    continue
        if dirty_since is None:
            continue
        dirty_since = None
        try:
            sync()
        except Exception:
            import traceback
            log("ERROR: sync failed (watcher continues):\n" + traceback.format_exc())
    log("fswatch exited; restarting via KeepAlive")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Sync Chromium bookmarks into Safari for Alfred.")
    parser.add_argument("--once", action="store_true", help="sync a single time and exit")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing (implies --once)")
    parser.add_argument("--force", action="store_true", help="sync even while Safari is running")
    args = parser.parse_args()

    if args.dry_run or args.once or args.force:
        sync(force=args.force, dry_run=args.dry_run)
    else:
        watch()


if __name__ == "__main__":
    main()
