#!/usr/bin/python3
"""
entity-filing.py — AI filing step for the entity layer.

Scans processed pipeline documents (Granola meeting notes, handwritten
notes, past daily notes) for facts about people, resolves each mention
against Lorebook/20_ENTITIES/People, and files dated, provenance-linked
bullets into each person's `## Biographical Log`. The LLM only performs
the messy-text -> structured-JSON extraction; everything that writes to
DEVONthink is deterministic (entity-dt-bridge.js ops built here).

Safety model:
  - suggest mode (default): every extraction becomes a proposal record in
    /20_ENTITIES/_Review containing a human summary plus the exact ops as
    a fenced JSON block. Moving a proposal into _Review/Approved makes the
    next run apply it; deleting it rejects it. Nothing touches a Person
    record without review.
  - auto mode (FILING_MODE=auto): ops for unambiguous existing people
    apply immediately; new-person creations and ambiguous matches still
    become proposals. A permanent manual-review path, per the design doc.
  - Meeting attendance (GranolaParticipants) bumps LastContact for
    matched people deterministically on every scan — no LLM involved, so
    it applies in both modes.

Privacy: daily notes live in /10_DAILY, which is excluded from
DEVONthink's AI chat by design. This script honors that boundary — daily
notes are only ever extracted through a local Ollama model, never through
DT chat (which may be a cloud provider). Meeting notes and handwritten
notes already flow through DT chat for enrichment, so either transport is
acceptable for them.

Config (~/.config/dt-pipeline/entities.conf, KEY=VALUE):
  TRANSPORT=auto|ollama|dtchat|off   default auto: ollama when the
                                     configured model responds, else dtchat
  OLLAMA_MODEL=<name>                required for the ollama transport
  OLLAMA_URL=http://127.0.0.1:11434
  FILING_MODE=suggest|auto           default suggest
  MAX_PER_RUN=<n>                    extraction budget per run, default 3
  SELF_NAME=<name>                   extra self-alias to exclude

Usage:
    entity-filing.py                 # launchd-driven scan + apply
    entity-filing.py --dry-run       # print planned ops, write nothing
    entity-filing.py --force UUID    # re-extract one source record
    entity-filing.py --apply-only    # only process _Review/Approved
    entity-filing.py --scan-only     # skip the apply phase
"""

import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local" / "bin"))
from pipeline_log import setup as setup_log

log = setup_log("entity-filing")

BRIDGE = os.path.expanduser("~/.local/bin/entity-dt-bridge.js")
CONFIG_FILE = os.path.expanduser("~/.config/dt-pipeline/entities.conf")
STATE_DIR = os.path.expanduser("~/.local/state/devonthink")
STATE_FILE = os.path.join(STATE_DIR, "entity-filing-state.json")
STATE_SCHEMA_VERSION = 1
REVIEW_PATH = "/20_ENTITIES/_Review"
APPROVED_PATH = "/20_ENTITIES/_Review/Approved"
MAX_ATTEMPTS = 5
UPDATE_FIELDS = ("employer", "role", "city", "email")

CHAT_ROLE = (
    "You are a personal-CRM extraction assistant that responds only with JSON."
)

PROMPT_TEMPLATE = """\
Extract facts about PEOPLE from the note below. Respond with JSON only, in
exactly this shape:

{{"people": [{{"name": "<canonical full name>",
  "match": "<exact name from KNOWN PEOPLE, or null>",
  "facts": [{{"date": "yyyy-mm-dd or null", "fact": "<one concise sentence>"}}],
  "updates": {{"employer": null, "role": null, "city": null, "email": null}}}}]}}

Rules:
- Only real individual humans the note's author personally interacted with or
  learned something about. No public figures mentioned in passing, no
  companies, no product or project names.
- Resolve pronouns and nicknames to one canonical person before extracting.
- Record durable biographical or relationship facts: job or role changes,
  moves, partner and family news, health, notable plans, strong preferences,
  how the author met them, significant things discussed WITH them. Skip
  meeting logistics, task assignments, and technical minutiae.
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

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "match": {"type": ["string", "null"]},
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": ["string", "null"]},
                                "fact": {"type": "string"},
                            },
                            "required": ["fact"],
                        },
                    },
                    "updates": {
                        "type": "object",
                        "properties": {
                            f: {"type": ["string", "null"]} for f in UPDATE_FIELDS
                        },
                    },
                },
                "required": ["name", "facts"],
            },
        }
    },
    "required": ["people"],
}


# ---------------------------------------------------------------------------
# Config / state
# ---------------------------------------------------------------------------


def load_config():
    config = {
        "TRANSPORT": "auto",
        "OLLAMA_MODEL": "",
        "OLLAMA_URL": "http://127.0.0.1:11434",
        "FILING_MODE": "suggest",
        "MAX_PER_RUN": "3",
        "SELF_NAME": "",
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
        return {"version": STATE_SCHEMA_VERSION, "processed_ids": [], "attempts": {}}
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
        and isinstance(data.get("processed_ids"), list)
    ):
        data.setdefault("attempts", {})
        return data
    raise RuntimeError(
        f"State file {STATE_FILE} has an unrecognized schema. Filing is "
        f"paused until the file is inspected and repaired or removed."
    )


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


# ---------------------------------------------------------------------------
# Bridge / transports
# ---------------------------------------------------------------------------


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
        raise RuntimeError(
            f"bridge op {out.get('failed_op')} failed: {out.get('error')}"
        )
    return out["results"]


def ollama_available(config):
    if not config["OLLAMA_MODEL"]:
        return False
    try:
        with urllib.request.urlopen(
            config["OLLAMA_URL"] + "/api/tags", timeout=3
        ) as resp:
            tags = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return False
    names = {m.get("name", "") for m in tags.get("models", [])}
    model = config["OLLAMA_MODEL"]
    return model in names or f"{model}:latest" in names


def extract_ollama(config, prompt):
    payload = json.dumps({
        "model": config["OLLAMA_MODEL"],
        "messages": [
            {"role": "system", "content": CHAT_ROLE},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "format": EXTRACTION_SCHEMA,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request(
        config["OLLAMA_URL"] + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.load(resp)
    return out["message"]["content"]


def extract_dtchat(prompt):
    return run_bridge(
        [{"op": "chat", "prompt": prompt, "role": CHAT_ROLE}], timeout=360
    )[0]["text"]


def parse_extraction(raw):
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        raise ValueError("extraction JSON missing 'people' array")
    return data["people"]


# ---------------------------------------------------------------------------
# Matching / ops
# ---------------------------------------------------------------------------


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


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


def roster_index(people):
    index = {}
    for p in people:
        keys = [norm(p["name"])] + [norm(a) for a in p.get("aliases", "").split(",")]
        for k in keys:
            if k:
                index.setdefault(k, []).append(p)
    return index


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


def fact_line(d, fact, source_uuid):
    fact = fact.rstrip(".") + "."
    return f"- {d} — {fact} ([source](x-devonthink-item://{source_uuid}))"


def build_person_plans(extracted, index, selves, source, source_date):
    """Deterministic resolution: LLM output in, per-person op plans out."""
    plans = []
    for person in extracted:
        name = str(person.get("name", "")).strip()
        if not name or norm(name) in selves:
            continue
        facts = []
        for f in (person.get("facts") or [])[:12]:
            text = str(f.get("fact", "")).strip()
            if text and len(text) <= 400:
                facts.append((valid_date(f.get("date")) or source_date, text))
        updates = {}
        for field in UPDATE_FIELDS:
            v = (person.get("updates") or {}).get(field)
            if isinstance(v, str) and v.strip():
                updates[field] = v.strip()
        if not facts and not updates:
            continue

        claimed = str(person.get("match") or "").strip()
        hits = index.get(norm(claimed)) or index.get(norm(name)) or []
        if len(hits) == 1:
            plans.append({
                "kind": "existing",
                "name": hits[0]["name"],
                "uuid": hits[0]["uuid"],
                "md": hits[0].get("md", {}),
                "facts": facts,
                "updates": updates,
            })
        elif len(hits) > 1:
            plans.append({
                "kind": "ambiguous",
                "name": name,
                "candidates": [h["name"] for h in hits],
                "facts": facts,
                "updates": updates,
            })
        else:
            plans.append({
                "kind": "new",
                "name": name,
                "single_token": len(name.split()) < 2,
                "facts": facts,
                "updates": updates,
            })
    return plans


def ops_for_plan(plan, source, source_date):
    src = source["uuid"]
    lines = [fact_line(d, fact, src) for d, fact in plan["facts"]]
    ops = []
    if plan["kind"] == "existing":
        for field, value in plan["updates"].items():
            previous = str(plan["md"].get("md" + field, "") or "")
            if norm(previous) == norm(value):
                continue
            ops.append({"op": "set_field", "uuid": plan["uuid"],
                        "field": field, "value": value})
            if previous:
                lines.append(fact_line(
                    source_date,
                    f"{field.capitalize()}: {previous} → {value}", src))
        if lines:
            ops.append({"op": "append_log", "uuid": plan["uuid"], "lines": lines})
        ops.append({"op": "bump_lastcontact", "uuid": plan["uuid"],
                    "date": source_date})
    else:
        fields = dict(plan["updates"])
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
        if plan["kind"] == "existing":
            lines.append(f"- **{plan['name']}** (existing record)")
        elif plan["kind"] == "ambiguous":
            cands = ", ".join(plan["candidates"])
            lines.append(f"- **{plan['name']}** — AMBIGUOUS: matches {cands};"
                         " edit the ops JSON before approving")
        else:
            flag = " — single-word name, verify before approving" \
                if plan.get("single_token") else ""
            lines.append(f"- **{plan['name']}** (new Person record){flag}")
        for d, fact in plan["facts"]:
            lines.append(f"  - {d} — {fact}")
        for field, value in plan["updates"].items():
            lines.append(f"  - {field} = {value}")
    lines += ["", "## Ops", "", "```json", json.dumps(ops, indent=2), "```", ""]
    return "\n".join(lines)


def apply_approved(dry_run):
    approved = run_bridge([{"op": "list_group", "path": APPROVED_PATH}])[0]
    for rec in approved:
        text = run_bridge([{"op": "get_text", "uuid": rec["uuid"]}])[0]["text"]
        blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
        if not blocks:
            log.warning("approved proposal has no ops block, skipping",
                        extra={"record_name": rec["name"],
                               "record_uuid": rec["uuid"]})
            continue
        try:
            ops = json.loads(blocks[-1])
        except json.JSONDecodeError as exc:
            log.error("approved proposal has invalid ops JSON (%s), skipping",
                      exc, extra={"record_name": rec["name"],
                                  "record_uuid": rec["uuid"]})
            continue
        if dry_run:
            log.info("[dry-run] would apply %d ops from %s", len(ops), rec["name"])
            continue
        run_bridge(ops + [{"op": "trash", "uuid": rec["uuid"]}])
        log.info("applied %d ops", len(ops),
                 extra={"record_name": rec["name"], "record_uuid": rec["uuid"]})


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def cap_words(text, head=6000, tail=1000):
    words = text.split()
    if len(words) <= head + tail:
        return text
    return " ".join(words[:head]) + "\n[...truncated...]\n" + " ".join(words[-tail:])


def pick_transport(config, kind):
    transport = config["TRANSPORT"]
    if transport == "off":
        return None
    ollama_ok = ollama_available(config)
    if kind == "daily":
        # /10_DAILY is excluded from DT chat by design; local model only.
        return "ollama" if ollama_ok and transport in ("auto", "ollama") else None
    if transport == "ollama":
        return "ollama" if ollama_ok else None
    if transport == "dtchat":
        return "dtchat"
    return "ollama" if ollama_ok else "dtchat"


def scan(config, state, dry_run, force_uuid):
    people, sources = run_bridge([
        {"op": "dump_people", "include_bodies": False},
        {"op": "list_sources"},
    ])
    index = roster_index(people)
    selves = self_names(config)
    processed = set(state["processed_ids"])

    # Deterministic attendance pass: meeting participants bump LastContact
    # whether or not extraction can run. bump_lastcontact only ever raises
    # the date, so re-running is harmless.
    bump_ops = []
    for source in sources:
        if source["kind"] != "meeting":
            continue
        d = source_date_of(source)
        for raw_name in (source.get("participants") or "").split(","):
            n = norm(raw_name)
            if not n or n in selves:
                continue
            hits = index.get(n) or []
            if len(hits) == 1:
                bump_ops.append({"op": "bump_lastcontact",
                                 "uuid": hits[0]["uuid"], "date": d})
    if bump_ops and not dry_run:
        run_bridge(bump_ops)

    if force_uuid:
        candidates = [s for s in sources if s["uuid"] == force_uuid]
        if not candidates:
            log.error("--force uuid %s not found among sources", force_uuid)
            return
    else:
        candidates = [s for s in sources if s["uuid"] not in processed]
        candidates.sort(key=source_date_of, reverse=True)

    limit = int(config["MAX_PER_RUN"])
    filing_mode = config["FILING_MODE"]
    extracted_count = 0
    for source in candidates:
        if extracted_count >= limit:
            break
        attempts = state["attempts"].get(source["uuid"], 0)
        if attempts >= MAX_ATTEMPTS and not force_uuid:
            log.warning("giving up after %d attempts, marking processed",
                        attempts, extra={"record_name": source["name"],
                                         "record_uuid": source["uuid"]})
            state["processed_ids"].append(source["uuid"])
            save_state(state)
            continue

        transport = pick_transport(config, source["kind"])
        if transport is None:
            continue  # no eligible transport (e.g. daily note without Ollama)

        source_date = source_date_of(source)
        text = run_bridge([{"op": "get_text", "uuid": source["uuid"]}])[0]["text"]
        if len(text.split()) < 20:
            state["processed_ids"].append(source["uuid"])
            if not dry_run:
                save_state(state)
            continue

        prompt = PROMPT_TEMPLATE.format(
            source_date=source_date,
            roster=roster_text(people),
            source_name=source["name"],
            content=cap_words(text),
        )
        extracted_count += 1
        log.info("extracting via %s", transport,
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})
        try:
            raw = (extract_ollama(config, prompt) if transport == "ollama"
                   else extract_dtchat(prompt))
            extracted = parse_extraction(raw)
        except Exception as exc:
            state["attempts"][source["uuid"]] = attempts + 1
            if not dry_run:
                save_state(state)
            log.error("extraction failed: %s: %s", type(exc).__name__, exc,
                      extra={"record_name": source["name"],
                             "record_uuid": source["uuid"]})
            continue

        plans = build_person_plans(extracted, index, selves, source, source_date)
        file_source(config, state, source, source_date, plans, filing_mode,
                    dry_run)


def file_source(config, state, source, source_date, plans, filing_mode, dry_run):
    direct_ops = []
    proposal_plans = []
    for plan in plans:
        if filing_mode == "auto" and plan["kind"] == "existing":
            direct_ops.extend(ops_for_plan(plan, source, source_date))
        else:
            proposal_plans.append(plan)

    proposal_ops = []
    for plan in proposal_plans:
        proposal_ops.extend(ops_for_plan(plan, source, source_date))
    proposal_ops.append({"op": "mark_filed", "uuid": source["uuid"]})

    if dry_run:
        log.info("[dry-run] %s: %d direct ops, %d proposal plans",
                 source["name"], len(direct_ops), len(proposal_plans))
        print(json.dumps({"source": source["name"], "direct": direct_ops,
                          "proposal": proposal_ops}, indent=2))
        return

    if direct_ops:
        run_bridge(direct_ops)
        log.info("auto-applied %d ops", len(direct_ops),
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})

    if proposal_plans:
        body = proposal_body(source, source_date, proposal_plans, proposal_ops)
        run_bridge([{
            "op": "create_record",
            "name": f"File: {source['name']}",
            "path": REVIEW_PATH,
            "text": body,
            "fields": {"documenttype": "Entity Filing Proposal"},
        }])
        log.info("proposal created (%d people)", len(proposal_plans),
                 extra={"record_name": source["name"],
                        "record_uuid": source["uuid"]})
    else:
        run_bridge([{"op": "mark_filed", "uuid": source["uuid"]}])

    state["processed_ids"].append(source["uuid"])
    state["attempts"].pop(source["uuid"], None)
    save_state(state)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    apply_only = "--apply-only" in args
    scan_only = "--scan-only" in args
    force_uuid = None
    if "--force" in args:
        idx = args.index("--force")
        if idx + 1 < len(args):
            force_uuid = args[idx + 1]
    user_invoked = dry_run or force_uuid or apply_only or scan_only

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

    config = load_config()
    state = load_state()

    if not scan_only:
        apply_approved(dry_run)
    if not apply_only:
        scan(config, state, dry_run, force_uuid)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)
