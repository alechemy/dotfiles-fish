#!/usr/bin/python3
"""
import-granola.py — retired Granola meeting-notes importer, kept as a skeleton.

Granola moved the local SQLCipher decryption key to a Team-ID-scoped
keychain entry this script has no entitlement to read, so the companion
parser it depends on (import-granola-parse.py) has been deleted and this
entry point no longer runs end to end. Nothing loads or schedules it.

The file is kept because everything past the parser boundary — state
tracking, idempotent DEVONthink import via osascript, failure reporting,
--dry-run/--force/--rebuild-state — is reusable if a parser built on
Granola's public API replaces the deleted local-decryption one. Such a
parser should keep the same split: this script runs under the
Apple-signed /usr/bin/python3 so AppleEvents to DEVONthink come from a
signed sender whose identity persists across system updates, while the
parser stays adhoc-signed (uv) and out of the AppleEvents path entirely.

Usage (once a parser is restored):
    import-granola.py                  # import new meetings
    import-granola.py --dry-run        # preview without importing
    import-granola.py --force ID       # re-import a specific document ID
    import-granola.py --rebuild-state  # re-derive imported IDs from DEVONthink
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_NAME = "Lorebook"
INBOX_GROUP = "/00_INBOX"
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "granola-imported.json")
VERSION_STATE_FILE = os.path.join(STATE_DIR, "granola-version.json")
FAILURE_STATE_FILE = os.path.join(STATE_DIR, "granola-failure.json")
OLD_STATE_FILE = os.path.expanduser("~/.granola-dt-imported.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/granola-import.log")
NOTES_FILE = "~/.local/share/granola-import/NOTES.md"

GRANOLA_APP = "/Applications/Granola.app"
PARSER_SCRIPT = os.path.expanduser("~/.local/bin/import-granola-parse.py")
# Granola generates enhanced panel notes some time after a meeting ends, and
# a panel can arrive late or malformed; keep retrying a no-content meeting
# for a few days before giving up on it for good.
NO_NOTES_GIVE_UP_MINUTES = 3 * 24 * 60

DRY_RUN = "--dry-run" in sys.argv
REBUILD_STATE = "--rebuild-state" in sys.argv
FORCE_ID = None
if "--force" in sys.argv:
    idx = sys.argv.index("--force")
    if idx + 1 < len(sys.argv):
        FORCE_ID = sys.argv[idx + 1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# dt-watchdog scans this log and pages on failure tokens; the /manual tag
# tells it a human was driving (same convention as pipeline_log.py).
def _is_manual():
    return (os.environ.get("PIPELINE_MANUAL") == "1"
            or sys.stdout.isatty() or sys.stderr.isatty())


_COMPONENT = "granola-import/manual" if _is_manual() else "granola-import"


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


def load_imported_ids():
    """Return the set of previously-imported Granola document IDs.

    Fails closed: raises on unreadable or unrecognized state rather than
    silently returning empty, which would cause every meeting Granola can
    decrypt to be re-imported into 00_INBOX (DEVONthink-side has no
    dedup — `create record` doesn't check for prior imports). The only
    case that legitimately returns empty is "file does not exist yet"
    (genuine first run).

    Accepts the legacy bare-list format ([id1, id2, ...]) for transparent
    migration; the next save_imported_ids() call upgrades the on-disk
    format to the v1 schema ({"version": 1, "ids": [...]}).
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


def save_imported_ids(ids):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=STATE_DIR, prefix=".granola-imported.", suffix=".json.tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"version": STATE_SCHEMA_VERSION, "ids": sorted(ids)},
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
# Granola version tracking
# ---------------------------------------------------------------------------


def get_granola_version():
    """Return Granola.app's CFBundleShortVersionString, or None if unreadable."""
    try:
        out = subprocess.check_output(
            ["defaults", "read",
             os.path.join(GRANOLA_APP, "Contents", "Info"),
             "CFBundleShortVersionString"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return out or None
    except Exception:
        return None


def check_version_change():
    """Log a one-liner only when Granola's version differs from last seen.

    Silent on first run and on unchanged versions. Useful when forensic-ing
    a regression: the log shows exactly which version transition correlates
    with the breakage.
    """
    current = get_granola_version()
    if not current:
        return
    last = None
    if os.path.exists(VERSION_STATE_FILE):
        try:
            with open(VERSION_STATE_FILE) as f:
                last = json.load(f).get("version")
        except Exception:
            pass
    if last and last != current:
        log(f"Granola version changed: {last} → {current}")
    if last != current:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(VERSION_STATE_FILE, "w") as f:
            json.dump(
                {"version": current, "seen_at": datetime.now().isoformat()}, f
            )


# ---------------------------------------------------------------------------
# Parser invocation
# ---------------------------------------------------------------------------


def run_parser(imported_ids, force_id):
    """Spawn import-granola-parse.py and return its parsed JSON output.

    The parser is launched via its own shebang (`uv run --script`), so this
    process tree stays out of the AppleEvents path entirely — uv churn is
    invisible to TCC.
    """
    parser_input = json.dumps({
        "imported_ids": sorted(imported_ids),
        "force_id": force_id,
    })
    # Launch agents run with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin),
    # so the parser's `#!/usr/bin/env -S uv run --script` shebang can't find
    # `uv` (Homebrew). Prepend the Homebrew bin dirs so `env` resolves it.
    parser_env = dict(os.environ)
    parser_env["PATH"] = (
        "/opt/homebrew/bin:/usr/local/bin:" + parser_env.get("PATH", "")
    )
    result = subprocess.run(
        [PARSER_SCRIPT],
        input=parser_input,
        capture_output=True,
        text=True,
        timeout=300,
        env=parser_env,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"parser exited {result.returncode}\n"
            f"--- stderr ---\n{result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"parser produced invalid JSON: {exc}\n"
            f"--- stdout (first 500 chars) ---\n{result.stdout[:500]}"
        )


# ---------------------------------------------------------------------------
# DEVONthink import via AppleScript
# ---------------------------------------------------------------------------

IMPORT_APPLESCRIPT = """
on run argv
    set contentPath to item 1 of argv
    set docTitle to item 2 of argv
    set granolaID to item 3 of argv
    set eventDate to item 4 of argv
    set docType to item 5 of argv
    set participantStr to item 6 of argv
    -- args 1-6: contentPath, docTitle, granolaID, eventDate, docType, participantStr

    set mdContent to do shell script "cat " & quoted form of contentPath without altering line endings

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

        -- Idempotency: the database is the source of truth. If a record with
        -- this GranolaID already exists (state file lost, restored database,
        -- concurrent run), adopt it instead of creating a duplicate. Only a
        -- record with empty text is repaired; flags are never re-primed, so
        -- an already-processed record is not pushed back into the pipeline.
        set hits to search "mdgranolaid==" & granolaID in root of targetDB
        repeat with hit in hits
            try
                if (get custom meta data for "GranolaID" from hit) as string is granolaID then
                    if (plain text of hit) is "" then
                        set plain text of hit to mdContent
                    end if
                    return "exists: " & (name of hit)
                end if
            end try
        end repeat

        set newRecord to create record with {name:docTitle, type:markdown} in destGroup
        set plain text of newRecord to mdContent

        -- Pipeline metadata. Recognized=1 + Commented=1 are pre-set to
        -- keep Extract: Native Text Bypass from matching this record and
        -- firing a mutation storm on it while DT's UI is rendering the
        -- new arrival; the markdown file was already lint-fixed on disk
        -- (see import_to_devonthink below) so the rule would be a no-op
        -- aside from those two flag writes.
        add custom meta data 1 for "NeedsProcessing" to newRecord
        add custom meta data 1 for "NameLocked" to newRecord
        add custom meta data 1 for "Recognized" to newRecord
        add custom meta data 1 for "Commented" to newRecord
        add custom meta data granolaID for "GranolaID" to newRecord
        add custom meta data docType for "DocumentType" to newRecord

        if eventDate is not "" then
            add custom meta data eventDate for "EventDate" to newRecord
        end if

        if participantStr is not "" then
            add custom meta data participantStr for "GranolaParticipants" to newRecord
        end if

        return "ok: " & (name of newRecord)
    end tell
end run
"""


def import_to_devonthink(meeting, script_path):
    fd, content_path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(meeting["markdown"])

        # Pre-lint the markdown on disk so the imported record arrives in
        # house style and we can pre-flag it with Recognized=1/Commented=1
        # to keep Extract: Native Text Bypass from matching. Non-fatal.
        lint_helper = os.path.expanduser("~/.local/bin/lint-markdown-file")
        if os.path.exists(lint_helper):
            subprocess.run(
                [lint_helper, content_path], capture_output=True, check=False
            )

        title = meeting["title"]
        if meeting["event_date"]:
            title = f"{meeting['event_date']} {title}"

        result = subprocess.run(
            [
                "/usr/bin/osascript",
                script_path,
                content_path,
                title,
                meeting["id"],
                meeting["event_date"],
                "Meeting Notes",
                meeting["participants"],
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        output = result.stdout.strip()
        if result.returncode != 0:
            return False, result.stderr.strip()
        if output.startswith("error:"):
            return False, output
        return True, output
    finally:
        os.unlink(content_path)


# ---------------------------------------------------------------------------
# Failure reporting
#
# Unhandled exceptions post a record to DEVONthink's 00_INBOX so silent
# breakage surfaces in the same place imports normally land. Dedup'd by
# (exception class, last frame) so a persistent failure doesn't spawn a
# new record every 30 minutes — only the first occurrence of each distinct
# failure mode reports.
# ---------------------------------------------------------------------------

ERROR_APPLESCRIPT = """
on run argv
    set contentPath to item 1 of argv
    set docTitle to item 2 of argv
    set mdContent to do shell script "cat " & quoted form of contentPath without altering line endings
    tell application id "DNtp"
        try
            set targetDB to database "%%DATABASE%%"
        on error
            return "error: database not found"
        end try
        set destGroup to get record at "%%INBOX%%" in targetDB
        if destGroup is missing value then return "error: inbox group not found"
        set newRecord to create record with {name:docTitle, type:markdown} in destGroup
        set plain text of newRecord to mdContent
        add custom meta data "Pipeline Error" for "DocumentType" to newRecord
        add custom meta data 1 for "NameLocked" to newRecord
        add custom meta data 1 for "Recognized" to newRecord
        add custom meta data 1 for "Commented" to newRecord
        -- Explicitly 0, not empty: Prime: Direct 00_INBOX Arrivals flips an
        -- empty NeedsProcessing to 1, which would send this operator-facing
        -- error record through enrichment and off to the archive.
        add custom meta data 0 for "NeedsProcessing" to newRecord
        return "ok: " & (name of newRecord)
    end tell
end run
"""


def _failure_signature(exc):
    tb = traceback.extract_tb(exc.__traceback__)
    f = tb[-1] if tb else None
    s = f"{type(exc).__name__}|{f.filename if f else ''}|" \
        f"{f.lineno if f else ''}|{f.name if f else ''}"
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def report_failure_to_devonthink(exc):
    sig = _failure_signature(exc)
    last_sig = None
    if os.path.exists(FAILURE_STATE_FILE):
        try:
            with open(FAILURE_STATE_FILE) as f:
                last_sig = json.load(f).get("signature")
        except Exception:
            pass
    if last_sig == sig:
        return  # already reported this exact failure mode

    version = get_granola_version() or "unknown"
    # 3-arg form: /usr/bin/python3 is 3.9, which lacks the 3.10+ single-arg
    # traceback.format_exception(exc) signature.
    tb_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    body = (
        "# Granola import failed\n\n"
        f"**Granola version:** {version}\n"
        f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"**Debug guide:** `{NOTES_FILE}`\n\n"
        "```\n"
        f"{tb_text}"
        "```\n"
    )
    title = f"Granola import failed: {type(exc).__name__}"

    fd_md, content_path = tempfile.mkstemp(suffix=".md")
    fd_s, script_path = tempfile.mkstemp(suffix=".applescript")
    try:
        with os.fdopen(fd_md, "w") as f:
            f.write(body)
        with os.fdopen(fd_s, "w") as f:
            f.write(
                ERROR_APPLESCRIPT.replace("%%DATABASE%%", DATABASE_NAME)
                .replace("%%INBOX%%", INBOX_GROUP)
            )
        result = subprocess.run(
            ["/usr/bin/osascript", script_path, content_path, title],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0 or result.stdout.strip().startswith("error:"):
            # The signature is deliberately not persisted: an undelivered
            # report must be retried on the next run, not remembered as sent.
            log(f"Failure record post failed: "
                f"{(result.stderr or result.stdout).strip()}")
            return
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(FAILURE_STATE_FILE, "w") as f:
            json.dump(
                {"signature": sig, "reported_at": datetime.now().isoformat()}, f
            )
    finally:
        for p in (content_path, script_path):
            try:
                os.unlink(p)
            except Exception:
                pass


def clear_failure_state():
    if os.path.exists(FAILURE_STATE_FILE):
        try:
            os.unlink(FAILURE_STATE_FILE)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# State rebuild (recovery)
#
# The database itself records every import via the GranolaID metadata field,
# so a fresh or restored machine can re-derive the local state file instead
# of re-importing history. Runs automatically when the state file is absent
# but DEVONthink already holds Granola records, and on demand via
# --rebuild-state.
# ---------------------------------------------------------------------------

REBUILD_APPLESCRIPT = """
on run
    tell application id "DNtp"
        try
            set targetDB to database "%%DATABASE%%"
        on error
            return "error: database not found"
        end try
        set out to ""
        set hits to search "mddocumenttype:~Meeting" in root of targetDB
        repeat with hit in hits
            try
                set gid to (get custom meta data for "GranolaID" from hit) as string
                if gid is not "" then set out to out & gid & linefeed
            end try
        end repeat
        return out
    end tell
end run
"""


def rebuild_ids_from_devonthink():
    """Return the set of GranolaIDs present in the database, or None when
    DEVONthink could not be queried (treat as unknown, not empty)."""
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
    return {line.strip() for line in output.splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# Deferral
# ---------------------------------------------------------------------------


def _age_minutes(created_at):
    if not created_at:
        return None
    s = created_at
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        created_dt = datetime.fromisoformat(s)
        return (datetime.now(created_dt.tzinfo) - created_dt).total_seconds() / 60
    except Exception:
        return None


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
            "granola-import",
            "1800",
        ],
        check=False,
    )

    # Skip launchd-driven runs on battery. User-invoked runs (--force,
    # --dry-run, --rebuild-state) bypass the gate so explicit intent always
    # wins.
    user_invoked = FORCE_ID is not None or DRY_RUN or REBUILD_STATE
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

    check_version_change()

    if REBUILD_STATE:
        rebuilt = rebuild_ids_from_devonthink()
        if rebuilt is None:
            sys.exit(1)
        existing = load_imported_ids() if os.path.exists(STATE_FILE) else set()
        merged = existing | rebuilt
        log(f"State rebuild: {len(rebuilt)} ID(s) in DEVONthink, "
            f"{len(existing)} in state file, {len(merged)} after merge")
        if DRY_RUN:
            log("[DRY RUN] state file not written")
        else:
            save_imported_ids(merged)
        return

    state_file_existed = os.path.exists(STATE_FILE)
    imported_ids = load_imported_ids()
    if not state_file_existed and not imported_ids:
        # Fresh or restored machine: the database remembers what was already
        # imported even when the local state file is gone.
        rebuilt = rebuild_ids_from_devonthink()
        if rebuilt:
            log(f"State file missing but DEVONthink holds {len(rebuilt)} "
                f"Granola record(s); rebuilding state from the database")
            imported_ids = rebuilt
            if not DRY_RUN:
                save_imported_ids(imported_ids)

    parsed = run_parser(imported_ids, FORCE_ID)
    meetings = parsed.get("meetings", [])

    # Write the AppleScript once, reuse for all meetings
    script_content = IMPORT_APPLESCRIPT.replace(
        "%%DATABASE%%", DATABASE_NAME
    ).replace("%%INBOX%%", INBOX_GROUP)
    fd, script_path = tempfile.mkstemp(suffix=".applescript")
    with os.fdopen(fd, "w") as f:
        f.write(script_content)

    try:
        for m in meetings:
            doc_id = m["id"]

            if not m["has_notes"]:
                # Defer no-content meetings for the retry window — Granola
                # generates enhanced panel notes after a meeting ends, and a
                # panel can arrive late or unreadable. Note: --force does not
                # override this; an empty meeting can't be made non-empty by
                # re-running.
                age = _age_minutes(m.get("created_at"))
                if age is not None and age < NO_NOTES_GIVE_UP_MINUTES:
                    log(
                        f"Deferring: {m['title']} "
                        f"(created {int(age)}m ago, no notes yet)"
                    )
                    continue
                if DRY_RUN:
                    log(f"[DRY RUN] Would give up on: {m['title']} "
                        f"(no notes, source={m['source']})")
                    continue
                if m.get("malformed_panels"):
                    log(f"WARNING: giving up on {m['title']}: "
                        f"{m['malformed_panels']} unreadable panel(s), likely "
                        f"Granola schema drift — see {NOTES_FILE}")
                else:
                    log(f"Skipping: {m['title']} (no notes after "
                        f"{NO_NOTES_GIVE_UP_MINUTES // 1440} days)")
                imported_ids.add(doc_id)
                save_imported_ids(imported_ids)
                continue

            if DRY_RUN:
                log(
                    f"[DRY RUN] Would import: {m['title']} "
                    f"({m['event_date']}) [source={m['source']}]"
                )
                continue

            log(
                f"Importing: {m['title']} ({m['event_date']}) "
                f"[source={m['source']}]"
            )
            if m.get("malformed_panels"):
                log(f"  note: {m['malformed_panels']} unreadable panel(s) "
                    f"skipped")
            success, msg = import_to_devonthink(m, script_path)
            if success:
                imported_ids.add(doc_id)
                save_imported_ids(imported_ids)
            else:
                log(f"  FAILED: {msg}")
    finally:
        os.unlink(script_path)

    clear_failure_state()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"FATAL: {type(exc).__name__}: {exc}")
        log(traceback.format_exc())
        try:
            report_failure_to_devonthink(exc)
        except Exception as report_exc:
            log(f"Failure report itself failed: {report_exc}")
        sys.exit(1)
