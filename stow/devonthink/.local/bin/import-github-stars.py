#!/usr/bin/python3
"""
import-github-stars.py — Import GitHub starred repos into DEVONthink.

Polls the GitHub API for newly starred repositories and creates bookmark
records in DEVONthink's 00_INBOX, pre-flagged Recognized/Commented so
Extract: Web Content skips them (the repo description in the Finder comment
is the content; no README download or HTML snapshot is wanted). Records
flow through the standard pipeline (AI enrichment, etc.).

Idempotent: tracks imported repo full names in a local state file.

Usage:
    python3 import-github-stars.py              # import new stars
    python3 import-github-stars.py --dry-run    # preview without importing
    python3 import-github-stars.py --backfill   # import entire star history
    python3 import-github-stars.py --force owner/repo  # re-import a specific repo
    python3 import-github-stars.py --rebuild-state  # re-derive state from DEVONthink
"""

import json
import os
import re
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
STALL_FILE = os.path.join(STATE_DIR, "github-stars-stalls")
LOG_FILE = os.path.expanduser("~/Library/Logs/github-stars-import.log")
GH_BIN = "/opt/homebrew/bin/gh"
GH_TIMEOUT = 30

# A stalled tick is routine (the agent re-runs every 30 minutes and the import
# is idempotent), so it stays quiet. An outage that survives this many
# consecutive ticks is not routine and pages instead.
STALL_ALERT_AFTER = 6

DRY_RUN = "--dry-run" in sys.argv
BACKFILL = "--backfill" in sys.argv
REBUILD_STATE = "--rebuild-state" in sys.argv
FORCE_REPO = None
if "--force" in sys.argv:
    idx = sys.argv.index("--force")
    if idx + 1 < len(sys.argv):
        FORCE_REPO = sys.argv[idx + 1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# dt-watchdog scans this log and pages on failure tokens; the /manual tag
# tells it a human was driving (same convention as pipeline_log.py).
_COMPONENT = (
    "github-stars/manual"
    if sys.stdout.isatty() or os.environ.get("PIPELINE_MANUAL") == "1"
    else "github-stars"
)

MANUAL_RUN = _COMPONENT.endswith("/manual")


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [{_COMPONENT}] {msg}"
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
    """Return (imported full_name set, retry list of star dicts).

    The retry list holds stars whose import failed: fetch_stars stops at the
    first already-imported repo, so a failed star older than a succeeded one
    would otherwise never be fetched again.

    Fails closed: raises on unreadable or unrecognized state rather than
    silently returning empty, which would cause every star in the user's
    history to be re-imported into 00_INBOX on the next launchd tick. The
    only case that legitimately returns empty is "file does not exist yet"
    (genuine first run).

    Accepts the legacy bare-list format ([id1, id2, ...]) for transparent
    migration; the next save_imported() call upgrades the on-disk format to
    the v1 schema ({"version": 1, "ids": [...], "retry": [...]}).
    """
    _migrate_state_file()
    if not os.path.exists(STATE_FILE):
        return set(), []
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
        return set(data), []
    if (
        isinstance(data, dict)
        and data.get("version") == STATE_SCHEMA_VERSION
        and isinstance(data.get("ids"), list)
    ):
        retry = data.get("retry", [])
        if not isinstance(retry, list):
            retry = []
        return set(data["ids"]), retry
    raise RuntimeError(
        f"State file {STATE_FILE} has an unrecognized schema "
        f"(top-level type: {type(data).__name__}). Imports are paused until "
        f"the file is inspected and either repaired or removed."
    )


def save_imported(repos, retry):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=STATE_DIR, prefix=".github-stars-imported.", suffix=".json.tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(
                {
                    "version": STATE_SCHEMA_VERSION,
                    "ids": sorted(repos),
                    "retry": retry,
                },
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


def merge_pending(stars, retry, imported):
    """Combine freshly-fetched stars with retry entries from prior failed
    imports. Retry entries are older than anything fetched, so they go last —
    the import loop runs reversed (oldest first). Drops retries already
    re-fetched or since imported."""
    fetched = {s["full_name"] for s in stars}
    return stars + [
        r for r in retry
        if r["full_name"] not in fetched and r["full_name"] not in imported
    ]


def record_stall(reason, scheduled):
    """Log a retryable fetch failure, escalating once it has outlived
    STALL_ALERT_AFTER consecutive scheduled ticks.

    Below the threshold the wording deliberately avoids dt-watchdog's failure
    tokens: a lone stall on a job that re-ticks every 30 minutes loses nothing
    and must not page. Above it, a WARNING pages (the watchdog dedups it to once
    a day) so a persistent stall can't skip forever in silence.

    Only scheduled runs touch the counter. A hand-run failure is exempt from the
    watchdog (it logs as <component>/manual), so counting it would let six failed
    --dry-run attempts make the next scheduled failure page as the seventh.
    """
    if not scheduled:
        log(f"skipping: {reason}; retrying next run")
        return

    try:
        with open(STALL_FILE) as f:
            count = int(f.read().strip())
    except FileNotFoundError:
        count = 0
    except (OSError, ValueError) as exc:
        log(f"WARNING stall counter unreadable ({exc}); restarting the count")
        count = 0
    count += 1

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STALL_FILE, "w") as f:
            f.write(str(count))
    except OSError as exc:
        log(f"WARNING could not record the stall counter ({exc})")

    if count >= STALL_ALERT_AFTER:
        log(f"WARNING gh has not completed a fetch in {count} consecutive runs — {reason}")
    else:
        log(f"skipping: {reason}; retrying next run")


def clear_stalls(scheduled):
    """Reset the counter on every outcome that is not a retryable failure, so
    "consecutive" keeps meaning consecutive."""
    if not scheduled:
        return
    try:
        os.unlink(STALL_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(f"WARNING could not clear the stall counter ({exc})")


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


class GhError(RuntimeError):
    """Base for a failed gh invocation."""


class GhUnavailable(GhError):
    """The fetch failed in a way that retrying can fix — a stall, a transport
    failure, a rate limit, a 5xx. This is the *only* outcome the stall counter
    tracks: it counts consecutive retryable failures, not unreachability, so
    every other outcome resets it."""


class FetchError(GhError):
    """A pagination request failed unretryably mid-fetch. The star list is
    incomplete, so the run must import nothing rather than commit an earlier
    page and advance the frontier past the repos it never saw."""


class GhAuthError(GhError):
    """gh has no usable credentials. Actionable by the user, so it pages."""


class GhLocalError(GhError):
    """gh is missing or unusable on this machine."""


# gh's documented exit code for "no authentication configured". It never
# reaches the network, so it is not a stall.
GH_EXIT_NO_AUTH = 4

_HTTP_STATUS_RE = re.compile(r"\(HTTP (\d{3})\)")

# Transport failures gh surfaces as a bare Go error. Matching them explicitly
# (rather than inferring "transient" from the *absence* of an HTTP status) keeps
# an unrecognized statusless failure fatal instead of silently skipping forever.
_TRANSPORT_MARKERS = (
    "dial tcp",
    "no such host",
    "i/o timeout",
    "tls handshake timeout",
    "connection refused",
    "connection reset",
    "connection timed out",
    "network is unreachable",
    "network is down",
    "no route to host",
    "broken pipe",
    "unexpected eof",
    "context deadline exceeded",
    "proxyconnect",
    "server misbehaving",
    "operation timed out",
)


def classify_gh_failure(returncode, stderr):
    """Sort a failed `gh api` into "auth", "transient", or "fatal".

    Order matters: the exit code settles missing credentials before any parsing,
    an HTTP status decides the rest, and only a recognized transport marker
    earns "transient". Anything left is fatal — an unknown failure must surface,
    not masquerade as a retryable blip.
    """
    if returncode == GH_EXIT_NO_AUTH:
        return "auth"

    match = _HTTP_STATUS_RE.search(stderr)
    if match:
        code = int(match.group(1))
        if code == 401:
            return "auth"
        if code in (408, 429) or code >= 500:
            return "transient"
        if code == 403 and "rate limit" in stderr.lower():
            return "transient"
        return "fatal"

    lowered = stderr.lower()
    if any(marker in lowered for marker in _TRANSPORT_MARKERS):
        return "transient"
    return "fatal"


def run_gh(args):
    """Run gh, converting a stall into GhUnavailable.

    A timeout only proves gh did not finish — it must not reach the top-level
    handler, which logs FATAL and pages the user via dt-watchdog.
    """
    try:
        return subprocess.run(
            [GH_BIN, *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GhUnavailable(f"gh {args[0]} did not complete within {GH_TIMEOUT}s")
    except OSError as exc:
        raise GhLocalError(f"cannot run gh at {GH_BIN}: {exc}")


def fetch_stars(imported, first_run):
    """Fetch starred repos newest-first via gh CLI.

    In normal mode, stops at the first already-imported repo. A --force target
    suppresses that stop until the target is reached, so a forced re-import can
    reach a repo older than the frontier.
    On first run (no state file), only fetches stars from the last 24 hours
    to avoid flooding the inbox. Use --backfill to import the full history.

    Raises FetchError if any page request fails, so a partial fetch is never
    committed, or GhUnavailable if GitHub was never reached at all.
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
    force_pending = FORCE_REPO is not None

    while True:
        url = (
            f"/user/starred?per_page={per_page}&page={page}&sort=created&direction=desc"
        )
        result = run_gh(
            ["api", url, "-H", "Accept: application/vnd.github.star+json"]
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            kind = classify_gh_failure(result.returncode, stderr)
            if kind == "auth":
                raise GhAuthError(
                    f"gh CLI not authenticated — run 'gh auth login' first ({stderr})"
                )
            if kind == "transient":
                raise GhUnavailable(stderr)
            raise FetchError(f"GitHub API request failed: {stderr}")

        try:
            stars = json.loads(result.stdout)
        except json.JSONDecodeError:
            log("ERROR parsing GitHub API response")
            raise FetchError("could not parse GitHub API response")

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
                if not BACKFILL and not force_pending:
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

            if full_name == FORCE_REPO:
                force_pending = False

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

        -- Idempotency: the database is the source of truth. A bookmark with
        -- this exact URL already exists when the state file was lost or the
        -- database restored; adopt it rather than creating a duplicate.
        set existing to lookup records with URL repoURL in targetDB
        if (count of existing) > 0 then
            return "exists: " & (name of item 1 of existing)
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


REBUILD_APPLESCRIPT = """
on run
    tell application id "DNtp"
        try
            set targetDB to database "%%DATABASE%%"
        on error
            return "error: database not found"
        end try
        set out to ""
        set hits to search "kind:bookmark url:~github.com" in root of targetDB
        repeat with hit in hits
            try
                set out to out & (URL of hit) & linefeed
            end try
        end repeat
        return out
    end tell
end run
"""


def rebuild_ids_from_devonthink():
    """Return the set of repo full names whose bookmarks exist in the
    database, or None when DEVONthink could not be queried (unknown, not
    empty). Only URLs shaped like a repo home page (github.com/owner/repo)
    count — deep links and gists captured by other pipelines are ignored."""
    fd, script_path = tempfile.mkstemp(suffix=".applescript")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(REBUILD_APPLESCRIPT.replace("%%DATABASE%%", DATABASE_NAME))
        result = subprocess.run(
            ["/usr/bin/osascript", script_path],
            capture_output=True, text=True, timeout=600,
        )
    finally:
        os.unlink(script_path)
    output = result.stdout.strip()
    if result.returncode != 0 or output.startswith("error:"):
        log(f"State rebuild query failed: {(result.stderr or output).strip()}")
        return None
    names = set()
    for line in output.splitlines():
        url = line.strip().rstrip("/")
        prefix = "https://github.com/"
        if not url.startswith(prefix):
            continue
        path = url[len(prefix):]
        if path.count("/") == 1:
            names.add(path)
    return names


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
    # --backfill, --dry-run, --rebuild-state) bypass the gate so explicit
    # intent always wins.
    user_invoked = BACKFILL or FORCE_REPO is not None or DRY_RUN or REBUILD_STATE
    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job")],
            capture_output=True,
            text=True,
        )
        if gate.returncode != 0:
            log(gate.stderr.strip() or "skipping: not on AC power")
            return

    # Role gate: setup.sh skips loading this agent on follower machines, but
    # a follower bootstrapped before the role split can still have it loaded —
    # importing there against the synced database with an independent local
    # state file produces duplicates. User-invoked runs bypass, matching
    # should-run-dt-driver's own --force semantics.
    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-dt-driver")],
            capture_output=True,
            text=True,
        )
        if gate.returncode != 0:
            log("skipping: this Mac is a pipeline follower (should-run-dt-driver)")
            return

    # Deliberately no `gh auth status` preflight: it reports an unreachable
    # network as "The token in keyring is invalid" — indistinguishable from a
    # genuinely bad token — so it would send the user to re-authenticate a
    # perfectly good one. The fetch below is the real test, and `gh api` does
    # separate the two (HTTP 401 vs. a transport error carrying no status).

    if REBUILD_STATE:
        rebuilt = rebuild_ids_from_devonthink()
        if rebuilt is None:
            sys.exit(1)
        existing, retry = (
            load_imported() if os.path.exists(STATE_FILE) else (set(), [])
        )
        merged = existing | rebuilt
        log(f"State rebuild: {len(rebuilt)} repo(s) in DEVONthink, "
            f"{len(existing)} in state file, {len(merged)} after merge")
        if DRY_RUN:
            log("[DRY RUN] state file not written")
        else:
            save_imported(merged, retry)
        return

    state_file_existed = os.path.exists(STATE_FILE)
    imported, retry = load_imported()
    if not state_file_existed and not imported:
        # Fresh or restored machine: the database remembers what was already
        # imported even when the local state file is gone.
        rebuilt = rebuild_ids_from_devonthink()
        if rebuilt:
            log(f"State file missing but DEVONthink holds {len(rebuilt)} "
                f"GitHub bookmark(s); rebuilding state from the database")
            imported = rebuilt
            if not DRY_RUN:
                save_imported(imported, retry)

    first_run = len(imported) == 0
    log(f"Fetching starred repos ({len(imported)} already imported)")

    scheduled = not (MANUAL_RUN or user_invoked)

    try:
        fetched = fetch_stars(imported, first_run)
    except GhUnavailable as exc:
        record_stall(str(exc), scheduled)
        return
    except (GhAuthError, GhLocalError) as exc:
        clear_stalls(scheduled)
        log(f"ERROR {exc}")
        sys.exit(1)
    except FetchError as exc:
        clear_stalls(scheduled)
        log(f"ERROR {exc}; nothing imported this run")
        return

    clear_stalls(scheduled)

    stars = merge_pending(fetched, retry, imported)
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
                retry = [r for r in retry if r["full_name"] != star["full_name"]]
                save_imported(imported, retry)
                log(f"  {msg}")
                new_count += 1
            else:
                if not any(r["full_name"] == star["full_name"] for r in retry):
                    retry.append(star)
                    save_imported(imported, retry)
                log(f"  FAILED: {msg} (queued for retry)")

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
