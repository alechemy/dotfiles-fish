"""Source-grounded deliberate capture resolution, separate from passive discovery."""

import hashlib
import json
import re
from datetime import datetime

from entity_candidates import norm, norm_email


PROMPT = """Identify the main subjects of this deliberate capture. Return JSON only:
{{"people": [{{"mention": "exact name as written in the capture",
"email": "email explicitly belonging to that subject, or empty string",
"passage": "exact contiguous passage about this subject"}}]}}

Identify subjects even if the information is short, mundane, or not biographical.
Never expand a name using the roster. Single names are valid. Do not invent a
surname. A relative mentioned as news about the subject is not another subject.
For one subject, its passage is the entire capture. For several subjects, select
non-overlapping passages that together preserve all the capture's information.
If no subject can be identified, return {{"people": []}}. Do not guess.

KNOWN PEOPLE:
{roster}

CAPTURE:
{content}
"""


def revision(text):
    return hashlib.sha256(text.encode()).hexdigest()


def in_text(value, text):
    return bool(value) and re.search(
        r"(?<!\w)" + re.escape(norm(value)) + r"(?!\w)", norm(text)) is not None


def flag(value):
    return str(value or "").strip().lower() in {"1", "true"}


def name_hits(mention, people):
    key = norm(mention)
    hits = {}
    for p in people:
        names = [p["name"]] + p.get("aliases", "").split(",")
        if any(norm(n) == key or (len(key.split()) == 1 and
                                 norm(n).split()[:1] == [key]) for n in names):
            hits[p["uuid"]] = p
    return list(hits.values())


def outcome(status, reason="", subjects=(), choices=()):
    return {"status": status, "reason": reason, "subjects": list(subjects),
            "choices": list(choices)}


def resolve(text, extracted, people, ignored=(), selves=()):
    """Resolve source mentions and explicit subject emails together, never model matches."""
    if not isinstance(extracted, list) or not extracted:
        return outcome("deferred", "no_subject")
    subjects = []
    used = []
    for raw in extracted:
        if not isinstance(raw, dict):
            return outcome("deferred", "invalid_subject")
        mention = raw.get("mention")
        email = raw.get("email") or ""
        if not isinstance(mention, str) or not isinstance(email, str):
            return outcome("deferred", "invalid_subject")
        mention = mention.strip()
        if not mention or len(mention) > 120 or "\n" in mention or "\r" in mention:
            return outcome("deferred", "invalid_subject")
        if not in_text(mention, text) or (email and not in_text(email, text)):
            return outcome("deferred", "ungrounded_subject")
        if norm(mention) in {"he", "she", "they", "him", "her", "them", "i", "me", "we", "us", "you"}:
            return outcome("deferred", "ungrounded_subject")
        if norm(mention) in selves:
            return outcome("unresolved", "self_identity")
        passage = text if len(extracted) == 1 else raw.get("passage", "")
        if not isinstance(passage, str) or not passage or not in_text(mention, passage):
            return outcome("unresolved", "passage_selection_needed")
        if email and not in_text(email, passage):
            return outcome("deferred", "ungrounded_subject")
        start = text.find(passage)
        end = start + len(passage)
        if start < 0 or any(start < b and end > a for a, b in used):
            return outcome("unresolved", "passage_selection_needed")
        used.append((start, end))
        hits = name_hits(mention, people)
        email_hits = [p for p in people if email and norm_email(
            p.get("md", {}).get("mdemail", "")) == norm_email(email)]
        by_name = {p["uuid"] for p in hits}
        by_email = {p["uuid"] for p in email_hits}
        choices = {p["uuid"]: p for p in hits + email_hits}
        if by_name and by_email and not by_name & by_email:
            return outcome("unresolved", "conflicting_identifiers", choices=choices.values())
        targets = by_name & by_email if by_name and by_email else by_name | by_email
        if len(targets) > 1:
            return outcome("unresolved", "ambiguous_name", choices=choices.values())
        target = choices[next(iter(targets))] if targets else None
        if target and flag(target.get("md", {}).get("mdfilingsuppressed")):
            return outcome("unresolved", "suppressed")
        if not target and norm(mention) in ignored:
            return outcome("unresolved", "ignored")
        subjects.append({"kind": "existing" if target else "new",
                         "name": target["name"] if target else mention,
                         "uuid": target["uuid"] if target else "",
                         "evidence": {"mention": mention, "email": email},
                         "passage": passage})
    cursor = 0
    for start, end in sorted(used):
        if text[cursor:start].strip():
            return outcome("unresolved", "passage_selection_needed")
        cursor = end
    if text[cursor:].strip():
        return outcome("unresolved", "passage_selection_needed")
    ids = [s["uuid"] or norm(s["name"]) for s in subjects]
    if len(set(ids)) != len(ids):
        return outcome("unresolved", "repeated_subject")
    return outcome("new" if any(s["kind"] == "new" for s in subjects) else "existing",
                   subjects=subjects)


def contribution(source_uuid, source_revision, subject, source_date):
    key = revision(source_uuid + "|" + source_revision + "|" + norm(subject["evidence"]["mention"]))
    quoted = "\n".join("> " + line for line in subject["passage"].split("\n"))
    block = (f"<!-- capture:{key}:begin -->\n"
             f"### Captured {source_date}\n\n{quoted}\n\n"
             f"[Source](x-devonthink-item://{source_uuid})\n"
             f"<!-- capture:{key}:end -->")
    return {"id": key, "block": block}


def prepare(source, source_date, text, result, processing_date):
    if result["status"] not in {"new", "existing"}:
        raise ValueError("capture is not resolved")
    source_revision = revision(text)
    subjects = []
    for s in result["subjects"]:
        subjects.append(dict(s, contribution=contribution(
            source["uuid"], source_revision, s, source_date), applied=False))
    return {"version": 1, "source_uuid": source["uuid"], "revision": source_revision,
            "text": text, "source_date": source_date, "processing_date": processing_date,
            "status": "filing", "subjects": subjects, "receipt": False}


def decode_manifest(raw, source_uuid):
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or
            manifest.get("source_uuid") != source_uuid or
            manifest.get("status") not in {"filing", "filed"} or
            not isinstance(manifest.get("text"), str) or
            revision(manifest["text"]) != manifest.get("revision") or
            not isinstance(manifest.get("subjects"), list) or not manifest["subjects"] or
            type(manifest.get("receipt")) is not bool):
        raise ValueError("unreadable capture operation; filing is paused")
    for key in ("source_date", "processing_date"):
        datetime.strptime(manifest[key], "%Y-%m-%d")
    for s in manifest["subjects"]:
        if (s.get("kind") not in {"new", "existing"} or
                not isinstance(s.get("name"), str) or not s["name"] or
                not isinstance(s.get("uuid"), str) or
                type(s.get("applied")) is not bool or
                not isinstance(s.get("evidence"), dict) or
                not isinstance(s["evidence"].get("mention"), str) or
                not isinstance(s.get("passage"), str) or
                s["contribution"] != contribution(source_uuid, manifest["revision"], s,
                                                   manifest["source_date"])):
            raise ValueError("unreadable capture contribution; filing is paused")
    evidence = [dict(s["evidence"], passage=s["passage"]) for s in manifest["subjects"]]
    if resolve(manifest["text"], evidence, [])["status"] not in {"new", "existing"}:
        raise ValueError("capture plan is not source-grounded")
    if manifest["status"] == "filed" and (not manifest["receipt"] or
                                            not all(s["applied"] and s["uuid"] for s in manifest["subjects"])):
        raise ValueError("capture completion is not verified")
    return manifest


def encode_manifest(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def receipt_line(manifest):
    def label(name):
        return re.sub(r"([\\\\\[\]])", r"\\\1", name.replace("\n", " "))
    links = ", ".join(f"[{label(s['name'])}](x-devonthink-item://{s['uuid']})"
                      for s in manifest["subjects"])
    key = revision(manifest["source_uuid"] + "|" + manifest["revision"])
    return (f"- 📝 Saved your note about {links}. "
            f"[Source](x-devonthink-item://{manifest['source_uuid']}) "
            f"<!-- capture-receipt:{key} -->")


def replay(bridge, manifest, previous=""):
    """Replay frozen source contributions. Call under filing and candidate mutation locks."""
    def persist():
        nonlocal previous
        encoded = encode_manifest(manifest)
        bridge([{"op": "capture_store", "uuid": manifest["source_uuid"],
                 "expected": previous, "value": encoded, "text": manifest["text"]}])
        previous = encoded
    if not previous:
        persist()
    for s in manifest["subjects"]:
        result = bridge([{"op": "capture_person", "name": s["name"],
                          "uuid": s["uuid"], "evidence": s["evidence"],
                          "creation_id": s["contribution"]["id"],
                          "source_uuid": manifest["source_uuid"],
                          "text": manifest["text"]}])[0]
        if not isinstance(result, dict) or not result.get("uuid") or not result.get("initialized"):
            raise RuntimeError("person initialization could not be verified")
        s["uuid"] = result["uuid"]
        persist()
        block = s["contribution"]
        bridge([{"op": "capture_append", "uuid": s["uuid"],
                 "id": block["id"], "block": block["block"], "expected_present": s["applied"],
                 "source_uuid": manifest["source_uuid"], "text": manifest["text"],
                 "evidence": s["evidence"]}])
        verification = bridge([{"op": "get_text", "uuid": s["uuid"]}])[0]
        if block["block"] not in verification["text"].replace("\r\n", "\n").replace("\r", "\n"):
            raise RuntimeError("capture contribution could not be verified")
        s["applied"] = True
        persist()
    if not manifest["receipt"]:
        day = manifest["processing_date"]
        daily = bridge([{"op": "get_or_create_daily", "date": day,
                         "heading": datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d, %Y")}])[0]
        line = receipt_line(manifest)
        bridge([{"op": "append_pinned", "uuid": daily["uuid"], "line": line}])
        if line not in bridge([{"op": "get_text", "uuid": daily["uuid"]}])[0]["text"]:
            raise RuntimeError("capture receipt could not be verified")
        manifest["receipt"] = True
        persist()
    bridge([{"op": "mark_filed", "uuid": manifest["source_uuid"]}])
    manifest["status"] = "filed"
    persist()
    return outcome("filed", subjects=manifest["subjects"])


def eligible(source, boundary):
    """An explicit persisted activation boundary is required even after local state loss."""
    if not boundary:
        return False
    datetime.strptime(boundary, "%Y-%m-%dT%H:%M:%S")
    return bool(source.get("kind") == "fact" and
                source.get("added_at", "") > boundary)
