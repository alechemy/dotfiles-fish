#!/usr/bin/env python3
"""Batch-capture all DEVONthink bookmarks with NeedsSingleFile=1.

Replaces the Capture: SingleFile Batch smart rule. Invoked manually by the
user (e.g. Keyboard Maestro hotkey or shell) whenever they want to drain
the queue of pending bookmarks, typically at a time when the browser can
be hijacked.

For each pending bookmark:
  1. Drive the running Chromium via `capture-with-singlefile --url <url>`
     (single-URL at a time, so we never race ourselves between bookmarks).
  2. Hand the resulting HTML file to `ingest-singlefile-html.py
     --bookmark <uuid>` which creates the HTML snapshot + markdown extract
     and cross-links them to the existing bookmark.
  3. On success, NeedsSingleFile is cleared on the bookmark by the ingester.

Logs to ~/Library/Logs/devonthink-pipeline.log alongside the ingester.

Usage:
    capture-bookmarks-batch.py          # capture all pending bookmarks
    capture-bookmarks-batch.py --dry-run  # list pending, don't capture
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log  # noqa: E402

CAPTURE_WITH_SINGLEFILE = Path.home() / ".local" / "bin" / "capture-with-singlefile"
INGEST_SINGLEFILE = Path.home() / ".local" / "bin" / "ingest-singlefile-html.py"

log = setup_log("capture-bookmarks-batch")

LIST_PENDING_APPLESCRIPT = r"""
tell application id "DNtp"
    set pending to {}
    try
        set results to search "kind:bookmark" in database "Lorebook"
        repeat with r in results
            try
                set needs to (get custom meta data for "NeedsSingleFile" from r)
                if needs is 1 then
                    set recURL to URL of r
                    if recURL is not "" and recURL is not missing value then
                        set end of pending to (uuid of r) & tab & recURL
                    end if
                end if
            end try
        end repeat
    end try
    set AppleScript's text item delimiters to linefeed
    set output to pending as text
    set AppleScript's text item delimiters to ""
    return output
end tell
"""


FIND_CAPTURED_BOOKMARK_APPLESCRIPT = r"""
on run argv
    set targetURL to item 1 of argv
    set excludeUUID to ""
    if (count of argv) > 1 then
        set excludeUUID to item 2 of argv
    end if
    tell application id "DNtp"
        try
            set candidates to lookup records with URL targetURL in database "Lorebook"
            repeat with r in candidates
                try
                    if (uuid of r) is not excludeUUID and (type of r) is bookmark then
                        set snapshotRaw to (get custom meta data for "WebClipSnapshot" from r)
                        if snapshotRaw is not missing value then
                            set snapshotText to snapshotRaw as text
                            if snapshotText is not "" and snapshotText is not "missing value" then
                                return uuid of r
                            end if
                        end if
                    end if
                end try
            end repeat
        end try
        return ""
    end tell
end run
"""


CLEAR_NEEDS_SINGLEFILE_APPLESCRIPT = r"""
on run argv
    set targetUUID to item 1 of argv
    tell application id "DNtp"
        try
            set r to get record with uuid targetUUID
            add custom meta data 0 for "NeedsSingleFile" to r
        end try
    end tell
end run
"""


def find_existing_capture(url: str, exclude_uuid: str) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-", url, exclude_uuid],
            input=FIND_CAPTURED_BOOKMARK_APPLESCRIPT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        log.warning("dedup lookup failed: %s", e.stderr)
        return ""


def clear_needs_singlefile(bookmark_uuid: str) -> None:
    subprocess.run(
        ["osascript", "-", bookmark_uuid],
        input=CLEAR_NEEDS_SINGLEFILE_APPLESCRIPT,
        capture_output=True,
        text=True,
        check=False,
    )




def list_pending() -> list[tuple[str, str]]:
    result = subprocess.run(
        ["osascript", "-e", LIST_PENDING_APPLESCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    out = result.stdout.strip()
    if not out:
        return []
    pairs = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        uuid, url = line.split("\t", 1)
        pairs.append((uuid.strip(), url.strip()))
    return pairs


def capture_one(url: str) -> Path | None:
    """Run capture-with-singlefile for a single URL, return the resulting HTML path."""
    result = subprocess.run(
        [str(CAPTURE_WITH_SINGLEFILE), "--url", url],
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = result.stdout.strip()
    if not stdout:
        log.error("capture failed: %s\n%s", url, result.stderr.strip())
        return None
    first_line = stdout.splitlines()[0]
    if not first_line.startswith("/"):
        log.error("capture failed: %s — %s", url, first_line)
        return None
    return Path(first_line)


def ingest_one(html_path: Path, bookmark_uuid: str, force: bool) -> bool:
    cmd = [str(INGEST_SINGLEFILE), str(html_path), "--bookmark", bookmark_uuid]
    if force:
        cmd.append("--force")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        log.error("ingest failed for %s: %s", html_path, result.stderr.strip())
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="list pending bookmarks, don't capture"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="capture even if another bookmark with the same URL is already captured",
    )
    args = parser.parse_args()

    try:
        pending = list_pending()
    except subprocess.CalledProcessError as e:
        log.error("failed to query DEVONthink: %s", e.stderr)
        return 1

    if not pending:
        log.info("no bookmarks with NeedsSingleFile=1; nothing to do")
        return 0

    log.info("%d pending bookmark(s):", len(pending))
    for uuid, url in pending:
        log.info("  %s  %s", uuid, url)

    if args.dry_run:
        return 0

    succeeded = 0
    skipped: list[str] = []
    failed: list[str] = []
    for i, (uuid, url) in enumerate(pending, start=1):
        # Pre-filter: if another bookmark with this URL already has a
        # WebClipSnapshot set, skip the browser hijack entirely and just
        # clear NeedsSingleFile on this duplicate. Saves ~15s of capture
        # time per dupe; the dups themselves are left in place for the
        # user to review via DT's built-in duplicate detector.
        if not args.force:
            existing = find_existing_capture(url, exclude_uuid=uuid)
            if existing:
                log.info(
                    "[%d/%d] skipping %s — URL already captured by bookmark %s",
                    i,
                    len(pending),
                    url,
                    existing,
                )
                clear_needs_singlefile(uuid)
                skipped.append(url)
                continue

        log.info("[%d/%d] capturing %s", i, len(pending), url)
        html_path = capture_one(url)
        if html_path is None:
            failed.append(url)
            continue
        if not ingest_one(html_path, uuid, args.force):
            failed.append(url)
            continue
        succeeded += 1

    log.info(
        "done: %d succeeded, %d skipped (dup), %d failed",
        succeeded,
        len(skipped),
        len(failed),
    )
    for url in failed:
        log.info("  FAILED: %s", url)

    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
