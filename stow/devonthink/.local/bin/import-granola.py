#!/usr/bin/env python3
"""
import-granola.py — Import Granola meeting notes into DEVONthink.

Fetches enhanced AI-generated notes from Granola's API, reads meeting
metadata and transcripts from the local cache, builds markdown documents,
and imports them into DEVONthink's 00_INBOX with pre-set metadata.
Documents then flow through the standard pipeline (AI enrichment → action
items → daily notes → archive).

Idempotent: tracks imported meeting IDs in a local state file.

Usage:
    python3 import-granola.py             # import new meetings
    python3 import-granola.py --dry-run   # preview without importing
    python3 import-granola.py --force ID  # re-import a specific meeting ID
"""

import gzip
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from glob import glob
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_NAME = "Lorebook"
INBOX_GROUP = "/00_INBOX"
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "granola-imported.json")
OLD_STATE_FILE = os.path.expanduser("~/.granola-dt-imported.json")
LOG_FILE = os.path.expanduser("~/Library/Logs/granola-import.log")
MAX_TRANSCRIPT_WORDS = 4000

DRY_RUN = "--dry-run" in sys.argv
FORCE_ID = None
if "--force" in sys.argv:
    idx = sys.argv.index("--force")
    if idx + 1 < len(sys.argv):
        FORCE_ID = sys.argv[idx + 1]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} [granola-import] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Granola cache
# ---------------------------------------------------------------------------


def find_granola_cache():
    pattern = os.path.expanduser("~/Library/Application Support/Granola/cache-v*.json")
    candidates = [f for f in glob(pattern) if not f.endswith(".tmp")]
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0]


def load_cache(cache_path):
    with open(cache_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if "cache" in raw:
        cache = raw["cache"]
        if isinstance(cache, str):
            cache = json.loads(cache)
        return cache.get("state", cache)
    return raw


# ---------------------------------------------------------------------------
# Granola API — fetch enhanced notes (stored server-side, not in local cache)
# ---------------------------------------------------------------------------

GRANOLA_API = "https://api.granola.ai/v1"
SUPABASE_FILE = os.path.expanduser(
    "~/Library/Application Support/Granola/supabase.json"
)


def _load_auth_token():
    """Read the WorkOS access token from Granola's local auth store."""
    try:
        with open(SUPABASE_FILE, "r") as f:
            data = json.load(f)
        tokens = json.loads(data["workos_tokens"])
        return tokens["access_token"]
    except Exception as e:
        log(f"Warning: could not read Granola auth token: {e}")
        return None


def _api_post(endpoint, payload, token):
    """POST to a Granola API endpoint, return parsed JSON or None."""
    req = urllib.request.Request(
        f"{GRANOLA_API}/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            try:
                body = gzip.decompress(raw)
            except Exception:
                body = raw
            return json.loads(body)
    except urllib.error.HTTPError as e:
        log(f"API error ({endpoint}): HTTP {e.code}")
        return None
    except Exception as e:
        log(f"API error ({endpoint}): {e}")
        return None


def fetch_enhanced_notes(document_id, token):
    """Fetch AI-generated panel notes for a meeting from the Granola API.

    Returns a list of {title, content (ProseMirror doc)} dicts, or [].
    """
    if not token:
        return []
    panels = _api_post("get-document-panels", {"document_id": document_id}, token)
    if not panels or not isinstance(panels, list):
        return []
    return panels


# ---------------------------------------------------------------------------
# Token refresh — launch Granola if auth token has expired
# ---------------------------------------------------------------------------


def _token_expired(token):
    """Check if a JWT access token has expired."""
    try:
        import base64

        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.now().timestamp() >= claims.get("exp", 0)
    except Exception:
        return True


def ensure_valid_token():
    """Load the auth token, launching Granola if it has expired.

    Returns the token string or None if it can't be obtained.
    """
    token = _load_auth_token()
    if token and not _token_expired(token):
        return token

    log("Auth token expired or missing — launching Granola to refresh")
    try:
        subprocess.run(["open", "-a", "Granola"], check=True)
        # Give the app time to start and refresh the token
        import time

        for _ in range(10):
            time.sleep(3)
            token = _load_auth_token()
            if token and not _token_expired(token):
                log("Token refreshed successfully")
                return token
        log("Warning: Granola launched but token still expired after 30s")
    except Exception as e:
        log(f"Warning: could not launch Granola: {e}")
    return _load_auth_token()


# ---------------------------------------------------------------------------
# ProseMirror → Markdown conversion
# ---------------------------------------------------------------------------


def prosemirror_to_markdown(doc, heading_offset=2):
    """Convert a ProseMirror document node to markdown.

    heading_offset shifts heading levels so panel h3 becomes markdown ##
    (offset=2 → level 3 maps to 3 - 2 + 1 = h2).
    """
    lines = []
    _convert_nodes(doc.get("content", []), lines, heading_offset, depth=0)
    return "\n".join(lines).strip() + "\n"


def _convert_nodes(nodes, lines, heading_offset, depth):
    for node in nodes:
        ntype = node.get("type", "")

        if ntype == "heading":
            level = node.get("attrs", {}).get("level", 3)
            md_level = max(1, level - heading_offset + 1)
            text = _inline_text(node.get("content", []))
            lines.append("")
            lines.append(f"{'#' * md_level} {text}")
            lines.append("")

        elif ntype == "paragraph":
            text = _inline_text(node.get("content", []))
            indent = "  " * depth if depth > 0 else ""
            lines.append(f"{indent}{text}")

        elif ntype == "bulletList":
            for item in node.get("content", []):
                _convert_list_item(item, lines, heading_offset, depth, ordered=False)

        elif ntype == "orderedList":
            for i, item in enumerate(node.get("content", []), 1):
                _convert_list_item(
                    item, lines, heading_offset, depth, ordered=True, index=i
                )

        elif ntype == "text":
            lines.append(_apply_marks(node))

        else:
            # Recurse into unknown nodes
            _convert_nodes(node.get("content", []), lines, heading_offset, depth)


def _convert_list_item(item, lines, heading_offset, depth, ordered=False, index=1):
    children = item.get("content", [])
    prefix = f"{index}. " if ordered else "- "
    indent = "  " * depth

    for i, child in enumerate(children):
        if child.get("type") == "paragraph":
            text = _inline_text(child.get("content", []))
            if i == 0:
                lines.append(f"{indent}{prefix}{text}")
            else:
                lines.append(f"{indent}  {text}")
        elif child.get("type") in ("bulletList", "orderedList"):
            _convert_nodes([child], lines, heading_offset, depth + 1)
        else:
            _convert_nodes([child], lines, heading_offset, depth)


def _inline_text(nodes):
    return "".join(_apply_marks(n) for n in nodes if n.get("type") == "text")


def _apply_marks(node):
    text = node.get("text", "")
    for mark in node.get("marks", []):
        mtype = mark.get("type", "")
        if mtype == "bold":
            text = f"**{text}**"
        elif mtype == "italic":
            text = f"*{text}*"
        elif mtype == "code":
            text = f"`{text}`"
        elif mtype == "link":
            href = mark.get("attrs", {}).get("href", "")
            text = f"[{text}]({href})"
    return text


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------


def _migrate_state_file():
    """Move state file from old ~ location to ~/.local/state/devonthink/."""
    if os.path.exists(OLD_STATE_FILE) and not os.path.exists(STATE_FILE):
        os.makedirs(STATE_DIR, exist_ok=True)
        os.rename(OLD_STATE_FILE, STATE_FILE)
        log(f"Migrated state file to {STATE_FILE}")


def load_imported_ids():
    _migrate_state_file()
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
    except Exception:
        pass
    return set()


def save_imported_ids(ids):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(ids), f, indent=2)


# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------


def detect_timezone():
    try:
        import time
        import zoneinfo

        tz_map = {
            "EST": "America/New_York",
            "EDT": "America/New_York",
            "CST": "America/Chicago",
            "CDT": "America/Chicago",
            "MST": "America/Denver",
            "MDT": "America/Denver",
            "PST": "America/Los_Angeles",
            "PDT": "America/Los_Angeles",
        }
        current = time.tzname[time.daylight]
        if current in tz_map:
            return zoneinfo.ZoneInfo(tz_map[current])
    except Exception:
        pass
    try:
        import zoneinfo

        return zoneinfo.ZoneInfo("UTC")
    except Exception:
        from datetime import timezone

        return timezone.utc


# ---------------------------------------------------------------------------
# Meeting parsing
# ---------------------------------------------------------------------------


def parse_meeting(doc_id, doc, local_tz):
    import zoneinfo

    title = doc.get("title") or ""
    gce = doc.get("google_calendar_event") or {}
    if not title and gce:
        title = gce.get("summary", "")
    if not title:
        title = "Untitled Meeting"

    # Event date from calendar event
    event_date = ""
    event_datetime = ""
    for source in [gce.get("start", {}), {"dateTime": doc.get("created_at", "")}]:
        if event_date:
            break
        dt_str = source.get("dateTime") or source.get("date", "")
        if not dt_str:
            continue
        try:
            if dt_str.endswith("Z"):
                dt_str = dt_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            local_dt = dt.astimezone(local_tz)
            event_date = local_dt.strftime("%Y-%m-%d")
            event_datetime = local_dt.strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            pass

    # Participants
    participants = []
    people = doc.get("people") or {}
    if isinstance(people, dict):
        for a in people.get("attendees", []):
            if isinstance(a, dict) and a.get("name"):
                participants.append(a["name"])
    elif isinstance(people, list):
        for p in people:
            if isinstance(p, dict) and p.get("name"):
                participants.append(p["name"])

    # Fallback from calendar event
    if not participants and gce:
        for a in gce.get("attendees", []):
            if isinstance(a, dict):
                email = a.get("email", "")
                if email and not a.get("self"):
                    name = email.split("@")[0].replace(".", " ").title()
                    participants.append(name)

    return {
        "id": doc_id,
        "title": title,
        "event_date": event_date,
        "event_datetime": event_datetime,
        "participants": participants,
    }


# ---------------------------------------------------------------------------
# Transcript formatting
# ---------------------------------------------------------------------------


def format_transcript(segments, max_words=MAX_TRANSCRIPT_WORDS):
    """Merge consecutive same-speaker segments, truncate to max_words."""
    if not segments:
        return ""

    merged = []
    current_speaker = None
    current_texts = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("source", "Unknown")

        if speaker == current_speaker:
            current_texts.append(text)
        else:
            if current_speaker and current_texts:
                merged.append((current_speaker, " ".join(current_texts)))
            current_speaker = speaker
            current_texts = [text]

    if current_speaker and current_texts:
        merged.append((current_speaker, " ".join(current_texts)))

    # Build markdown
    lines = []
    word_count = 0
    for speaker, text in merged:
        words = text.split()
        if word_count + len(words) > max_words:
            remaining = max_words - word_count
            if remaining > 0:
                lines.append(f"**{speaker}:** {' '.join(words[:remaining])}...")
            lines.append("\n*[Transcript truncated]*")
            break
        lines.append(f"**{speaker}:** {text}")
        word_count += len(words)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown building
# ---------------------------------------------------------------------------


def build_markdown(meeting, notes_markdown, transcript_text):
    md = f"# {meeting['title']}\n\n"

    if meeting["event_datetime"]:
        md += f"**Date:** {meeting['event_datetime']}\n"
    if meeting["participants"]:
        md += f"**Participants:** {', '.join(meeting['participants'])}\n"

    md += "\n---\n\n"

    if notes_markdown:
        md += notes_markdown
        if not notes_markdown.endswith("\n"):
            md += "\n"
    elif transcript_text:
        md += "## Transcript\n\n"
        md += transcript_text + "\n"

    return md


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


def import_to_devonthink(meeting, markdown_content, script_path):
    # Write markdown to temp file
    fd, content_path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        # Pre-lint the markdown on disk so the imported record arrives in
        # house style and we can pre-flag it with Recognized=1/Commented=1
        # to keep Extract: Native Text Bypass from matching. Non-fatal.
        lint_helper = os.path.expanduser("~/.local/bin/lint-markdown-file")
        if os.path.exists(lint_helper):
            subprocess.run(
                [lint_helper, content_path], capture_output=True, check=False
            )

        # Build title with date prefix (matching pipeline convention)
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
                ", ".join(meeting["participants"]),
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
# Main
# ---------------------------------------------------------------------------


def main():
    cache_path = find_granola_cache()
    if not cache_path:
        log("No Granola cache found")
        sys.exit(1)

    log(f"Loading cache: {cache_path}")
    cache = load_cache(cache_path)

    documents = cache.get("documents", {})
    transcripts = cache.get("transcripts", {})
    if not documents:
        log("No documents in cache")
        return

    local_tz = detect_timezone()
    imported_ids = load_imported_ids()
    auth_token = ensure_valid_token()
    log(f"Found {len(documents)} meetings, {len(imported_ids)} already imported")
    if not auth_token:
        log("Warning: no auth token — will fall back to cache notes_markdown only")

    # Write the AppleScript once, reuse for all meetings
    script_content = IMPORT_APPLESCRIPT.replace("%%DATABASE%%", DATABASE_NAME).replace(
        "%%INBOX%%", INBOX_GROUP
    )
    fd, script_path = tempfile.mkstemp(suffix=".applescript")
    with os.fdopen(fd, "w") as f:
        f.write(script_content)

    try:
        new_count = 0
        skip_count = 0

        for doc_id, doc in documents.items():
            # Skip already-imported unless --force
            if doc_id in imported_ids and doc_id != FORCE_ID:
                skip_count += 1
                continue

            # Skip deleted meetings
            if doc.get("deleted_at"):
                imported_ids.add(doc_id)
                continue

            # Try fetching enhanced notes from the API first
            notes_md = ""
            panels = fetch_enhanced_notes(doc_id, auth_token)
            if panels:
                panel_sections = []
                for panel in panels:
                    content = panel.get("content", {})
                    md = prosemirror_to_markdown(content)
                    if md.strip():
                        panel_sections.append(md)
                if panel_sections:
                    notes_md = "\n".join(panel_sections)

            # Fall back to cache notes_markdown
            if not notes_md:
                notes_md = doc.get("notes_markdown") or ""

            transcript_segments = transcripts.get(doc_id, [])

            has_notes = len(notes_md) > 50
            has_transcript = (
                isinstance(transcript_segments, list) and len(transcript_segments) > 10
            )

            if not has_notes and not has_transcript:
                log(f"Skipping: {doc.get('title', '?')} (no notes or transcript)")
                imported_ids.add(doc_id)
                continue

            # Defer recent meetings that only have a transcript — Granola
            # needs time after a meeting ends to generate enhanced notes.
            if not has_notes and has_transcript:
                created_str = doc.get("created_at", "")
                if created_str:
                    try:
                        if created_str.endswith("Z"):
                            created_str = created_str[:-1] + "+00:00"
                        created_dt = datetime.fromisoformat(created_str)
                        age_minutes = (
                            datetime.now(created_dt.tzinfo) - created_dt
                        ).total_seconds() / 60
                        if age_minutes < 60:
                            log(
                                f"Deferring: {doc.get('title', '?')} (created {int(age_minutes)}m ago, no enhanced notes yet)"
                            )
                            continue
                    except Exception:
                        pass

            meeting = parse_meeting(doc_id, doc, local_tz)

            # Build transcript text if available
            transcript_text = ""
            if has_transcript:
                transcript_text = format_transcript(transcript_segments)

            markdown = build_markdown(
                meeting,
                notes_md if has_notes else "",
                transcript_text,
            )

            content_source = (
                "api-panels"
                if panels
                else ("cache-notes" if has_notes else "transcript-only")
            )

            if DRY_RUN:
                log(
                    f"[DRY RUN] Would import: {meeting['title']} "
                    f"({meeting['event_date']}) "
                    f"[source={content_source}, "
                    f"transcript={'yes' if has_transcript else 'no'}]"
                )
                new_count += 1
                continue

            log(
                f"Importing: {meeting['title']} ({meeting['event_date']}) [source={content_source}]"
            )
            success, msg = import_to_devonthink(meeting, markdown, script_path)

            if success:
                imported_ids.add(doc_id)
                save_imported_ids(imported_ids)
                log(f"  {msg}")
                new_count += 1
            else:
                log(f"  FAILED: {msg}")

        log(f"Done: {new_count} imported, {skip_count} already imported")

    finally:
        os.unlink(script_path)


if __name__ == "__main__":
    main()
