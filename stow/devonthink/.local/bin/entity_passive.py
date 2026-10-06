import copy
import json
import os
import stat
import tempfile
import re
import unicodedata

import entity_biographical as bio

MAX_BYTES = 2 * 1024 * 1024
CONTROL_FIELDS = ("entitytype", "filingsuppressed", "email")


def snapshot(source, raw_text, text, source_date, extraction_input=None, raw_field="plainText"):
    value = {"uuid": source["uuid"], "name": source["name"], "kind": source["kind"],
             "eventdate": source.get("eventdate", ""), "added": source.get("added", ""),
             "date": source_date, "raw_field": raw_field, "raw_revision": bio.digest(raw_text), "revision": bio.digest(text),
             "input": text if extraction_input is None else extraction_input,
             "input_parameters": {"head_words": 6000, "tail_words": 1000}}
    value["input_revision"] = bio.digest(value["input"])
    if len(raw_text.encode()) + len(text.encode()) < 200000:
        value.update(raw_text=raw_text, text=text)
    return value


def controls(person):
    return {"uuid": person["uuid"], "name": person["name"], "aliases": person.get("aliases", ""),
            **{field: str(person.get("md", {}).get("md" + field, "") or "") for field in CONTROL_FIELDS}}


def union_aliases(existing, incoming):
    values, seen = [], set()
    for value in (existing + "," + incoming).split(","):
        value = value.strip()
        if value and key(value) not in seen:
            seen.add(key(value))
            values.append(value)
    return ", ".join(values)


def key(value):
    value = unicodedata.normalize("NFKD", value or "")
    return re.sub(r"\s+", " ", "".join(c for c in value if not unicodedata.combining(c))).strip().casefold()


def person_keys(person):
    return {key(person["name"])} | {key(v) for v in person.get("aliases", "").split(",") if key(v)} | {key(str(person.get("md", {}).get("mdemail", "") or "")).removeprefix("mailto:")}


def identity_owners(keys, people):
    return {key(value): sorted(p["uuid"] for p in people if key(value) in person_keys(p)) for value in keys if key(value)}


def creation_id(plan_id, name):
    return bio.digest(bio.encoded([plan_id, "new:" + name]))


def normalize_email(value):
    return re.sub(r"^mailto:", "", str(value or "").strip(), flags=re.I).lower()


def targets_for(ops, people, plan_id):
    targets = {}
    for op in ops:
        if op["op"] == "ensure_person":
            targets["new:" + op["name"]] = {"name": op["name"], "new": True,
                "creation": creation_id(plan_id, op["name"]), "aliases": op.get("aliases", ""), "email": (op.get("fields") or {}).get("email", "")}
        elif op["op"] in {"append_log", "set_field", "bump_lastcontact", "add_aliases"} and not op.get("target_name"):
            person = next((p for p in people if p["uuid"] == op["uuid"]), None)
            if person is None:
                raise ValueError("The Person changed. Prepare a new plan.")
            targets.setdefault(op["uuid"], controls(person))
    for target in targets.values():
        target["owners"] = identity_owners([target["name"]] + target["aliases"].split(",") + [target["email"]], people)
        if target.get("new"):
            continue
        target["allowed_aliases"] = [target["aliases"]]
        target["allowed_email"] = [target["email"]]
        aliases = target["aliases"]
        for op in ops:
            if op.get("uuid") != target["uuid"]:
                continue
            if op["op"] == "add_aliases":
                aliases = union_aliases(aliases, op["aliases"])
                target["allowed_aliases"].append(aliases)
            if op["op"] == "set_field" and op["field"] == "email":
                target["allowed_email"].append(normalize_email(op["value"]))
    return list(targets.values())


def prepare(ops, sources, people, suggest=None, origin=None, auto=False, blocked="", analyze_now=True):
    identity = bio.digest(bio.encoded([ops, sources, origin]))
    envelope = {"op": "passive_plan", "version": 1, "id": identity, "sources": sources,
                "inputs": copy.deepcopy(ops), "targets": [],
                "origin": origin, "auto": auto, "status": "waiting", "reason": blocked or "comparison_unavailable",
                "questions": [], "answers": {}, "ops": []}
    store_evidence(envelope)
    try:
        envelope["targets"] = targets_for(ops, people, identity)
    except ValueError:
        blocked = blocked or "identity_changed"
        envelope["reason"] = blocked
    store_evidence(envelope)
    if blocked:
        envelope["status"] = "blocked"
        return bounded(envelope)
    return analyze(envelope, people, suggest) if analyze_now else bounded(envelope)


def read(ops, allow_oversized=False):
    markers = [op for op in ops if op.get("op") == "passive_plan"]
    if not markers:
        return None
    if len(markers) != 1 or ops[0] is not markers[0]:
        raise ValueError("The passive review plan is unreadable.")
    envelope = markers[0]
    if envelope.get("variant") == "oversized_evidence":
        if (set(envelope) != {"op", "version", "variant", "id", "evidence", "sources", "origin", "auto", "status", "reason", "inputs", "targets", "questions", "answers", "ops"} or
                envelope["version"] != 1 or envelope["status"] != "waiting" or envelope["reason"] != "oversized_review" or
                not re.fullmatch(r"[0-9a-f]{64}", envelope.get("evidence", "")) or not re.fullmatch(r"[0-9a-f]{64}", envelope.get("id", "")) or
                any(envelope[k] for k in ("inputs", "targets", "questions", "answers", "ops")) or ops != [envelope] or
                len(bio.encoded(envelope).encode()) > MAX_BYTES):
            raise ValueError("The oversized evidence pointer changed.")
        return envelope
    if (envelope.get("version") != 1 or envelope.get("status") not in {"waiting", "blocked", "question", "ready"} or
            not isinstance(envelope.get("sources"), list) or not isinstance(envelope.get("inputs"), list) or
            envelope.get("id") != bio.digest(bio.encoded([envelope["inputs"], envelope["sources"], envelope.get("origin")])) or
            ops[1:] != envelope.get("ops") or not allow_oversized and len(bio.encoded(envelope).encode()) > MAX_BYTES):
        raise ValueError("The passive review plan changed. Prepare a new plan.")
    for source in envelope["sources"]:
        if (source.get("input_parameters") != {"head_words": 6000, "tail_words": 1000} or
                bio.digest(source["input"]) != source["input_revision"] or
                "raw_text" in source and bio.digest(source["raw_text"]) != source["raw_revision"] or
                "text" in source and bio.digest(source["text"]) != source["revision"]):
            raise ValueError("The frozen source evidence changed.")
    return envelope


def fence(envelope):
    return [envelope] + envelope["ops"]


def candidates(person):
    return [row for row in bio.render(person.get("body", ""), person["uuid"]) if
            row["visible"] == row["data"]["baseline"]["text"] and row["date"] == row["data"]["baseline"]["log_date"]] + bio.legacy_rows(
                person.get("body", ""), person["uuid"], [person["name"]] + person.get("aliases", "").split(","))


def analyze(envelope, people, suggest):
    result = copy.deepcopy(envelope)
    result.update(status="ready", reason="", questions=[], answers={}, ops=copy.deepcopy(envelope["inputs"]))
    failed = False
    for op in result["ops"]:
        if op["op"] == "ensure_person":
            op["passive_creation"] = creation_id(envelope["id"], op["name"])
        if not op.get("assertions"):
            continue
        person = next((p for p in people if p["uuid"] == op.get("uuid")), None)
        try:
            rows = candidates(person) if person else []
        except ValueError:
            result.update(status="blocked", reason="ownership_unreadable", questions=[], ops=[])
            return bounded(result, envelope)
        draft = []
        for assertion in op["assertions"]:
            baseline = assertion["baseline"]
            assertion["passive_explicit"] = True
            exact = [r for r in rows + draft if r["data"]["baseline"]["text"] == baseline["text"]]
            compatible = [r for r in exact if r["data"]["baseline"]["temporal_context"] == baseline["temporal_context"]]
            if len(compatible) == 1:
                assertion.update(id=compatible[0]["data"]["id"], baseline=compatible[0]["data"]["baseline"], existing=True,
                                 selected={"visible": compatible[0]["visible"], "date": compatible[0]["date"]})
                if compatible[0] in draft:
                    assertion["draft"] = True
                if compatible[0].get("legacy_line"):
                    assertion["legacy"] = {"line": compatible[0]["legacy_line"], "data": compatible[0]["data"]}
            elif exact:
                proposed = exact
            elif rows:
                try:
                    if suggest is None:
                        raise bio.ComparisonUnavailable()
                    ids = suggest(baseline["text"], assertion["reference"]["original_date"],
                                  [{"id": r["data"]["id"], **r["data"]["baseline"]} for r in rows])
                    if not isinstance(ids, list) or any(not isinstance(i, str) or i not in {r["data"]["id"] for r in rows} for i in ids):
                        raise bio.ComparisonUnavailable()
                    proposed = [r for r in rows if r["data"]["id"] in ids]
                except bio.ComparisonUnavailable:
                    failed = True
                    proposed = []
            else:
                proposed = []
            if not assertion.get("existing") and proposed:
                result["questions"].append({"key": assertion["reference"]["id"], "person_uuid": person["uuid"],
                    "person_name": person["name"], "assertion": baseline["text"], "evidence": assertion["reference"]["evidence"],
                    "observed_date": assertion["reference"]["original_date"], "candidates": [
                        {"id": r["data"]["id"], **r["data"]["baseline"], "visible": r["visible"], "date": r["date"]} for r in proposed]})
            if not assertion.get("existing"):
                assertion["separate"] = True
                draft.append({"data": {"id": assertion["id"], "baseline": baseline}, "visible": baseline["text"], "date": baseline["log_date"]})
    if failed:
        result.update(status="waiting", reason="comparison_unavailable", questions=[], ops=[])
    elif result["questions"]:
        result["status"] = "question"
    return bounded(result, envelope)


def validate_people(envelope, people):
    for target in envelope["targets"]:
        allowed = target.get("uuid")
        if target.get("new"):
            if target["creation"] != creation_id(envelope["id"], target["name"]):
                raise ValueError("The creation identity changed.")
            owned = [p for p in people if "<!-- passive-created:" + target["creation"] + ":" + p["uuid"] + " -->" in p.get("body", "")]
            if len(owned) > 1:
                raise ValueError("The new Person ownership is duplicated.")
            allowed = owned[0]["uuid"] if owned else None
        current_owners = identity_owners(target["owners"], people)
        if any(set(current_owners[k]) - {allowed} != set(owners) - {allowed} for k, owners in target["owners"].items()):
            raise ValueError("The Person resolution changed.")
        if target.get("new"):
            keys = {target["name"].casefold()} | {v.strip().casefold() for v in target["aliases"].split(",") if v.strip()}
            hits = [p for p in people if keys.intersection({p["name"].casefold()} | {v.strip().casefold() for v in p.get("aliases", "").split(",")}) or
                    target["email"] and target["email"].casefold() == str(p.get("md", {}).get("mdemail", "")).casefold()]
            allowed_emails = ["", target["email"], normalize_email(target["email"])] + [normalize_email(op["value"]) for op in envelope["ops"] if op.get("target_name") == target["name"] and op["op"] == "set_field" and op["field"] == "email"]
            if hits and (len(hits) != 1 or "<!-- passive-created:" + target["creation"] + ":" + hits[0]["uuid"] + " -->" not in hits[0].get("body", "") or
                    hits[0]["name"] != target["name"] or hits[0].get("aliases", "") not in {"", target["aliases"]} or
                    controls(hits[0])["email"] not in allowed_emails or controls(hits[0])["entitytype"] != "Person" or
                    controls(hits[0])["filingsuppressed"].lower() not in {"", "0", "false", "no"}):
                raise ValueError("The new Person now resolves to a saved Person. Reanalyze before filing.")
            continue
        person = next((p for p in people if p["uuid"] == target["uuid"]), None)
        if person is None:
            raise ValueError("The Person is missing.")
        current = controls(person)
        if (current["name"] != target["name"] or current["aliases"] not in target["allowed_aliases"] or
                current["email"] not in target["allowed_email"] or current["entitytype"] != "Person" or
                current["filingsuppressed"].lower() not in {"", "0", "false", "no"}):
            raise ValueError("The Person identity or filing controls changed.")


def answer(envelope, answers, people):
    if envelope["status"] != "question" or not isinstance(answers, dict) or set(answers) != {q["key"] for q in envelope["questions"]}:
        raise ValueError("Answer every displayed comparison before approving.")
    validate_people(envelope, people)
    result = copy.deepcopy(envelope)
    for question in envelope["questions"]:
        chosen = answers[question["key"]]
        assertion = next(a for op in result["ops"] for a in op.get("assertions", []) if a["reference"]["id"] == question["key"])
        if chosen in {"separate", "source"}:
            assertion["separate"] = True
            if chosen == "source":
                assertion["baseline"]["text"] = assertion["reference"]["evidence"]
            continue
        expected = next((c for c in question["candidates"] if c["id"] == chosen), None)
        person = next((p for p in people if p["uuid"] == question["person_uuid"]), None)
        rows = candidates(person) if person else []
        matches = [r for r in rows if r["data"]["id"] == chosen]
        if expected is None or len(matches) != 1 or matches[0]["data"]["baseline"] != {k: expected[k] for k in assertion["baseline"]} or matches[0]["visible"] != expected["visible"] or matches[0]["date"] != expected["date"]:
            raise ValueError("The selected assertion changed. Refresh and retry comparison.")
        assertion.update(id=chosen, baseline=matches[0]["data"]["baseline"], existing=True,
                         selected={"visible": expected["visible"], "date": expected["date"]})
        if matches[0].get("legacy_line"):
            assertion["legacy"] = {"line": matches[0]["legacy_line"], "data": matches[0]["data"]}
        assertion.pop("separate", None)
    result.update(status="ready", answers=answers)
    return bounded(result, envelope)


def executable(envelope):
    if envelope["status"] != "ready" or set(envelope["answers"]) != {q["key"] for q in envelope["questions"]}:
        raise ValueError("Passive comparison is unresolved. Use the review app.")
    if len(envelope["ops"]) != len(envelope["inputs"]):
        raise ValueError("The passive operation changed.")
    drafts = {}
    for original, op in zip(envelope["inputs"], envelope["ops"]):
        if op["op"] == "ensure_person" and op.get("passive_creation") != creation_id(envelope["id"], op["name"]):
            raise ValueError("The passive creation identity changed.")
        before = {k: v for k, v in original.items() if k != "assertions"}
        after = {k: v for k, v in op.items() if k not in {"assertions", "passive_creation"}}
        if before != after or len(original.get("assertions", [])) != len(op.get("assertions", [])):
            raise ValueError("The passive operation changed.")
        for incoming, assertion in zip(original.get("assertions", []), op.get("assertions", [])):
            if assertion.get("passive_explicit") is not True or assertion.get("draft") and drafts.get(assertion["id"]) != assertion["baseline"]:
                raise ValueError("The passive assertion mode changed.")
            if not assertion.get("existing"):
                drafts[assertion["id"]] = assertion["baseline"]
            ref = incoming["reference"]
            source = next((s for s in envelope["sources"] if s["uuid"] == ref["source_uuid"]), None)
            if source is None or ref["source_revision"] != source["revision"] or ref["original_date"] != source["date"] or assertion["reference"] != ref:
                raise ValueError("Passive support is not source-bound.")
            chosen = envelope["answers"].get(ref["id"])
            if assertion.get("existing"):
                if chosen is not None:
                    question = next(q for q in envelope["questions"] if q["key"] == ref["id"])
                    expected = next((c for c in question["candidates"] if c["id"] == chosen), None)
                    if chosen != assertion["id"] or expected is None or assertion["baseline"] != {k: expected[k] for k in assertion["baseline"]}:
                        raise ValueError("The passive choice changed.")
                elif (assertion["baseline"]["text"] != incoming["baseline"]["text"] or
                      assertion["baseline"]["temporal_context"] != incoming["baseline"]["temporal_context"]):
                    raise ValueError("Passive equivalence was not confirmed.")
            elif (assertion["id"] != incoming["id"] or assertion["baseline"] != dict(incoming["baseline"], text=ref["evidence"] if chosen == "source" else incoming["baseline"]["text"])):
                raise ValueError("The separate passive assertion changed.")
    return copy.deepcopy(envelope["ops"])


def evidence_directory():
    directory = os.path.expanduser("~/.local/state/devonthink/entity-passive-evidence")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if not stat.S_ISDIR(os.lstat(directory).st_mode):
        raise ValueError("The evidence namespace is unavailable.")
    return directory


def evidence_path(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("Invalid evidence identity.")
    return os.path.join(evidence_directory(), identity + ".json")


def evidence_bytes(identity):
    path = evidence_path(identity)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("The evidence is not a regular file.")
            data = stream.read()
        if bio.digest(data.decode("utf-8")) != identity:
            raise ValueError("The evidence digest changed.")
        value = json.loads(data)
        if bio.encoded(value).encode() != data:
            raise ValueError("The evidence encoding changed.")
        return value
    except (OSError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The frozen evidence is missing or unreadable.") from exc


def store_evidence(envelope):
    data = bio.encoded(envelope).encode()
    identity = bio.digest(data.decode())
    path = evidence_path(identity)
    if os.path.lexists(path):
        if evidence_bytes(identity) != envelope:
            raise ValueError("The frozen evidence changed.")
        return identity
    fd, temporary = tempfile.mkstemp(dir=evidence_directory(), prefix=".evidence-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if evidence_bytes(identity) != envelope:
                raise ValueError("The frozen evidence changed.")
    finally:
        os.unlink(temporary)
    return identity


def load_evidence(pointer):
    read([pointer])
    envelope = evidence_bytes(pointer["evidence"])
    read(fence(envelope), allow_oversized=True)
    if oversized_pointer(envelope) != pointer:
        raise ValueError("The frozen evidence pointer changed.")
    return envelope


def oversized_pointer(envelope):
    return {"op": "passive_plan", "version": 1, "variant": "oversized_evidence", "id": envelope["id"],
            "evidence": store_evidence(envelope), "sources": [{"uuid": s["uuid"], "name": "Oversized passive evidence", "date": s["date"]} for s in envelope["sources"][:1]],
            "origin": {"uuid": envelope["origin"]["uuid"]} if envelope.get("origin") else None,
            "auto": envelope["auto"], "status": "waiting", "reason": "oversized_review", "inputs": [], "targets": [],
            "questions": [], "answers": {}, "ops": []}


def bounded(envelope, original=None):
    if len(body(envelope).encode()) > MAX_BYTES:
        return oversized_pointer(original or envelope)
    return envelope


def body(envelope):
    source = envelope["sources"][0] if envelope["sources"] else {"name": "Candidate evidence", "uuid": "", "date": ""}
    return "\n".join(["# File: " + source["name"], "", "Source: [" + source["name"] + "](x-devonthink-item://" + source["uuid"] + ") (" + source["date"] + ")", "",
        "Passive comparison: " + envelope["status"] + (". " + envelope["reason"] if envelope["reason"] else ""),
        "Use the private review app. Moving unresolved comparisons to Approved does not apply them.", "", "## Ops", "", "```json", json.dumps(fence(envelope), indent=2, ensure_ascii=True), "```", ""])
