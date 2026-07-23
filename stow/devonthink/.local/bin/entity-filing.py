#!/usr/bin/python3
"""
entity-filing.py — AI filing step for the entity layer.

Scans processed pipeline documents (Granola meeting notes, handwritten
notes, past daily notes) for facts about people, resolves each mention
against Lorebook/20_ENTITIES/People, and files dated, provenance-linked
bullets into each person's `## Biographical Log`. Daily notes are
stripped of the morning brief's generated sections first, so extraction
only ever sees human-authored content. The LLM only performs
the messy-text -> structured-JSON extraction; everything that writes to
DEVONthink is deterministic (entity-dt-bridge.js ops built here).

Safety model:
  - suggest mode (default): every extraction about *known* people becomes a
    proposal record in /20_ENTITIES/_Review containing a human summary plus
    the exact ops as a fenced JSON block. Moving a proposal into
    _Review/Approved makes the next run apply it; deleting it rejects it.
    Nothing touches a Person record without review.
  - auto mode (FILING_MODE=auto): ops for unambiguous existing people
    apply immediately; ambiguous matches still become proposals. A
    permanent manual-review path, per the design doc.
  - People absent from the roster never enter proposals at all. Each gets
    one durable candidate record under /20_ENTITIES/_Candidates (see
    entity_candidates.py) accumulating per-source sightings; move it to
    _Candidates/Approved to track them (promotion files the accumulated
    evidence), to _Candidates/Ignored to never hear about them again, or
    delete it to forget. The calendar pass in dt-morning-brief.py feeds the
    same store, so one stranger has one decision, made once.
  - Extraction is gated on a seeded roster (MIN_ROSTER). The prompt's
    whole resolution step is the roster, and a source is only extracted
    again when its content changes, so extracting against an empty People
    group burns each source on a proposal full of unresolvable bare first
    names.
  - An approved proposal is re-verified against the *live* roster before
    its ops run: a frozen `ensure_person "Alison"` written before
    "Alison Vance" was seeded would otherwise create a duplicate record.
  - The boolean custom-metadata flag FilingSuppressed, on the Person record
    itself, mutes a person entirely: nothing is proposed or written about
    them. It is a noise control for someone who saturates the sources, not a
    privacy control — see filing_suppressed(). The privacy control is a
    different flag on the same record, BriefingSuppressed, which redacts
    rendered output and is read only by dt-morning-brief.py. Neither flag
    reads the other; setting one does nothing for the other.

Lifecycle: a source is only discovered once its upstream pipeline is
complete (NeedsProcessing cleared — a Boox record mid-OCR has no text
yet), and completion is keyed on a content hash plus DEVONthink's
modification date rather than a bare UUID: a later OCR pass, notebook
re-export, or hand edit re-enters filing automatically, with fact-level
dedup in the bridge keeping re-runs idempotent. Field updates carry an
effective date; the bridge refuses writes older than a field's recorded
as-of date, so an old source processed late can never overwrite newer
state. Sources that fail MAX_ATTEMPTS times are parked with their last
error and retry automatically when their content changes (or via
--force).

Privacy: extraction runs only through the local oMLX model. DT chat, which
may be a cloud provider, is never used — there is no other transport.

Config (~/.config/dt-pipeline/entities.conf, KEY=VALUE):
  TRANSPORT=local|off                local (default): extraction runs on
                                     oMLX and waits when the server is
                                     down. off: pause extraction entirely.
  OMLX_MODEL=<name>                  model id as listed by /v1/models
  OMLX_URL=http://127.0.0.1:8000
  OMLX_API_KEY=<key>                 required when oMLX auth is enabled
                                     (Settings -> auth.api_key)
  FILING_MODE=suggest|auto           default suggest
  MAX_PER_RUN=<n>                    extraction budget per run, default 3
  MIN_ROSTER=<n>                     extract only once People holds at least
                                     this many records, default 1. Applying
                                     approved proposals is never gated.
                                     TRANSPORT=off is the blunter pause: it
                                     stops extraction without recording
                                     anything processed.
  SELF_NAME=<name>                   extra self-alias to exclude
  SKIP_SOURCE_TITLES=<regex>         sources whose name matches are never
                                     extracted (recurring standups etc.);
                                     case-insensitive, unanchored
  IDLE_MINUTES=<n>                   optional idle gate: additionally require
                                     this many minutes of user inactivity
                                     before local extraction; default 0
                                     (off). Extraction always defers while
                                     macOS reports elevated memory pressure,
                                     so inference never lands on an
                                     already-tight machine.
  THINGS_SYNC=on|off                 default off. Mirror each pending
                                     proposal as a to-do in Things 3: the
                                     note carries an editable line-format
                                     rendering of the proposal, completing
                                     the to-do approves it (edits included),
                                     canceling or deleting it rejects it.
                                     Pending candidates mirror too, as one
                                     to-do each with a compact summary and
                                     DT link (never the evidence): complete
                                     = track, cancel/delete = ignore, and a
                                     decision made in DEVONthink first wins
                                     — the task just closes to match.
                                     Person names and facts sync through
                                     Things Cloud — an explicit exception
                                     to the entity layer's local-only rule.
  THINGS_PROJECT=<title>             Things project holding the proposal
                                     to-dos, created on demand; default
                                     "Entity Filing"

Usage:
    entity-filing.py                 # launchd-driven scan + apply
    entity-filing.py --dry-run       # print planned ops, write nothing
    entity-filing.py --force UUID    # re-extract one source record
    entity-filing.py --apply-only    # only process _Review/Approved
    entity-filing.py --scan-only     # skip the apply phase
    entity-filing.py --rebuild-state # seed processed state from EntityFiled
    entity-filing.py --split-candidate UUID SID...
                                     # move sightings to a new candidate
    entity-filing.py --merge-candidates KEEP FOLD
                                     # fold one candidate into another
    entity-filing.py --migrate-candidates
                                     # one-shot calendar-ledger conversion
"""

import fcntl
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

import brief_events as be
import entity_candidates as ec
import things_bridge

log = setup_log("entity-filing")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/entities.conf")
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "entity-filing-state.json")
LOCK_FILE = os.path.join(STATE_DIR, "entity-filing.lock")
LLM_LOCK_FILE = os.path.join(STATE_DIR, "local-llm.lock")
SUCCESS_FILE = os.path.join(STATE_DIR, "entity-filing.last-success")
STATE_SCHEMA_VERSION = 2
REVIEW_PATH = "/20_ENTITIES/_Review"
APPROVED_PATH = "/20_ENTITIES/_Review/Approved"
FACTS_FILED_PATH = "/20_ENTITIES/_Facts/Filed"
MAX_ATTEMPTS = 5
UPDATE_FIELDS = ("employer", "role", "city", "email")
THINGS_MAP_FILE = os.path.join(STATE_DIR, "things-filing-map.json")
THINGS_CAND_MAP_FILE = os.path.join(STATE_DIR, "things-candidates-map.json")
THINGS_MAP_VERSION = 1
THINGS_URL_LIMIT = 3500
THINGS_WHEN = "today"
BOUNCE_LIMIT = 3
SPEC_SENTINEL = "=== proposed v1 ==="
BANNER_PREFIX = "⚠ entity-filing:"
PROPOSAL_MARKER = "Proposal: x-devonthink-item://"
CANDIDATE_MARKER = "Candidate: x-devonthink-item://"

CHAT_ROLE = (
    "You are a personal-CRM extraction assistant that responds only with JSON."
)

PROMPT_TEMPLATE = """\
Extract facts about PEOPLE from the note below. Respond with JSON only, in
exactly this shape:

{{"people": [{{"name": "<canonical full name>",
  "match": "<exact name from KNOWN PEOPLE, or null>",
  "interacted": true,
  "facts": [{{"date": "yyyy-mm-dd or null", "fact": "<one concise sentence>"}}],
  "updates": {{"employer": null, "role": null, "city": null, "email": null}}}}],
 "events": [{{"name": "<short reusable title>", "date": "yyyy-mm-dd or null",
  "location": "<place name or null>", "attendees": ["<name>"],
  "summary": "<one sentence or null>"}}]}}

Rules:
- Only real individual humans the note's author personally interacted with or
  learned something about. No public figures mentioned in passing, no
  companies, no product or project names.
- Resolve pronouns and nicknames to one canonical person before extracting.
- Record durable biographical or relationship facts: job or role changes,
  moves, partner and family news, health, notable plans, how the author met
  them, significant personal things discussed WITH them. Skip meeting
  logistics, task assignments, and technical minutiae.
- Do NOT record workplace working style: tool preferences, how someone uses
  AI, how they run meetings, opinions on process, or what they said in a
  work discussion. A work fact belongs only when it changes their biography
  (new job, new role, promotion, leaving, relocation).
- When unsure whether a fact is durable and personally meaningful, omit it.
  Fewer, better facts. An empty list is a good answer for a technical note.
- "events" is for a distinct real-world occasion the note documents: a trip,
  celebration, milestone, or one-off gathering. Routine or recurring work
  meetings, standups, syncs, and 1:1 calls are NEVER events. Give it a short
  reusable title (e.g. "Portland Hiking Trip"). Most notes have no event —
  an empty "events" list is the normal answer.
- Set "interacted" to true only when the author directly spoke, met, or
  corresponded with that person in what this note documents. Someone the
  author merely heard about, or whose news arrived secondhand, gets
  "interacted": false even when the facts about them are worth recording.
- Set an "updates" value only when the note states that person's CURRENT
  employer, job role, home city, or email address; otherwise leave it null.
- If the note contains no such facts, return {{"people": []}}.
- The note is dated {source_date}; use that date for facts phrased in the
  present tense.

KNOWN PEOPLE (use these exact names in "match"; aliases in parentheses):
{roster}

NOTE ({source_name}):
{content}
"""

FACT_PREFACE = (
    "This note is a single fact the author deliberately recorded about a "
    "person. Extract it even if it is brief; do not discard it as too short "
    "or too minor.\n\n"
)

# ---------------------------------------------------------------------------
# Config / state
# ---------------------------------------------------------------------------


def load_config():
    config = {
        "TRANSPORT": "local",
        "OMLX_MODEL": "",
        "OMLX_URL": "http://127.0.0.1:8000",
        "OMLX_API_KEY": "",
        "FILING_MODE": "suggest",
        "MAX_PER_RUN": "3",
        "MIN_ROSTER": "1",
        "SELF_NAME": "",
        "SKIP_SOURCE_TITLES": "",
        "IDLE_MINUTES": "0",
        "THINGS_SYNC": "off",
        "THINGS_PROJECT": "Entity Filing",
    }
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config


def load_state():
    """Fail closed like the Granola importer: an unreadable state file must
    pause filing, not silently re-extract (and re-propose) every source."""
    if not os.path.exists(STATE_FILE):
        return {"version": STATE_SCHEMA_VERSION, "processed": {},
                "attempts": {}, "parked": {}}
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"State file {STATE_FILE} is unreadable ({exc}). Filing is paused "
            f"until the file is inspected and repaired or removed."
        ) from exc
    if (
        isinstance(data, dict)
        and data.get("version") == STATE_SCHEMA_VERSION
        and isinstance(data.get("processed"), dict)
    ):
        data.setdefault("attempts", {})
        data.setdefault("parked", {})
        return data
    raise RuntimeError(
        f"State file {STATE_FILE} has an unrecognized schema. Filing is "
        f"paused until the file is inspected and repaired or removed."
    )


def remember_processed(state, source, text, modified=None):
    state["processed"][source["uuid"]] = {
        "modified": modified if modified is not None
        else source.get("modified", ""),
        "hash": hashlib.sha256(text.encode()).hexdigest(),
    }
    state["attempts"].pop(source["uuid"], None)
    state["parked"].pop(source["uuid"], None)


def record_attempt(state, uuid, error):
    entry = state["attempts"].setdefault(uuid, {"count": 0})
    entry["count"] = entry.get("count", 0) + 1
    entry["last_error"] = str(error)[:300]


def rebuild_processed_from_dt(state):
    """Seed processed entries for sources DEVONthink's EntityFiled audit
    flag says were already filed, with v1-migration semantics (processed as
    of now, hash unknown): a fresh or restored machine then re-extracts only
    sources that change afterwards, instead of re-proposing all history.
    Returns the number of entries added."""
    sources = run_bridge([{"op": "list_sources"}])[0]
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    added = 0
    for s in sources:
        if s.get("entityfiled") and s["uuid"] not in state["processed"]:
            state["processed"][s["uuid"]] = {"modified": stamp, "hash": None}
            added += 1
    return added


def source_needs_filing(source, state):
    """Discovery predicate: the source's upstream pipeline is complete and it
    is new, changed since it was last filed, or parked but changed since
    parking. Change detection is DEVONthink's modification date; the scan
    loop still hash-checks before extracting, so a metadata-only touch never
    burns an extraction."""
    if not source.get("ready", True):
        return False
    modified = source.get("modified", "")
    parked = state["parked"].get(source["uuid"])
    if parked is not None:
        return bool(modified) and modified > (parked.get("modified") or "")
    entry = state["processed"].get(source["uuid"])
    if entry is None:
        return True
    return bool(modified) and modified > (entry.get("modified") or "")


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".entity-filing.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def acquire_lock():
    """Hold a non-blocking exclusive lock for the run's lifetime so a
    hand-looped --scan-only never interleaves with the 30-minute launchd
    tick — concurrent runs would duplicate proposals and drop each other's
    processed entries (the state file is last-writer-wins). Returns the open fd
    (kept referenced to hold the lock) or None if another run holds it."""
    os.makedirs(STATE_DIR, exist_ok=True)
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


def acquire_llm_lock():
    """Cross-pipeline lock serializing local inference with journal OCR
    (boox-process.py holds it for its OCR phase), so two ~18 GB models
    are never resident in unified memory at once — the sequential holders
    let oMLX's LRU eviction swap cleanly. Non-blocking: a busy lock defers
    this run's local extraction to the next tick."""
    fd = open(LLM_LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return None
    return fd


# ---------------------------------------------------------------------------
# Bridge / transports
# ---------------------------------------------------------------------------


class BridgeUnavailable(RuntimeError):
    """DEVONthink is not answering or the Lorebook database is not open.

    Transient by nature (app relaunch, database still loading), so callers
    end the run quietly rather than charging a source an attempt for it.
    """


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
            f"bridge op {out.get('failed_op')} failed: {out.get('error')}"
        )
    return out["results"]


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


def memory_pressure_normal():
    """True when macOS reports normal memory pressure (level 1; warn is 2,
    critical 4), so a ~19 GB model load never lands on an already-tight
    machine. Fails open: oMLX's own load-time memory guard is the backstop
    when the probe is unavailable."""
    try:
        out = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            text=True)
        return int(out.strip()) <= 1
    except (OSError, subprocess.SubprocessError, ValueError):
        return True


_availability_cache = {}


def omlx_available(config):
    if "omlx" in _availability_cache:
        return _availability_cache["omlx"]
    ok = False
    if config["OMLX_MODEL"]:
        try:
            req = urllib.request.Request(
                config["OMLX_URL"] + "/v1/models",
                headers=_omlx_headers(config),
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                models = json.load(resp)
            ok = config["OMLX_MODEL"] in {
                m.get("id", "") for m in models.get("data", [])
            }
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            ok = False
    _availability_cache["omlx"] = ok
    return ok


def _omlx_headers(config):
    headers = {"Content-Type": "application/json"}
    if config["OMLX_API_KEY"]:
        headers["Authorization"] = "Bearer " + config["OMLX_API_KEY"]
    return headers


class LLMUnavailable(RuntimeError):
    """oMLX could not serve the request for a reason unrelated to the
    source: connection failure, timeout, or a server-side refusal (5xx or
    429 — including the memory guard declining to load the model under RAM
    pressure). Transient by nature, so callers defer extraction to a later
    tick without charging the source an attempt.
    """


def _http_error_detail(exc):
    try:
        return exc.read().decode("utf-8", "replace")[:300].strip()
    except Exception:
        return str(exc.reason)


def extract_omlx(config, prompt):
    # No response_format: oMLX's strict json_schema decoding degenerates
    # with some models (Qwen3-VL burns the full max_tokens per call and
    # returns an empty object). Free-form decode at temperature 0 yields
    # valid JSON from every model tested, and parse_extraction validates it.
    payload = json.dumps({
        "model": config["OMLX_MODEL"],
        "messages": [
            {"role": "system", "content": CHAT_ROLE},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 4096,
        # Qwen3.5 thinks by default; extraction neither needs nor wants a
        # reasoning phase. Unused by templates without the variable.
        "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(
        config["OMLX_URL"] + "/v1/chat/completions",
        data=payload,
        headers=_omlx_headers(config),
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            out = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == 429:
            raise LLMUnavailable(
                f"HTTP {exc.code}: {_http_error_detail(exc)}") from exc
        raise
    except OSError as exc:
        raise LLMUnavailable(exc) from exc
    return out["choices"][0]["message"]["content"]


def close_orphaned_arrays(text):
    """Insert the `]` a model dropped after an array's last element.

    A colon is only legal after a key in an object, so a colon reached while
    the innermost open container is an array proves that array should have
    closed before the preceding string — `{"people": [{...}, "events": []}`
    is the whole defect. Purely syntactic: it moves a bracket and changes no
    value, and an output it cannot balance still fails to parse.
    """
    inserts = []
    stack = []
    key_start = None
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            key_start = i
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c in "[{":
            stack.append(c)
        elif c in "]}":
            if stack:
                stack.pop()
        elif c == ":" and stack and stack[-1] == "[" and key_start is not None:
            j = key_start - 1
            while j >= 0 and text[j].isspace():
                j -= 1
            # Reuse the comma that separated the array's last element from the
            # orphaned key; an array the model never put an element in has none.
            inserts.append((j, "]") if j >= 0 and text[j] == ","
                           else (key_start, "],"))
            stack.pop()
        i += 1
    for pos, patch in reversed(inserts):
        text = text[:pos] + patch + text[pos:]
    return text


def parse_extraction(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        repaired = close_orphaned_arrays(text)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError:
            raise exc from None
        log.info("repaired %d unclosed array(s) in the model's JSON",
                 repaired.count("]") - text.count("]"))
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        raise ValueError("extraction JSON missing 'people' array")
    events = data.get("events")
    return data["people"], events if isinstance(events, list) else []


# ---------------------------------------------------------------------------
# Matching / ops
# ---------------------------------------------------------------------------


def norm(s):
    """casefold, not lower: only casefold folds the case *pairs* that are not
    one-to-one, so "STRASSE" and "Straße" reach the same key here, in the
    brief's norm, and in the bridge's normName alike."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def norm_email(v):
    """The Email field is url-typed in DT, so a GUI-entered value can carry
    a mailto: prefix even though scripts store bare addresses."""
    return norm(v).removeprefix("mailto:")


def collapse_ws(s):
    """Flattens an embedded newline the LLM sometimes emits mid-sentence,
    which would otherwise split one filed line into two once appended to a
    record body — breaking factSignature dedup, the Things note grammar,
    and the brief's fact-marker identity alike."""
    return re.sub(r"\s+", " ", s).strip()


def self_names(config):
    names = {norm(config["SELF_NAME"])} if config["SELF_NAME"] else set()
    try:
        gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0]
        if gecos:
            names.add(norm(gecos))
            names.add(norm(gecos.split()[0]))
    except Exception:
        pass
    names.discard("")
    return names


def md_flag(value):
    """A flag set by script reads back as '1'; the same flag ticked in
    DEVONthink's Info panel reads back as 'true'. Comparing against either
    alone silently ignores flags set the other way."""
    return str(value or "").strip().lower() in {"1", "true"}


def filing_suppressed(p):
    """FilingSuppressed is a *noise* control, not a privacy one.

    Nothing is ever proposed or written about this person: no fact bullets,
    no field updates, no LastContact bump. It is for someone who saturates
    the sources — a partner, a housemate — where every journal entry mentions
    them and the proposals are all things you already know.

    They stay in the roster the LLM is prompted with, deliberately. Dropping
    them there would not silence them, it would make them *worse*: every
    mention would fail to resolve and come back as a `new` plan proposing to
    create a second record for a person who already has one.

    Their name can still reach a record this flag does not own — an Event's
    `**Who:**` line resolves through the same roster and is filtered here too,
    but an Event's free-text summary is never scrubbed. To keep a name out of
    rendered output, use BriefingSuppressed (dt-morning-brief.py), which is
    the privacy control and redacts free text."""
    return md_flag(p.get("md", {}).get("mdfilingsuppressed", ""))


def roster_index(people):
    index = {}
    for p in people:
        keys = [norm(p["name"])] + [norm(a) for a in p.get("aliases", "").split(",")]
        email = norm_email(p.get("md", {}).get("mdemail", ""))
        if email:
            keys.append(email)
        for k in keys:
            if k:
                index.setdefault(k, []).append(p)
    return index


def resolves_suppressed(name, index):
    hits = index.get(norm(name)) or []
    return len(hits) == 1 and filing_suppressed(hits[0])


def roster_text(people):
    if not people:
        return "(none yet)"
    lines = []
    for p in sorted(people, key=lambda x: x["name"]):
        aliases = p.get("aliases", "").strip()
        lines.append(f"- {p['name']}" + (f" ({aliases})" if aliases else ""))
    return "\n".join(lines)


def valid_date(s):
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return None
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        return None


def source_date_of(source):
    d = valid_date(source.get("eventdate", ""))
    if d:
        return d
    d = valid_date(source.get("name", "")[:10])
    if d:
        return d
    return valid_date(source.get("added", "")) or date.today().isoformat()


def fact_id(d, fact, source_uuid):
    """Deterministic 8-hex handle for one filed assertion. Text-derived on
    purpose: re-filing the identical fact from the same source reuses the
    ID (stateless idempotency), while a rephrased fact gets a new one —
    matching across rephrasings is the reconciliation layer's job, not the
    marker's."""
    return hashlib.sha1(f"{source_uuid}|{d}|{fact}".encode()).hexdigest()[:8]


def fact_line(d, fact, source_uuid):
    fact = fact.rstrip(".") + "."
    return (f"- {d} — {fact} ([source](x-devonthink-item://{source_uuid}))"
            f" <!-- fact:{fact_id(d, fact, source_uuid)} -->")


def near_matches(name, people, limit=3):
    """Roster people sharing a name token with an unmatched extraction —
    surfaces likely alias gaps ("Robert Carter" vs an existing "Bob Carter")
    in the proposal so dedup is a checkbox, not detective work."""
    toks = {t for t in norm(name).split() if len(t) >= 3}
    if not toks:
        return []
    out = []
    for p in people:
        ptoks = set()
        for n in [p["name"]] + p.get("aliases", "").split(","):
            ptoks.update(t for t in norm(n).split() if len(t) >= 3)
        if toks & ptoks:
            out.append(p["name"])
    return out[:limit]


def min_words_for(kind):
    """Fact captures are terse and deliberate; the 20-word scaffolding gate
    that protects meeting/daily extraction would silently drop a one-line
    fact ("Dana Parker moved to Denver")."""
    return 1 if kind == "fact" else 20


def effective_filing_mode(kind, configured):
    """Hand-authored fact captures auto-file (subject to the verbatim-name
    guard in file_source); every other kind honors the configured mode."""
    return "auto" if kind == "fact" else configured


def name_in_text(name, text):
    """True when `name` appears in `text` as a whole-token run, case- and
    diacritic-insensitively (both sides folded through norm)."""
    n = norm(name)
    if not n:
        return False
    return re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", norm(text)) is not None


def strip_leading_h1(text):
    """Drop a fact capture's leading `# <title>` line (present so the global
    H1-sync smart rule no-ops) before extraction, so the model sees only the
    fact text."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        return "\n".join(lines[i:])
    return text


def fact_match_is_strong(plan, text):
    """A fact plan may auto-apply only when the resolved person is named
    unambiguously in the capture: their full name or a multi-word alias
    appears verbatim. A bare first name — even one the model expanded to a
    full name in `match` — stays a proposal, since single first names
    collide silently as the roster grows."""
    if plan.get("weak_match"):
        return False
    candidates = [plan.get("name", "")]
    candidates += (plan.get("aliases") or "").split(",")
    return any(len(c.split()) >= 2 and name_in_text(c, text) for c in candidates)


def build_person_plans(extracted, index, selves, people, source_date):
    """Deterministic resolution: LLM output in, per-person op plans out."""
    plans = []
    suppressed = 0
    for person in extracted:
        name = str(person.get("name", "")).strip()
        if not name or norm(name) in selves:
            continue
        facts = []
        for f in (person.get("facts") or [])[:12]:
            text = collapse_ws(str(f.get("fact", "")))
            if text and len(text) <= 400:
                facts.append((valid_date(f.get("date")) or source_date, text))
        updates = {}
        for field in UPDATE_FIELDS:
            v = (person.get("updates") or {}).get(field)
            if isinstance(v, str) and v.strip():
                updates[field] = collapse_ws(v)
        if not facts and not updates:
            continue

        interacted = bool(person.get("interacted"))
        claimed = str(person.get("match") or "").strip()
        matched_key = ""
        hits = []
        for cand in (claimed, name):
            k = norm(cand)
            if k and index.get(k):
                matched_key, hits = k, index[k]
                break
        if len(hits) == 1:
            if filing_suppressed(hits[0]):
                suppressed += 1
                continue
            plans.append({
                "kind": "existing",
                "name": hits[0]["name"],
                "uuid": hits[0]["uuid"],
                "md": hits[0].get("md", {}),
                # Resolved only through a single-token alias ("maya" ->
                # Maya Chen): too weak to auto-apply — common first names
                # collide silently as the roster grows.
                "weak_match": len(matched_key.split()) < 2
                and matched_key != norm(hits[0]["name"]),
                "aliases": hits[0].get("aliases", ""),
                "interacted": interacted,
                "facts": facts,
                "updates": updates,
            })
        elif len(hits) > 1:
            plans.append({
                "kind": "ambiguous",
                "name": name,
                "candidates": [h["name"] for h in hits],
                "interacted": interacted,
                "facts": facts,
                "updates": updates,
            })
        else:
            plans.append({
                "kind": "new",
                "name": name,
                "single_token": len(name.split()) < 2,
                "near": near_matches(name, people),
                "interacted": interacted,
                "facts": facts,
                "updates": updates,
            })
    if suppressed:
        log.info("dropped %d extracted person(s) (FilingSuppressed)", suppressed)
    return plans


def build_event_plans(events_raw, index, selves, source_date):
    plans = []
    for ev in events_raw[:4]:
        name = str(ev.get("name", "")).strip()
        if not name or len(name) > 80:
            continue
        attendees = []
        for a in (ev.get("attendees") or [])[:20]:
            a = str(a).strip()
            if not a or norm(a) in selves or a in attendees:
                continue
            if resolves_suppressed(a, index):
                continue
            attendees.append(a)
        summary = collapse_ws(str(ev.get("summary") or ""))[:300]
        plans.append({
            "kind": "event",
            "name": name,
            "date": valid_date(ev.get("date")) or source_date,
            "location": str(ev.get("location") or "").strip()[:80],
            "attendees": attendees,
            "summary": summary,
        })
    return plans


def ops_for_plan(plan, source, source_date):
    src = source["uuid"]
    if plan["kind"] == "event":
        op = {"op": "ensure_event", "name": plan["name"],
              "date": plan["date"], "location": plan["location"],
              "attendees": plan["attendees"], "summary": plan["summary"],
              "source_uuid": src}
        if plan["summary"]:
            op["log_line"] = fact_line(plan["date"], plan["summary"], src)
        return [op]
    lines = [fact_line(d, fact, src) for d, fact in plan["facts"]]
    ops = []
    if plan["kind"] == "existing":
        for field, value in plan["updates"].items():
            previous = str(plan["md"].get("md" + field, "") or "")
            if norm(previous) == norm(value):
                continue
            op = {"op": "set_field", "uuid": plan["uuid"],
                  "field": field, "value": value,
                  "effective_date": source_date,
                  "expected_previous": previous}
            if previous:
                # Rides inside set_field so a refused stale write never
                # files its own transition line.
                op["transition_line"] = fact_line(
                    source_date,
                    f"{field.capitalize()}: {previous} → {value}", src)
            ops.append(op)
        if lines:
            ops.append({"op": "append_log", "uuid": plan["uuid"], "lines": lines})
        # A typed fact is knowledge, not contact evidence — the calendar and
        # Messages passes own LastContact; a fact must not move that clock.
        if plan.get("interacted") and source.get("kind") != "fact":
            ops.append({"op": "bump_lastcontact", "uuid": plan["uuid"],
                        "date": source_date})
    else:
        fields = dict(plan["updates"])
        if plan.get("interacted") and source.get("kind") != "fact":
            fields["lastcontact"] = source_date
        ops.append({"op": "ensure_person", "name": plan["name"],
                    "fields": fields, "log_lines": lines})
    return ops


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def proposal_body(source, source_date, plans, ops):
    lines = [
        f"# File: {source['name']}",
        "",
        f"Source: [{source['name']}](x-devonthink-item://{source['uuid']})"
        f" ({source_date})",
        "",
        "Move this record into `20_ENTITIES/_Review/Approved` to apply it on"
        " the next filing run, or delete it to reject.",
        "",
        "## Proposed",
        "",
    ]
    for plan in plans:
        if plan["kind"] == "event":
            who = ", ".join(plan["attendees"]) or "—"
            where = f" at {plan['location']}" if plan["location"] else ""
            lines.append(f"- **EVENT: {plan['name']}** ({plan['date']}{where})"
                         f" — who: {who}")
            if plan["summary"]:
                lines.append(f"  - {plan['summary']}")
        elif plan["kind"] == "existing":
            contact = ", direct interaction" if plan.get("interacted") else ""
            weak = (" — matched via single-token alias, verify before"
                    " approving") if plan.get("weak_match") else ""
            lines.append(f"- **{plan['name']}** (existing record{contact}){weak}")
        elif plan["kind"] == "ambiguous":
            cands = ", ".join(plan["candidates"])
            lines.append(f"- **{plan['name']}** — AMBIGUOUS: matches {cands};"
                         " edit the ops JSON before approving")
        else:
            # Unreachable in normal flow — new people divert to candidates —
            # but a plan that slips through must render, not vanish.
            lines.append(f"- **{plan['name']}** (new Person record)")
        for d, fact in plan.get("facts", []):
            lines.append(f"  - {d} — {fact}")
        for field, value in plan.get("updates", {}).items():
            lines.append(f"  - {field} = {value}")
    lines += ["", "## Ops", "", "```json", json.dumps(ops, indent=2), "```", ""]
    return "\n".join(lines)


def fallback_review_body(source, source_date, text):
    """Review stub for a fact capture the model extracted nothing from. The
    empty ops fence makes approving it a harmless no-op that just clears it;
    the intended moves are to file the fact by hand or delete the stub."""
    return "\n".join([
        f"# Review capture: {source['name']}",
        "",
        f"Source: [{source['name']}](x-devonthink-item://{source['uuid']})"
        f" ({source_date})",
        "",
        "The local model found no filable fact in this capture. File it by "
        "hand (add a bullet to the right Person's `## Biographical Log`), or "
        "delete this to discard. Approving as-is applies nothing and clears it.",
        "",
        "## Captured text",
        "",
        text.strip(),
        "",
        "## Ops",
        "",
        "```json",
        "[]",
        "```",
        "",
    ])


def stale_person_ops(ops, index, people):
    """`ensure_person` ops whose name no longer resolves the way the proposal
    assumed. The ops are frozen when the proposal is written, but the roster
    keeps growing: a proposal that says `ensure_person "Alison"` because People
    was empty at extraction time would, once "Alison Vance" is seeded, match
    nothing and quietly create a second record. `ensure_person` resolves on
    exact name/alias only, so a shared name token is the signal. A name
    matching two or more roster records is stale too, regardless of
    `confirm_new`: the bridge's ensure_person throws on multiple hits and
    never reads that flag. Returns [(name, [near matches])]; `"confirm_new":
    true` in an op opts out of the single near-match case only."""
    stale = []
    for op in ops:
        if op.get("op") != "ensure_person":
            continue
        name = str(op.get("name", "")).strip()
        if not name:
            continue
        hits = index.get(norm(name)) or []
        if len(hits) > 1:
            stale.append((name, [h["name"] for h in hits]))
            continue
        if op.get("confirm_new") or hits:
            continue
        near = near_matches(name, people)
        if near:
            stale.append((name, near))
    return stale


def proposal_ops(text):
    """Ops list from the last ```json fence of a proposal body, or None when
    no fence exists. Line endings are normalized first: proposals are meant
    to be hand-edited before approval, and DEVONthink's editor saves
    markdown with classic-Mac \\r endings. Raises ValueError (including
    json.JSONDecodeError) when the fence isn't a JSON array of objects."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if not blocks:
        return None
    ops = json.loads(blocks[-1])
    if not isinstance(ops, list) or not all(isinstance(o, dict) for o in ops):
        raise ValueError("ops are not a JSON array of objects")
    return ops


def ignored_conflicts(ops, index, ignored):
    """Names an approved proposal's frozen ops would file that the user has
    since Ignored. Roster-first, exactly as during extraction: a name that
    now resolves is the roster's to handle (stale_person_ops converts it),
    whatever the Ignored group says — only zero-hit names are checked."""
    conflicts = []
    for op in ops:
        if op.get("op") == "ensure_person":
            name = str(op.get("name", "")).strip()
            if name and not index.get(norm(name)) and norm(name) in ignored:
                conflicts.append(name)
        elif op.get("op") == "ensure_event":
            for a in op.get("attendees") or []:
                a = str(a).strip()
                if a and not index.get(norm(a)) and norm(a) in ignored:
                    conflicts.append(a)
    return conflicts


def apply_approved(dry_run):
    approved = run_bridge([{"op": "list_group", "path": APPROVED_PATH}])[0]
    if not approved:
        return
    people, listing = run_bridge([
        {"op": "dump_people", "include_bodies": False},
        {"op": "list_candidates"},
    ])
    index = roster_index(people)
    ignored = ec.CandidateIndex(listing).ignored_names()
    for rec in approved:
        text = run_bridge([{"op": "get_text", "uuid": rec["uuid"]}])[0]["text"]
        try:
            ops = proposal_ops(text)
        except ValueError as exc:
            log.error("approved proposal has invalid ops JSON (%s), skipping",
                      exc, extra={"record_name": rec["name"],
                                  "record_uuid": rec["uuid"]})
            continue
        if ops is None:
            log.warning("approved proposal has no ops block, skipping",
                        extra={"record_name": rec["name"],
                               "record_uuid": rec["uuid"]})
            continue
        conflicts = ignored_conflicts(ops, index, ignored)
        if conflicts:
            log.warning(
                "approved proposal names Ignored candidate(s) %s; bouncing to "
                "_Review for re-approval — edit the ops or un-ignore the "
                "candidate, then re-approve. The approved fence is exactly "
                "what runs; nothing was filtered silently",
                ", ".join(repr(c) for c in conflicts),
                extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})
            if not dry_run:
                run_bridge([{"op": "move_to", "uuid": rec["uuid"],
                             "group": REVIEW_PATH}])
            continue
        stale = stale_person_ops(ops, index, people)
        if stale:
            for name, near in stale:
                log.warning(
                    'proposal would create "%s" alongside %s — add "%s" as an '
                    'alias on the existing record, or set "confirm_new": true '
                    "in the op to create a separate person; leaving in Approved",
                    name, ", ".join(near), name,
                    extra={"record_name": rec["name"],
                           "record_uuid": rec["uuid"]})
            continue
        if dry_run:
            log.info("[dry-run] would apply %d ops from %s", len(ops), rec["name"])
            continue
        try:
            results = run_bridge(ops + [{"op": "trash", "uuid": rec["uuid"]}])
        except BridgeUnavailable:
            raise
        except Exception as exc:
            log.error("applying proposal failed (%s), leaving in Approved: %s",
                      type(exc).__name__, exc,
                      extra={"record_name": rec["name"],
                             "record_uuid": rec["uuid"]})
            continue
        for op, res in zip(ops, results):
            if op.get("op") == "set_field" and isinstance(res, dict) \
                    and res.get("stale"):
                log.warning(
                    "approved %s update refused as stale (current value %r "
                    "is newer than this proposal); update the record by hand "
                    "if the proposal was right",
                    op.get("field"), res.get("previous"),
                    extra={"record_name": rec["name"],
                           "record_uuid": rec["uuid"]})
        log.info("applied %d ops", len(ops),
                 extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})
        if any(op.get("op") == "ensure_person" for op in ops):
            people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
            index = roster_index(people)


# ---------------------------------------------------------------------------
# Candidate promotion
# ---------------------------------------------------------------------------


def promotion_target(data, md, people, index):
    """Identifier-wide preflight: (person_uuid_or_None, bounce_reason,
    near). No mutation happens here — every collision is caught before
    ensure_person could create anything. A None uuid with no reason means
    "create a new Person"."""
    identifiers = [data["name"]] + list(data["name_variants"])
    keys = {norm(i) for i in identifiers if norm(i)}
    keys.update(e for e in data["emails"] if e)
    hits = {}
    for key in keys:
        for p in index.get(key, []):
            hits[p["uuid"]] = p
    near = ec.near_matches(data["name"], people)
    track_target = str(md.get("mdtracktarget", "") or "").strip()
    create_distinct = md_flag(md.get("mdcreatedistinct", ""))
    if track_target:
        if not any(p["uuid"] == track_target for p in people):
            return None, (f"TrackTarget {track_target!r} is not a Person "
                          "record UUID"), near
        others = [p["name"] for u, p in hits.items() if u != track_target]
        if others:
            return None, ("alias collision: this candidate's identifiers "
                          "already resolve to " + ", ".join(others)), near
        return track_target, None, near
    if len(hits) > 1:
        return None, ("identifiers resolve to multiple people ("
                      + ", ".join(p["name"] for p in hits.values())
                      + ") — set TrackTarget to choose"), near
    if len(hits) == 1:
        return next(iter(hits)), None, near
    if ec.needs_confirmation(data, near) and not create_distinct:
        reason = ("possible existing records: " + ", ".join(near)
                  if near else "single-word name")
        return None, (f"approval needs confirmation ({reason}) — set "
                      "TrackTarget to file into an existing person or "
                      "CreateDistinct to confirm a new one"), near
    return None, None, near


def promotion_evidence_ops(data, target_uuid, current_md):
    """Evidence ops for an already-resolved Person UUID: chronological
    set_field through the FieldAsOf guard, one append_log of provenance-
    carrying fact lines, and a LastContact bump from the latest interacted
    non-fact sighting."""
    ops = []
    sightings = sorted(data["sightings"].items(),
                       key=lambda kv: (kv[1].get("date", ""), kv[0]))
    first_write = set()
    log_lines = []
    last_contact = ""
    for sid, s in sightings:
        d = valid_date(s.get("date", "")) or date.today().isoformat()
        for field in UPDATE_FIELDS:
            value = (s.get("updates") or {}).get(field)
            # A calendar sighting observes the email outside `updates`; it
            # must still reach the Person, not just the candidate's key set.
            if field == "email" and not value:
                value = s.get("email")
            if not isinstance(value, str) or not value.strip():
                continue
            op = {"op": "set_field", "uuid": target_uuid, "field": field,
                  "value": collapse_ws(value), "effective_date": d}
            if field not in first_write:
                first_write.add(field)
                op["expected_previous"] = str(
                    current_md.get("md" + field, "") or "")
            ops.append(op)
        if sid.startswith("dt:"):
            src = sid[3:]
            for fd, fact in (s.get("facts") or []):
                log_lines.append(
                    fact_line(valid_date(fd) or d, str(fact), src))
        if s.get("interacted") and s.get("kind") != "fact" and d > last_contact:
            last_contact = d
    if log_lines:
        ops.append({"op": "append_log", "uuid": target_uuid,
                    "lines": log_lines})
    if last_contact:
        ops.append({"op": "bump_lastcontact", "uuid": target_uuid,
                    "date": last_contact})
    return ops


def bounce_candidate(rec, data, reason, near, dry_run):
    if dry_run:
        log.info("[dry-run] would bounce candidate %r: %s", data["name"],
                 reason)
        return
    run_bridge([
        {"op": "set_text", "uuid": rec["uuid"],
         "text": ec.render_candidate(data, near, notice=reason)},
        {"op": "move_to", "uuid": rec["uuid"], "group": ec.CANDIDATES_PATH},
    ])
    log.warning("candidate not promoted: %s", reason,
                extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})


def promote_candidates(dry_run):
    """Promote every record in _Candidates/Approved via the staged,
    crash-resumable protocol: preflight (no mutation), resolve-or-create the
    target, alias completion, guarded evidence ops, then trash the
    candidate. The candidate stays in Approved until the final step, so a
    crash anywhere re-runs the whole protocol idempotently (ensure_person
    re-resolves the created name; fact dedup, FieldAsOf, and the LastContact
    guard absorb the replays)."""
    approved = run_bridge([{"op": "list_candidates"}])[0]["approved"]
    if not approved:
        return
    for stale_rec in approved:
        lock = ec.acquire_candidates_lock()
        try:
            listing, people = run_bridge([
                {"op": "list_candidates"},
                {"op": "dump_people", "include_bodies": False},
            ])
            rec = next((r for r in listing["approved"]
                        if r["uuid"] == stale_rec["uuid"]), None)
            if rec is None:
                continue
            if rec["name"].startswith(ec.QUARANTINE_PREFIX):
                continue
            try:
                data = ec.parse_candidate(rec["text"])
            except ValueError as exc:
                log.warning("approved candidate has an unreadable data fence "
                            "(%s); quarantining", exc,
                            extra={"record_name": rec["name"],
                                   "record_uuid": rec["uuid"]})
                if not dry_run:
                    run_bridge(ec.quarantine_ops(
                        [(rec["uuid"], rec["name"], str(exc))]))
                continue
            index = roster_index(people)
            target, reason, near = promotion_target(
                data, rec.get("md", {}), people, index)
            if reason:
                bounce_candidate(rec, data, reason, near, dry_run)
                continue
            if dry_run:
                log.info("[dry-run] would promote %r into %s", data["name"],
                         target or "a new Person record")
                continue
            variants = [data["name"]] + list(data["name_variants"])
            if target is None:
                aliases = [v for v in data["name_variants"]
                           if norm(v) != norm(data["name"])]
                created = run_bridge([{"op": "ensure_person",
                                       "name": data["name"],
                                       "aliases": ", ".join(aliases)}])[0]
                target = created["uuid"]
                current_md = {}
            else:
                person = next(p for p in people if p["uuid"] == target)
                covered = {norm(person["name"])}
                covered.update(norm(a)
                               for a in person.get("aliases", "").split(","))
                missing = [v for v in variants
                           if norm(v) and norm(v) not in covered]
                if missing:
                    run_bridge([{"op": "add_aliases", "uuid": target,
                                 "aliases": ", ".join(missing)}])
                current_md = person.get("md", {})
            ops = promotion_evidence_ops(data, target, current_md)
            ops.append({"op": "trash", "uuid": rec["uuid"]})
            results = run_bridge(ops)
            for op, res in zip(ops, results):
                if op["op"] == "set_field" and isinstance(res, dict) \
                        and res.get("stale"):
                    log.info("stale %s update refused during promotion "
                             "(current value %r is newer)", op["field"],
                             res.get("previous"),
                             extra={"record_name": rec["name"],
                                    "record_uuid": rec["uuid"]})
            log.info("promoted candidate %r into person %s (%d sighting(s))",
                     data["name"], target, len(data["sightings"]),
                     extra={"record_name": rec["name"],
                            "record_uuid": rec["uuid"]})
        finally:
            lock.close()


def _find_candidate(listing, uuid):
    for group in ("pending", "approved", "ignored"):
        for rec in listing[group]:
            if rec["uuid"] == uuid:
                return rec, group
    return None, None


def split_candidate(cand_uuid, sids, dry_run):
    """Move the named sightings into a new Pending candidate. The original
    keeps its keys (and every future automatic sighting); the split-off half
    derives keys only from its own sightings' emails, so an email-less
    split-off is created detached and must be decided by hand."""
    lock = ec.acquire_candidates_lock()
    try:
        listing, people = run_bridge([
            {"op": "list_candidates"},
            {"op": "dump_people", "include_bodies": False},
        ])
        rec, group = _find_candidate(listing, cand_uuid)
        if rec is None:
            log.error("--split-candidate: no candidate record %s", cand_uuid)
            return 1
        data = ec.parse_candidate(rec["text"])
        sids = list(dict.fromkeys(sids))
        missing = [s for s in sids if s not in data["sightings"]]
        if missing:
            log.error("--split-candidate: sighting id(s) not on %r: %s",
                      data["name"], ", ".join(missing))
            return 1
        if len(sids) >= len(data["sightings"]):
            log.error("--split-candidate: refusing to move every sighting — "
                      "that is a rename, not a split")
            return 1
        moved = {s: data["sightings"].pop(s) for s in sids}
        ec.recompute_derived(data)
        new_data = ec.new_candidate(
            next(iter(moved.values())).get("person") or data["name"])
        new_data["sightings"] = moved
        ec.recompute_derived(new_data)
        if not new_data["emails"]:
            new_data["detached"] = True
        if dry_run:
            log.info("[dry-run] would split %d sighting(s) off %r into %r%s",
                     len(moved), data["name"], new_data["name"],
                     " (detached)" if new_data["detached"] else "")
            return 0
        near = ec.near_matches(new_data["name"], people)
        run_bridge([
            {"op": "set_text", "uuid": rec["uuid"],
             "text": ec.render_candidate(
                 data, ec.near_matches(data["name"], people))},
            {"op": "create_record",
             "name": ec.record_name(new_data),
             "path": ec.CANDIDATES_PATH,
             "text": ec.render_candidate(new_data, near),
             "fields": {"entitytype": "Candidate"}},
        ])
        log.info("split %d sighting(s) off %r into new candidate %r%s",
                 len(moved), data["name"], new_data["name"],
                 " (detached)" if new_data["detached"] else "",
                 extra={"record_name": rec["name"], "record_uuid": cand_uuid})
        return 0
    finally:
        lock.close()


def merge_candidates(a_uuid, b_uuid, dry_run):
    """Fold candidate b's sightings into candidate a and trash b — the
    manual convergence for one human the conservative key policy split
    across two records. a's sighting wins a shared sighting id (the two
    halves of an earlier split re-merging)."""
    lock = ec.acquire_candidates_lock()
    try:
        listing, people = run_bridge([
            {"op": "list_candidates"},
            {"op": "dump_people", "include_bodies": False},
        ])
        a_rec, _a_group = _find_candidate(listing, a_uuid)
        b_rec, _b_group = _find_candidate(listing, b_uuid)
        if a_rec is None or b_rec is None:
            log.error("--merge-candidates: no candidate record %s",
                      a_uuid if a_rec is None else b_uuid)
            return 1
        a = ec.parse_candidate(a_rec["text"])
        b = ec.parse_candidate(b_rec["text"])
        for sid, s in b["sightings"].items():
            a["sightings"].setdefault(sid, s)
        ec.recompute_derived(a)
        for v in [b["name"]] + b["name_variants"]:
            ec.add_variant(a, v)
        for e in b["emails"]:
            ec.add_email(a, e)
        if dry_run:
            log.info("[dry-run] would merge %r (%d sighting(s)) into %r",
                     b["name"], len(b["sightings"]), a["name"])
            return 0
        run_bridge([
            {"op": "set_text", "uuid": a_uuid,
             "text": ec.render_candidate(
                 a, ec.near_matches(a["name"], people))},
            {"op": "trash", "uuid": b_uuid},
        ])
        log.info("merged candidate %r into %r (%d sighting(s) total)",
                 b["name"], a["name"], len(a["sightings"]),
                 extra={"record_name": a_rec["name"], "record_uuid": a_uuid})
        return 0
    finally:
        lock.close()


# ---------------------------------------------------------------------------
# Things review loop — note spec
# ---------------------------------------------------------------------------


class SpecParseError(ValueError):
    """An edited Things note no longer matches the spec grammar. Always
    bounced back to the user verbatim — the parser never guesses."""


SPEC_KINDS = {"existing", "new", "ambiguous"}
SPEC_FACT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(?:—|–|--|-)\s+(.+)$")
SPEC_PAREN_RE = re.compile(r"^(.*)\s*\(([^()]*)\)$")
SPEC_EVENT_RE = re.compile(r"^(.*)\s+\((\d{4}-\d{2}-\d{2})( at (.*))?\)$")
SPEC_FIELD_RE = re.compile(r"^([A-Za-z]+)\s*=\s*(.+)$")
TASK_MARKER_RE = re.compile(re.escape(PROPOSAL_MARKER) + r"([A-Za-z0-9-]+)")


def things_note_body(source_uuid, proposal_uuid, plans):
    lines = [
        "Review, edit if needed, then complete this to-do to file it.",
        "Delete a line to drop it. Cancel or delete the to-do to reject.",
        f"Source: x-devonthink-item://{source_uuid}",
        f"{PROPOSAL_MARKER}{proposal_uuid}",
        "",
        SPEC_SENTINEL,
    ]
    for plan in plans:
        if plan["kind"] == "event":
            where = f" at {plan['location']}" if plan["location"] else ""
            lines.append(f"EVENT {plan['name']} ({plan['date']}{where})")
            if plan["attendees"]:
                lines.append("- with: " + ", ".join(plan["attendees"]))
            if plan["summary"]:
                lines.append(f"- {plan['date']} — {plan['summary']}")
        else:
            kind = plan["kind"] if plan["kind"] in SPEC_KINDS else "new"
            met = ", met" if plan.get("interacted") else ""
            lines.append(f"PERSON {plan['name']} ({kind}{met})")
            for d, fact in plan.get("facts", []):
                lines.append(f"- {d} — {fact}")
            for field, value in plan.get("updates", {}).items():
                lines.append(f"- {field} = {value}")
    return "\n".join(lines)


def things_note_stub(source_uuid, proposal_uuid):
    lines = [
        "This proposal can't be edited here — review it in DEVONthink, then",
        "complete this to-do to apply it as written, or cancel to reject.",
    ]
    if source_uuid:
        lines.append(f"Source: x-devonthink-item://{source_uuid}")
    lines.append(f"{PROPOSAL_MARKER}{proposal_uuid}")
    return "\n".join(lines)


def _spec_fact(body, facts):
    m = SPEC_FACT_RE.match(body)
    if m:
        if not valid_date(m.group(1)):
            raise SpecParseError(f"invalid date in line: {body!r}")
        text = m.group(2).strip()
        if len(text) > 400:
            raise SpecParseError(f"fact too long (>400 chars): {text[:60]!r}…")
        facts.append({"date": m.group(1), "fact": text})
        return True
    return False


def parse_things_note(text):
    """(people, events) extraction-shaped dicts from an edited task note.

    The grammar is deliberately strict: every line below the sentinel must
    parse exactly, and structural limits the plan builders would otherwise
    enforce by *silently dropping* content (fact length, event name length,
    attendee count) are hard errors here instead — an edit must never be
    half-applied.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    sentinels = [i for i, line in enumerate(lines) if line.strip() == SPEC_SENTINEL]
    if len(sentinels) != 1:
        raise SpecParseError(
            f"expected exactly one {SPEC_SENTINEL!r} line, found {len(sentinels)}")

    people, events = [], []
    current = None
    for line in lines[sentinels[0] + 1:]:
        if any(ord(c) < 32 and c != "\t" for c in line):
            raise SpecParseError("line contains control characters")
        if not line.strip():
            continue
        if line.startswith("PERSON "):
            rest = line[len("PERSON "):].strip()
            met = False
            m = SPEC_PAREN_RE.match(rest)
            if m:
                tokens = [t.strip().lower() for t in m.group(2).split(",") if t.strip()]
                if tokens and all(t in SPEC_KINDS or t == "met" for t in tokens):
                    rest = m.group(1).strip()
                    met = "met" in tokens
            if not rest:
                raise SpecParseError(f"PERSON line has no name: {line!r}")
            if any(norm(p["name"]) == norm(rest) for p in people):
                raise SpecParseError(f"duplicate person: {rest!r}")
            current = {"name": rest, "match": None, "interacted": met,
                       "facts": [], "updates": {}}
            people.append(current)
        elif line.startswith("EVENT "):
            rest = line[len("EVENT "):].strip()
            m = SPEC_EVENT_RE.match(rest)
            if not m or not valid_date(m.group(2)):
                raise SpecParseError(
                    f"EVENT line must end with (YYYY-MM-DD[ at Location]): {line!r}")
            name = m.group(1).strip()
            if not name:
                raise SpecParseError(f"EVENT line has no name: {line!r}")
            if len(name) > 80:
                raise SpecParseError(f"event name too long (>80 chars): {name!r}")
            if any(norm(e["name"]) == norm(name) and e["date"] == m.group(2)
                   for e in events):
                raise SpecParseError(f"duplicate event: {name!r}")
            current = {"name": name, "date": m.group(2),
                       "location": (m.group(4) or "").strip() or None,
                       "attendees": [], "summary": None}
            events.append(current)
        elif line.startswith("- "):
            body = line[2:].strip()
            if current is None:
                raise SpecParseError(f"line belongs to no PERSON/EVENT: {line!r}")
            if "facts" in current:
                facts = []
                if _spec_fact(body, facts):
                    current["facts"].extend(facts)
                    if len(current["facts"]) > 12:
                        raise SpecParseError(
                            f"more than 12 facts for {current['name']!r}")
                    continue
                m = SPEC_FIELD_RE.match(body)
                if m and m.group(1).lower() in UPDATE_FIELDS:
                    field = m.group(1).lower()
                    if field in current["updates"]:
                        raise SpecParseError(
                            f"duplicate {field!r} for {current['name']!r}")
                    current["updates"][field] = m.group(2).strip()
                    continue
                raise SpecParseError(f"unparseable line: {line!r}")
            else:
                if body.startswith("with:"):
                    if current["attendees"]:
                        raise SpecParseError(
                            f"duplicate with: line for {current['name']!r}")
                    names = [a.strip() for a in body[len("with:"):].split(",")
                             if a.strip()]
                    if len(names) > 20:
                        raise SpecParseError(
                            f"more than 20 attendees for {current['name']!r}")
                    current["attendees"] = names
                    continue
                facts = []
                if _spec_fact(body, facts):
                    if current["summary"] is not None:
                        raise SpecParseError(
                            f"more than one summary line for {current['name']!r}")
                    if len(facts[0]["fact"]) > 300:
                        raise SpecParseError(
                            f"event summary too long (>300 chars) for "
                            f"{current['name']!r}")
                    current["summary"] = facts[0]["fact"]
                    continue
                raise SpecParseError(f"unparseable line: {line!r}")
        else:
            raise SpecParseError(f"unparseable line: {line!r}")

    if not people and not events:
        raise SpecParseError("no PERSON or EVENT entries below the sentinel")
    return people, events


# ---------------------------------------------------------------------------
# Things review loop — ops inversion / state
# ---------------------------------------------------------------------------


FACT_LINE_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) — (.+?) "
    r"\(\[source\]\(x-devonthink-item://[^)]*\)\) <!-- fact:[0-9a-f]{8} -->$")


def plans_from_ops(ops, people):
    """Plan dicts back out of a proposal's ops fence, for rendering the
    Things note spec. Returns (plans, editable, source_uuid): editable goes
    False when any op can't be inverted faithfully (hand-edited fact line,
    uuid gone from the roster, unknown op) — the task is then created
    edit-disabled and completing it applies the frozen ops unchanged.
    """
    by_uuid = {p["uuid"]: p for p in people}
    plans, order = {}, []
    editable = True
    source_uuid = None

    def plan_for(key, name, kind):
        if key not in plans:
            plans[key] = {"kind": kind, "name": name, "interacted": False,
                          "facts": [], "updates": {}}
            order.append(key)
        return plans[key]

    def take_facts(plan, fact_lines):
        nonlocal editable
        for line in fact_lines or []:
            m = FACT_LINE_RE.match(line)
            if m:
                plan["facts"].append((m.group(1), m.group(2)))
            else:
                editable = False

    for op in ops:
        name = op.get("op")
        if name == "mark_filed":
            source_uuid = op.get("uuid")
        elif name == "ensure_person":
            person = str(op.get("name", "")).strip()
            plan = plan_for("name:" + norm(person), person, "new")
            fields = dict(op.get("fields") or {})
            if fields.pop("lastcontact", None):
                plan["interacted"] = True
            for field, value in fields.items():
                if field in UPDATE_FIELDS:
                    plan["updates"][field] = value
                else:
                    editable = False
            take_facts(plan, op.get("log_lines"))
        elif name in ("append_log", "set_field", "bump_lastcontact"):
            person = by_uuid.get(op.get("uuid"))
            if person is None:
                editable = False
                continue
            plan = plan_for("uuid:" + op["uuid"], person["name"], "existing")
            if name == "append_log":
                take_facts(plan, op.get("lines"))
            elif name == "set_field":
                if op.get("field") in UPDATE_FIELDS:
                    plan["updates"][op["field"]] = op.get("value")
                else:
                    editable = False
            else:
                plan["interacted"] = True
        elif name == "ensure_event":
            key = "event:" + norm(str(op.get("name", ""))) + "|" + str(op.get("date", ""))
            plans[key] = {"kind": "event", "name": str(op.get("name", "")),
                          "date": str(op.get("date", "")),
                          "location": str(op.get("location") or ""),
                          "attendees": list(op.get("attendees") or []),
                          "summary": str(op.get("summary") or "")}
            if key not in order:
                order.append(key)
        else:
            editable = False
    return [plans[k] for k in order], editable, source_uuid


def ops_hash(ops):
    return hashlib.sha256(
        json.dumps(ops, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def settle_snapshot(row):
    """What must hold still across two ticks before a terminal task state is
    acted on: Things Cloud can deliver a completion before the final notes
    revision from another device, and acting immediately would file stale
    content."""
    return {"status": row["status"], "trashed": row["trashed"],
            "notes_sha": hashlib.sha256((row["notes"] or "").encode()).hexdigest(),
            "mod": row["userModificationDate"]}


def proposal_uuid_from_notes(notes):
    m = TASK_MARKER_RE.search(notes or "")
    return m.group(1) if m else None


def things_note_is_editable(notes):
    return SPEC_SENTINEL in (notes or "")


def strip_banner(notes):
    lines = notes.split("\n")
    while lines and lines[0].startswith(BANNER_PREFIX):
        lines.pop(0)
        if lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines)


def load_things_map(path=None):
    """None means the map is unusable; the Things phases skip this run.
    Recovery is moving the file aside — the marker in every task's notes
    lets the next run rebuild the map from Things itself."""
    path = path or THINGS_MAP_FILE
    if not os.path.exists(path):
        return {"version": THINGS_MAP_VERSION, "project_uuid": None, "tasks": {}}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Things map %s is unreadable (%s); move it aside to rebuild",
                    path, exc)
        return None
    if not (isinstance(data, dict) and data.get("version") == THINGS_MAP_VERSION
            and isinstance(data.get("tasks"), dict)):
        log.warning("Things map %s has an unrecognized schema; move it aside "
                    "to rebuild", path)
        return None
    data.setdefault("project_uuid", None)
    return data


def save_things_map(m, path=None):
    path = path or THINGS_MAP_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=".things-map.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(m, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Things review loop — phases
# ---------------------------------------------------------------------------


def _mirror_lost(config, m, dry_run, path=None, what="proposal(s)"):
    """True when the mirror project itself was deleted or completed out from
    under mapped tasks — a project-level event, not N individual decisions.
    Callers must not act on any mapped task this tick when this fires; the
    caller that should recover (things_reconcile) resolves a fresh project
    on its next _things_project call once the stale mapping is cleared."""
    puuid = m.get("project_uuid")
    if not puuid or things_bridge.project_alive(puuid) or not m["tasks"]:
        return False
    log.warning("Things project %r is gone or completed while %d mirrored "
                "%s still existed; treating this as a lost mirror "
                "rather than acting on each task — dropping the stale "
                "mapping so it re-mirrors into a fresh project",
                config["THINGS_PROJECT"], len(m["tasks"]), what)
    if dry_run:
        return True
    m["tasks"] = {}
    m["project_uuid"] = None
    save_things_map(m, path)
    return True


def _things_project(config, m, dry_run, path=None):
    if m.get("project_uuid") and things_bridge.project_alive(m["project_uuid"]):
        return m["project_uuid"]
    if dry_run:
        log.info("[dry-run] would resolve/create Things project %r",
                 config["THINGS_PROJECT"])
        return None
    uuid = things_bridge.ensure_project(config["THINGS_PROJECT"])
    m["project_uuid"] = uuid
    save_things_map(m, path)
    return uuid


def things_decisions(config, dry_run):
    if config["THINGS_SYNC"] != "on":
        return
    try:
        _things_decisions(config, dry_run)
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("Things decisions phase failed: %s: %s",
                    type(exc).__name__, exc)


def _things_decisions(config, dry_run):
    m = load_things_map()
    if m is None:
        return
    if _mirror_lost(config, m, dry_run):
        return
    project = _things_project(config, m, dry_run)
    if project is None:
        return
    tasks = m["tasks"]
    if tasks:
        things_bridge.prewarm()
    token = things_bridge.auth_token()

    review, approved = run_bridge([{"op": "list_group", "path": REVIEW_PATH},
                                   {"op": "list_group", "path": APPROVED_PATH}])
    approved_uuids = {r["uuid"] for r in approved}
    # Rebuild only markers whose proposal still exists: every processed
    # decision leaves its task in the Logbook with the marker intact, and
    # resurrecting those would churn rebuild -> settle -> drop every run.
    live_proposals = ({r["uuid"] for r in review if r["name"] != "Approved"}
                      | approved_uuids)
    rows_by_proposal = {}
    orphans = []
    for row in things_bridge.read_project_tasks(project):
        puuid = proposal_uuid_from_notes(row["notes"])
        if not puuid:
            continue
        if puuid in live_proposals:
            rows_by_proposal.setdefault(puuid, []).append(row)
        elif puuid not in tasks and row["status"] == 0 and not row["trashed"]:
            orphans.append(row)
    rebuilt = 0
    for puuid, rows in rows_by_proposal.items():
        if puuid in tasks:
            continue
        live = [r for r in rows if not r["trashed"]]
        if len(live) > 1:
            log.warning("two live Things tasks carry proposal %s; trash one "
                        "of them to proceed", puuid)
            continue
        row = live[0] if live else rows[0]
        # A rebuilt task without the editable sentinel might be a frozen
        # stub, or an editable note whose sentinel line got stripped by
        # hand — the two look identical from here. edit_disabled=False
        # makes the ambiguous case bounce with a parse error on completion
        # instead of silently applying frozen ops over real user edits.
        tasks[puuid] = {"task_uuid": row["uuid"], "source_uuid": None,
                        "fence_hash": None, "prepared_hash": None,
                        "warned": {}, "edit_disabled": False,
                        "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
        rebuilt += 1
    if rebuilt:
        log.info("rebuilt %d Things map entries from task notes", rebuilt)
        if not dry_run:
            save_things_map(m)
    if orphans:
        if dry_run:
            log.info("[dry-run] would cancel %d orphaned Things task(s) for "
                     "proposals that no longer exist in DEVONthink",
                     len(orphans))
        else:
            for row in orphans:
                if things_bridge.update_todo(row["uuid"], token,
                                             {"canceled": "true"},
                                             {"status": 2}):
                    log.info("canceled an orphaned Things task for a "
                             "proposal that no longer exists in "
                             "DEVONthink", extra={"record_uuid": row["uuid"]})
                else:
                    log.info("could not cancel orphaned Things task %s "
                             "(missing auth token?); will retry", row["uuid"])

    for rec in approved:
        entry = tasks.get(rec["uuid"])
        if not entry:
            continue
        if dry_run:
            log.info("[dry-run] would complete the Things task for %s "
                     "(approved in DEVONthink)", rec["name"])
            continue
        if things_bridge.update_todo(entry["task_uuid"], token,
                                     {"completed": "true"}, {"status": 3}):
            tasks.pop(rec["uuid"])
            save_things_map(m)
            log.info("proposal approved in DEVONthink; completed its Things task",
                     extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})
        else:
            log.info("could not complete the Things task for %s (missing auth "
                     "token?); will retry", rec["name"])

    rows = things_bridge.read_tasks([e["task_uuid"] for e in tasks.values()])
    for puuid in list(tasks):
        entry = tasks[puuid]
        try:
            _decide_task(config, m, puuid, entry,
                         rows.get(entry["task_uuid"]), token, dry_run,
                         approved_uuids)
        except BridgeUnavailable:
            raise
        except Exception as exc:
            log.warning("Things decision for proposal %s failed: %s: %s",
                        puuid, type(exc).__name__, exc)


def _decide_task(config, m, puuid, entry, row, token, dry_run, approved_uuids):
    tasks = m["tasks"]
    if row is None:
        log.info("Things task for proposal %s is gone (emptied trash?); "
                 "dropping the mapping — a still-pending proposal gets a new "
                 "task", puuid)
        if not dry_run:
            tasks.pop(puuid)
            save_things_map(m)
        return
    if row["status"] == 0 and not row["trashed"]:
        cleared = entry.pop("settle", None) is not None
        cleared = entry.pop("prepared_hash", None) is not None or cleared
        cleared = entry.pop("bounce_count", None) is not None or cleared
        if cleared and not dry_run:
            save_things_map(m)
        return
    snap = settle_snapshot(row)
    if entry.get("settle") != snap:
        entry["settle"] = snap
        if not dry_run:
            save_things_map(m)
        log.info("Things task for proposal %s reached a terminal state; "
                 "acting next run once it settles", puuid)
        return
    if row["status"] == 3 and not row["trashed"]:
        _approve_completed(config, m, puuid, entry, row, token, dry_run)
        return
    if puuid in approved_uuids:
        # A failed completion write (e.g. no auth token) can leave a task
        # mapped after DT already approved it; without this, canceling that
        # stale task here would trash the user's explicit DT approval.
        log.warning("Things task for proposal %s was canceled or deleted, "
                    "but the proposal is already approved in DEVONthink; "
                    "keeping the approval and dropping the stale Things "
                    "mapping", puuid)
        if not dry_run:
            tasks.pop(puuid)
            save_things_map(m)
        return
    if dry_run:
        log.info("[dry-run] would trash proposal %s (task canceled/deleted "
                 "in Things)", puuid)
        return
    try:
        run_bridge([{"op": "trash", "uuid": puuid}])
        log.info("proposal rejected from Things; trashed",
                 extra={"record_uuid": puuid})
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.info("proposal %s already gone from DEVONthink (%s)", puuid, exc)
    tasks.pop(puuid)
    save_things_map(m)


def _bounce(m, puuid, entry, row, token, dry_run, message):
    if entry.get("bounce_count", 0) >= BOUNCE_LIMIT:
        log.error("Things task for proposal %s has bounced %d times without "
                  "a successful re-open; leaving it for manual review",
                  puuid, BOUNCE_LIMIT)
        return
    log.warning("Things approval for proposal %s bounced: %s", puuid, message)
    entry.pop("settle", None)
    if dry_run:
        return
    entry["bounce_count"] = entry.get("bounce_count", 0) + 1
    notes = f"{BANNER_PREFIX} {message}\n\n{strip_banner(row['notes'] or '')}"
    params = {"auth-token": token or "", "id": entry["task_uuid"],
              "completed": "false", "notes": notes}
    if len(things_bridge.build_url("update", params)) > THINGS_URL_LIMIT:
        notes = f"{BANNER_PREFIX} {message}"
    if not things_bridge.update_todo(
            entry["task_uuid"], token,
            {"completed": "false", "notes": notes},
            {"status": 0, "notes": notes}):
        log.warning("could not re-open the Things task for %s (missing auth "
                    "token?); the proposal stays in _Review", puuid)
    save_things_map(m)


def _approve_completed(config, m, puuid, entry, row, token, dry_run):
    tasks = m["tasks"]
    try:
        text = run_bridge([{"op": "get_text", "uuid": puuid}])[0]["text"]
    except BridgeUnavailable:
        raise
    except Exception:
        log.warning("proposal %s vanished from DEVONthink but its Things task "
                    "was completed; dropping the mapping", puuid)
        if not dry_run:
            tasks.pop(puuid)
            save_things_map(m)
        return
    try:
        ops = proposal_ops(text)
    except ValueError:
        ops = None
    if not ops:
        _bounce(m, puuid, entry, row, token, dry_run,
                "the proposal's ops block is missing or invalid; review it in "
                "DEVONthink")
        return
    current = ops_hash(ops)
    if entry.get("fence_hash") is None:
        entry["fence_hash"] = current
        log.info("baselined the ops fence for rebuilt mapping %s (edits made "
                 "in DEVONthink before this run can't be detected)", puuid)
    if current == entry.get("prepared_hash"):
        if not dry_run:
            run_bridge([{"op": "move_to", "uuid": puuid, "group": APPROVED_PATH}])
            tasks.pop(puuid)
            save_things_map(m)
        log.info("resumed an interrupted approval for proposal %s", puuid)
        return
    if current != entry["fence_hash"]:
        _bounce(m, puuid, entry, row, token, dry_run,
                "the proposal was edited in DEVONthink after this task was "
                "created; review and approve it there instead")
        return
    if entry.get("edit_disabled"):
        if dry_run:
            log.info("[dry-run] would move proposal %s to Approved (frozen ops)",
                     puuid)
            return
        run_bridge([{"op": "move_to", "uuid": puuid, "group": APPROVED_PATH}])
        tasks.pop(puuid)
        save_things_map(m)
        log.info("proposal approved from Things (frozen ops)",
                 extra={"record_uuid": puuid})
        return

    source_uuid = entry.get("source_uuid") or next(
        (op.get("uuid") for op in ops if op.get("op") == "mark_filed"), None)
    if not source_uuid:
        _bounce(m, puuid, entry, row, token, dry_run,
                "the proposal has no mark_filed op; review it in DEVONthink")
        return
    try:
        source = run_bridge([{"op": "get_source", "uuid": source_uuid}])[0]
    except BridgeUnavailable:
        raise
    except Exception:
        _bounce(m, puuid, entry, row, token, dry_run,
                "the source record is missing; review in DEVONthink")
        return
    source_date = source_date_of(source)

    try:
        people_ext, events_ext = parse_things_note(row["notes"] or "")
    except SpecParseError as exc:
        _bounce(m, puuid, entry, row, token, dry_run,
                f"could not parse the edited note ({exc})")
        return

    people = run_bridge([{"op": "dump_people", "include_bodies": False}])[0]
    index = roster_index(people)
    selves = self_names(config)
    plans = build_person_plans(people_ext, index, selves, people, source_date)
    plans += build_event_plans(events_ext, index, selves, source_date)
    if not plans:
        _bounce(m, puuid, entry, row, token, dry_run,
                "nothing left to file after edits; cancel the task to reject "
                "instead")
        return
    ambiguous = [p for p in plans if p["kind"] == "ambiguous"]
    if ambiguous:
        detail = "; ".join(f"{p['name']!r} matches {', '.join(p['candidates'])}"
                           for p in ambiguous)
        _bounce(m, puuid, entry, row, token, dry_run,
                f"ambiguous name — {detail}; edit to the exact roster name")
        return

    new_ops = []
    for plan in plans:
        new_ops.extend(ops_for_plan(plan, source, source_date))
    new_ops.append({"op": "mark_filed", "uuid": source_uuid})

    stale = stale_person_ops(new_ops, index, people)
    if stale:
        warned = entry.setdefault("warned", {})
        notes_sha = hashlib.sha256((row["notes"] or "").encode()).hexdigest()
        # "Complete again unchanged to confirm" only holds if the notes are
        # byte-identical to the warning; any other edit re-enters the normal
        # bounce path instead of blindly trusting a stale confirmation.
        to_warn = [(n, near) for n, near in stale
                  if warned.get(norm(n)) != notes_sha]
        if to_warn:
            for n, _ in to_warn:
                warned[norm(n)] = notes_sha
            detail = "; ".join(f'"{n}" resembles {", ".join(near)}'
                               for n, near in to_warn)
            _bounce(m, puuid, entry, row, token, dry_run,
                    f"{detail} — edit to the exact roster name to file into "
                    "the existing record, or complete again unchanged to "
                    "confirm a separate new person")
            return
        confirmed = {norm(n) for n, _ in stale}
        for op in new_ops:
            if op.get("op") == "ensure_person" \
                    and norm(op.get("name", "")) in confirmed:
                op["confirm_new"] = True

    body = proposal_body(source, source_date, plans, new_ops)
    if dry_run:
        log.info("[dry-run] would apply %d regenerated ops for proposal %s",
                 len(new_ops), puuid)
        return
    entry["prepared_hash"] = ops_hash(new_ops)
    save_things_map(m)
    run_bridge([{"op": "set_text", "uuid": puuid, "text": body},
                {"op": "move_to", "uuid": puuid, "group": APPROVED_PATH}])
    tasks.pop(puuid)
    save_things_map(m)
    log.info("proposal approved from Things (%d plans)", len(plans),
             extra={"record_name": source.get("name"), "record_uuid": puuid})


def things_reconcile(config, dry_run):
    if config["THINGS_SYNC"] != "on":
        return
    try:
        _things_reconcile(config, dry_run)
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("Things reconcile phase failed: %s: %s",
                    type(exc).__name__, exc)


def _things_reconcile(config, dry_run):
    m = load_things_map()
    if m is None:
        return
    # A lost mirror clears the stale mapping; _things_project then resolves
    # a fresh one below in the same tick, so pending proposals re-mirror now.
    _mirror_lost(config, m, dry_run)
    project = _things_project(config, m, dry_run)
    if project is None and not dry_run:
        return
    tasks = m["tasks"]
    token = things_bridge.auth_token()

    children, approved_children = run_bridge(
        [{"op": "list_group", "path": REVIEW_PATH},
         {"op": "list_group", "path": APPROVED_PATH}])
    pending = {r["uuid"]: r for r in children if r["name"] != "Approved"}
    approved = {r["uuid"] for r in approved_children}

    people = None
    for puuid, rec in pending.items():
        if puuid in tasks:
            continue
        try:
            text = run_bridge([{"op": "get_text", "uuid": puuid}])[0]["text"]
            try:
                ops = proposal_ops(text)
            except ValueError:
                ops = None
            if ops is not None and not ops:
                # Intentional review-only stub (an empty fact capture): nothing
                # to approve from Things, so it stays DEVONthink-side quietly.
                continue
            if not ops:
                log.warning("proposal has no usable ops block; not mirroring "
                            "it to Things",
                            extra={"record_name": rec["name"],
                                   "record_uuid": puuid})
                continue
            if people is None:
                people = run_bridge([{"op": "dump_people",
                                      "include_bodies": False}])[0]
            plans, editable, source_uuid = plans_from_ops(ops, people)
            if not source_uuid:
                editable = False
            note = things_note_body(source_uuid, puuid, plans) if editable \
                else things_note_stub(source_uuid, puuid)
            add_url = things_bridge.build_url("add", things_bridge.add_todo_params(
                project, rec["name"], note, THINGS_WHEN))
            if len(add_url) > THINGS_URL_LIMIT:
                if editable:
                    note = things_note_stub(source_uuid, puuid)
                    editable = False
                    add_url = things_bridge.build_url(
                        "add", things_bridge.add_todo_params(
                            project, rec["name"], note, THINGS_WHEN))
                if len(add_url) > THINGS_URL_LIMIT:
                    log.warning("proposal %s's Things note exceeds the URL "
                                "size limit even as a stub (title too "
                                "long?); not mirroring it", puuid,
                                extra={"record_name": rec["name"],
                                       "record_uuid": puuid})
                    continue
            if dry_run:
                log.info("[dry-run] would mirror proposal %s to Things%s",
                         rec["name"], "" if editable else " (edit-disabled)")
                continue
            task_uuid = things_bridge.add_todo(project, rec["name"], note,
                                               puuid, when=THINGS_WHEN)
            tasks[puuid] = {"task_uuid": task_uuid, "source_uuid": source_uuid,
                            "fence_hash": ops_hash(ops), "prepared_hash": None,
                            "warned": {}, "edit_disabled": not editable,
                            "created": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
            save_things_map(m)
            log.info("mirrored proposal to Things%s",
                     "" if editable else " (edit-disabled)",
                     extra={"record_name": rec["name"], "record_uuid": puuid})
        except BridgeUnavailable:
            raise
        except Exception as exc:
            log.warning("Things mirror for proposal %s failed: %s: %s",
                        puuid, type(exc).__name__, exc)

    resolved = [p for p in list(tasks) if p not in pending and p not in approved]
    rows = things_bridge.read_tasks(
        [tasks[p]["task_uuid"] for p in resolved]) if resolved else {}
    for puuid in resolved:
        row = rows.get(tasks[puuid]["task_uuid"])
        if dry_run:
            log.info("[dry-run] would cancel the Things task for resolved "
                     "proposal %s", puuid)
            continue
        if row is None or row["trashed"] or row["status"] != 0:
            tasks.pop(puuid)
            save_things_map(m)
            continue
        if things_bridge.update_todo(tasks[puuid]["task_uuid"], token,
                                     {"canceled": "true"}, {"status": 2}):
            tasks.pop(puuid)
            save_things_map(m)
            log.info("proposal %s resolved in DEVONthink; canceled its Things "
                     "task", puuid)
        else:
            log.info("could not cancel the Things task for %s (missing auth "
                     "token?); will retry", puuid)


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def cap_words(text, head=6000, tail=1000):
    words = text.split()
    if len(words) <= head + tail:
        return text
    return " ".join(words[:head]) + "\n[...truncated...]\n" + " ".join(words[-tail:])


# Section headers dt-morning-brief.py upserts into daily notes — keep in sync.
GENERATED_SECTIONS = frozenset({
    "## Briefing", "## Reconnect", "## Birthdays", "## Entity Review",
    "## Journal", "## On This Day",
})

HEADING_RE = re.compile(r"^#{1,2}\s")

# A bullet whose entire content is a machine-written cross-link or marker —
# the journal/wikilink lines boox-process.py and the post-enrich smart rules
# append to a daily note, or a bare provenance comment — with no free prose
# of its own.
MACHINE_LINK_BULLET_RE = re.compile(
    r"^-\s+(?:\d{1,2}:\d{2}(?:am|pm):\s+)?"
    r"(?:\[[^\]]*\]\(x-devonthink-item://[0-9A-Za-z-]+\)"
    r"|x-devonthink-item://[0-9A-Za-z-]+"
    r"|<!--[^>]*-->)\s*$",
    re.IGNORECASE,
)


def strip_generated_sections(text):
    """Daily-note text minus the pipeline's machine-written lines.
    Extraction must only see human-authored content: event scaffolding
    otherwise round-trips into attendance pseudo-facts, a person's news
    sub-lines re-surface old entries as if they were dated today, and a late
    journal/post-enrich link line would otherwise re-trigger extraction on a
    note nothing new was written into.

    Three layers, covering both daily-note formats forever (old notes are
    never migrated): the legacy generated `##` sections (each spans its
    header through the next heading of level 1 or 2, matching the bridge's
    section-span rule); any bullet that is nothing but a machine-written
    link or marker; and the flat timeline's machine bullets — emoji-typed
    top-level lines plus their machine sub-lines, where a manual sub-line
    typed under an event survives as extraction input."""
    out, skipping, machine_block = [], False, False
    for line in text.splitlines():
        stripped = line.strip()
        if HEADING_RE.match(stripped):
            skipping = stripped in GENERATED_SECTIONS
            machine_block = False
            if skipping:
                continue
        if skipping or MACHINE_LINK_BULLET_RE.match(stripped):
            continue
        if be.is_machine_bullet(line):
            machine_block = True
            continue
        if machine_block and stripped and line[:1].isspace():
            if be.is_machine_subline(line):
                continue
            out.append(line)
            continue
        machine_block = False
        out.append(line)
    return "\n".join(out)


def normalize_source_text(kind, text):
    if kind == "daily":
        return strip_generated_sections(text)
    if kind == "fact":
        return strip_leading_h1(text)
    return text


def pick_transport(config):
    if config["TRANSPORT"] == "off":
        return None
    return "omlx" if omlx_available(config) else None


def scan(config, state, dry_run, force_uuid, user_invoked):
    people, sources, listing = run_bridge([
        {"op": "dump_people", "include_bodies": False},
        {"op": "list_sources"},
        {"op": "list_candidates"},
    ])
    index = roster_index(people)
    ignored = ec.CandidateIndex(listing).ignored_names()
    selves = self_names(config)

    min_roster = int(config["MIN_ROSTER"])
    if len(people) < min_roster and not force_uuid:
        log.info("People holds %d record(s), MIN_ROSTER is %d — extraction "
                 "paused until /20_ENTITIES/People is seeded",
                 len(people), min_roster)
        return

    skip_re = None
    if config["SKIP_SOURCE_TITLES"]:
        try:
            skip_re = re.compile(config["SKIP_SOURCE_TITLES"], re.IGNORECASE)
        except re.error as exc:
            log.warning("bad SKIP_SOURCE_TITLES regex, ignoring: %s", exc)

    if force_uuid:
        candidates = [s for s in sources if s["uuid"] == force_uuid]
        if not candidates:
            try:
                candidates = [run_bridge(
                    [{"op": "get_source", "uuid": force_uuid}])[0]]
            except Exception as exc:
                log.error("--force uuid %s could not be fetched: %s",
                          force_uuid, exc)
                return
        if not candidates[0].get("ready", True):
            log.warning("--force target still has NeedsProcessing set — its "
                        "content may not be final; extracting anyway",
                        extra={"record_name": candidates[0].get("name", ""),
                               "record_uuid": force_uuid})
    else:
        candidates = [
            s for s in sources
            if source_needs_filing(s, state)
            and not (skip_re and skip_re.search(s["name"]))
        ]
        # Newest first, but a fact capture leads its day: a deliberate
        # capture shouldn't wait behind same-day meetings for a MAX_PER_RUN slot.
        candidates.sort(key=lambda s: (source_date_of(s), s["kind"] == "fact"),
                        reverse=True)

    limit = int(config["MAX_PER_RUN"])
    filing_mode = config["FILING_MODE"]
    idle_min = float(config["IDLE_MINUTES"])
    defer_reason = None
    if not user_invoked:
        if idle_min > 0:
            idle = user_idle_seconds()
            if idle is not None and idle < idle_min * 60:
                defer_reason = "user active"
        if defer_reason is None and not memory_pressure_normal():
            defer_reason = "memory pressure elevated"
    defer_logged = False
    no_transport = 0
    extracted_count = 0
    llm_lock = None
    llm_lock_failed = False
    for source in candidates:
        if extracted_count >= limit:
            break
        uuid = source["uuid"]
        parked = state["parked"].get(uuid)
        # Discovery only surfaces a parked source when its content changed
        # since parking; give it a fresh set of attempts.
        attempts = 0 if parked is not None \
            else (state["attempts"].get(uuid) or {}).get("count", 0)
        if attempts >= MAX_ATTEMPTS and not force_uuid:
            last_error = (state["attempts"].get(uuid) or {}).get(
                "last_error", "")
            log.warning("parking after %d failed attempts%s — retries when "
                        "the source changes, or via --force %s",
                        attempts,
                        f" (last error: {last_error})" if last_error else "",
                        uuid,
                        extra={"record_name": source["name"],
                               "record_uuid": uuid})
            if not dry_run:
                state["parked"][uuid] = {
                    "name": source["name"],
                    "attempts": attempts,
                    "last_error": last_error,
                    "modified": source.get("modified", ""),
                    "parked_at": date.today().isoformat(),
                }
                state["attempts"].pop(uuid, None)
                save_state(state)
            continue

        transport = pick_transport(config)
        if transport is None:
            no_transport += 1
            continue
        if defer_reason:
            if not defer_logged:
                log.info("%s, deferring local extraction to a later run",
                         defer_reason)
                defer_logged = True
            continue
        if llm_lock is None and not llm_lock_failed:
            llm_lock = acquire_llm_lock()
            if llm_lock is None:
                llm_lock_failed = True
                log.info("local-llm lock held (journal OCR?), deferring "
                         "local extraction to the next run")
        if llm_lock is None:
            continue

        source_date = source_date_of(source)
        text = run_bridge([{"op": "get_text", "uuid": uuid}])[0]["text"]
        text = normalize_source_text(source["kind"], text)
        entry = state["processed"].get(uuid)
        if uuid != force_uuid and entry is not None and entry.get("hash") == \
                hashlib.sha256(text.encode()).hexdigest():
            # Metadata-only touch (tags, EntityFiled, sync churn): the text
            # already filed is unchanged, so just re-baseline the mod stamp.
            if not dry_run:
                entry["modified"] = source.get("modified", "")
                save_state(state)
            continue
        if len(text.split()) < min_words_for(source["kind"]):
            if not dry_run:
                remember_processed(state, source, text)
                save_state(state)
            continue

        if parked is not None and not dry_run:
            state["parked"].pop(uuid, None)
            state["attempts"].pop(uuid, None)
            save_state(state)

        prompt = PROMPT_TEMPLATE.format(
            source_date=source_date,
            roster=roster_text(people),
            source_name=source["name"],
            content=cap_words(text),
        )
        if source["kind"] == "fact":
            prompt = FACT_PREFACE + prompt
        extracted_count += 1
        log.info("extracting via %s", transport,
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})
        try:
            raw = extract_omlx(config, prompt)
            extracted_people, extracted_events = parse_extraction(raw)
        except LLMUnavailable as exc:
            log.info("oMLX unavailable (%s), deferring remaining extraction "
                     "to the next run", exc)
            break
        except BridgeUnavailable:
            raise
        except Exception as exc:
            if not dry_run:
                record_attempt(state, uuid, f"{type(exc).__name__}: {exc}")
                save_state(state)
            log.error("extraction failed: %s: %s", type(exc).__name__, exc,
                      extra={"record_name": source["name"],
                             "record_uuid": uuid})
            continue

        try:
            plans = build_person_plans(extracted_people, index, selves, people,
                                       source_date)
            plans += build_event_plans(extracted_events, index, selves,
                                       source_date)
            plans, handled = divert_new_plans(
                plans, source, source_date, text, index, ignored, dry_run)
            mode = effective_filing_mode(source["kind"], filing_mode)
            file_source(config, state, source, source_date, plans, mode,
                        dry_run, text, candidate_handled=handled)
        except BridgeUnavailable:
            raise
        except Exception as exc:
            if not dry_run:
                record_attempt(state, uuid, f"{type(exc).__name__}: {exc}")
                save_state(state)
            log.error("filing failed: %s: %s", type(exc).__name__, exc,
                      extra={"record_name": source["name"],
                             "record_uuid": uuid})
            continue

    if no_transport and not extracted_count:
        log.info("%d candidate source(s) waiting: no eligible transport "
                 "(TRANSPORT=%s)", no_transport, config["TRANSPORT"])


def review_group_has_name(name):
    """True when a record named `name` already sits in `_Review` — the guard
    against a crash between create_record and save_state re-creating the
    same proposal or stub on the next tick's retry."""
    return any(r["name"] == name for r in
               run_bridge([{"op": "list_group", "path": REVIEW_PATH}])[0])


def candidate_mentions(plans, source, source_date, text):
    """(kept plans, mentions) — every `new` plan leaves the proposal path and
    becomes a candidate mention; everything else files as before."""
    kept, mentions = [], []
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    for plan in plans:
        if plan["kind"] != "new":
            kept.append(plan)
            continue
        mentions.append({
            "name": plan["name"],
            "email": (plan.get("updates") or {}).get("email", ""),
            "sid": ec.dt_sighting_id(source["uuid"]),
            "sighting": {
                "person": plan["name"],
                "name": source["name"],
                "kind": source.get("kind", ""),
                "date": source_date,
                "hash": text_hash,
                "interacted": bool(plan.get("interacted")),
                "facts": [[d, f] for d, f in plan.get("facts", [])],
                "updates": dict(plan.get("updates") or {}),
                "evidence": "extraction",
            },
            "plan": plan,
        })
    return kept, mentions


def divert_new_plans(plans, source, source_date, text, index, ignored,
                     dry_run):
    """Route unresolved people into the candidate store; filter Ignored
    names out of event attendee lists (roster-first: an attendee who now
    resolves is the roster's, whatever the Ignored group says). Returns
    (plans to file, count handled by candidates)."""
    kept, mentions = candidate_mentions(plans, source, source_date, text)
    for plan in kept:
        if plan["kind"] == "event":
            plan["attendees"] = [
                a for a in plan["attendees"]
                if index.get(norm(a)) or norm(a) not in ignored]
    if not mentions:
        return kept, 0
    handled = 0
    for m, disposition in ec.upsert_mentions(run_bridge, mentions, log,
                                             dry_run=dry_run):
        if disposition == "resolved":
            # The roster got them mid-run (a racing promotion): keep the
            # plan on the proposal path — its frozen ensure_person resolves
            # to the existing record at apply time and files there.
            kept.append(m["plan"])
        elif disposition == "ignored":
            log.info("dropped mention of ignored candidate %r", m["name"],
                     extra={"record_name": source["name"],
                            "record_uuid": source["uuid"]})
        else:
            handled += 1
            log.info("candidate %s for %r (%d fact(s))", disposition,
                     m["name"], len(m["sighting"]["facts"]),
                     extra={"record_name": source["name"],
                            "record_uuid": source["uuid"]})
    return kept, handled


def file_source(config, state, source, source_date, plans, filing_mode,
                dry_run, text, candidate_handled=0):
    is_fact = source.get("kind") == "fact"
    filed_ops = [{"op": "mark_filed", "uuid": source["uuid"]}]
    if is_fact:
        # Retiring a fact out of _Facts keeps discovery's wholesale
        # enumeration from growing forever; the review-stub path below skips
        # this, since that source is still pending human review.
        filed_ops += [{"op": "ensure_group", "path": FACTS_FILED_PATH},
                      {"op": "move_to", "uuid": source["uuid"],
                       "group": FACTS_FILED_PATH}]
    direct_ops = []
    proposal_plans = []
    for plan in plans:
        strong = filing_mode == "auto" and plan["kind"] == "existing" \
            and not plan.get("weak_match")
        # A fact auto-applies only when its person is named unambiguously in
        # the capture text; a bare first name the model expanded stays a proposal.
        if strong and is_fact and not fact_match_is_strong(plan, text):
            strong = False
        if strong:
            direct_ops.extend(ops_for_plan(plan, source, source_date))
        else:
            proposal_plans.append(plan)

    fence_ops = []
    for plan in proposal_plans:
        fence_ops.extend(ops_for_plan(plan, source, source_date))
    fence_ops.append({"op": "mark_filed", "uuid": source["uuid"]})

    # A deliberately-authored fact must never vanish: when extraction yields
    # nothing to apply, propose, or record on a candidate, surface the raw
    # capture for manual review instead of silently marking it filed. A
    # capture whose people all landed on Pending/Approved candidates is
    # handled — the evidence lives on the candidate awaiting decision.
    empty_fact = is_fact and not direct_ops and not proposal_plans \
        and not candidate_handled

    if dry_run:
        log.info("[dry-run] %s: %d direct ops, %d proposal plans%s",
                 source["name"], len(direct_ops), len(proposal_plans),
                 " (empty fact → review stub)" if empty_fact else "")
        print(json.dumps({"source": source["name"], "direct": direct_ops,
                          "proposal": fence_ops}, indent=2))
        return

    if direct_ops:
        results = run_bridge(direct_ops)
        for op, res in zip(direct_ops, results):
            if op["op"] == "set_field" and isinstance(res, dict) \
                    and res.get("stale"):
                log.info("stale %s update refused (current value %r is newer, "
                         "as of %s)", op["field"], res.get("previous"),
                         res.get("asof", "?"),
                         extra={"record_name": source["name"],
                                "record_uuid": source["uuid"]})
        log.info("auto-applied %d ops", len(direct_ops),
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})

    if proposal_plans:
        proposal_name = f"File: {source['name']}"
        if review_group_has_name(proposal_name):
            log.warning("a proposal named %r already exists in _Review; not "
                        "duplicating it", proposal_name,
                        extra={"record_name": source["name"],
                               "record_uuid": source["uuid"]})
            run_bridge(filed_ops)
        else:
            body = proposal_body(source, source_date, proposal_plans, fence_ops)
            run_bridge([{
                "op": "create_record",
                "name": proposal_name,
                "path": REVIEW_PATH,
                "text": body,
                "fields": {"documenttype": "Entity Filing Proposal"},
            }] + filed_ops)
            log.info("proposal created (%d people)", len(proposal_plans),
                     extra={"record_name": source["name"],
                            "record_uuid": source["uuid"]})
    elif empty_fact:
        stub_name = f"Review capture: {source['name']}"
        if review_group_has_name(stub_name):
            log.warning("a review stub named %r already exists in _Review; "
                        "not duplicating it", stub_name,
                        extra={"record_name": source["name"],
                               "record_uuid": source["uuid"]})
            run_bridge([{"op": "mark_filed", "uuid": source["uuid"]}])
        else:
            run_bridge([{
                "op": "create_record",
                "name": stub_name,
                "path": REVIEW_PATH,
                "text": fallback_review_body(source, source_date, text),
                "fields": {"documenttype": "Entity Filing Proposal"},
            }, {"op": "mark_filed", "uuid": source["uuid"]}])
            log.info("no filable fact extracted — capture surfaced for review",
                     extra={"record_name": source["name"],
                            "record_uuid": source["uuid"]})
    else:
        run_bridge(filed_ops)

    # mark_filed just bumped the record's modification date; re-read it so
    # the stored stamp doesn't immediately re-candidate the source. Only
    # adopt the fresh stamp when the record's content still matches what was
    # actually filed — an edit landing during extraction would otherwise be
    # stamped as already seen and never re-enter filing.
    modified = source.get("modified", "")
    try:
        fresh, fresh_text_res = run_bridge([
            {"op": "get_source", "uuid": source["uuid"]},
            {"op": "get_text", "uuid": source["uuid"]},
        ])
        if normalize_source_text(source.get("kind"), fresh_text_res["text"]) == text:
            modified = fresh.get("modified", "") or modified
    except Exception:
        pass
    remember_processed(state, source, text, modified=modified)
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Things candidate mirror
# ---------------------------------------------------------------------------


CAND_MARKER_RE = re.compile(re.escape(CANDIDATE_MARKER) + r"([A-Za-z0-9-]+)")


def candidate_uuid_from_notes(notes):
    m = CAND_MARKER_RE.search(notes or "")
    return m.group(1) if m else None


def candidate_task_summary(uuid, data, near):
    """Compact title/notes for a candidate's to-do: counts, hints, and the
    DT link — never the accumulated evidence, which lives on the record (the
    URL transport degrades past THINGS_URL_LIMIT)."""
    title = ("! " if data.get("urgent") else "") + ec.record_name(data)
    dates = ec.sighting_dates(data)
    lines = [
        "Complete this to-do to track this person; cancel or delete it to "
        "ignore them permanently. Evidence:",
        f"{CANDIDATE_MARKER}{uuid}",
        f"Sightings: {len(data['sightings'])}"
        + (f" (last {dates[-1]})" if dates else ""),
    ]
    if ec.needs_confirmation(data, near):
        hint = f" (possible existing: {', '.join(near)})" if near else \
            " (single-word name)"
        lines.append("Completing alone will bounce" + hint + " — set "
                     "TrackTarget or CreateDistinct on the record in "
                     "DEVONthink first.")
    if data.get("urgent"):
        lines.append("URGENT: contains a deliberately captured fact.")
    return title, "\n".join(lines)


def summary_hash(title, notes):
    return hashlib.sha256(f"{title}\n{notes}".encode()).hexdigest()


def load_cand_map():
    return load_things_map(THINGS_CAND_MAP_FILE)


def save_cand_map(m):
    save_things_map(m, THINGS_CAND_MAP_FILE)


def _cand_pending(listing):
    return {r["uuid"]: r for r in listing["pending"]
            if not r["name"].startswith(ec.QUARANTINE_PREFIX)}


def _close_out_task(entry, token, decided, cuuid):
    """DT decided first — the task closes to match, never the reverse."""
    attrs, expect = ({"completed": "true"}, {"status": 3}) \
        if decided == "approved" else ({"canceled": "true"}, {"status": 2})
    if things_bridge.update_todo(entry["task_uuid"], token, attrs, expect):
        log.info("candidate %s decided in DEVONthink (%s); closed its Things "
                 "task to match", cuuid, decided)
        return True
    log.info("could not close the Things task for candidate %s (missing "
             "auth token?); will retry", cuuid)
    return False


def candidate_things_decisions(config, dry_run):
    if config["THINGS_SYNC"] != "on":
        return
    try:
        _candidate_things_decisions(config, dry_run)
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("Things candidate decisions phase failed: %s: %s",
                    type(exc).__name__, exc)


def _candidate_things_decisions(config, dry_run):
    m = load_cand_map()
    if m is None:
        return
    if _mirror_lost(config, m, dry_run, THINGS_CAND_MAP_FILE, "candidate(s)"):
        return
    project = _things_project(config, m, dry_run, THINGS_CAND_MAP_FILE)
    if project is None:
        return
    tasks = m["tasks"]
    things_bridge.prewarm()
    token = things_bridge.auth_token()
    listing = run_bridge([{"op": "list_candidates"}])[0]
    pending = _cand_pending(listing)
    decided = {r["uuid"]: "approved" for r in listing["approved"]}
    decided.update({r["uuid"]: "ignored" for r in listing["ignored"]})

    # Rebuild mappings from task notes (map lost/aside) and adopt only tasks
    # whose candidate is still pending — decided candidates' tasks are
    # closed out below; a task for a vanished candidate is canceled.
    rebuilt = 0
    for row in things_bridge.read_project_tasks(m["project_uuid"]):
        cuuid = candidate_uuid_from_notes(row["notes"])
        if not cuuid or cuuid in tasks or row["trashed"]:
            continue
        if cuuid in pending or cuuid in decided:
            if row["status"] == 0 or cuuid in pending:
                tasks[cuuid] = {"task_uuid": row["uuid"], "summary": None,
                                "created": datetime.now().strftime(
                                    "%Y-%m-%dT%H:%M:%S")}
                rebuilt += 1
        elif row["status"] == 0:
            if dry_run:
                log.info("[dry-run] would cancel the orphaned Things task "
                         "for vanished candidate %s", cuuid)
            elif things_bridge.update_todo(row["uuid"], token,
                                           {"canceled": "true"}, {"status": 2}):
                log.info("canceled an orphaned Things task for a candidate "
                         "that no longer exists",
                         extra={"record_uuid": row["uuid"]})
            else:
                log.info("could not cancel orphaned Things task %s (missing "
                         "auth token?); will retry", row["uuid"])
    if rebuilt:
        log.info("rebuilt %d Things candidate map entries from task notes",
                 rebuilt)
        if not dry_run:
            save_cand_map(m)

    rows = things_bridge.read_tasks([e["task_uuid"] for e in tasks.values()])
    for cuuid in list(tasks):
        entry = tasks[cuuid]
        row = rows.get(entry["task_uuid"])
        if row is None:
            log.info("Things task for candidate %s is gone (emptied trash?); "
                     "dropping the mapping — a still-pending candidate gets "
                     "a new task", cuuid)
            if not dry_run:
                tasks.pop(cuuid)
                save_cand_map(m)
            continue
        if cuuid in decided:
            if dry_run:
                log.info("[dry-run] would close the Things task for %s "
                         "(decided in DEVONthink)", cuuid)
            elif _close_out_task(entry, token, decided[cuuid], cuuid):
                tasks.pop(cuuid)
                save_cand_map(m)
            continue
        if cuuid not in pending:
            # Promoted or hard-deleted candidate whose earlier close-out
            # never landed (no auth token): the task must not stay mapped
            # and open forever. A terminal task already reflects a decision,
            # so only an open one is canceled.
            if row["status"] != 0 or row["trashed"]:
                if not dry_run:
                    tasks.pop(cuuid)
                    save_cand_map(m)
            elif dry_run:
                log.info("[dry-run] would cancel the Things task for "
                         "vanished candidate %s", cuuid)
            elif things_bridge.update_todo(entry["task_uuid"], token,
                                           {"canceled": "true"}, {"status": 2}):
                log.info("candidate %s no longer exists; canceled its Things "
                         "task", cuuid)
                tasks.pop(cuuid)
                save_cand_map(m)
            else:
                log.info("could not cancel the Things task for vanished "
                         "candidate %s (missing auth token?); will retry",
                         cuuid)
            continue
        if row["status"] == 0 and not row["trashed"]:
            if entry.pop("settle", None) is not None and not dry_run:
                save_cand_map(m)
            continue
        snap = settle_snapshot(row)
        if entry.get("settle") != snap:
            entry["settle"] = snap
            if not dry_run:
                save_cand_map(m)
            log.info("Things task for candidate %s reached a terminal state; "
                     "acting next run once it settles", cuuid)
            continue
        target = ec.CANDIDATES_APPROVED_PATH \
            if row["status"] == 3 and not row["trashed"] \
            else ec.CANDIDATES_IGNORED_PATH
        verb = "track" if target == ec.CANDIDATES_APPROVED_PATH else "ignore"
        if dry_run:
            log.info("[dry-run] would %s candidate %s (decided in Things)",
                     verb, cuuid)
            continue
        lock = ec.acquire_candidates_lock()
        try:
            fresh = run_bridge([{"op": "list_candidates"}])[0]
            if cuuid in _cand_pending(fresh):
                run_bridge([{"op": "move_to", "uuid": cuuid, "group": target}])
                log.info("candidate decided from Things (%s)", verb,
                         extra={"record_uuid": cuuid})
            else:
                log.info("candidate %s was decided in DEVONthink while its "
                         "Things decision settled; DT wins, dropping the "
                         "mapping", cuuid)
        finally:
            lock.close()
        tasks.pop(cuuid)
        save_cand_map(m)


def mirror_candidates(config, dry_run):
    if config["THINGS_SYNC"] != "on":
        return
    try:
        _mirror_candidates(config, dry_run)
    except BridgeUnavailable:
        raise
    except Exception as exc:
        log.warning("Things candidate mirror phase failed: %s: %s",
                    type(exc).__name__, exc)


def _mirror_candidates(config, dry_run):
    m = load_cand_map()
    if m is None:
        return
    if _mirror_lost(config, m, dry_run, THINGS_CAND_MAP_FILE, "candidate(s)"):
        return
    listing, people = run_bridge([
        {"op": "list_candidates"},
        {"op": "dump_people", "include_bodies": False},
    ])
    pending = _cand_pending(listing)
    if not pending and not m["tasks"]:
        return
    project = _things_project(config, m, dry_run, THINGS_CAND_MAP_FILE)
    if project is None and not dry_run:
        return
    tasks = m["tasks"]
    token = things_bridge.auth_token()
    rows = things_bridge.read_tasks([e["task_uuid"] for e in tasks.values()])
    # A live marker-carrying task must be adopted, never duplicated: with the
    # map lost (or the decisions phase racing), add_todo here would mint a
    # second to-do the decisions loop will never read.
    live_by_cuuid = {}
    if project is not None:
        for row in things_bridge.read_project_tasks(project):
            cuuid = candidate_uuid_from_notes(row["notes"])
            if cuuid and not row["trashed"] and row["status"] == 0:
                live_by_cuuid.setdefault(cuuid, []).append(row)
    for cuuid, rec in pending.items():
        try:
            data = ec.parse_candidate(rec["text"])
        except ValueError:
            continue
        near = ec.near_matches(data["name"], people)
        title, notes = candidate_task_summary(cuuid, data, near)
        digest = summary_hash(title, notes)
        entry = tasks.get(cuuid)
        if entry is None and cuuid in live_by_cuuid:
            live = live_by_cuuid[cuuid]
            if len(live) > 1:
                log.warning("two live Things tasks carry candidate %s; trash "
                            "one of them to proceed", cuuid)
                continue
            if not dry_run:
                tasks[cuuid] = {"task_uuid": live[0]["uuid"], "summary": None,
                                "created": datetime.now().strftime(
                                    "%Y-%m-%dT%H:%M:%S")}
                save_cand_map(m)
                rows[live[0]["uuid"]] = live[0]
            entry = tasks.get(cuuid)
            if entry is None:
                continue
        if entry is None:
            if dry_run:
                log.info("[dry-run] would mirror candidate %r to Things",
                         data["name"])
                continue
            when = THINGS_WHEN if data.get("urgent") else None
            url = things_bridge.build_url("add", things_bridge.add_todo_params(
                project, title, notes, when))
            if len(url) > THINGS_URL_LIMIT:
                notes = (f"Review in DEVONthink.\n{CANDIDATE_MARKER}{cuuid}")
                digest = summary_hash(title, notes)
            try:
                task_uuid = things_bridge.add_todo(
                    project, title, notes, f"{CANDIDATE_MARKER}{cuuid}", when)
            except things_bridge.ThingsError as exc:
                log.warning("mirroring candidate %r failed: %s",
                            data["name"], exc)
                continue
            tasks[cuuid] = {"task_uuid": task_uuid, "summary": digest,
                            "created": datetime.now().strftime(
                                "%Y-%m-%dT%H:%M:%S")}
            save_cand_map(m)
            log.info("mirrored candidate to Things",
                     extra={"record_name": rec["name"], "record_uuid": cuuid})
            continue
        if entry.get("summary") == digest:
            continue
        row = rows.get(entry["task_uuid"])
        # Only an open task refreshes: a terminal one is mid-settle in the
        # decisions phase, and rewriting it would fight the user's decision.
        if row is None or row["status"] != 0 or row["trashed"]:
            continue
        if dry_run:
            log.info("[dry-run] would refresh the Things summary for %r",
                     data["name"])
            continue
        update_url = things_bridge.build_url("update", {
            "auth-token": token or "", "id": entry["task_uuid"],
            "title": title, "notes": notes})
        if len(update_url) > THINGS_URL_LIMIT:
            notes = (f"Review in DEVONthink.\n{CANDIDATE_MARKER}{cuuid}")
            digest = summary_hash(title, notes)
        if things_bridge.update_todo(entry["task_uuid"], token,
                                     {"title": title, "notes": notes},
                                     {"title": title, "notes": notes}):
            entry["summary"] = digest
            save_cand_map(m)
            log.info("refreshed the Things summary for candidate %r "
                     "(sightings/urgency/hints changed)", data["name"])
        else:
            log.info("could not refresh the Things task for %r (missing "
                     "auth token?); will retry", data["name"])


IDENTITY_PROVENANCE_FILE = os.path.expanduser(
    "~/.local/state/devonthink/identity-provenance.json")
BRIEF_LOCK_FILE = os.path.expanduser(
    "~/.local/state/devonthink/dt-morning-brief.lock")


def migrate_candidates(dry_run):
    """One-shot conversion of the brief's calendar candidate ledger into the
    candidate store: a still-pending calendar proposal becomes a Pending
    candidate (through the normal lookup-create path, so it unifies with any
    filing-opened candidate); a gone proposal whose name now resolves is
    dropped (they got tracked); a gone proposal with no roster hit becomes an
    Ignored candidate, preserving the ledger's never-repropose promise."""
    try:
        with open(IDENTITY_PROVENANCE_FILE) as f:
            prov = json.load(f)
    except FileNotFoundError:
        log.info("no identity-provenance state; nothing to migrate")
        return 0
    ledger = (prov or {}).get("candidates") or {}
    if not ledger:
        log.info("calendar candidate ledger is empty; nothing to migrate")
        return 0
    brief_lock = open(BRIEF_LOCK_FILE, "w")
    try:
        fcntl.flock(brief_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error("dt-morning-brief is running; re-run migration when it "
                  "finishes")
        return 1
    try:
        review, people = run_bridge([
            {"op": "list_group", "path": REVIEW_PATH},
            {"op": "dump_people", "include_bodies": False},
        ])
        by_uuid = {r["uuid"]: r for r in review}
        rkeys = ec.roster_keys(people)
        pending_mentions = []
        ignored_data = []
        for key, entry in sorted(ledger.items()):
            puuid = str((entry or {}).get("proposal_uuid", ""))
            event_date = valid_date((entry or {}).get("event_date", "")) or ""
            rec = by_uuid.get(puuid)
            if rec is not None:
                text = run_bridge([{"op": "get_text", "uuid": puuid}])[0]["text"]
                name, email = "", ""
                try:
                    for op in proposal_ops(text) or []:
                        if op.get("op") == "ensure_person":
                            name = str(op.get("name", "")).strip()
                            email = str(
                                (op.get("fields") or {}).get("email", ""))
                except ValueError:
                    pass
                if not name:
                    log.warning("ledger entry %r: pending proposal %s has no "
                                "readable ensure_person op; leaving both",
                                key, puuid)
                    continue
                pending_mentions.append(({
                    "name": name,
                    "email": email,
                    "sid": "cal:migrated-" + hashlib.sha256(
                        key.encode()).hexdigest()[:24],
                    "sighting": {
                        "person": name,
                        "email": ec.norm_email(email),
                        "kind": "calendar",
                        "date": event_date,
                        "title": "(migrated calendar candidate)",
                        "interacted": False,
                        "facts": [],
                        "updates": {},
                        "evidence": "calendar attendee",
                    },
                }, puuid))
                continue
            if key in rkeys:
                log.info("ledger entry %r resolves in the roster; dropping",
                         key)
                continue
            name = key
            email = key if "@" in key else ""
            data = ec.new_candidate(name)
            if email:
                ec.add_email(data, email)
            data["sightings"]["cal:migrated-" + hashlib.sha256(
                key.encode()).hexdigest()[:24]] = {
                "person": name, "email": email, "kind": "calendar",
                "date": event_date, "title": "(migrated calendar candidate, "
                "previously rejected)", "interacted": False, "facts": [],
                "updates": {}, "evidence": "calendar attendee",
            }
            ignored_data.append((key, data))
        if dry_run:
            log.info("[dry-run] would migrate %d pending proposal(s) and "
                     "create %d Ignored candidate(s) from %d ledger entries",
                     len(pending_mentions), len(ignored_data), len(ledger))
            return 0
        results = ec.upsert_mentions(
            run_bridge, [m for m, _p in pending_mentions], log)
        trashed = []
        for (m, puuid), (_m, disposition) in zip(pending_mentions, results):
            log.info("migrated pending calendar proposal for %r (%s)",
                     m["name"], disposition)
            trashed.append({"op": "trash", "uuid": puuid})
        if trashed:
            run_bridge(trashed)
        if ignored_data:
            lock = ec.acquire_candidates_lock()
            try:
                listing = run_bridge(ec.ensure_group_ops()
                                     + [{"op": "list_candidates"}])[-1]
                index = ec.CandidateIndex(listing)
                ops = []
                for key, data in ignored_data:
                    hit, _action = index.lookup(
                        data["name"], next(iter(data["emails"]), ""))
                    if hit is not None:
                        log.warning(
                            "ledger entry %r (previously rejected) collides "
                            "with live candidate %r — leaving the candidate; "
                            "ignore it by hand if still unwanted",
                            key, hit["data"]["name"])
                        continue
                    ops.append({"op": "create_record",
                                "name": ec.record_name(data),
                                "path": ec.CANDIDATES_IGNORED_PATH,
                                "text": ec.render_candidate(data),
                                "fields": {"entitytype": "Candidate"}})
                if ops:
                    run_bridge(ops)
                log.info("created %d Ignored candidate(s) from rejected "
                         "ledger entries", len(ops))
            finally:
                lock.close()
        prov["candidates"] = {}
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(IDENTITY_PROVENANCE_FILE),
            prefix=".identity-provenance.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(prov, f, indent=2, sort_keys=True)
        os.replace(tmp, IDENTITY_PROVENANCE_FILE)
        log.info("calendar candidate ledger retired (%d entries migrated)",
                 len(ledger))
        return 0
    finally:
        brief_lock.close()


def should_record_success(dry_run):
    return not dry_run


def record_success():
    with open(SUCCESS_FILE, "w") as f:
        f.write(str(int(datetime.now().timestamp())))


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    apply_only = "--apply-only" in args
    scan_only = "--scan-only" in args
    rebuild_state = "--rebuild-state" in args
    force_uuid = None
    if "--force" in args:
        idx = args.index("--force")
        if idx + 1 < len(args):
            force_uuid = args[idx + 1]
    migrate = "--migrate-candidates" in args
    split_args = merge_args = None
    if "--split-candidate" in args:
        idx = args.index("--split-candidate")
        split_args = []
        for a in args[idx + 1:]:
            if a.startswith("--"):
                break
            split_args.append(a)
        if len(split_args) < 2:
            print("usage: entity-filing.py --split-candidate "
                  "<candidate-uuid> <sighting-id>...", file=sys.stderr)
            sys.exit(2)
    if "--merge-candidates" in args:
        idx = args.index("--merge-candidates")
        merge_args = args[idx + 1:idx + 3]
        if len(merge_args) != 2:
            print("usage: entity-filing.py --merge-candidates <keep-uuid> "
                  "<fold-uuid>", file=sys.stderr)
            sys.exit(2)
    user_invoked = bool(dry_run or force_uuid or apply_only or scan_only
                        or rebuild_state or migrate or split_args
                        or merge_args)

    if not dry_run:
        subprocess.run(
            [os.path.expanduser("~/.local/bin/pipeline-record-run"),
             "entity-filing", "1800"],
            check=False,
        )

    if not user_invoked:
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-background-job")],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: battery gate")
            return
        gate = subprocess.run(
            [os.path.expanduser("~/.local/bin/should-run-dt-driver")],
            capture_output=True, text=True,
        )
        if gate.returncode != 0:
            log.info("skipping: follower machine")
            return

    lock_fd = None
    if not dry_run:
        lock_fd = acquire_lock()
        if lock_fd is None:
            log.info("another entity-filing run holds the lock, exiting")
            return

    if should_record_success(dry_run):
        record_success()

    config = load_config()
    state_file_existed = os.path.exists(STATE_FILE)
    state = load_state()

    try:
        if split_args:
            sys.exit(split_candidate(split_args[0], split_args[1:], dry_run))
        if merge_args:
            sys.exit(merge_candidates(merge_args[0], merge_args[1], dry_run))
        if migrate:
            sys.exit(migrate_candidates(dry_run))
        if rebuild_state or not state_file_existed:
            added = rebuild_processed_from_dt(state)
            if added or rebuild_state:
                log.info("state rebuild: %d source(s) marked processed from "
                         "their EntityFiled flag", added)
            if not dry_run:
                save_state(state)
            if rebuild_state:
                return
        if not scan_only:
            things_decisions(config, dry_run)
            candidate_things_decisions(config, dry_run)
            promote_candidates(dry_run)
            apply_approved(dry_run)
        if not apply_only:
            scan(config, state, dry_run, force_uuid, user_invoked)
        if not scan_only:
            things_reconcile(config, dry_run)
            mirror_candidates(config, dry_run)
    except BridgeUnavailable as exc:
        log.info("skipping: %s", exc)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)
