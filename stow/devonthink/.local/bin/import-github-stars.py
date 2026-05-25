#!/usr/bin/python3
"""
import-github-stars.py — Import GitHub starred repos into DEVONthink.

Polls the GitHub API for newly starred repositories and creates bookmark
records in DEVONthink's 00_INBOX. The existing Extract: Web Content smart
rule then downloads the README as markdown and archives an HTML snapshot.
Documents flow through the standard pipeline (AI enrichment, etc.).

Idempotent: tracks imported repo full names in a local state file.

Usage:
    python3 import-github-stars.py              # import new stars
    python3 import-github-stars.py --dry-run    # preview without importing
    python3 import-github-stars.py --backfill   # import entire star history
    python3 import-github-stars.py --force owner/repo  # re-import a specific repo
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_NAME = "Lorebook"
INBOX_GROUP = "/00_INBOX"
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "github-stars-imported.json")
OLD_STATE_FILE = os.path.expanduser("~/.github-stars-dt-imported.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/github-stars-import.log")
GH_BIN = "/opt/homebrew/bin/gh"

DRY_RUN = "--dry-run" in sys.argv
BACKFILL = "--backfill" in sys.argv
FORCE_REPO = None
if "--force" in sys.argv:
    idx = sys.argv.index("--force")
    if idx + 1 < len(sys.argv):
        FORCE_REPO = sys.argv[idx + 1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [github-stars] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------


def _migrate_state_file():
    """Move state file from old ~ location to ~/.local/state/devonthink/."""
    if os.path.exists(OLD_STATE_FILE) and not os.path.exists(STATE_FILE):
        os.makedirs(STATE_DIR, exist_ok=True)
        os.rename(OLD_STATE_FILE, STATE_FILE)
        log(f"Migrated state file to {STATE_FILE}")


STATE_SCHEMA_VERSION = 1


def load_imported():
    """Return the set of previously-imported full_name strings.

    Fails closed: raises on unreadable or unrecognized state rather than
    silently returning empty, which would cause every star in the user's
    history to be re-imported into 00_INBOX on the next launchd tick. The
    only case that legitimately returns empty is "file does not exist yet"
    (genuine first run).

    Accepts the legacy bare-list format ([id1, id2, ...]) for transparent
    migration; the next save_imported() call upgrades the on-disk format to
    the v1 schema ({"version": 1, "ids": [...]}).
    """
    _migrate_state_file()
    if not os.path.exists(STATE_FILE):
        return set()
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"State file {STATE_FILE} is unreadable ({type(exc).__name__}: {exc}). "
            f"Imports are paused until the file is inspected and either "
            f"repaired or removed."
        ) from exc
    if isinstance(data, list):
        return set(data)
    if (
        isinstance(data, dict)
        and data.get("version") == STATE_SCHEMA_VERSION
        and isinstance(data.get("ids"), list)
    ):
        return set(data["ids"])
    raise RuntimeError(
        f"State file {STATE_FILE} has an unrecognized schema "
        f"(top-level type: {type(data).__name__}). Imports are paused until "
        f"the file is inspected and either repaired or removed."
    )


def save_imported(repos):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=STATE_DIR, prefix=".github-stars-imported.", suffix=".json.tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"version": STATE_SCHEMA_VERSION, "ids": sorted(repos)},
                f,
                indent=2,
            )
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def fetch_stars(imported, first_run):
    """Fetch starred repos newest-first via gh CLI.

    In normal mode, stops at the first already-imported repo.
    On first run (no state file), only fetches stars from the last 24 hours
    to avoid flooding the inbox. Use --backfill to import the full history.
    """
    if first_run and not BACKFILL:
        cutoff = datetime.utcnow().replace(microsecond=0) - timedelta(hours=24)
        cutoff_iso = cutoff.isoformat() + "Z"
        log(
            f"First run — only importing stars after {cutoff_iso} (use --backfill for full history)"
        )
    else:
        cutoff_iso = None

    all_stars = []
    page = 1
    per_page = 100

    while True:
        url = (
            f"/user/starred?per_page={per_page}&page={page}&sort=created&direction=desc"
        )
        try:
            result = subprocess.run(
                [GH_BIN, "api", url, "-H", "Accept: application/vnd.github.star+json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            log(f"gh CLI not found at {GH_BIN}")
            sys.exit(1)

        if result.returncode != 0:
            log(f"GitHub API error: {result.stderr.strip()}")
            break

        try:
            stars = json.loads(result.stdout)
        except json.JSONDecodeError:
            log("Failed to parse GitHub API response")
            break

        if not stars:
            break

        stop = False
        for star in stars:
            repo = star.get("repo", {})
            full_name = repo.get("full_name", "")
            if not full_name:
                continue

            # On first run, skip stars older than the cutoff
            if cutoff_iso and star.get("starred_at", "") < cutoff_iso:
                stop = True
                break

            if full_name in imported and full_name != FORCE_REPO:
                if not BACKFILL:
                    stop = True
                    break
                continue

            all_stars.append(
                {
                    "full_name": full_name,
                    "url": repo.get("html_url", f"https://github.com/{full_name}"),
                    "description": repo.get("description") or "",
                    "starred_at": star.get("starred_at", ""),
                }
            )

        if stop or len(stars) < per_page:
            break

        page += 1

    return all_stars


# ---------------------------------------------------------------------------
# DEVONthink import via AppleScript
# ---------------------------------------------------------------------------

IMPORT_APPLESCRIPT = """
on run argv
    set repoName to item 1 of argv
    set repoURL to item 2 of argv
    set repoDesc to item 3 of argv

    tell application id "DNtp"
        try
            set targetDB to database "%%DATABASE%%"
        on error
            return "error: database not found"
        end try

        set destGroup to get record at "%%INBOX%%" in targetDB
        if destGroup is missing value then
            return "error: inbox group not found"
        end if

        set newRecord to create record with {name:repoName, type:bookmark, URL:repoURL} in destGroup

        -- Skip Extract: Web Content by pre-setting Recognized and Commented.
        -- The description (from the GitHub API) is stored as the Finder comment
        -- so AI Enrichment has something to generate tags and a summary from.
        add custom meta data 1 for "NeedsProcessing" to newRecord
        add custom meta data 1 for "Recognized" to newRecord
        add custom meta data 1 for "Commented" to newRecord

        if repoDesc is not "" then
            set comment of newRecord to repoDesc
        end if

        return "ok: " & (name of newRecord)
    end tell
end run
"""


def import_to_devonthink(star, script_path):
    name = star["full_name"]
    url = star["url"]
    desc = star["description"]

    result = subprocess.run(
        ["/usr/bin/osascript", script_path, name, url, desc],
        capture_output=True,
        text=True,
        timeout=30,
    )

    output = result.stdout.strip()
    if result.returncode != 0:
        return False, result.stderr.strip()
    if output.startswith("error:"):
        return False, output
    return True, output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # Surface missed runs from laptop sleep cycles. Best-effort; never block
    # the import on the helper failing. Runs *before* the battery gate so
    # routine battery skips don't register as missed runs.
    subprocess.run(
        [
            os.path.expanduser("~/.local/bin/pipeline-record-run"),
            "github-stars-import",
            "1800",
        ],
        check=False,
    )

    # Skip launchd-driven runs on battery. User-invoked runs (--force,
    # --backfill, --dry-run) bypass the gate so explicit intent always wins.
    user_invoked = BACKFILL or FORCE_REPO is not None or DRY_RUN
    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job")],
            capture_output=True,
            text=True,
        )
        if gate.returncode != 0:
            log(gate.stderr.strip() or "skipping: not on AC power")
            return

    # Verify gh CLI is authenticated
    try:
        auth_check = subprocess.run(
            [GH_BIN, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        log(f"gh CLI not found at {GH_BIN}")
        sys.exit(1)

    if auth_check.returncode != 0:
        log("gh CLI not authenticated — run 'gh auth login' first")
        sys.exit(1)

    imported = load_imported()
    first_run = len(imported) == 0
    log(f"Fetching starred repos ({len(imported)} already imported)")

    stars = fetch_stars(imported, first_run)
    if not stars:
        log("No new stars to import")
        return

    log(f"Found {len(stars)} new star(s)")

    # Write AppleScript to temp file
    script_content = IMPORT_APPLESCRIPT.replace("%%DATABASE%%", DATABASE_NAME).replace(
        "%%INBOX%%", INBOX_GROUP
    )
    fd, script_path = tempfile.mkstemp(suffix=".applescript")
    with os.fdopen(fd, "w") as f:
        f.write(script_content)

    try:
        new_count = 0

        # Import oldest-first so pipeline processes in chronological order
        for i, star in enumerate(reversed(stars)):
            desc = f" — {star['description'][:80]}" if star["description"] else ""

            if DRY_RUN:
                log(f"[DRY RUN] Would import: {star['full_name']}{desc}")
                new_count += 1
                continue

            log(f"Importing: {star['full_name']}{desc}")
            success, msg = import_to_devonthink(star, script_path)

            if success:
                imported.add(star["full_name"])
                save_imported(imported)
                log(f"  {msg}")
                new_count += 1
            else:
                log(f"  FAILED: {msg}")

            # Small delay between imports to avoid overwhelming DT
            if i < len(stars) - 1:
                time.sleep(0.5)

        log(f"Done: {new_count} imported")

    finally:
        os.unlink(script_path)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as exc:
        # Surface fatal errors in the regular log file rather than only in
        # /tmp/github-stars-import.log, which the user is unlikely to check.
        log(f"FATAL: {type(exc).__name__}: {exc}")
        log(traceback.format_exc())
        sys.exit(1)
