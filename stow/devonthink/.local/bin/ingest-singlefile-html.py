#!/usr/bin/env python3
"""Ingest a SingleFile HTML capture into DEVONthink.

Creates (up to) three cross-linked records in a single AppleScript pass:
  - Bookmark (lightweight link, 99_ARCHIVE) — reused via --bookmark UUID, else created
  - HTML snapshot (99_ARCHIVE)
  - Markdown extract (00_INBOX, enters AI enrichment pipeline) — only if defuddle succeeds

Replaces the Capture: SingleFile Batch + Process: SingleFile Import smart-rule
chain. DT never sees the staging file until this script hands it over as
finished records, so there's no race with Sweep, Every-Minute ticks, or DT's
own inbox auto-import.

Usage:
    ingest-singlefile-html.py <html-path> [--bookmark <UUID>]

--bookmark is used by the batch capture path (Scenario 2 in the README) where
the triggering bookmark already exists in DT; omit it for the desktop save
path (Scenario 1) and the ingester will create a bookmark in 99_ARCHIVE.

On success the staging HTML file is deleted.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log  # noqa: E402

CLEAN_WEB_TITLE = Path.home() / ".local" / "bin" / "clean-web-title"
COMPRESS_IMAGES = Path.home() / ".local" / "bin" / "compress-singlefile-images.py"
LINT_MARKDOWN_FILE = Path.home() / ".local" / "bin" / "lint-markdown-file"
DEFUDDLE_SHIM = Path.home() / ".local" / "share" / "mise" / "shims" / "defuddle"

log = setup_log("singlefile-ingest")

APPLESCRIPT = r"""
on run argv
    set htmlPath to item 1 of argv
    set mdPath to item 2 of argv
    set bookmarkUUID to item 3 of argv
    set sourceURL to item 4 of argv
    set safeTitle to item 5 of argv

    tell application id "DNtp"
        set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"
        set inboxGroup to get record at "/00_INBOX" in database "Lorebook"

        -- Resolve or create the bookmark.
        set isNewBookmark to (bookmarkUUID is "")
        if isNewBookmark then
            set bmRecord to create record with {name:safeTitle, type:bookmark, URL:sourceURL} in archiveGroup
            add custom meta data 1 for "NameLocked" to bmRecord
            add custom meta data 1 for "Recognized" to bmRecord
            add custom meta data 1 for "Commented" to bmRecord
            add custom meta data 1 for "AIEnriched" to bmRecord
        else
            set bmRecord to get record with uuid bookmarkUUID
            add custom meta data 0 for "NeedsSingleFile" to bmRecord
        end if

        -- Import the HTML snapshot directly into 99_ARCHIVE.
        set htmlRecord to import htmlPath to archiveGroup
        set URL of htmlRecord to sourceURL
        set name of htmlRecord to safeTitle
        add custom meta data 1 for "NameLocked" to htmlRecord
        add custom meta data 1 for "Recognized" to htmlRecord
        add custom meta data 1 for "Commented" to htmlRecord

        set bmLink to "x-devonthink-item://" & (uuid of bmRecord)
        set htmlLink to "x-devonthink-item://" & (uuid of htmlRecord)
        add custom meta data bmLink for "WebClipSource" to htmlRecord
        add custom meta data htmlLink for "WebClipSnapshot" to bmRecord

        if mdPath is "" then
            -- No markdown — let Enrich: AI Metadata process the HTML directly.
            -- Leave Recognized/Commented=1, AIEnriched empty, NeedsProcessing=1.
            add custom meta data 1 for "NeedsProcessing" to htmlRecord
            set resultUUIDs to (uuid of bmRecord) & "|" & (uuid of htmlRecord) & "|"
        else
            -- Markdown carries enrichment — fully fast-track the HTML.
            add custom meta data 1 for "AIEnriched" to htmlRecord

            -- Pre-lint + pre-flag the markdown so Extract: Native Text
            -- Bypass doesn't match it and fire a mutation storm on the
            -- record while DT's UI is rendering the fresh arrival.
            -- Recognized=1, Commented=1 here stand in for the flags that
            -- rule would have set; the mdPath file was already lint-fixed
            -- on disk before we imported it.
            set mdRecord to import mdPath to inboxGroup
            set URL of mdRecord to sourceURL
            add custom meta data 1 for "NeedsProcessing" to mdRecord
            add custom meta data 1 for "NameLocked" to mdRecord
            add custom meta data 1 for "Recognized" to mdRecord
            add custom meta data 1 for "Commented" to mdRecord

            set mdLink to "x-devonthink-item://" & (uuid of mdRecord)
            add custom meta data bmLink for "WebClipSource" to mdRecord
            add custom meta data mdLink for "WebClipMarkdown" to bmRecord
            add custom meta data mdLink for "WebClipMarkdown" to htmlRecord

            set resultUUIDs to (uuid of bmRecord) & "|" & (uuid of htmlRecord) & "|" & (uuid of mdRecord)
        end if

        -- Scenario 1 only: log the new bookmark to today's daily note.
        -- Scenario 2 bookmarks were already logged by Post-Enrich & Archive
        -- when they first arrived via Extract: Web Content, so re-logging
        -- here would duplicate the entry.
        if isNewBookmark then
            try
                set cDate to current date
                set cYear to year of cDate as text
                set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
                set cDay to text -2 thru -1 of ("0" & (day of cDate))
                set todayFilename to cYear & "-" & cMonth & "-" & cDay & ".md"
                set targetNote to get record at ("/10_DAILY/" & todayFilename) in database "Lorebook"

                if targetNote is not missing value then
                    set bmUUID to uuid of bmRecord
                    set noteText to plain text of targetNote
                    if noteText does not contain bmUUID then
                        set secSinceMidnight to time of cDate
                        set cHour to secSinceMidnight div 3600
                        set cMin to (secSinceMidnight mod 3600) div 60
                        if cHour ≥ 12 then
                            set ampm to "pm"
                            if cHour > 12 then set cHour to cHour - 12
                        else
                            set ampm to "am"
                            if cHour is 0 then set cHour to 12
                        end if
                        set timeStr to (cHour as text) & ":" & text -2 thru -1 of ("0" & (cMin as text)) & ampm

                        set linkText to "- " & timeStr & ": [🔗 " & safeTitle & "](x-devonthink-item://" & bmUUID & ")"

                        set tmpPath to do shell script "mktemp /tmp/sf-ingest-daily.XXXXXX"
                        set fileRef to open for access (POSIX file tmpPath) with write permission
                        set eof of fileRef to 0
                        write noteText to fileRef as «class utf8»
                        close access fileRef

                        set newText to do shell script ¬
                            "/usr/bin/python3 $HOME/.local/bin/insert-daily-note-section.py" & ¬
                            " --header " & quoted form of "## Today's Notes" & ¬
                            " --content " & quoted form of (linkText & linefeed) & ¬
                            " < " & quoted form of tmpPath without altering line endings
                        do shell script "rm -f " & quoted form of tmpPath

                        set plain text of targetNote to newText
                    end if
                    add custom meta data 1 for "DailyNoteLinked" to bmRecord
                end if
            on error errMsg
                -- Daily note logging is non-fatal; records are already
                -- created and cross-linked. Log and move on.
                log message "ingest-singlefile-html: daily note append failed: " & errMsg
            end try
        end if

        return resultUUIDs
    end tell
end run
"""


def parse_source_url(html_path: Path) -> str | None:
    with open(html_path, "rb") as f:
        head = f.read(4096).decode("utf-8", errors="replace")
    if "Page saved with SingleFile" not in head:
        return None
    m = re.search(r"url:\s+(https?://\S+)", head)
    return m.group(1) if m else None


def derive_title(html_path: Path) -> str:
    """Start from the filename, strip SingleFile's date-time suffix, normalize."""
    stem = html_path.stem
    stem = re.sub(r" \([0-9].*$", "", stem)
    cleaned = subprocess.run(
        [str(CLEAN_WEB_TITLE)], input=stem, capture_output=True, text=True, check=False
    ).stdout.strip()
    title = cleaned or stem
    title = re.sub(r"[\\/:]", "-", title)
    title = re.sub(r"-{2,}", "-", title)[:120]
    return title or "Web Clip"


def run_defuddle(html_path: Path, out_md_path: Path) -> bool:
    """Return True if defuddle produced markdown with ≥20 content words."""
    try:
        subprocess.run(
            [
                str(DEFUDDLE_SHIM),
                "parse",
                str(html_path),
                "--markdown",
                "--output",
                str(out_md_path),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        log.warning(
            "defuddle failed: %s", e.stderr.decode("utf-8", errors="replace")
        )
        return False
    except FileNotFoundError:
        log.warning("defuddle shim not found at %s", DEFUDDLE_SHIM)
        return False

    if not out_md_path.exists():
        return False
    text = out_md_path.read_text(errors="replace")
    stripped = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", "", stripped)
    stripped = re.sub(r"https?://\S+", "", stripped)
    return len(stripped.split()) >= 20


def lint_markdown(path: Path) -> None:
    """In-place lint of a markdown file via the shared helper. Non-fatal."""
    try:
        subprocess.run(
            [str(LINT_MARKDOWN_FILE), str(path)],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.warning("lint-markdown-file failed: %s", e)


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


def find_existing_capture(url: str, exclude_uuid: str = "") -> str:
    """Return the UUID of an existing bookmark with this URL that already
    has WebClipSnapshot set, or empty string if none. Strict URL match."""
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
    try:
        subprocess.run(
            ["osascript", "-", bookmark_uuid],
            input=CLEAR_NEEDS_SINGLEFILE_APPLESCRIPT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        log.warning("clear NeedsSingleFile failed for %s: %s", bookmark_uuid, e)


def compress_images(html_path: Path) -> None:
    try:
        subprocess.run(
            ["python3", str(COMPRESS_IMAGES), str(html_path)],
            check=False,
            capture_output=True,
        )
    except Exception as e:
        log.warning("image compression failed: %s", e)


def import_to_devonthink(
    html_path: Path,
    md_path: Path | None,
    bookmark_uuid: str,
    source_url: str,
    safe_title: str,
) -> str:
    """Run the single-pass AppleScript. Returns the pipe-joined UUIDs."""
    argv = [
        str(html_path),
        str(md_path) if md_path else "",
        bookmark_uuid,
        source_url,
        safe_title,
    ]
    result = subprocess.run(
        ["osascript", "-", *argv],
        input=APPLESCRIPT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_path", type=Path)
    parser.add_argument(
        "--bookmark", type=str, default="", help="UUID of existing bookmark to reuse"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="ingest even if the URL is already captured by another bookmark",
    )
    args = parser.parse_args()

    log.info(
        "ingest start: %s (bookmark=%s)", args.html_path, args.bookmark or "new"
    )

    html_path = args.html_path.resolve()
    if not html_path.exists():
        log.error("HTML not found: %s", html_path)
        return 1

    source_url = parse_source_url(html_path)
    if not source_url:
        log.warning("Not a SingleFile HTML, leaving in place: %s", html_path)
        return 0

    # Dedup: if another bookmark with this URL already has a WebClipSnapshot
    # set, skip creating a new triad. Strict URL match (no normalization) —
    # errs toward capturing when in doubt. Pass --force to override.
    if not args.force:
        existing = find_existing_capture(source_url, exclude_uuid=args.bookmark)
        if existing:
            log.info(
                "skipping: URL already captured by bookmark %s (pass --force to re-capture)",
                existing,
            )
            if args.bookmark:
                clear_needs_singlefile(args.bookmark)
            try:
                html_path.unlink()
            except OSError:
                pass
            return 0

    safe_title = derive_title(html_path)

    compress_images(html_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        md_path = Path(tmpdir) / f"{safe_title}.md"
        has_md = run_defuddle(html_path, md_path)
        if has_md:
            lint_markdown(md_path)

        try:
            uuids = import_to_devonthink(
                html_path=html_path,
                md_path=md_path if has_md else None,
                bookmark_uuid=args.bookmark,
                source_url=source_url,
                safe_title=safe_title,
            )
        except subprocess.CalledProcessError as e:
            log.error("AppleScript import failed: %s", e.stderr)
            return 1

    log.info("ingest complete: %s (%s)", html_path.name, uuids)

    try:
        html_path.unlink()
    except OSError as e:
        log.warning("could not delete staging file %s: %s", html_path, e)

    return 0


if __name__ == "__main__":
    sys.exit(main())
