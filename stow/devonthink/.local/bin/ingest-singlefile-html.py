#!/usr/bin/python3
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

# Pinned to /usr/bin/python3 (3.9) for TCC stability — see CLAUDE.md.
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log  # noqa: E402

CLEAN_WEB_TITLE = Path.home() / ".local" / "bin" / "clean-web-title"
COMPRESS_IMAGES = Path.home() / ".local" / "bin" / "compress-singlefile-images.py"
LINT_MARKDOWN_FILE = Path.home() / ".local" / "bin" / "lint-markdown-file"
DEFUDDLE_SHIM = Path.home() / ".local" / "share" / "mise" / "shims" / "defuddle"

# Guardrails for pathological captures (media-heavy pages, runaway image galleries).
# Post-compression size over this threshold → skip ingest, flag the bookmark as
# SingleFileTooLarge so the user can review and re-capture manually if desired.
MAX_INGEST_BYTES = 25 * 1024 * 1024
COMPRESS_TIMEOUT_SECS = 120
DEFUDDLE_TIMEOUT_SECS = 60
TRANSFORM_TIMEOUT_SECS = 240

# Hosts whose SingleFile captures are LLM chat transcripts. The defuddle output
# for these is a raw turn-by-turn transcript; transform_to_report() rewrites it
# into a topic-organized writeup before import.
AI_CHAT_HOSTS = {
    "claude.ai": "Claude",
    "gemini.google.com": "Gemini",
    "chatgpt.com": "ChatGPT",
}

# Filename stems that SingleFile (or our cleaner) can leave when a page has no
# usable <title>. Treated as placeholders rather than real titles: the markdown
# record skips NameLocked on import so AI enrichment can supply a real title,
# and Post-Enrich & Archive then propagates that name back to the bookmark and
# HTML snapshot.
GENERIC_TITLE_STEMS = {"no title", "untitled"}

# Reddit serves a generic <title> on post pages ("From the X community on
# Reddit") rather than the actual post title, so the SingleFile filename and
# any DT bookmark created from the page are useless as descriptors. We hit
# Reddit's public .json endpoint to recover the verbatim user-authored title;
# on failure, we treat the boilerplate as generic and let AI enrichment fill
# it in from the body text.
REDDIT_HOSTS = {
    "reddit.com",
    "www.reddit.com",
    "old.reddit.com",
    "new.reddit.com",
    "np.reddit.com",
}
REDDIT_POST_PATH_RE = re.compile(
    r"^/r/[^/]+/comments/[a-z0-9]+(?:/[^/]*)?/?$", re.IGNORECASE
)
REDDIT_BOILERPLATE_RE = re.compile(
    r"^(From the .+ community on Reddit|Reddit|Reddit - .+)$", re.IGNORECASE
)
REDDIT_FETCH_TIMEOUT_SECS = 8

# Title length cap. Hit at the word boundary nearest (but ≤) this many chars,
# with an ellipsis appended when truncation occurred. Reddit post titles in
# particular blow well past this; older byte-truncation produced mid-word cuts.
TITLE_MAX_LEN = 120

log = setup_log("singlefile-ingest")

APPLESCRIPT = r"""
on run argv
    set htmlPath to item 1 of argv
    set mdPath to item 2 of argv
    set bookmarkUUID to item 3 of argv
    set sourceURL to item 4 of argv
    set safeTitle to item 5 of argv
    set aiChatPlatform to item 6 of argv
    set isGenericTitle to ((item 7 of argv) is "1")
    set overrideExistingName to ((item 8 of argv) is "1")

    tell application id "DNtp"
        set archiveGroup to get record at "/99_ARCHIVE" in database "Lorebook"
        set inboxGroup to get record at "/00_INBOX" in database "Lorebook"

        -- Resolve or create the bookmark. When the page had no usable title
        -- (isGenericTitle), leave the bookmark unlocked so Post-Enrich &
        -- Archive can later propagate the AI-enriched name from the
        -- markdown sibling. The bookmark still gets fast-tracked
        -- (Recognized/Commented/AIEnriched=1) — only NameLocked changes.
        set isNewBookmark to (bookmarkUUID is "")
        if isNewBookmark then
            set bmRecord to create record with {name:safeTitle, type:bookmark, URL:sourceURL} in archiveGroup
            if not isGenericTitle then
                add custom meta data 1 for "NameLocked" to bmRecord
            end if
            add custom meta data 1 for "Recognized" to bmRecord
            add custom meta data 1 for "Commented" to bmRecord
            add custom meta data 1 for "AIEnriched" to bmRecord
        else
            set bmRecord to get record with uuid bookmarkUUID
            add custom meta data 0 for "NeedsSingleFile" to bmRecord
            -- Reddit carve-out: existing bookmark was created by the
            -- clipper with a "From the X community on Reddit" boilerplate
            -- name. derive_title got the verbatim post title from the
            -- .json API; force the rename and lock it so AI enrichment
            -- doesn't overwrite the user-authored title.
            if overrideExistingName then
                if (name of bmRecord) is not safeTitle then
                    set name of bmRecord to safeTitle
                end if
                add custom meta data 1 for "NameLocked" to bmRecord
            end if
        end if

        -- Import the HTML snapshot directly into 99_ARCHIVE. The Python
        -- caller pre-renamed the staging copy to <safeTitle>.html, so DT
        -- names the imported record correctly without us issuing a `set
        -- name` rename event (which would fire Util: Lock Name on Rename
        -- and force NameLocked=1 even in the untitled-page fallback path).
        -- Keep the `set name` as a defensive no-op only if DT somehow
        -- normalized the name during import.
        set htmlRecord to import htmlPath to archiveGroup
        set URL of htmlRecord to sourceURL
        if (name of htmlRecord) is not safeTitle then
            set name of htmlRecord to safeTitle
        end if
        if not isGenericTitle then
            add custom meta data 1 for "NameLocked" to htmlRecord
        end if
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
            if not isGenericTitle then
                add custom meta data 1 for "NameLocked" to mdRecord
            end if
            add custom meta data 1 for "Recognized" to mdRecord
            add custom meta data 1 for "Commented" to mdRecord

            set mdLink to "x-devonthink-item://" & (uuid of mdRecord)
            add custom meta data bmLink for "WebClipSource" to mdRecord
            add custom meta data mdLink for "WebClipMarkdown" to bmRecord
            add custom meta data mdLink for "WebClipMarkdown" to htmlRecord

            -- AI chat transcripts: flag the record and insert a provenance
            -- line pointing at the HTML snapshot. Done here (not in Python)
            -- because the snapshot UUID isn't known until htmlRecord exists.
            -- The line goes after the body's H1 if present, otherwise at the top.
            if aiChatPlatform is not "" then
                add custom meta data 1 for "AIChatTranscript" to mdRecord

                set cDate to current date
                set cYear to year of cDate as text
                set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
                set cDay to text -2 thru -1 of ("0" & (day of cDate))
                set isoDate to cYear & "-" & cMonth & "-" & cDay
                set provenance to "*Generated from a conversation with " & aiChatPlatform & " on " & isoDate & ". Original capture: [" & safeTitle & "](" & htmlLink & ").*"
                set bodyText to plain text of mdRecord

                set lfPos to offset of linefeed in bodyText
                if lfPos > 1 and (length of bodyText) ≥ 2 and (text 1 thru 2 of bodyText) is "# " then
                    set titleLine to text 1 thru (lfPos - 1) of bodyText
                    set restOfBody to text (lfPos + 1) thru -1 of bodyText
                    repeat while restOfBody starts with linefeed
                        if (length of restOfBody) ≤ 1 then
                            set restOfBody to ""
                            exit repeat
                        end if
                        set restOfBody to text 2 thru -1 of restOfBody
                    end repeat
                    set plain text of mdRecord to titleLine & linefeed & linefeed & provenance & linefeed & linefeed & restOfBody
                else
                    set plain text of mdRecord to provenance & linefeed & linefeed & bodyText
                end if
            end if

            set resultUUIDs to (uuid of bmRecord) & "|" & (uuid of htmlRecord) & "|" & (uuid of mdRecord)
        end if

        -- Scenario 1 only: log the new bookmark to today's daily note.
        -- Scenario 2 bookmarks were already logged by Post-Enrich & Archive
        -- when they first arrived via Extract: Web Content, so re-logging
        -- here would duplicate the entry. When the page had no usable
        -- <title> (isGenericTitle), defer logging — Post-Enrich & Archive
        -- writes the daily-note line after AI enrichment supplies a real
        -- title, so the entry lands with a meaningful name instead of
        -- "No title — host/path".
        if isNewBookmark and not isGenericTitle then
            try
                set cDate to current date
                set cYear to year of cDate as text
                set cMonth to text -2 thru -1 of ("0" & ((month of cDate) as integer))
                set cDay to text -2 thru -1 of ("0" & (day of cDate))
                set todayStr to cYear & "-" & cMonth & "-" & cDay
                set dailyGroup to get record at "/10_DAILY" in database "Lorebook"
                set targetNote to missing value
                if dailyGroup is not missing value then
                    set targetNote to my getOrCreateDailyNote(database "Lorebook", dailyGroup, "/10_DAILY", todayStr)
                end if

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

-- Returns the daily note for dateStr (YYYY-MM-DD), creating it in destGroup
-- if it doesn't exist yet. The 6:15 AM launchd job (create-daily-note.sh)
-- normally seeds these, but desktop captures between midnight and 06:15
-- land before the note exists; creating on demand keeps the wikilink from
-- being dropped. Mirrors create-daily-note.sh's content and "Daily Note"
-- tag so an on-demand note is indistinguishable from a seeded one.
on getOrCreateDailyNote(targetDB, destGroup, groupPath, dateStr)
    tell application id "DNtp"
        set noteFilename to dateStr & ".md"
        set existingNote to get record at (groupPath & "/" & noteFilename) in targetDB
        if existingNote is not missing value then return existingNote

        set headingDate to do shell script "date -j -f '%Y-%m-%d' " & quoted form of dateStr & " '+%A, %B %-d, %Y'"
        set noteContent to "# " & headingDate & return & return & "- " & return

        set newNote to create record with {name:dateStr, type:markdown} in destGroup
        set plain text of newNote to noteContent
        set tags of newNote to {"Daily Note"}
        return newNote
    end tell
end getOrCreateDailyNote
"""


def parse_source_url(html_path: Path) -> str | None:
    with open(html_path, "rb") as f:
        head = f.read(4096).decode("utf-8", errors="replace")
    if "Page saved with SingleFile" not in head:
        return None
    m = re.search(r"url:\s+(https?://\S+)", head)
    return m.group(1) if m else None


def truncate_at_word(s: str, limit: int = TITLE_MAX_LEN) -> str:
    """Truncate `s` to ≤ `limit` characters at a word boundary, appending an
    ellipsis when truncation actually occurred. Falls back to a hard cut when
    the string has no whitespace (or all whitespace appears too early to give
    a useful split)."""
    if len(s) <= limit:
        return s
    head = s[: limit - 1]
    last_space = head.rfind(" ")
    if last_space > limit // 2:
        cut = head[:last_space].rstrip()
    else:
        cut = head.rstrip()
    cut = cut.rstrip(",;:.!?-—")
    return f"{cut}…"


def normalize_title(s: str) -> str:
    """NFKC-normalize and collapse internal whitespace. Lighter than
    clean-web-title because it skips the brand-suffix strip — used on titles
    that come from a trusted authored source (Reddit's API) rather than a
    page <title> tag."""
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).strip()


def is_reddit_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (parsed.netloc or "").lower() in REDDIT_HOSTS


def fetch_reddit_post_title(url: str) -> str | None:
    """Return the verbatim post title for a Reddit post permalink, or None.

    Returns None for non-Reddit URLs, non-post Reddit URLs (subreddit/user
    pages), and any network/parsing failure. Callers fall back to treating
    the page's <title> as generic so AI enrichment can supply one.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host not in REDDIT_HOSTS:
        return None
    path = parsed.path or ""
    if not REDDIT_POST_PATH_RE.match(path):
        return None

    # old.reddit avoids the SPA shell that occasionally serves HTML for the
    # .json path on www.reddit. Strip the trailing slash before appending so
    # we don't end up with `/.json`.
    json_url = f"https://old.reddit.com{path.rstrip('/')}.json"
    req = urllib.request.Request(
        json_url,
        headers={"User-Agent": "dt-singlefile-ingest/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REDDIT_FETCH_TIMEOUT_SECS) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.warning("Reddit title fetch failed for %s: %s", url, e)
        return None

    try:
        title = payload[0]["data"]["children"][0]["data"]["title"]
    except (KeyError, IndexError, TypeError):
        log.warning("Reddit JSON shape unexpected for %s", url)
        return None

    if not isinstance(title, str):
        return None
    title = normalize_title(title)
    return title or None


def derive_title(html_path: Path, source_url: str) -> tuple[str, bool, bool]:
    """Return (title, is_generic, override_existing_name).

    Strips SingleFile's date-time suffix and normalizes. When the filename
    stem is one of the SingleFile placeholders (`No title`, `Untitled` —
    typically because the page had no <title> tag), is_generic=True is
    returned and the title is augmented with a URL-derived suffix so the
    three records produced by the ingester remain identifiable in DT until
    AI enrichment supplies a real title.

    Reddit carve-out: when the source URL is a Reddit post permalink, hit
    Reddit's .json API for the verbatim post title and use that. The
    `override_existing_name` return is True only in that case, signalling
    to the AppleScript that any pre-existing bookmark passed via --bookmark
    should also be renamed (its current name is the page's "From the X
    community on Reddit" boilerplate, not a real title). When the API call
    fails on a Reddit URL whose page <title> matches the boilerplate, we
    flag the title as generic so AI enrichment fills it in from the body.
    """
    reddit_title = fetch_reddit_post_title(source_url)
    if reddit_title:
        title = re.sub(r"[\\/:]", "-", reddit_title)
        title = re.sub(r"-{2,}", "-", title)
        return (truncate_at_word(title) or "Web Clip", False, True)

    stem = html_path.stem
    stem = re.sub(r" \([0-9].*$", "", stem)
    cleaned = subprocess.run(
        [str(CLEAN_WEB_TITLE)], input=stem, capture_output=True, text=True, check=False
    ).stdout.strip()
    title = cleaned or stem
    is_generic = (not title) or title.strip().lower() in GENERIC_TITLE_STEMS

    # Reddit fallback: API failed but the title is the known boilerplate.
    # Treat as generic so AI enrichment supplies a real title from the body.
    if (
        not is_generic
        and is_reddit_url(source_url)
        and REDDIT_BOILERPLATE_RE.match(title.strip())
    ):
        is_generic = True

    if is_generic:
        title = augment_generic_title(title.strip() or "No title", source_url)
    title = re.sub(r"[\\/:]", "-", title)
    title = re.sub(r"-{2,}", "-", title)
    return (truncate_at_word(title) or "Web Clip", is_generic, False)


def augment_generic_title(base: str, source_url: str) -> str:
    """Append a host/path snippet to a generic placeholder so siblings of
    the same SingleFile capture don't collide visually in DT before AI
    enrichment runs. Slashes survive here and get rewritten to dashes by
    the caller's filename sanitization."""
    try:
        parsed = urlparse(source_url)
    except ValueError:
        return base
    host = (parsed.netloc or "").removeprefix("www.")
    path = (parsed.path or "").rstrip("/")
    if not host:
        return base
    snippet = host + path
    snippet = snippet[:100]
    return f"{base} — {snippet}"


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
            timeout=DEFUDDLE_TIMEOUT_SECS,
        )
    except subprocess.CalledProcessError as e:
        log.warning("defuddle failed: %s", e.stderr.decode("utf-8", errors="replace"))
        return False
    except subprocess.TimeoutExpired:
        log.warning(
            "defuddle timed out after %ds on %s", DEFUDDLE_TIMEOUT_SECS, html_path.name
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


def strip_inline_data_images(md_path: Path) -> None:
    """Remove markdown references to inline `data:image/...` URIs.

    SingleFile embeds every image as a base64 data URI for self-contained
    HTML. defuddle faithfully carries them into the markdown as
    `![](data:image/...)` or, when an image is wrapped in an anchor,
    `[![](data:image/...)](link)`. The result is a bookmark body stuffed
    with hundreds of KB of encoded pixels, which drowns AI enrichment and
    makes the record useless to read. We drop the image references
    entirely — the HTML snapshot retains them for anyone who needs the
    visuals.
    """
    text = md_path.read_text(errors="replace")
    before = len(text)
    # Linked image first so the outer brackets don't orphan.
    text = re.sub(
        r"\[!\[[^\]]*\]\(data:image/[^)]*\)\]\([^)]*\)", "", text, flags=re.DOTALL
    )
    text = re.sub(r"!\[[^\]]*\]\(data:image/[^)]*\)", "", text, flags=re.DOTALL)
    if len(text) != before:
        md_path.write_text(text)
        log.info(
            "stripped %d bytes of inline image data URIs from %s",
            before - len(text),
            md_path.name,
        )


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


def is_ai_chat_url(url: str) -> str:
    """Return the platform label (e.g. 'Claude') if the URL hosts an AI chat
    transcript, or '' otherwise."""
    m = re.match(r"https?://([^/]+)", url, re.IGNORECASE)
    if not m:
        return ""
    host = m.group(1).lower().split(":", 1)[0]
    return AI_CHAT_HOSTS.get(host, "")


# CODE BLOCKS are called out as their own absolute rule below because an
# earlier prompt iteration that listed them as a sub-bullet of "preserve
# verbatim" produced an ingest where the LLM dropped all 4 fenced blocks
# from a 7048-word troubleshooting transcript while compressing to 677
# words. The length-budget pressure beat the preserve directive when the
# two competed, so code blocks now have an explicit override.
TRANSFORM_PROMPT = """\
TASK
Extract the assistant's content from the chat transcript below and present
it as a standalone reference document. This is reformatting, not
rewriting. Preserve the assistant's wording verbatim wherever possible.
Paraphrase only when necessary to remove conversational framing.

CODE BLOCKS (absolute rule)
Every fenced code block in the source MUST appear in the output, complete
and unmodified, in the same order it appeared in the source. Code blocks
are never dropped, summarized, paraphrased, abridged, truncated, or
replaced with a description of what they contained. This rule applies to
every fenced code block regardless of length, including blocks under 10
lines and short snippets the assistant flagged as "the relevant bit" or
similar. This rule overrides the LENGTH BUDGET below — if preserving the
code blocks pushes the output near or over the budget, that is correct
behavior.

LENGTH BUDGET
The output's word count must not exceed the assistant's word count in
the source. This is a ceiling, not a target. There is no benefit to
producing fewer words than the content requires; do not drop substantive
content (code blocks, tables, lists, specs, recommendations, caveats) to
hit a lower number. The budget exists to prevent inflation from added
transitions, framing sentences, section intros, and summary openers, all
of which are failure modes. If you find yourself writing prose that
wasn't in the source, stop.

DROP
- The user's turns entirely, except for constraints or preferences
  that meaningfully shaped the answer (a budget, a specific use case,
  a stated dislike). When such a constraint is load-bearing, fold it in
  as a brief note ("Context: budget under $200") rather than dialogue.
  When in doubt, drop.
- Greetings, sign-offs, "great question", "happy to help", model
  signatures, restatements of the user's question, "you asked about X".
- Decorative emoji.

PRESERVE VERBATIM (wording, not selection)
- The assistant's lists, tables, bullet points. If a section is already
  a clean bulleted list of recommendations or specs, copy it as-is. Do
  not paraphrase its items.
- Numbers, names, prices, dates, model identifiers, comparisons,
  caveats, qualifiers. Every fact the assistant produced (subject to
  the supersession rule below).
- Sentence-level wording. Do not rephrase prose that's already clean.
- This is about wording. Selecting which content to include and merging
  facts across turns is governed by RESTRUCTURE.

RESTRUCTURE
- Cross-turn consolidation is in scope and expected. When the same
  entity, option, or topic appears in multiple turns (the user asks
  about reliability in one turn and fuel economy in another), merge
  its facts into one section. Synthesizing facts about the same entity
  from multiple sources is the kind of work this step exists to do.
  Paraphrasing already-clean sentences is not.
- Supersession: when a later turn refined the scope (the user
  narrowed the question, the assistant's later list replaced or
  refined an earlier one), follow the refinement. Drop earlier-turn
  content that the later turns explicitly replaced. Keep the final
  refined version.
- When the assistant restated earlier content with more detail, keep
  the more complete version.
- If the conversation has multiple distinct subjects, add `##` section
  headings. If it's one continuous answer, no added section structure
  is needed.

OUTPUT FORMAT
- Markdown. Top-level `#` heading naming the topic.
- Do NOT add a "Generated from a conversation" header. That line is
  prepended separately.
- Do NOT wrap the output in ``` fences.
- Output the document only. No preamble ("Here is the rewrite:"),
  no closing remarks.

DO NOT
- Invent information the assistant did not produce.
- Fact-check, contradict, or add caveats not in the source.
- Add citations or links not in the source.

TRANSCRIPT FOLLOWS
---
"""


TRANSFORM_APPLESCRIPT = r"""
on run argv
    set inputPath to item 1 of argv
    set outputPath to item 2 of argv

    set fileRef to open for access (POSIX file inputPath)
    set transcriptText to read fileRef as «class utf8»
    close access fileRef

    set theRole to "You extract content from chat transcripts and present it as standalone reference documents. Your job is reformatting, not rewriting. You preserve the source's wording verbatim wherever possible and never let the output exceed the source in length."

    tell application id "DNtp"
        set rewritten to get chat response for message transcriptText ¬
            role theRole ¬
            mode "auto" ¬
            thinking false ¬
            tool calls false
    end tell

    if rewritten is missing value then error "empty response from get chat response"
    set rewritten to rewritten as text
    if rewritten is "" then error "empty response from get chat response"

    set outRef to open for access (POSIX file outputPath) with write permission
    set eof of outRef to 0
    write rewritten to outRef as «class utf8»
    close access outRef
end run
"""


def transform_to_report(md_path: Path, platform_label: str) -> bool:
    """Rewrite the markdown at md_path as a topic-organized writeup via DT's
    `get chat response`. Returns True if the file was replaced; False on
    failure (caller falls through to raw transcript)."""
    raw = md_path.read_text(errors="replace")
    if len(raw.split()) < 30:
        log.info("AI chat transcript too short to transform, leaving raw")
        return False

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as in_f:
        in_f.write(TRANSFORM_PROMPT + raw)
        in_path = Path(in_f.name)
    out_path = Path(tempfile.mkstemp(suffix=".md")[1])

    try:
        result = subprocess.run(
            ["osascript", "-", str(in_path), str(out_path)],
            input=TRANSFORM_APPLESCRIPT,
            capture_output=True,
            text=True,
            timeout=TRANSFORM_TIMEOUT_SECS,
        )
        if result.returncode != 0:
            log.warning(
                "AI chat transform failed (%s): %s",
                platform_label,
                result.stderr.strip() or "no stderr",
            )
            return False

        rewritten = out_path.read_text(errors="replace").strip()
        if not rewritten:
            log.warning("AI chat transform produced empty output (%s)", platform_label)
            return False

        # Defensive: strip a leading ```markdown / ``` fence if the model
        # wrapped its response despite the prompt asking it not to.
        rewritten = re.sub(r"^```(?:markdown|md)?\s*\n", "", rewritten)
        rewritten = re.sub(r"\n```\s*$", "", rewritten)
        rewritten = rewritten.strip() + "\n"

        md_path.write_text(rewritten)
        log.info(
            "AI chat transformed: %s (%s, %d -> %d words)",
            md_path.name,
            platform_label,
            len(raw.split()),
            len(rewritten.split()),
        )
        return True
    except subprocess.TimeoutExpired:
        log.warning(
            "AI chat transform timed out after %ds (%s)",
            TRANSFORM_TIMEOUT_SECS,
            platform_label,
        )
        return False
    except Exception as e:
        log.warning("AI chat transform errored (%s): %s", platform_label, e)
        return False
    finally:
        for p in (in_path, out_path):
            try:
                p.unlink()
            except OSError:
                pass


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


MARK_TOO_LARGE_APPLESCRIPT = r"""
on run argv
    set targetUUID to item 1 of argv
    tell application id "DNtp"
        try
            set r to get record with uuid targetUUID
            add custom meta data 1 for "SingleFileTooLarge" to r
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
        # /usr/bin/python3 explicitly: compress opens the staging file inside
        # TCC-protected Downloads, so it must run under the Apple-signed
        # interpreter even when this script is invoked from a mise shell.
        result = subprocess.run(
            ["/usr/bin/python3", str(COMPRESS_IMAGES), str(html_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=COMPRESS_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "image compression timed out after %ds on %s; continuing with original",
            COMPRESS_TIMEOUT_SECS,
            html_path.name,
        )
        return
    except Exception as e:
        log.warning("image compression failed: %s", e)
        return

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        log.warning(
            "image compression exited %d on %s; continuing with original HTML%s",
            result.returncode,
            html_path.name,
            f": {stderr}" if stderr else "",
        )


def mark_too_large(bookmark_uuid: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-", bookmark_uuid],
            input=MARK_TOO_LARGE_APPLESCRIPT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        log.warning("mark SingleFileTooLarge failed for %s: %s", bookmark_uuid, e)


def import_to_devonthink(
    html_path: Path,
    md_path: Path | None,
    bookmark_uuid: str,
    source_url: str,
    safe_title: str,
    ai_chat_platform: str = "",
    is_generic_title: bool = False,
    override_existing_name: bool = False,
) -> str:
    """Run the single-pass AppleScript. Returns the pipe-joined UUIDs.

    `ai_chat_platform` is the human-readable label ('Claude', 'Gemini', …)
    when the markdown is a transformed AI chat transcript; empty string
    otherwise. The AppleScript uses it to prepend a provenance header and
    set AIChatTranscript=1 on the markdown record.

    `is_generic_title` signals that `safe_title` is a placeholder (the page
    had no usable <title>). The AppleScript leaves NameLocked unset on all
    three records so AI enrichment can supply a real title and Post-Enrich
    & Archive can propagate it back to the bookmark and HTML snapshot.

    `override_existing_name` tells the AppleScript that the bookmark passed
    via `bookmark_uuid` was created with a known-bad name (currently only
    the Reddit "From the X community on Reddit" boilerplate) and should be
    renamed to `safe_title` with NameLocked=1. Ignored when bookmark_uuid
    is empty.
    """
    argv = [
        str(html_path),
        str(md_path) if md_path else "",
        bookmark_uuid,
        source_url,
        safe_title,
        ai_chat_platform,
        "1" if is_generic_title else "0",
        "1" if override_existing_name else "0",
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

    log.info("ingest start: %s (bookmark=%s)", args.html_path, args.bookmark or "new")

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

    safe_title, is_generic_title, override_existing_name = derive_title(
        html_path, source_url
    )

    compress_images(html_path)

    size_bytes = html_path.stat().st_size
    if size_bytes > MAX_INGEST_BYTES:
        log.warning(
            "HTML too large after compression (%.1f MB > %.0f MB limit), skipping: %s",
            size_bytes / 1024 / 1024,
            MAX_INGEST_BYTES / 1024 / 1024,
            html_path.name,
        )
        if args.bookmark:
            mark_too_large(args.bookmark)
            try:
                html_path.unlink()
            except OSError:
                pass
            return 0
        # No bookmark means a deliberate desktop capture with no trace in DT;
        # deleting it here would silently destroy the user's only copy.
        quarantine_dir = Path.home() / "Desktop" / "DT_Import_Errors"
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dest = quarantine_dir / html_path.name
            n = 1
            while dest.exists():
                dest = quarantine_dir / f"{html_path.stem} ({n}){html_path.suffix}"
                n += 1
            shutil.move(str(html_path), str(dest))
            log.warning("moved oversized capture to %s", dest)
        except OSError as e:
            log.error("quarantine move failed, leaving in place: %s", e)
        return 0

    ai_chat_platform = is_ai_chat_url(source_url)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy the staging file into tmpdir up front and do all downstream
        # work against that copy. Two reasons:
        #
        # 1. defuddle is a Node binary (mise shim, ad-hoc signed). Under the
        #    launchd watcher it has no TCC grant for the Downloads folder, so
        #    reading ~/Downloads/SingleFile/*.html directly fails with EPERM
        #    and silently drops the markdown extract. (Apple-signed
        #    /usr/bin/python3 — this script and compress_images — is exempt,
        #    which is why parse/compress/import all still work.) tmpdir lives
        #    under the per-user $TMPDIR (/var/folders/…), which is not a
        #    TCC-protected location, so Node can read it.
        #
        # 2. DT names an imported record after the file's stem. When
        #    safe_title differs from the staging file's stem (always true in
        #    the generic "No title" case, since we augment with a URL
        #    suffix), importing the raw staging file would land with the
        #    wrong name and require a `set name` rename event to fix it. That
        #    rename fires Util: Lock Name on Rename, which would force
        #    NameLocked=1 and defeat the untitled-page fallback. The
        #    properly-named twin makes import land with the right name from
        #    the start.
        import_html_path = Path(tmpdir) / f"{safe_title}.html"
        shutil.copy2(html_path, import_html_path)

        md_path = Path(tmpdir) / f"{safe_title}.md"
        has_md = run_defuddle(import_html_path, md_path)
        if has_md:
            strip_inline_data_images(md_path)
            # AI chat transform runs on the raw defuddle output; lint after,
            # so the rewritten report also goes through markdownlint --fix.
            if ai_chat_platform:
                transform_to_report(md_path, ai_chat_platform)
            lint_markdown(md_path)

        try:
            uuids = import_to_devonthink(
                html_path=import_html_path,
                md_path=md_path if has_md else None,
                bookmark_uuid=args.bookmark,
                source_url=source_url,
                safe_title=safe_title,
                ai_chat_platform=ai_chat_platform if has_md else "",
                is_generic_title=is_generic_title,
                override_existing_name=override_existing_name,
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
