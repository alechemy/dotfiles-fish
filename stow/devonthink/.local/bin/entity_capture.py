"""Source-grounded deliberate capture resolution, separate from passive discovery."""

import hashlib
import json
import re
from datetime import datetime

from entity_candidates import CandidateIndex, norm, norm_email


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


def source_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        return "\n".join(lines[i:])
    return text


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


def resolve(text, extracted, people, ignored=(), selves=(), decisions=None):
    """Resolve source mentions and explicit subject emails together, never model matches."""
    if not isinstance(extracted, list) or not extracted:
        return outcome("deferred", "no_subject")
    subjects = []
    used = []
    for position, raw in enumerate(extracted):
        if not isinstance(raw, dict):
            return outcome("deferred", "invalid_subject")
        mention = raw.get("mention")
        email = raw.get("email") or ""
        if not isinstance(mention, str) or not isinstance(email, str):
            return outcome("deferred", "invalid_subject")
        decision = (decisions or {}).get(position, {})
        selected = decision.get("target", "")
        mention = mention.strip()
        if (not mention and not selected) or len(mention) > 120 or "\n" in mention or "\r" in mention:
            return outcome("deferred", "invalid_subject")
        surface = re.search(r"(?<!\w)" + re.escape(mention) + r"(?!\w)", text, re.IGNORECASE) if mention else None
        if surface:
            mention = surface.group()
        if (not selected and not surface) or (email and not in_text(email, text)):
            return outcome("deferred", "ungrounded_subject")
        if not decision and norm(mention) in {"he", "she", "they", "him", "her", "them", "i", "me", "we", "us", "you"}:
            return outcome("deferred", "ungrounded_subject")
        if not decision and norm(mention) in selves:
            return outcome("unresolved", "self_identity")
        passage = text if len(extracted) == 1 else raw.get("passage", "")
        if not isinstance(passage, str) or not passage or (not selected and not in_text(mention, passage)):
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
        if len(targets) > 1 and not (decisions or {}).get(position):
            return outcome("unresolved", "ambiguous_name", choices=choices.values())
        distinct = decision.get("new", False)
        if selected and distinct:
            return outcome("unresolved", "invalid_choice")
        if selected:
            target = next((p for p in people if p["uuid"] == selected), None)
            if target is None or (by_email and selected not in by_email):
                return outcome("unresolved", "conflicting_identifiers")
        elif distinct:
            if by_email:
                return outcome("unresolved", "conflicting_identifiers", choices=choices.values())
            target = None
        else:
            target = choices[next(iter(targets))] if targets else None
        if target and flag(target.get("md", {}).get("mdfilingsuppressed")):
            return outcome("unresolved", "suppressed")
        if not target and norm(mention) in ignored:
            return outcome("unresolved", "ignored")
        if not decision and len(mention.split()) == 1 and any(norm(n).split()[:1] == [norm(mention)] for n in ignored):
            return outcome("unresolved", "ignored_collision")
        if not target and not decision and norm(mention) in {"daughter", "son", "sister", "brother", "wife", "husband", "friend", "colleague", "boss", "mother", "father"}:
            return outcome("unresolved", "no_subject")
        subjects.append({"kind": "existing" if target else "new",
                         "name": target["name"] if target else mention,
                         "uuid": target["uuid"] if target else "",
                         "evidence": dict({"mention": mention, "email": email},
                                          **({"selected_target": selected} if selected else {}),
                                          **({"distinct": True} if distinct else {})),
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


def contribution(source_uuid, source_revision, subject, source_date, legacy=False):
    identity = source_uuid + "|" + source_revision + "|" + norm(subject["evidence"]["mention"])
    if not legacy:
        identity = "capture-passage-v1|" + json.dumps([source_uuid, source_revision, norm(subject["evidence"]["mention"]), source_date, revision(subject["passage"])], separators=(",", ":"))
    key = revision(identity)
    quoted = "\n".join("> " + line for line in subject["passage"].split("\n"))
    block = (f"<!-- capture:{key}:begin -->\n"
             f"### Captured {source_date}\n\n{quoted}\n\n"
             f"[Source](x-devonthink-item://{source_uuid})\n"
             f"<!-- capture:{key}:end -->")
    return {"id": key, "block": block}


def prepare(source, source_date, text, result, processing_date):
    if result["status"] not in {"new", "existing"}:
        raise ValueError("capture is not resolved")
    datetime.strptime(source_date, "%Y-%m-%d")
    datetime.strptime(processing_date, "%Y-%m-%d")
    source_revision = revision(text)
    subjects = []
    for s in result["subjects"]:
        subjects.append(dict(s, contribution=contribution(
            source["uuid"], source_revision, s, source_date), applied=False))
    return {"version": 1, "source_uuid": source["uuid"], "revision": source_revision,
            "text": text, "source_date": source_date, "processing_date": processing_date,
            "status": "filing", "subjects": subjects, "receipt": False}


def pending(source, source_date, text, extracted, result, processing_date):
    return {"version": 1, "source_uuid": source["uuid"], "revision": revision(text),
            "text": text, "source_date": source_date, "processing_date": processing_date,
            "status": "question" if result["status"] == "unresolved" else "deferred",
            "reason": result["reason"], "extracted": extracted, "subjects": [], "receipt": False}


def decode_manifest(raw, source_uuid):
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or
            manifest.get("source_uuid") != source_uuid or
            manifest.get("status") not in {"filing", "filed", "question", "deferred", "correcting", "undone", "revision_question"} or
            not isinstance(manifest.get("text"), str) or
            revision(manifest["text"]) != manifest.get("revision") or
            not isinstance(manifest.get("subjects"), list) or
            type(manifest.get("receipt")) is not bool):
        raise ValueError("unreadable capture operation; filing is paused")
    for key in ("source_date", "processing_date"):
        datetime.strptime(manifest[key], "%Y-%m-%d")
    for s in manifest["subjects"]:
        if (not isinstance(s, dict) or s.get("kind") not in {"new", "existing"} or
                not isinstance(s.get("name"), str) or not s["name"] or
                not isinstance(s.get("uuid"), str) or
                type(s.get("applied")) is not bool or
                not isinstance(s.get("evidence"), dict) or
                not isinstance(s["evidence"].get("mention"), str) or
                not isinstance(s.get("passage"), str) or
                s["contribution"] not in [contribution(source_uuid, manifest["revision"], s, manifest["source_date"]),
                                          contribution(source_uuid, manifest["revision"], s, manifest["source_date"], legacy=True)]):
            raise ValueError("unreadable capture contribution; filing is paused")
    evidence = [dict(s["evidence"], passage=s["passage"]) for s in manifest["subjects"]]
    saved_people = [{"uuid": s["uuid"], "name": s["name"], "aliases": "", "md": {}} for s in manifest["subjects"] if s["uuid"]]
    decisions = {i: {"target": s["evidence"].get("selected_target", ""), "new": s["evidence"].get("distinct", False)}
                 for i, s in enumerate(manifest["subjects"]) if s["evidence"].get("selected_target") or s["evidence"].get("distinct")}
    if any(s["evidence"].get("selected_target") and s["evidence"]["selected_target"] != s["uuid"] for s in manifest["subjects"]):
        raise ValueError("capture target is not source-bound")
    if evidence and resolve(manifest["text"], evidence, saved_people, decisions=decisions)["status"] not in {"new", "existing"}:
        raise ValueError("capture plan is not source-grounded")
    if manifest["status"] in {"filing", "filed", "correcting", "revision_question"} and not evidence:
        raise ValueError("capture operation has no source contributions")
    if manifest["status"] in {"question", "deferred"} and not isinstance(manifest.get("extracted"), list):
        raise ValueError("capture question has no extraction evidence")
    if manifest["status"] == "correcting":
        correction = manifest.get("correction")
        if not isinstance(correction, dict) or correction.get("action") not in {"undo", "reassign", "revise"}:
            raise ValueError("capture correction is unreadable")
        if not isinstance(correction.get("text"), str) or not isinstance(correction.get("removed"), list):
            raise ValueError("capture correction progress is unreadable")
        if correction.get("next"):
            if correction["next"].get("status") not in {"filing", "filed"} or correction["next"].get("text") != correction["text"]:
                raise ValueError("capture correction destination is unreadable")
            decode_manifest(encode_manifest(correction["next"]), source_uuid)
        elif correction["action"] != "undo":
            raise ValueError("capture correction has no destination")
        edit = correction.get("source_edit")
        if edit:
            if (not isinstance(edit, dict) or not isinstance(edit.get("from"), str) or edit.get("to") != correction["text"] or
                    edit.get("from_revision", revision(edit["from"])) != revision(edit["from"]) or
                    edit.get("to_revision", revision(edit["to"])) != revision(edit["to"])):
                raise ValueError("capture source edit is unreadable")
        if correction.get("receipt_date"):
            datetime.strptime(correction["receipt_date"], "%Y-%m-%d")
    if manifest.get("replacement"):
        replacement = manifest["replacement"]
        baseline = manifest.get("replacement_from")
        if (manifest["status"] not in {"question", "deferred"} or not isinstance(baseline, str) or
                manifest.get("replacement_from_revision", manifest["revision"]) != revision(baseline) or
                replacement.get("status") not in {"filing", "filed"}):
            raise ValueError("capture replacement is unreadable")
        decode_manifest(encode_manifest(replacement), source_uuid)
    if not isinstance(manifest.get("history", []), list):
        raise ValueError("capture history is unreadable")
    for old in manifest.get("history", []):
        if not isinstance(old, dict) or old.get("source_uuid") != source_uuid or not isinstance(old.get("text"), str) or old.get("revision") != revision(old["text"]):
            raise ValueError("capture history lost its source evidence")
    if manifest["status"] == "filed" and (not manifest["receipt"] or
                                            not all(s["applied"] and s["uuid"] for s in manifest["subjects"])):
        raise ValueError("capture completion is not verified")
    return manifest


def source_history(prior, text, source_date):
    history = list(prior.get("history", []))
    if prior["revision"] != revision(text) or prior["source_date"] != source_date:
        history.append({k: v for k, v in prior.items() if k != "history"})
    return history


def encode_manifest(manifest):
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def receipt_line(manifest):
    def label(name):
        return re.sub(r"([\\\\\[\]])", r"\\\1", name.replace("\n", " "))
    links = ", ".join(f"[{label(s['name'])}](x-devonthink-item://{s['uuid']})"
                      for s in manifest["subjects"])
    key = manifest.get("receipt_id") or revision(manifest["source_uuid"] + "|" + manifest["revision"])
    detail = manifest.get("review_url", "")
    correction_link = f" [Details and correction]({detail}#{manifest['source_uuid']})" if detail else ""
    return (f"- 📝 Saved your note about {links}. "
            f"[Source](x-devonthink-item://{manifest['source_uuid']}){correction_link} "
            f"<!-- capture-receipt:{key} -->")


def validate_frozen(bridge, manifest, selves=()):
    people, candidates = bridge([{"op": "dump_people", "include_bodies": False}, {"op": "list_candidates"}])
    extracted = [dict(s["evidence"], passage=s["passage"]) for s in manifest["subjects"]]
    decisions = {i: {"target": s["evidence"].get("selected_target", ""), "new": s["evidence"].get("distinct", False)}
                 for i, s in enumerate(manifest["subjects"]) if s["evidence"].get("selected_target") or s["evidence"].get("distinct")}
    result = resolve(manifest["text"], extracted, people, CandidateIndex(candidates).ignored_names(), selves, decisions)
    if result["status"] not in {"new", "existing"}:
        raise RuntimeError(QUESTIONS.get(result["reason"], "The capture identity changed. Its source is retained."))
    for frozen, current in zip(manifest["subjects"], result["subjects"]):
        if frozen["evidence"].get("distinct"):
            continue
        if frozen["uuid"] and frozen["uuid"] != current["uuid"]:
            raise RuntimeError("The capture identity changed. Its source is retained.")
        if not frozen["uuid"] and current["uuid"]:
            frozen.update(uuid=current["uuid"], kind="existing", name=current["name"])


def replay(bridge, manifest, previous="", persist_manifest=None, selves=(), preserve_contributions=()):
    """Replay frozen source contributions. Call under filing and candidate mutation locks."""
    def persist():
        nonlocal previous
        if persist_manifest:
            persist_manifest(manifest)
            return
        encoded = encode_manifest(manifest)
        bridge([{"op": "capture_store", "uuid": manifest["source_uuid"],
                 "expected": previous, "value": encoded, "text": manifest["text"]}])
        previous = encoded
    if manifest["status"] != "filed":
        validate_frozen(bridge, manifest, selves)
    if not previous or previous != encode_manifest(manifest):
        persist()
    for s in manifest["subjects"]:
        if s["contribution"]["id"] in preserve_contributions:
            if not s["uuid"]:
                raise RuntimeError("An edited contribution has no verified destination.")
            s["applied"] = True
            persist()
            continue
        result = bridge([{"op": "capture_person", "name": s["name"],
                          "uuid": s["uuid"], "evidence": s["evidence"],
                          "creation_id": s["contribution"]["id"], "initialize": not bool(s["uuid"]),
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
        manifest["receipt_uuid"] = daily["uuid"]
        persist()
        line = receipt_line(manifest)
        bridge([{"op": "append_pinned", "uuid": daily["uuid"], "line": line}])
        if line not in bridge([{"op": "get_text", "uuid": daily["uuid"]}])[0]["text"]:
            raise RuntimeError("capture receipt could not be verified")
        manifest["receipt"] = True
        persist()
    bridge([{"op": "mark_filed", "uuid": manifest["source_uuid"]}])
    manifest["status"] = "filed"
    persist()
    if not persist_manifest:
        bridge([{"op": "capture_retire_source", "uuid": manifest["source_uuid"]}])
    return outcome("filed", subjects=manifest["subjects"])


QUESTIONS = {
    "ambiguous_name": "Which person is this note about?",
    "conflicting_identifiers": "The name and email point to different people. Correct the note or separate its passages.",
    "passage_selection_needed": "Which passage belongs to each person?",
    "repeated_subject": "Combine the passages about the same person before saving.",
    "suppressed": "Filing is paused for this person. Change their filing preference in DEVONthink before saving.",
    "ignored": "This identity was explicitly ignored. Restore that decision before saving.",
    "ignored_collision": "This name could refer to an ignored person. Is it someone else?",
    "source_changed": "This saved note changed. Review its replacement before saving it.",
    "edited_contribution": "An earlier contribution was edited. Keep that text and finish this capture's correction?",
    "destination_edited": "The destination changed before correction finished. Keep that change and finish this correction?",
    "no_subject": "Who is this note about?",
    "legacy_filing": "This note already contributed to a Person. Review that filing before saving; Undo only removes the new capture's contribution.",
    "ungrounded_subject": "The subject could not be identified from the note. Select its name and passage.",
    "model_unavailable": "Waiting for local processing. Your note is retained.",
}


def view(manifest, people):
    status = manifest["status"]
    text = manifest.get("correction", {}).get("text", manifest.get("changed_text", manifest["text"]))
    choices = []
    if status in {"question", "deferred"}:
        result = resolve(manifest["text"], manifest.get("extracted", []), people)
        choices = [{"uuid": p["uuid"], "name": p["name"],
                    "city": p.get("md", {}).get("mdcity", ""),
                    "employer": p.get("md", {}).get("mdemployer", "")} for p in result["choices"]]
    return {"uuid": manifest["source_uuid"], "revision": revision(text),
            "status": status, "text": text,
            "date": manifest["source_date"], "deferred": manifest.get("deferred", False),
            "question": QUESTIONS.get(manifest.get("reason"), "Review this retained note before saving."),
            "conflict": manifest.get("reason") in {"edited_contribution", "destination_edited"},
            "choices": choices, "subjects": [{"uuid": s["uuid"], "name": s["name"],
                "mention": s["evidence"]["mention"], "email": s["evidence"].get("email", ""),
                "passage": s["passage"]} for s in manifest["subjects"]] or [
                {"uuid": "", "name": s["mention"], "mention": s["mention"], "email": s.get("email", ""),
                 "passage": manifest["text"] if len(manifest.get("extracted", [])) == 1 else s.get("passage", "")}
                for s in manifest.get("extracted", []) if isinstance(s, dict) and
                isinstance(s.get("mention"), str) and in_text(s["mention"], manifest["text"]) and
                isinstance(s.get("email", ""), str) and (not s.get("email") or in_text(s["email"], manifest["text"]))]}


def start_correction(manifest, action, next_manifest=None, current_text=None):
    if manifest["status"] == "correcting":
        return manifest
    if action not in {"undo", "reassign", "revise"}:
        raise ValueError("unknown capture correction")
    if manifest["status"] not in {"filed", "filing", "revision_question"}:
        raise ValueError("this capture has no filing to correct")
    key = revision(encode_manifest(manifest) + "|" + action + "|" + encode_manifest(next_manifest))
    manifest["correction"] = {"id": key, "action": action, "next": next_manifest,
                              "text": current_text if current_text is not None else manifest["text"],
                              "removed": [], "receipt_date": datetime.now().strftime("%Y-%m-%d")}
    if next_manifest:
        next_manifest["receipt_id"] = key
    manifest["status"] = "correcting"
    return manifest


def correct(bridge, manifest, previous, selves=()):
    correction = manifest["correction"]
    if correction.get("source_edit"):
        repair_source_edit(bridge, manifest["source_uuid"], correction["source_edit"])
    def persist(_next=None):
        nonlocal previous
        if _next is not None:
            correction["next"] = _next
        encoded = encode_manifest(manifest)
        bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
                 "value": encoded, "text": correction["text"]}])
        previous = encoded
    persist()
    destination = correction.get("next")
    def verify_destinations(only_started=False):
        destinations = []
        for subject in destination["subjects"] if destination else []:
            if only_started and not subject["uuid"]:
                continue
            token = "destination|" + subject["uuid"] + "|" + subject["contribution"]["id"]
            if token in correction.get("keep_edited", []):
                continue
            actual = bridge([{"op": "get_text", "uuid": subject["uuid"]}])[0]["text"].replace("\r\n", "\n").replace("\r", "\n")
            started = (destination["status"] == "filed" or subject["applied"] or
                       f"<!-- capture:{subject['contribution']['id']}:" in actual or
                       any(old["applied"] and old["uuid"] == subject["uuid"] and
                           old["contribution"]["id"] == subject["contribution"]["id"]
                           for old in manifest["subjects"]))
            if only_started and not started:
                continue
            if actual.count(subject["contribution"]["block"]) != 1:
                manifest["reason"] = "destination_edited"
                correction["conflict_token"] = token
                persist()
                raise RuntimeError("The destination contribution changed. Its source and original filing are retained.")
            destinations.append({"uuid": subject["uuid"], "body": actual})
        return destinations
    verify_destinations(only_started=True)
    if destination and destination["status"] != "filed":
        preserved = [s["contribution"]["id"] for s in destination["subjects"]
                     if "destination|" + s["uuid"] + "|" + s["contribution"]["id"] in correction.get("keep_edited", [])]
        replay(bridge, destination, persist_manifest=persist, selves=selves, preserve_contributions=preserved)
    destinations = verify_destinations()
    for s in manifest["subjects"]:
        token = s["uuid"] + "|" + s["contribution"]["id"]
        if token in correction["removed"] or not s["uuid"]:
            continue
        if destination and any(n["uuid"] == s["uuid"] and n["contribution"] == s["contribution"]
                               for n in destination["subjects"]):
            continue
        body = bridge([{"op": "get_text", "uuid": s["uuid"]}])[0]["text"].replace("\r\n", "\n").replace("\r", "\n")
        if token in correction.get("keep_edited", []):
            correction["removed"].append(token)
            persist()
            continue
        block = s["contribution"]["block"]
        intents = correction.setdefault("removal_intents", {})
        already_removed = intents.get(token) == revision(body)
        if not already_removed and (body.count(block) != 1):
            if s["applied"] or f"<!-- capture:{s['contribution']['id']}:" in body:
                manifest["reason"] = "edited_contribution"
                correction["conflict_token"] = token
                persist()
                raise RuntimeError("The capture was edited on the Person. Its content is retained.")
            already_removed = True
        if not already_removed:
            intents[token] = revision(body.replace(block, ""))
            persist()
            bridge([{"op": "capture_remove", "uuid": s["uuid"], "id": s["contribution"]["id"],
                     "block": block, "source_uuid": manifest["source_uuid"], "expected_body": body,
                     "expected_operation": previous, "text": correction["text"], "destinations": destinations}])
            body = bridge([{"op": "get_text", "uuid": s["uuid"]}])[0]["text"]
            if f"<!-- capture:{s['contribution']['id']}:begin -->" in body:
                raise RuntimeError("capture removal could not be verified")
        for target in destinations:
            if target["uuid"] == s["uuid"]:
                target["body"] = body.replace("\r\n", "\n").replace("\r", "\n")
        correction["removed"].append(token)
        persist()
    day = correction.setdefault("receipt_date", datetime.now().strftime("%Y-%m-%d"))
    if not correction.get("receipt_uuid"):
        daily = bridge([{"op": "get_or_create_daily", "date": day, "heading": day}])[0]
        correction["receipt_uuid"] = daily["uuid"]
        persist()
    daily = {"uuid": correction["receipt_uuid"]}
    word = "Undid" if correction["action"] == "undo" else "Corrected"
    retained = " Edited text was kept." if correction.get("keep_edited") else ""
    line = (f"- 📝 {word} this capture's filing.{retained} [Source](x-devonthink-item://{manifest['source_uuid']}) "
            f"<!-- capture-receipt:{revision(correction['id'] + '|status')} -->")
    bridge([{"op": "append_pinned", "uuid": daily["uuid"], "line": line}])
    if line not in bridge([{"op": "get_text", "uuid": daily["uuid"]}])[0]["text"]:
        raise RuntimeError("correction receipt could not be verified")
    history = list(manifest.get("history", []))
    snapshot = {k: v for k, v in manifest.items() if k not in {"history", "correction"}}
    snapshot["status"] = "undone"
    snapshot["preserved_manual"] = list(correction.get("keep_edited", []))
    history.append(snapshot)
    edit = correction.get("source_edit")
    if edit and edit["from"] != manifest["text"]:
        history.append(pending({"uuid": manifest["source_uuid"]}, manifest["source_date"], edit["from"], [],
                               outcome("unresolved", "source_changed"), day))
    if destination:
        final = dict(destination, history=history)
    else:
        final = dict(snapshot, history=history, subjects=[], text=correction["text"],
                     revision=revision(correction["text"]))
        final.pop("changed_text", None)
        final.pop("reason", None)
    bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
             "value": encode_manifest(final), "text": correction["text"]}])
    bridge([{"op": "capture_retire_source", "uuid": manifest["source_uuid"]}])
    return final


def repair_source_edit(bridge, source_uuid, edit):
    actual = source_text(bridge([{"op": "get_text", "uuid": source_uuid}])[0]["text"])
    if actual == edit["from"]:
        bridge([{"op": "capture_source_edit", "uuid": source_uuid,
                 "expected_text": actual, "text": edit["to"]}])
    elif actual != edit["to"]:
        raise RuntimeError("The source changed during correction. Its text is retained.")


def replace_pending(bridge, manifest, previous, selves=()):
    next_manifest = manifest["replacement"]
    repair_source_edit(bridge, manifest["source_uuid"], {"from": manifest["replacement_from"], "to": next_manifest["text"]})
    return replay(bridge, next_manifest, previous, selves=selves)


def eligible(source, boundary):
    """An explicit persisted activation boundary is required even after local state loss."""
    if not boundary:
        return False
    datetime.strptime(boundary, "%Y-%m-%dT%H:%M:%S")
    return bool(source.get("kind") == "fact" and
                source.get("added_at", "") > boundary)
