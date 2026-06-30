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

DEBOUNCE = 2.0
SETTLE = 1.0


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
    return fmt, plistlib.loads(raw)


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
    except (json.JSONDecodeError, OSError) as e:
        log(f"could not read Chromium bookmarks (will retry on next event): {e}")
        return

    managed = build_managed(chromium)
    fmt, safari = load_safari()
    if safari is None:
        return

    existing = safari.get("Children", [])
    new_children = [c for c in existing if not is_managed(c)]
    if managed is not None:
        new_children.append(managed)

    if new_children == existing:
        log(f"up to date ({count_leaves(managed)} bookmark(s))")
        return

    n = count_leaves(managed)
    if dry_run:
        verb = "remove empty managed folder" if managed is None else f"write {n} bookmark(s)"
        log(f"[dry-run] would {verb} in Safari folder {MANAGED_TITLE!r}")
        return

    safari["Children"] = new_children
    write_safari(safari, fmt)
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
        log("ERROR: fswatch not found (brew install fswatch)")
        sys.exit(1)
    if not CHROMIUM_BM.exists():
        log(f"Chromium bookmarks not found at {CHROMIUM_BM}; exiting")
        sys.exit(0)

    log(f"starting; watching {CHROMIUM_BM}")
    sync()

    proc = subprocess.Popen(
        [fswatch, "-0", "--latency", "1", str(CHROMIUM_BM)], stdout=subprocess.PIPE
    )
    last = 0.0
    buf = b""
    while True:
        chunk = proc.stdout.read(1)
        if not chunk:
            break
        if chunk != b"\0":
            buf += chunk
            continue
        path = buf.decode("utf-8", "replace")
        buf = b""
        if os.path.basename(path) != "Bookmarks":
            continue
        now = time.monotonic()
        if now - last < DEBOUNCE:
            continue
        time.sleep(SETTLE)
        sync()
        last = time.monotonic()
    log("fswatch exited; stopping")


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
