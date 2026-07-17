#!/usr/bin/python3
"""boox-process.py — local OCR pipeline for every named Boox notebook.

Consumes notebook PDFs staged by boox-stage.sh, detects which pages are
new or edited via per-page pixel signatures, and transcribes only those
pages with a local vision model (oMLX). Handwritten content never
reaches the cloud-backed smart-rule stages (DT OCR, comment formatting,
chat enrichment): transcription, formatting, and metadata all happen
here, on-device. Two notebook shapes:

  - The daily journal ("<year> Journal", one dated page per day) files
    one markdown record per day into /15_JOURNAL/<year>/, linked from
    its daily note. The transcription's first heading is parsed as the
    entry date and validated (weekday check digit, notebook year,
    monotonic page order); a page that fails validation is parked with
    its reason rather than filed under a guessed date.
  - Every other notebook keeps the classic record model: a monochrome
    TIFF in 00_INBOX, deduplicated by SourceFile (a re-export replaces
    the backing file in place, preserving UUID/name/tags/WikiLinks),
    with the assembled markdown transcription in the Finder comment and
    a local metadata pass supplying EventDate/tags/summary. Pipeline
    flags arrive pre-set (Recognized/Commented/AIEnriched) so only the
    LLM-free Post-Enrich & Archive rule ever matches — it still does
    daily-note extraction, Things tasks, and archiving. The record is
    filed only once every page has transcribed, so a partially-OCR'd
    comment never enters the pipeline.

Change detection: pages are rendered once per staged export (grayscale
PNG, kept in a per-notebook workdir) and identified by ImageMagick's
pixel signature, so an unchanged page is never re-OCR'd no matter how
often the Boox re-exports the notebook, and an edit to any old page —
or a page inserted mid-notebook, which shifts every later signature —
re-enters processing automatically. Per-page transcriptions are cached
in the state file, so a one-page edit re-OCRs one page.

RAM safety: OCR holds the shared local-LLM lock that entity-filing.py
also honors, so two ~18 GB models are never loaded into unified memory
simultaneously; oMLX's LRU eviction handles sequential swaps.

Config (~/.config/dt-pipeline/journal.conf, KEY=VALUE; OMLX_URL and
OMLX_API_KEY default from entities.conf so the shared server is
configured once):
  OMLX_MODEL=<name>      vision model id as listed by /v1/models
                         (default Qwen3-VL-32B-Instruct-4bit)
  OMLX_URL=http://127.0.0.1:8000
  OMLX_API_KEY=<key>
  MAX_PER_RUN=<n>        OCR budget per run (backfill pacing), default 5
  IDLE_MINUTES=<n>       run OCR only after this much user inactivity,
                         default 10, 0 disables the gate
  DENSITY=<dpi>          page render density, default 200
  THINGS_TASKS=on|off    journal only: send bullets under a Tasks:/Action
                         Items: section to Things 3, default off —
                         journal musings are not usually deliberate task
                         lists. Regular notebooks get Things extraction
                         from Post-Enrich & Archive as always.

Usage:
    boox-process.py                  # launchd-driven tick
    boox-process.py --dry-run        # report planned work, write nothing
    boox-process.py --force         # re-queue parked pages, bypass gates
    boox-process.py --status        # print per-notebook state summary
    boox-process.py --rebuild-state # reseed journal state from DT records
"""

import base64
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("boox-process")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/journal.conf")
ENTITIES_CONFIG = os.path.expanduser("~/.config/dt-pipeline/entities.conf")
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
BOOX_DIR = os.path.join(STATE_DIR, "boox")
STAGING_DIR = os.path.join(BOOX_DIR, "staging")
WORK_DIR = os.path.join(BOOX_DIR, "work")
DONE_DIR = os.path.join(BOOX_DIR, "done")
STATE_FILE = os.path.join(BOOX_DIR, "state.json")
LOCK_FILE = os.path.join(BOOX_DIR, "boox-process.lock")
LLM_LOCK_FILE = os.path.join(STATE_DIR, "local-llm.lock")
SUCCESS_FILE = os.path.join(STATE_DIR, "boox-process.last-success")
STATE_SCHEMA_VERSION = 1

JOURNAL_GROUP = "/15_JOURNAL"
INBOX_GROUP = "/00_INBOX"
DAILY_SECTION = "## Today's Notes"
JOURNAL_RE = re.compile(r"^(\d{4}) Journal$")
MAGICK = "/opt/homebrew/bin/magick"
MARKDOWNLINT = "/opt/homebrew/bin/markdownlint"
TIFF_DENSITY = 300
MAX_TIFF_MB = 50

DEFAULTS = {
    "OMLX_MODEL": "Qwen3-VL-32B-Instruct-4bit",
    "OMLX_URL": "http://127.0.0.1:8000",
    "OMLX_API_KEY": "",
    "MAX_PER_RUN": "5",
    "IDLE_MINUTES": "10",
    "DENSITY": "200",
    "THINGS_TASKS": "off",
}

TASK_HEADER_RE = re.compile(
    r"^\s*#*\s*(Action Items|Todos|To-Dos|To Do|Tasks):?\s*$", re.IGNORECASE)
TASK_BULLET_RE = re.compile(r"^\s*[-*•]\s*(?:\[\s?[xX]?\]\s*)?(.+)")
MD_HEADER_RE = re.compile(r"^\s*#+\s")

OCR_ROLE = "You transcribe handwritten note pages into clean Markdown."
OCR_RULES = """\
- Replace middle dots, bullet characters, and other non-standard list \
markers with standard Markdown bullets (-), preserving nesting via \
indentation.
- Replace drawn arrows and connectors with nested lists or blockquotes to \
show relationships.
- Replace circled numbers or other enclosed number forms with standard \
ordered list items (1., 2., 3.).
- When text wraps across multiple lines as a single thought or sentence, \
join it into one line rather than treating each line as a separate item.
- Preserve line breaks between distinct thoughts.
- Output ONLY the reformatted Markdown — no preamble, no code fences."""

JOURNAL_OCR_PROMPT = """\
Transcribe this handwritten journal page as clean Markdown. Preserve ALL \
original content exactly — do not add, remove, rephrase, or comment on \
anything.

Rules:
- The page begins with a handwritten date line (e.g. "Fri, Jul 11"). \
Transcribe it verbatim as a level-1 heading: "# Fri, Jul 11".
- Use ## / ### headers for section titles and section breaks (replace \
underlines or horizontal rules). Reserve # for the date line.
""" + OCR_RULES

NOTE_OCR_PROMPT = """\
Transcribe this handwritten note page as clean Markdown. Preserve ALL \
original content exactly — do not add, remove, rephrase, or comment on \
anything. If a heading ends with "(cont.)", keep it verbatim.

Rules:
- The page's title is a level-1 heading (e.g. "# Project Kickoff"). Use \
## / ### headers for section titles and section breaks within the page \
(replace underlines or horizontal rules).
""" + OCR_RULES

METADATA_ROLE = ("You are a document cataloguing assistant that responds "
                 "only in JSON.")
METADATA_PROMPT = """\
Based on this handwritten note, respond with ONLY a JSON object containing:
- "eventDate": a strict yyyy-mm-dd date string, or "". Set ONLY when a \
specific single date is intrinsic to the note's meaning (meeting notes, a \
call log, an appointment, a dated occurrence). It may come from an \
explicit date in the content or from relative references ("today", "this \
morning") resolved against today's date below. Do NOT set it for \
reference material, study notes, how-to notes, brainstorms, or plans — a \
note is not event-anchored merely because it was written on some day. \
Return "" when unsure.
- "tags": an array of 1-3 concise, singular, Title-Cased topical tags. \
Prefer the existing tags listed below when applicable; only create a new \
tag when none fits.
- "summary": a 1-2 sentence plain-English summary of the note.

Note title: {name}
Today's date: {today}
Existing tags in the database: {tags}

Note content:
{content}"""

WEEKDAY_MAP = {}
for i, names in enumerate([
    ("monday", "mon"), ("tuesday", "tue", "tues"), ("wednesday", "wed"),
    ("thursday", "thu", "thur", "thurs"), ("friday", "fri"),
    ("saturday", "sat"), ("sunday", "sun"),
]):
    for n in names:
        WEEKDAY_MAP[n] = i

MONTH_MAP = {}
for i, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"),
    ("april", "apr"), ("may",), ("june", "jun"), ("july", "jul"),
    ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
    ("november", "nov"), ("december", "dec"),
], start=1):
    for n in names:
        MONTH_MAP[n] = i

MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Config / state
# ---------------------------------------------------------------------------


def _read_conf(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def load_config():
    config = dict(DEFAULTS)
    # Server endpoint and key are shared with the entity layer; the model
    # is deliberately NOT inherited — entities.conf points at a text model.
    entities = _read_conf(ENTITIES_CONFIG)
    for key in ("OMLX_URL", "OMLX_API_KEY"):
        if entities.get(key):
            config[key] = entities[key]
    config.update(_read_conf(CONFIG_FILE))
    return config


def load_state():
    """Fail closed on a corrupt state file: entries is the sole date->UUID
    mapping, and starting from empty state would duplicate every journal
    record."""
    if not os.path.exists(STATE_FILE):
        return {"schema": STATE_SCHEMA_VERSION, "notebooks": {}}
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"state file {STATE_FILE} is unreadable ({exc}); inspect or "
            f"remove it, then run boox-process.py --rebuild-state"
        ) from exc
    if state.get("schema") != STATE_SCHEMA_VERSION:
        log.warning("state schema %s != %s, starting fresh",
                    state.get("schema"), STATE_SCHEMA_VERSION)
        return {"schema": STATE_SCHEMA_VERSION, "notebooks": {}}
    return state


def save_state(state):
    os.makedirs(BOOX_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=BOOX_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def acquire_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = open(path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------


class BridgeUnavailable(RuntimeError):
    pass


def run_bridge(ops, timeout=300):
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"ops": ops}, f)
        result = subprocess.run(
            ["/usr/bin/osascript", "-l", "JavaScript", BRIDGE, path],
            capture_output=True, text=True, timeout=timeout,
        )
    finally:
        os.unlink(path)
    if result.returncode != 0:
        raise RuntimeError(f"bridge failed: {result.stderr.strip()}")
    out = json.loads(result.stdout)
    if not out.get("ok"):
        if out.get("unavailable"):
            raise BridgeUnavailable(out.get("error"))
        raise RuntimeError(
            f"bridge op {out.get('failed_op')} failed: {out.get('error')}")
    return out["results"]


# ---------------------------------------------------------------------------
# Rendering / signatures
# ---------------------------------------------------------------------------


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_magick(args, timeout):
    env = dict(os.environ)
    # magick rasterizes PDF by exec'ing `gs` off PATH, which launchd does not populate.
    env["PATH"] = os.pathsep.join(
        p for p in (os.path.dirname(MAGICK), env.get("PATH")) if p)
    proc = subprocess.run([MAGICK] + args, capture_output=True, text=True,
                          timeout=timeout, env=env)
    if proc.returncode != 0:
        detail = " ".join(proc.stderr.split()) or "no stderr"
        raise RuntimeError(f"magick exited {proc.returncode}: {detail[:500]}")
    return proc


def render_pages(pdf_path, workdir, density):
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    run_magick(
        ["-density", str(density), pdf_path,
         "-colorspace", "gray", "-background", "white",
         "-alpha", "remove", "-alpha", "off",
         os.path.join(workdir, "page-%04d.png")],
        timeout=600,
    )
    return sorted(glob.glob(os.path.join(workdir, "page-*.png")))


def page_signatures(pngs):
    out = run_magick(["identify", "-format", "%#\n"] + pngs, timeout=300)
    sigs = out.stdout.split()
    if len(sigs) != len(pngs):
        raise RuntimeError(
            f"identify returned {len(sigs)} signatures for {len(pngs)} pages")
    return sigs


# ---------------------------------------------------------------------------
# OCR transport
# ---------------------------------------------------------------------------


def _chat(config, role, content, timeout=900):
    payload = json.dumps({
        "model": config["OMLX_MODEL"],
        "messages": [
            {"role": "system", "content": role},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if config["OMLX_API_KEY"]:
        headers["Authorization"] = "Bearer " + config["OMLX_API_KEY"]
    req = urllib.request.Request(
        config["OMLX_URL"] + "/v1/chat/completions",
        data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.load(resp)
    return out["choices"][0]["message"]["content"].strip()


def strip_fence(text):
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def ocr_page(config, png_path, prompt):
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + b64}},
    ]
    return strip_fence(_chat(config, OCR_ROLE, content))


def extract_metadata(config, name, text, tags_pool, today):
    """Local replacement for the cloud enrichment pass: eventDate, tags,
    summary. Returns None on any failure — filing proceeds without
    metadata rather than blocking on it, matching the enrich rule's
    advance-on-timeout behavior."""
    prompt = METADATA_PROMPT.format(
        name=name, today=today.isoformat(),
        tags=", ".join(tags_pool) if tags_pool else "(none)",
        content=text[:24000])
    try:
        raw = strip_fence(_chat(config, METADATA_ROLE, prompt))
        meta = json.loads(raw)
    except (urllib.error.URLError, OSError, json.JSONDecodeError,
            KeyError, TypeError, AttributeError) as exc:
        log.warning("metadata extraction failed for %s: %s", name, exc)
        return None
    ed = str(meta.get("eventDate") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", ed):
        ed = ""
    tags = [str(t) for t in meta.get("tags") or [] if str(t).strip()][:3]
    summary = str(meta.get("summary") or "").strip()
    return {"eventDate": ed, "tags": tags, "summary": summary}


def omlx_available(config):
    try:
        headers = {}
        if config["OMLX_API_KEY"]:
            headers["Authorization"] = "Bearer " + config["OMLX_API_KEY"]
        req = urllib.request.Request(config["OMLX_URL"] + "/v1/models",
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=3) as resp:
            models = json.load(resp)
        return config["OMLX_MODEL"] in {
            m.get("id", "") for m in models.get("data", [])}
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False


CONT_HEADING_RE = re.compile(r"^\s*#+\s*.+?\s*\(cont\.?\)\s*$", re.IGNORECASE)


def assemble_pages(texts):
    """Join per-page transcriptions into one document. A page whose first
    line is a "(cont.)" heading is a page-break continuation of the
    previous section, so that heading is dropped and its content flows on."""
    parts = []
    for text in texts:
        lines = text.splitlines()
        first = next((k for k, l in enumerate(lines) if l.strip()), None)
        if parts and first is not None and CONT_HEADING_RE.match(lines[first]):
            lines = lines[:first] + lines[first + 1:]
        part = "\n".join(lines).strip()
        if part:
            parts.append(part)
    return "\n\n".join(parts)


def promote_title(markdown):
    """A note's opening heading is its title, so it belongs at level 1. The
    model still demotes it to ## on occasion; promote it only when the
    document has no level-1 heading, leaving section headings where they
    were transcribed."""
    lines = markdown.splitlines()
    if any(line.startswith("# ") for line in lines):
        return markdown
    first = next((k for k, l in enumerate(lines) if l.strip()), None)
    if first is None or not lines[first].startswith("## "):
        return markdown
    lines[first] = lines[first][1:]
    return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")


def markdownlint_fix(text):
    fd, tmp = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        subprocess.run([MARKDOWNLINT, tmp, "--quiet", "--fix"],
                       capture_output=True, timeout=60)
        with open(tmp) as f:
            return f.read()
    except (OSError, subprocess.SubprocessError):
        return text
    finally:
        os.unlink(tmp)


def convert_tiff(pdf_path, workdir, stem):
    """Monochrome Group4 TIFF, the stored record artifact — same recipe as
    the retired boox-import.sh (flattening matters for vector PDFs)."""
    out = os.path.join(workdir, f"{stem}.tiff")
    run_magick(
        ["-density", str(TIFF_DENSITY), pdf_path,
         "-background", "white", "-alpha", "remove", "-alpha", "off",
         "-threshold", "50%", "-monochrome", "-compress", "Group4", out],
        timeout=300)
    size = os.path.getsize(out)
    if size > MAX_TIFF_MB * 1024 * 1024:
        raise RuntimeError(f"TIFF too large ({size} bytes)")
    return out


def user_idle_seconds():
    try:
        out = subprocess.check_output(
            ["/usr/sbin/ioreg", "-c", "IOHIDSystem"], text=True)
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                return int(line.split("=")[-1].strip()) / 1_000_000_000
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


class DateParseError(ValueError):
    pass


def parse_date_line(line, notebook_year, today):
    """Entry date from a transcribed date heading, or DateParseError.

    Accepts ISO (2026-07-11), month-name (Fri, Jul 11 / July 11th 2026),
    and US numeric (7/11, 7/11/26) forms, with an optional weekday
    anywhere in the line. The weekday, when present, must agree with the
    parsed date — handwritten digits are the most commonly misread
    characters, and the weekday acts as a check digit.
    """
    raw = line.strip().lstrip("#").strip()
    if not raw:
        raise DateParseError("first line is empty")
    text = re.sub(r"[,.]", " ", raw.lower())
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)

    weekday = None
    kept = []
    for token in text.split():
        if token in WEEKDAY_MAP and weekday is None:
            weekday = WEEKDAY_MAP[token]
        else:
            kept.append(token)
    text = " ".join(kept)

    year, month, day = None, None, None
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        month_names = "|".join(sorted(MONTH_MAP, key=len, reverse=True))
        m = re.search(r"\b(%s)\s+(\d{1,2})(?:\s+(\d{4}))?\b" % month_names,
                      text)
        if m:
            month, day = MONTH_MAP[m.group(1)], int(m.group(2))
            year = int(m.group(3)) if m.group(3) else notebook_year
        else:
            m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                year = notebook_year
                if m.group(3):
                    year = int(m.group(3))
                    if year < 100:
                        year += 2000
    if month is None:
        raise DateParseError(f"no date found in {raw!r}")

    try:
        parsed = date(year, month, day)
    except ValueError:
        raise DateParseError(f"invalid date in {raw!r}")
    if parsed.year != notebook_year:
        raise DateParseError(
            f"{parsed.isoformat()} is outside notebook year {notebook_year}")
    if parsed > today:
        raise DateParseError(f"{parsed.isoformat()} is in the future")
    if weekday is not None and parsed.weekday() != weekday:
        raise DateParseError(
            f"written weekday does not match {parsed.isoformat()} "
            f"({parsed.strftime('%A')}) in {raw!r}")
    return parsed


def first_heading_line(markdown):
    for line in markdown.splitlines():
        if line.strip():
            return line
    return ""


# ---------------------------------------------------------------------------
# DEVONthink filing
# ---------------------------------------------------------------------------


def ensure_groups(year, chat_warned):
    results = run_bridge([
        {"op": "ensure_group", "path": JOURNAL_GROUP, "exclude_chat": True},
        {"op": "ensure_group", "path": f"{JOURNAL_GROUP}/{year}"},
    ])
    if not results[0]["chat_excluded"] and not chat_warned.get("done"):
        log.warning("could not set 'exclude from chat' on %s — set it "
                    "manually in DEVONthink's Info panel", JOURNAL_GROUP)
        chat_warned["done"] = True


def upsert_entry(notebook_name, entry_date, page_index, sig, text, entries):
    """Create or update the day's record; returns (uuid, changed)."""
    iso = entry_date.isoformat()
    name = f"{iso} Journal"
    text_sha = hashlib.sha256(text.encode()).hexdigest()
    fields = {
        "EventDate": iso,
        "SourceFile": notebook_name,
        "JournalEntry": 1,
        "PageIndex": page_index + 1,
        "PageSignature": sig,
    }
    existing = entries.get(iso)
    if existing:
        if existing.get("text_sha") == text_sha:
            run_bridge([{"op": "set_fields", "uuid": existing["uuid"],
                         "fields": fields}])
            return existing["uuid"], False
        run_bridge([
            {"op": "set_text", "uuid": existing["uuid"], "text": text},
            {"op": "set_fields", "uuid": existing["uuid"], "fields": fields},
        ])
        existing["text_sha"] = text_sha
        return existing["uuid"], True
    path = f"{JOURNAL_GROUP}/{entry_date.year}/{name}"
    found = run_bridge([{"op": "get_at_path", "path": path}])[0]
    if found:
        run_bridge([
            {"op": "set_text", "uuid": found["uuid"], "text": text},
            {"op": "set_fields", "uuid": found["uuid"], "fields": fields},
        ])
        entries[iso] = {"uuid": found["uuid"], "text_sha": text_sha}
        return found["uuid"], True
    uuid = run_bridge([{
        "op": "create_record",
        "name": name,
        "path": f"{JOURNAL_GROUP}/{entry_date.year}",
        "text": text,
        "fields": fields,
        "tags": ["Journal"],
    }])[0]["uuid"]
    entries[iso] = {"uuid": uuid, "text_sha": text_sha}
    return uuid, True


def extract_tasks(text):
    """Bullets under a Tasks:/Action Items: header, ending at the next
    markdown header. Same section grammar as Post-Enrich & Archive uses
    for regular handwritten notebooks."""
    tasks = []
    in_section = False
    for line in text.splitlines():
        if TASK_HEADER_RE.match(line):
            in_section = True
            continue
        if in_section:
            if MD_HEADER_RE.match(line):
                in_section = False
                continue
            m = TASK_BULLET_RE.match(line.strip())
            if m and m.group(1).strip():
                tasks.append(m.group(1).strip())
    return tasks


def send_tasks_to_things(entry_date, uuid, text, entry):
    """Send unseen tasks via the Things URL scheme. /usr/bin/open -g adds
    in the background without stealing focus and — unlike Things
    AppleEvents — carries no per-app Automation grant that a headless
    launchd prompt could fumble."""
    from urllib.parse import quote
    sent = entry.setdefault("tasks_sent", [])
    new = [t for t in extract_tasks(text) if t not in sent]
    for task in new:
        notes = (f"From journal {entry_date.isoformat()}\n"
                 f"x-devonthink-item://{uuid}")
        url = (f"things:///add?title={quote(task)}&notes={quote(notes)}")
        result = subprocess.run(["/usr/bin/open", "-g", url],
                                capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("Things add failed for %r: %s", task,
                        result.stderr.strip())
            continue
        sent.append(task)
    return len(new)


def link_daily_note(entry_date, uuid):
    heading = (f"{entry_date:%A}, {entry_date:%B} {entry_date.day}, "
               f"{entry_date.year}")
    daily = run_bridge([{"op": "get_or_create_daily",
                         "date": entry_date.isoformat(),
                         "heading": heading}])[0]
    line = f"- [\U0001F4D4 Journal](x-devonthink-item://{uuid})"
    run_bridge([{"op": "insert_under_section", "uuid": daily["uuid"],
                 "header": DAILY_SECTION, "line": line}])


def file_regular_note(stem, tiff_path, markdown, meta):
    """Create or update the notebook's TIFF record in 00_INBOX,
    deduplicated by SourceFile. All pipeline flags arrive set, so of the
    smart rules only Post-Enrich & Archive (no LLM) ever matches; a
    re-export replaces the backing file in place and moves the record
    back to the inbox for a fresh idempotent post-enrich pass."""
    fields = {
        "SourceFile": stem,
        "Handwritten": 1,
        "NeedsProcessing": 1,
        "Recognized": 1,
        "Commented": 1,
        "AIEnriched": 1,
        "DocumentType": "Handwritten Note",
    }
    if meta is not None:
        fields["EventDate"] = meta["eventDate"]
    if meta and meta["summary"]:
        fields["summary"] = meta["summary"]
    hits = run_bridge([{"op": "find_by_field", "field": "SourceFile",
                        "value": stem}])[0]
    if hits:
        uuid = hits[0]["uuid"]
        run_bridge([
            {"op": "replace_file", "uuid": uuid, "path": tiff_path},
            {"op": "set_comment", "uuid": uuid, "comment": markdown},
            {"op": "set_fields", "uuid": uuid, "fields": fields},
            {"op": "move_to", "uuid": uuid, "group": INBOX_GROUP},
        ])
        return uuid, "updated"
    name = stem
    if meta and meta["eventDate"]:
        name = f"{meta['eventDate']} {stem}"
        fields["NameLocked"] = 1
    ops = [
        {"op": "import_record", "path": tiff_path, "group": INBOX_GROUP},
    ]
    uuid = run_bridge(ops)[0]["uuid"]
    ops = [
        {"op": "set_comment", "uuid": uuid, "comment": markdown},
        {"op": "set_fields", "uuid": uuid, "fields": fields},
        {"op": "set_name", "uuid": uuid, "name": name},
    ]
    if meta and meta["tags"]:
        ops.append({"op": "set_tags", "uuid": uuid, "tags": meta["tags"]})
    run_bridge(ops)
    return uuid, "imported"


# ---------------------------------------------------------------------------
# Notebook processing
# ---------------------------------------------------------------------------


def notebook_state(state, name):
    nb = state["notebooks"].setdefault(name, {
        "render_sha": "",
        "pages": [],
        "entries": {},
    })
    return nb


def page_done(page, is_journal):
    return bool(page["date"] if is_journal else page.get("text"))


def finish_notebook(basename, pdf_sha, pdf_path, workdir):
    os.makedirs(DONE_DIR, exist_ok=True)
    with open(os.path.join(DONE_DIR, basename + ".sha256"), "w") as f:
        f.write(pdf_sha)
    os.unlink(pdf_path)
    shutil.rmtree(workdir, ignore_errors=True)
    log.info("%s fully processed, staged PDF removed", basename)


def process_notebook(pdf_path, state, config, dry_run, force, budget,
                     chat_warned):
    """Process one staged notebook; returns pages OCR'd this run."""
    basename = os.path.basename(pdf_path)
    stem = basename[:-4]
    is_journal = bool(JOURNAL_RE.match(stem))
    nb = notebook_state(state, stem)
    workdir = os.path.join(WORK_DIR, stem)

    if force:
        for page in nb["pages"]:
            if page.get("parked"):
                page["parked"] = ""
                page["attempts"] = 0

    pdf_sha = sha256_file(pdf_path)
    if pdf_sha != nb["render_sha"]:
        if dry_run:
            # render_pages rmtrees+rewrites workdir, which a real run may be
            # relying on concurrently — a preview must not touch it.
            log.info("[dry-run] %s: changed since last render — page-level "
                     "preview unavailable without a real run", basename)
            return 0
        log.info("rendering %s at %s dpi", basename, config["DENSITY"])
        try:
            pngs = render_pages(pdf_path, workdir, config["DENSITY"])
            sigs = page_signatures(pngs)
        except (subprocess.SubprocessError, RuntimeError) as exc:
            log.error("render failed for %s, keeping staged for retry: %s",
                      basename, exc)
            return 0
        old_pages = nb["pages"]
        pages = []
        for i, sig in enumerate(sigs):
            if i < len(old_pages) and old_pages[i].get("sig") == sig:
                pages.append(old_pages[i])
            else:
                pages.append({"sig": sig, "date": "", "text": "",
                              "parked": "", "attempts": 0})
                nb["dirty"] = True
        nb["pages"] = pages
        nb["render_sha"] = pdf_sha
        save_state(state)
    else:
        pngs = sorted(glob.glob(os.path.join(workdir, "page-*.png")))
        if len(pngs) != len(nb["pages"]):
            if dry_run:
                log.info("[dry-run] %s: workdir out of sync — page-level "
                         "preview unavailable without a real run", basename)
                return 0
            # Workdir lost (cleanup, reboot of a mid-backfill run): drop the
            # render stamp so the next run re-renders from the staged PDF.
            nb["render_sha"] = ""
            save_state(state)
            log.warning("workdir out of sync for %s, will re-render next run",
                        basename)
            return 0

    pending = [i for i, p in enumerate(nb["pages"])
               if not page_done(p, is_journal) and not p["parked"]]
    parked = [i for i, p in enumerate(nb["pages"]) if p["parked"]]

    if dry_run:
        log.info("[dry-run] %s (%s): %d page(s), %d pending OCR %s, "
                 "%d parked %s", basename,
                 "journal" if is_journal else "notebook", len(nb["pages"]),
                 len(pending), [i + 1 for i in pending], len(parked),
                 [i + 1 for i in parked])
        return 0

    if is_journal:
        processed = process_journal_pages(
            nb, stem, basename, workdir, pending, config, budget, state,
            chat_warned)
    else:
        processed = process_note_pages(
            nb, basename, workdir, pending, config, budget, state)

    pending_after = [i for i, p in enumerate(nb["pages"])
                     if not page_done(p, is_journal) and not p["parked"]]
    parked_after = [i + 1 for i, p in enumerate(nb["pages"]) if p["parked"]]
    if pending_after:
        return processed
    if parked_after:
        log.warning("%s: page(s) %s parked — fix and re-run with --force",
                    basename, parked_after)
        return processed

    if not is_journal:
        if not file_note_if_needed(nb, stem, pdf_path, workdir, config, state):
            return processed
    finish_notebook(basename, pdf_sha, pdf_path, workdir)
    return processed


def process_journal_pages(nb, stem, basename, workdir, pending, config,
                          budget, state, chat_warned):
    year = int(JOURNAL_RE.match(stem).group(1))
    today = date.today()
    processed = 0
    if pending:
        ensure_groups(year, chat_warned)
    for i in pending:
        if processed >= budget:
            log.info("OCR budget reached (%d), remaining pages wait for the "
                     "next run", budget)
            break
        page = nb["pages"][i]
        png = os.path.join(workdir, f"page-{i:04d}.png")
        try:
            text = ocr_page(config, png, JOURNAL_OCR_PROMPT)
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                KeyError, TypeError, AttributeError) as exc:
            page["attempts"] = page.get("attempts", 0) + 1
            log.warning("OCR failed for %s page %d (attempt %d): %s",
                        basename, i + 1, page["attempts"], exc)
            if page["attempts"] >= MAX_ATTEMPTS:
                page["parked"] = f"OCR failed {MAX_ATTEMPTS} times: {exc}"
                log.error("parked %s page %d: %s", basename, i + 1,
                          page["parked"])
            save_state(state)
            continue
        processed += 1

        prev_date = None
        for j in range(i - 1, -1, -1):
            if nb["pages"][j]["date"]:
                prev_date = date.fromisoformat(nb["pages"][j]["date"])
                break
        try:
            entry_date = parse_date_line(first_heading_line(text), year, today)
            if prev_date is not None and entry_date <= prev_date:
                raise DateParseError(
                    f"{entry_date.isoformat()} does not increase over "
                    f"page {j + 1}'s {prev_date.isoformat()}")
        except DateParseError as exc:
            # Deterministic input, deterministic misread — retrying the
            # same page cannot help, so park immediately.
            page["parked"] = str(exc)
            log.error("parked %s page %d: %s", basename, i + 1, exc)
            save_state(state)
            continue

        uuid, changed = upsert_entry(stem, entry_date, i, page["sig"], text,
                                     nb["entries"])
        link_daily_note(entry_date, uuid)
        if config["THINGS_TASKS"].strip().lower() == "on":
            sent = send_tasks_to_things(entry_date, uuid, text,
                                        nb["entries"][entry_date.isoformat()])
            if sent:
                log.info("sent %d task(s) to Things from %s", sent,
                         entry_date.isoformat())
        page["date"] = entry_date.isoformat()
        page["parked"] = ""
        page["attempts"] = 0
        save_state(state)
        log.info("%s page %d -> %s (%s)", basename, i + 1,
                 entry_date.isoformat(), "updated" if changed else "unchanged")
    return processed


def process_note_pages(nb, basename, workdir, pending, config, budget, state):
    processed = 0
    for i in pending:
        if processed >= budget:
            log.info("OCR budget reached (%d), remaining pages wait for the "
                     "next run", budget)
            break
        page = nb["pages"][i]
        png = os.path.join(workdir, f"page-{i:04d}.png")
        try:
            text = ocr_page(config, png, NOTE_OCR_PROMPT)
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                KeyError, TypeError, AttributeError) as exc:
            page["attempts"] = page.get("attempts", 0) + 1
            log.warning("OCR failed for %s page %d (attempt %d): %s",
                        basename, i + 1, page["attempts"], exc)
            if page["attempts"] >= MAX_ATTEMPTS:
                page["parked"] = f"OCR failed {MAX_ATTEMPTS} times: {exc}"
                log.error("parked %s page %d: %s", basename, i + 1,
                          page["parked"])
            save_state(state)
            continue
        processed += 1
        page["text"] = text or " "
        page["parked"] = ""
        page["attempts"] = 0
        save_state(state)
        log.info("%s page %d transcribed", basename, i + 1)
    return processed


def file_note_if_needed(nb, stem, pdf_path, workdir, config, state):
    """Assemble, enrich, and upsert a completed regular notebook. Returns
    True when the notebook is fully filed (caller may clean up), False
    when filing must be retried next run."""
    markdown = markdownlint_fix(promote_title(
        assemble_pages([p.get("text", "") for p in nb["pages"]])))
    content_sha = hashlib.sha256(markdown.encode()).hexdigest()
    hits = run_bridge([{"op": "find_by_field", "field": "SourceFile",
                        "value": stem}])[0]
    if hits and nb.get("filed_sha") == content_sha and not nb.get("dirty"):
        return True

    try:
        tags_pool = run_bridge([{"op": "list_tags"}])[0]
    except (RuntimeError, BridgeUnavailable):
        tags_pool = []
    meta = extract_metadata(config, stem, markdown, tags_pool, date.today())

    try:
        tiff = convert_tiff(pdf_path, workdir, stem)
    except (subprocess.SubprocessError, RuntimeError) as exc:
        log.error("TIFF conversion failed for %s, keeping staged: %s",
                  stem, exc)
        return False
    uuid, status = file_regular_note(stem, tiff, markdown, meta)
    nb["filed_sha"] = content_sha
    nb["dirty"] = False
    save_state(state)
    log.info("%s %s as %s (eventDate=%s)", stem, status, uuid,
             (meta or {}).get("eventDate", "") or "none")
    return True


# ---------------------------------------------------------------------------
# State rebuild
# ---------------------------------------------------------------------------


def rebuild_state(state, dry_run=False):
    """Reseed entries maps from the records in /15_JOURNAL. Render stamps
    and page arrays are left empty: the next staged export re-renders and
    re-derives them, with matching text hashes preventing DT churn."""
    try:
        years = run_bridge([{"op": "list_group", "path": JOURNAL_GROUP}])[0]
    except BridgeUnavailable:
        raise
    except RuntimeError as exc:
        log.info("no journal group to rebuild from: %s", exc)
        return
    rebuilt = 0
    for year in years:
        if not re.match(r"^\d{4}$", year["name"]):
            continue
        records = run_bridge([{"op": "list_group",
                               "path": f"{JOURNAL_GROUP}/{year['name']}"}])[0]
        for rec in records:
            m = re.match(r"^(\d{4}-\d{2}-\d{2}) Journal$", rec["name"])
            if not m:
                continue
            iso = m.group(1)
            fields, text = run_bridge([
                {"op": "get_fields", "uuid": rec["uuid"],
                 "fields": ["SourceFile"]},
                {"op": "get_text", "uuid": rec["uuid"]},
            ])
            notebook = fields["fields"]["SourceFile"] or f"{iso[:4]} Journal"
            nb = notebook_state(state, notebook)
            nb["entries"][iso] = {
                "uuid": rec["uuid"],
                "text_sha": hashlib.sha256(
                    text["text"].encode()).hexdigest(),
            }
            rebuilt += 1
    if dry_run:
        log.info("[dry-run] state rebuild: %d entr(ies) would be reseeded "
                 "from DT, not saved", rebuilt)
        return
    save_state(state)
    log.info("state rebuild: %d entr(ies) reseeded from DT", rebuilt)


def auto_rebuild_if_missing(state, state_file_existed, dry_run):
    """A first-ever or lost state file reseeds entries from DT instead of
    starting empty, which would mint a duplicate journal record for every
    date DT already has filed."""
    if state_file_existed or dry_run:
        return
    log.warning("state file missing, rebuilding entries from DT before "
                "processing")
    rebuild_state(state)


def print_status(state):
    for name, nb in sorted(state["notebooks"].items()):
        is_journal = bool(JOURNAL_RE.match(name))
        pages = nb["pages"]
        done = sum(1 for p in pages if page_done(p, is_journal))
        parked = [(i + 1, p["parked"]) for i, p in enumerate(pages)
                  if p["parked"]]
        pending = len(pages) - done - len(parked)
        detail = (f"{len(nb['entries'])} entries" if is_journal
                  else ("filed" if nb.get("filed_sha") and not nb.get("dirty")
                        else "not yet filed"))
        print(f"{name}: {len(pages)} page(s) rendered, {done} transcribed, "
              f"{pending} pending, {detail}")
        for idx, reason in parked:
            print(f"  parked page {idx}: {reason}")
    staged = sorted(glob.glob(os.path.join(STAGING_DIR, "*.pdf")))
    if staged:
        print("staged:", ", ".join(os.path.basename(p) for p in staged))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    status = "--status" in args
    rebuild = "--rebuild-state" in args
    user_invoked = bool(dry_run or force or status or rebuild)

    # --dry-run/--status are pure inspection: a human running one to check on
    # a possibly-dead agent must not itself refresh the liveness stamp it's
    # trying to read.
    if not dry_run and not status:
        subprocess.run(
            [os.path.expanduser("~/.local/bin/pipeline-record-run"),
             "boox-process", "1800"],
            check=False,
        )

    if status:
        print_status(load_state())
        return

    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job")],
            capture_output=True, text=True)
        if gate.returncode != 0:
            log.info("skipping: battery gate")
            return
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-dt-driver")],
            capture_output=True, text=True)
        if gate.returncode != 0:
            log.info("skipping: follower machine")
            return

    lock_fd = None
    if not dry_run:
        lock_fd = acquire_lock(LOCK_FILE)
        if lock_fd is None:
            log.info("another boox-process run holds the lock, exiting")
            return
        # A fresh .last-run only proves the process ticked; this proves it
        # also got past the role/battery gates instead of being silently
        # skipped by them every time (dt-watchdog checks the two against
        # each other).
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(SUCCESS_FILE, "w") as f:
            f.write(str(int(time.time())))

    config = load_config()
    state_file_existed = os.path.exists(STATE_FILE)
    state = load_state()

    if rebuild:
        rebuild_state(state, dry_run=dry_run)
        return

    auto_rebuild_if_missing(state, state_file_existed, dry_run)

    staged = sorted(glob.glob(os.path.join(STAGING_DIR, "*.pdf")))
    if not staged:
        return

    idle_min = int(config["IDLE_MINUTES"])
    if not user_invoked and idle_min > 0:
        idle = user_idle_seconds()
        if idle is not None and idle < idle_min * 60:
            log.info("user active, deferring OCR to an idle run")
            return

    llm_lock = None
    if not dry_run:
        if not omlx_available(config):
            log.info("oMLX unavailable or model %s not served, deferring",
                     config["OMLX_MODEL"])
            return
        llm_lock = acquire_lock(LLM_LOCK_FILE)
        if llm_lock is None:
            log.info("local-llm lock held (entity extraction?), deferring OCR")
            return

    budget = int(config["MAX_PER_RUN"])
    chat_warned = {}
    processed = 0
    for pdf_path in staged:
        try:
            processed += process_notebook(
                pdf_path, state, config, dry_run, force,
                budget - processed, chat_warned)
        except BridgeUnavailable as exc:
            log.info("DEVONthink unavailable, ending run: %s", exc)
            break
        except (RuntimeError, subprocess.SubprocessError) as exc:
            log.error("processing failed for %s: %s",
                      os.path.basename(pdf_path), exc)
    if processed:
        log.info("run complete: %d page(s) transcribed", processed)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)
