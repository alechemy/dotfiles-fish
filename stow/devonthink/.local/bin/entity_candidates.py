"""entity_candidates — shared model for provisional-identity candidate records.

A candidate is one machine-owned markdown record per person seen in sources
but absent from the roster, living under /20_ENTITIES/_Candidates ("Pending"
is the group root; Approved and Ignored are subgroups). The record body is a
human summary plus a fenced JSON block that is the machine truth; the body is
regenerated wholesale on every write, which is safe because decisions are
group moves and custom-metadata fields (TrackTarget, CreateDistinct), never
body edits.

Identity model:
  - An **email-keyed** candidate carries one or more emails and is looked up
    by email only.
  - A **name-only** candidate carries no emails; at most one exists per
    normalized name key, which is what makes name-only lookup deterministic.
    A name-only candidate that later gains an email becomes email-keyed; a
    later name-only mention of that name then opens a fresh name-only
    candidate (conservative duplicates — never merge two strangers
    automatically).
  - A **detached** candidate (created by --split-candidate with no email of
    its own) claims no keys and receives no automatic sightings.

Sightings are replaceable per-source contributions keyed by generic IDs —
"dt:<record-uuid>" for filing extractions (carrying the source-text hash) and
"cal:<event-fingerprint>" for calendar attendance — so a re-extracted source
replaces its contribution instead of appending duplicates.

This module is pure logic plus the shared cross-process lock; all DEVONthink
I/O stays in the callers (entity-filing.py, dt-morning-brief.py) via
entity-dt-bridge.js. Stdlib only: both callers are tier-1 launchd scripts.
"""

import fcntl
import hashlib
import json
import os
import re
import unicodedata

CANDIDATES_PATH = "/20_ENTITIES/_Candidates"
CANDIDATES_APPROVED_PATH = CANDIDATES_PATH + "/Approved"
CANDIDATES_IGNORED_PATH = CANDIDATES_PATH + "/Ignored"
CANDIDATE_LOCK_FILE = os.path.expanduser(
    "~/.local/state/devonthink/candidates.lock")
RECORD_PREFIX = "Candidate: "
QUARANTINE_PREFIX = "[unreadable] "
SCHEMA_VERSION = 1
MAX_FACTS_PER_SIGHTING = 12
MAX_CAL_SIGHTINGS = 10


def norm(s):
    """Same fold as entity-filing.py's norm — casefold, not lower, so case
    pairs that are not one-to-one reach the same key everywhere (covered by
    the normalizer-parity battery)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def norm_email(v):
    return norm(v).removeprefix("mailto:")


def acquire_candidates_lock():
    """Blocking shared-file lock serializing candidate-record critical
    sections across entity-filing and dt-morning-brief, whose process locks
    are independent. Sections are short (one candidate's lookup + write), so
    blocking is safe. Returns the open fd; hold it for the section's life."""
    os.makedirs(os.path.dirname(CANDIDATE_LOCK_FILE), exist_ok=True)
    fd = open(CANDIDATE_LOCK_FILE, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def dt_sighting_id(source_uuid):
    return "dt:" + source_uuid


def cal_fingerprint(ev):
    """Event half of the brief's calendar_observation_id — the candidate
    record itself scopes the person, so the person is not in the key."""
    event_key = "|".join([
        str(ev.get("event_id", "")),
        str(ev.get("calendar_id", "")),
        str(ev.get("source_id", "")),
        str(ev.get("start", "")),
        str(ev.get("end", "")),
        str(ev.get("title", "")),
    ])
    return "cal:" + hashlib.sha256(event_key.encode()).hexdigest()[:24]


def new_candidate(name):
    return {
        "v": SCHEMA_VERSION,
        "name": str(name).strip(),
        "name_variants": [str(name).strip()],
        "emails": [],
        "urgent": False,
        "detached": False,
        "sightings": {},
    }


def add_variant(data, name):
    name = str(name or "").strip()
    if not name:
        return
    known = {norm(v) for v in data["name_variants"]}
    if norm(name) not in known:
        data["name_variants"].append(name)


def add_email(data, email):
    email = norm_email(email)
    if email and email not in data["emails"]:
        data["emails"].append(email)


def upsert_sighting(data, sid, sighting, mention_name="", mention_email=""):
    """Replace-or-add one source's contribution. Same sid always replaces —
    identical content is a no-op by value, changed content (a re-extracted
    source) supersedes its earlier contribution instead of appending. The
    sighting's `person` is the observed mention name and `email` the observed
    address — stored per-sighting so --split-candidate can recompute each
    half's variants and emails from its own evidence alone."""
    facts = sighting.get("facts") or []
    sighting["facts"] = facts[:MAX_FACTS_PER_SIGHTING]
    if mention_name and not sighting.get("person"):
        sighting["person"] = mention_name
    if mention_email and not sighting.get("email"):
        sighting["email"] = norm_email(mention_email)
    data["sightings"][sid] = sighting
    cal = sorted((s for s in data["sightings"] if s.startswith("cal:")),
                 key=lambda s: (data["sightings"][s].get("date", ""), s))
    # A recurring meeting must not grow the record forever: attendance
    # evidence beyond the newest MAX_CAL_SIGHTINGS says nothing new.
    for old in cal[:-MAX_CAL_SIGHTINGS]:
        del data["sightings"][old]
    add_variant(data, sighting.get("person", ""))
    add_email(data, sighting.get("email", ""))
    add_email(data, (sighting.get("updates") or {}).get("email", ""))
    if sighting.get("kind") == "fact":
        data["urgent"] = True


def recompute_derived(data):
    """Re-derive name_variants, emails, and urgency from the sightings alone
    (canonical name always retained) — the inverse-direction pass split and
    merge rely on."""
    data["name_variants"] = [data["name"]]
    data["emails"] = []
    data["urgent"] = False
    for s in data["sightings"].values():
        add_variant(data, s.get("person", ""))
        add_email(data, s.get("email", ""))
        add_email(data, (s.get("updates") or {}).get("email", ""))
        if s.get("kind") == "fact":
            data["urgent"] = True


def candidate_keys(data):
    """Lookup handles this candidate claims: email keys for an email-keyed
    candidate, the normalized canonical name for a name-only one, nothing
    when detached."""
    if data.get("detached"):
        return []
    if data["emails"]:
        return list(data["emails"])
    return [norm(data["name"])]


def sighting_dates(data):
    return sorted(d for d in
                  (s.get("date", "") for s in data["sightings"].values()) if d)


def parse_candidate(text):
    """Machine data from the last ```json fence of a candidate body. Raises
    ValueError on anything malformed — callers quarantine, never overwrite.
    CRs are normalized first: a DT-side edit saves classic-Mac endings."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    if not blocks:
        raise ValueError("no json fence")
    data = json.loads(blocks[-1])
    if not isinstance(data, dict):
        raise ValueError("fence is not an object")
    if data.get("v") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version {data.get('v')!r}")
    if not str(data.get("name", "")).strip():
        raise ValueError("missing name")
    if not isinstance(data.get("sightings"), dict):
        raise ValueError("missing sightings object")
    data.setdefault("name_variants", [data["name"]])
    data.setdefault("emails", [])
    data.setdefault("urgent", False)
    data.setdefault("detached", False)
    if not isinstance(data["name_variants"], list) \
            or not isinstance(data["emails"], list):
        raise ValueError("name_variants/emails are not lists")
    return data


def record_name(data):
    return RECORD_PREFIX + data["name"]


def single_token(data):
    return len(norm(data["name"]).split()) < 2


def needs_confirmation(data, near):
    """True when bare approval is too weak — near-matches or a single-token
    name require an explicit TrackTarget or CreateDistinct, since the
    approval gesture may come from a compact Things summary where the hints
    were not visible."""
    return bool(near) or single_token(data)


def render_candidate(data, near=(), peers=(), notice=""):
    """Full record body. `near` is roster near-match names; `peers` is
    [(name, uuid)] of same-named email-keyed candidates (name-only records
    only); `notice` is a promotion-bounce explanation surfaced at the top."""
    dates = sighting_dates(data)
    lines = [
        f"# {record_name(data)}",
        "",
    ]
    if notice:
        lines += [f"**Needs attention:** {notice}", ""]
    lines += [
        "Machine-owned: this body is rewritten by the pipeline. Decide by "
        "moving the record, not by editing text.",
        "",
        f"- Track: move into `{CANDIDATES_APPROVED_PATH}` to create this "
        "person (or file into an existing one — see below).",
        f"- Ignore: move into `{CANDIDATES_IGNORED_PATH}` to never propose "
        "them again.",
        "- Forget: delete this record; a future sighting re-proposes them.",
        "",
        f"Sightings: {len(data['sightings'])}"
        + (f" (last {dates[-1]})" if dates else ""),
    ]
    if data["emails"]:
        lines.append("Emails: " + ", ".join(data["emails"]))
    if len(data["name_variants"]) > 1:
        lines.append("Also seen as: " + ", ".join(data["name_variants"][1:]))
    if data.get("urgent"):
        lines.append("URGENT: contains a deliberately captured fact.")
    if data.get("detached"):
        lines.append("Detached by split: receives no automatic sightings — "
                     "decide it promptly.")
    if near:
        lines += [
            "",
            "Possible existing records: " + ", ".join(near) + ".",
            "To file into one of them instead of creating a new person, set "
            "this record's `TrackTarget` custom-metadata field to that "
            "record's UUID, then approve.",
        ]
    if needs_confirmation(data, near):
        reason = "near matches exist" if near else "single-word name"
        lines.append(
            f"Approval alone is not enough ({reason}): set `TrackTarget` to "
            "file into an existing person, or `CreateDistinct` to confirm a "
            "new one.")
    if peers:
        links = ", ".join(
            f"[{n}](x-devonthink-item://{u})" for n, u in peers)
        lines += [
            "",
            f"Same name, different email: {links}. If these are one person, "
            "merge with `entity-filing.py --merge-candidates <this-uuid> "
            "<other-uuid>`.",
        ]
    lines += ["", "## Sightings", ""]
    by_date = sorted(data["sightings"].items(),
                     key=lambda kv: (kv[1].get("date", ""), kv[0]))
    for sid, s in by_date:
        d = s.get("date", "?")
        evidence = s.get("evidence", "extraction")
        if sid.startswith("dt:"):
            src = sid[3:]
            title = s.get("name", "source")
            lines.append(
                f"- {d} — [{title}](x-devonthink-item://{src}) ({evidence})")
        else:
            lines.append(f"- {d} — {s.get('title', 'event')} ({evidence})")
        for fd, fact in (s.get("facts") or []):
            lines.append(f"  - {fd} — {fact}")
        for field, value in (s.get("updates") or {}).items():
            lines.append(f"  - {field} = {value}")
    lines += [
        "",
        "## Data",
        "",
        "```json",
        json.dumps(data, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


class CandidateIndex:
    """In-memory view of one list_candidates bridge result.

    Entries are dicts: {uuid, name, md, data, group} with group one of
    "pending" | "approved" | "ignored". Records whose fence fails to parse
    land in `broken` ([(uuid, name, error)]) for the caller to quarantine —
    they are invisible to lookup, so a broken record can never absorb or
    suppress a sighting.
    """

    def __init__(self, listing):
        self.entries = []
        self.broken = []
        self.by_email = {}
        self.by_name = {}
        for group in ("pending", "approved", "ignored"):
            for rec in listing.get(group, []):
                if rec["name"].startswith(QUARANTINE_PREFIX):
                    continue
                try:
                    data = parse_candidate(rec["text"])
                except ValueError as exc:
                    self.broken.append((rec["uuid"], rec["name"], str(exc)))
                    continue
                entry = {"uuid": rec["uuid"], "name": rec["name"],
                         "md": rec.get("md", {}), "data": data, "group": group}
                self.entries.append(entry)
                for key in candidate_keys(data):
                    if data["emails"]:
                        self.by_email.setdefault(key, []).append(entry)
                    else:
                        self.by_name.setdefault(key, []).append(entry)

    def lookup(self, name, email=""):
        """The plan's key rules. Returns (entry, action) where action is
        "attach" (upsert into entry), "upgrade" (attach and add the email),
        or (None, "create") when a new candidate is called for."""
        if email:
            hit = self.by_email.get(norm_email(email))
            if hit:
                return hit[0], "attach"
            named = self.by_name.get(norm(name)) or []
            if len(named) == 1:
                return named[0], "upgrade"
            return None, "create"
        named = self.by_name.get(norm(name)) or []
        if named:
            return named[0], "attach"
        for entry in self.entries:
            if entry["data"]["emails"] or entry["data"].get("detached"):
                continue
            if any(norm(v) == norm(name)
                   for v in entry["data"]["name_variants"]):
                return entry, "attach"
        return None, "create"

    def ignored_names(self):
        """Normalized name keys the Ignored group suppresses. Email-keyed
        ignored candidates suppress their canonical name and variants too:
        a filing mention has no email, so the name is the only handle a
        drop decision can act on."""
        out = set()
        for entry in self.entries:
            if entry["group"] != "ignored":
                continue
            out.add(norm(entry["data"]["name"]))
            out.update(norm(v) for v in entry["data"]["name_variants"])
        return out

    def email_peers(self, name):
        """Same-named email-keyed candidates, for a name-only record's body
        hints: [(record name, uuid)]."""
        out = []
        for entry in self.entries:
            if not entry["data"]["emails"]:
                continue
            keys = {norm(entry["data"]["name"])}
            keys.update(norm(v) for v in entry["data"]["name_variants"])
            if norm(name) in keys:
                out.append((entry["name"], entry["uuid"]))
        return out


def near_matches(name, people, limit=3):
    """Same token heuristic as entity-filing.py's near_matches, over
    dump_people entries."""
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


def roster_keys(people):
    """Normalized name/alias/email keys → [person uuid]. The email keys are
    what the bridge's own person index lacks, so live re-checks must resolve
    through this, not a bridge-side name lookup."""
    index = {}
    for p in people:
        keys = [norm(p["name"])] + [norm(a)
                                    for a in p.get("aliases", "").split(",")]
        email = norm_email(p.get("md", {}).get("mdemail", ""))
        if email:
            keys.append(email)
        for k in keys:
            if k:
                index.setdefault(k, []).append(p["uuid"])
    return index


def ensure_group_ops():
    return [{"op": "ensure_group", "path": p, "exclude_chat": True}
            for p in (CANDIDATES_PATH, CANDIDATES_APPROVED_PATH,
                      CANDIDATES_IGNORED_PATH)]


def quarantine_ops(broken):
    return [{"op": "set_name", "uuid": uuid,
             "name": QUARANTINE_PREFIX + name}
            for uuid, name, _err in broken]


def upsert_mentions(bridge, mentions, log, dry_run=False):
    """Record a batch of unresolved mentions as candidate sightings.

    `mentions` is [{name, email, sid, sighting}]. Holds the shared candidates
    lock for the whole batch and re-reads both roster and candidate state
    inside it, so a mention classified "unresolved" from a stale snapshot
    cannot recreate a candidate another process just promoted or absorb one
    it just decided. Returns [(mention, disposition)] with disposition one of
    resolved | ignored | attached | upgraded | created; broken candidate
    records are quarantined (renamed), never overwritten.
    """
    if not mentions:
        return []
    out = []
    lock = acquire_candidates_lock()
    try:
        fetch = [] if dry_run else ensure_group_ops()
        listing, people = bridge(fetch + [
            {"op": "list_candidates"},
            {"op": "dump_people", "include_bodies": False},
        ])[-2:]
        index = CandidateIndex(listing)
        for uuid, name, err in index.broken:
            log.warning("candidate record has an unreadable data fence (%s); "
                        "quarantining", err,
                        extra={"record_name": name, "record_uuid": uuid})
        if index.broken and not dry_run:
            bridge(quarantine_ops(index.broken))
        roster = roster_keys(people)
        ignored = index.ignored_names()
        dirty = {}
        created = []
        for m in mentions:
            keys = [k for k in (norm(m["name"]), norm_email(m.get("email", "")))
                    if k]
            if any(k in roster for k in keys):
                out.append((m, "resolved"))
                continue
            entry, action = index.lookup(m["name"], m.get("email", ""))
            if entry is not None and entry["group"] == "ignored":
                out.append((m, "ignored"))
                continue
            if entry is None and norm(m["name"]) in ignored:
                out.append((m, "ignored"))
                continue
            if entry is None:
                data = new_candidate(m["name"])
                upsert_sighting(data, m["sid"], m["sighting"],
                                m["name"], m.get("email", ""))
                new_entry = {"uuid": None, "name": record_name(data),
                             "md": {}, "data": data, "group": "pending"}
                index.entries.append(new_entry)
                for key in candidate_keys(data):
                    target = index.by_email if data["emails"] else index.by_name
                    target.setdefault(key, []).append(new_entry)
                created.append((m, new_entry))
                out.append((m, "created"))
            else:
                had_emails = bool(entry["data"]["emails"])
                upsert_sighting(entry["data"], m["sid"], m["sighting"],
                                m["name"], m.get("email", ""))
                if not had_emails and entry["data"]["emails"]:
                    # Upgraded mid-batch: it is email-keyed now, so later
                    # mentions in this batch must find it by email and a
                    # name-only mention must no longer land on it.
                    for bucket in index.by_name.values():
                        if entry in bucket:
                            bucket.remove(entry)
                    for key in candidate_keys(entry["data"]):
                        index.by_email.setdefault(key, []).append(entry)
                dirty[id(entry)] = entry
                out.append((m, "upgraded" if action == "upgrade" else "attached"))
        if dry_run:
            return out
        def body(data):
            peers = index.email_peers(data["name"]) if not data["emails"] else ()
            peers = [p for p in peers if p[1] is not None]
            return render_candidate(
                data, near_matches(data["name"], people), peers)

        ops = []
        for entry in dirty.values():
            if entry["uuid"] is None:
                continue
            ops.append({"op": "set_text", "uuid": entry["uuid"],
                        "text": body(entry["data"])})
        for _m, entry in created:
            ops.append({"op": "create_record",
                        "name": record_name(entry["data"]),
                        "path": CANDIDATES_PATH,
                        "text": body(entry["data"]),
                        "fields": {"entitytype": "Candidate"}})
        if ops:
            bridge(ops)
        return out
    finally:
        lock.close()
