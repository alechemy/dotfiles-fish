import copy
import hashlib
from datetime import date
import json
import re

HEADER = "## Biographical Log"
MARKER = re.compile(r"<!-- bio:v2:(.*?) -->")
FLAT = re.compile(r"^- (\d{4}-\d{2}-\d{2}) — (.*)$")
GROUP = re.compile(r"^- (\d{4}-\d{2}-\d{2})\s*$")
SOURCE = re.compile(r"\s*\(\[source\]\(x-devonthink-item://[^)]+\)\)")
FACT = re.compile(r"\s*<!-- fact:[a-f0-9]+ -->")
LINK = re.compile(r"\[([^]]+)\]\(x-devonthink-item://[^)]+\)")
LITERAL = re.compile("<(\u2060*)!-- (?=bio:v2:|fact:|capture(?:-receipt|-created)?:)")


def encode_literal(text):
    return LITERAL.sub(lambda match: "<" + "\u2060" * (len(match[1]) + 1) + "!-- ", text)


def decode_literal(text):
    def replace(match):
        if not match[1]:
            raise ValueError("Unescaped ownership syntax in assertion text.")
        return "<" + "\u2060" * (len(match[1]) - 1) + "!-- "
    value = LITERAL.sub(replace, text)
    if encode_literal(value) != text:
        raise ValueError("Assertion rendering is not canonical.")
    return value


def assertion_text(text, baseline):
    original = set(match[0] for match in LINK.finditer(baseline))
    return LINK.sub(lambda match: match[0] if match[0] in original else match[1], text)


def source_links(data):
    return "".join(" ([source](x-devonthink-item://" + uuid + "))" for uuid in
                   dict.fromkeys(ref["source_uuid"] for ref in data["references"]))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e")


def clean(text):
    return LINK.sub(r"\1", FACT.sub("", SOURCE.sub("", MARKER.sub("", text)))).strip()


def relative(text, mention):
    if isinstance(mention, (tuple, list)):
        for name in sorted(set(mention), key=len, reverse=True):
            changed = relative(text, name.strip())
            if changed != text:
                return changed
        return text
    verbs = r"(?:has|have|is|was|likes|loves|moved|visited|works|worked|lives|lived|started|starts|did|does|may|might|will|won't|cannot|can't)\b"
    match = re.match(r"^" + re.escape(mention) + r"\s+(?=" + verbs + r")", text, re.I) if mention else None
    if not match or any(value in text[:match.end()] for value in ('"', "[", "://")):
        return text
    value = text[match.end():]
    return value[:1].upper() + value[1:]


def spans(passage):
    if "\n" in passage or any(token in passage for token in ('"', "'", '‘', '’', '“', '”', '`', '[', '://')):
        return [(0, len(passage))]
    boundaries = [m.end() for m in re.finditer(r"[.!?]\s+(?=[A-Z])", passage)] + [len(passage)]
    result, start = [], 0
    for end in boundaries:
        if passage[start:end].strip():
            result.append((start, end))
        start = end
    return result


def valid_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def valid_data(data):
    if (not isinstance(data, dict) or set(data) != {"id", "person_uuid", "baseline", "protected", "references"} or
            not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-f0-9]{64}", data["id"]) or
            not isinstance(data.get("person_uuid"), str) or not data["person_uuid"] or
            type(data["protected"]) is not bool or not isinstance(data["references"], list) or
            (not data["references"] and not data["protected"])):
        raise ValueError("Assertion ownership is unreadable.")
    baseline = data["baseline"]
    if (not isinstance(baseline, dict) or set(baseline) != {"text", "log_date", "temporal_context", "origin"} or
            not isinstance(baseline["text"], str) or not baseline["text"].strip() or
            baseline["origin"] not in {"capture", "protected"} or
            not valid_day(baseline["log_date"]) or
            not isinstance(baseline["temporal_context"], str)):
        raise ValueError("Assertion baseline is unreadable.")
    seen = set()
    for ref in data["references"]:
        expected_keys = {"id", "kind", "source_uuid", "source_revision", "original_date", "start", "end", "evidence", "evidence_kind"}
        if isinstance(ref, dict) and ref.get("fingerprint"):
            expected_keys.add("fingerprint")
        unknown_legacy = (isinstance(ref, dict) and ref.get("kind") == "legacy" and ref.get("source_revision") is None and
                          data["protected"] and baseline["origin"] == "protected" and
                          re.fullmatch(r"[a-f0-9]{8}", str(ref.get("fingerprint", ""))))
        if (not isinstance(ref, dict) or set(ref) != expected_keys or
                ref["kind"] not in {"capture", "passive", "legacy"} or
                (ref.get("fingerprint") and ref["kind"] != "legacy") or
                not isinstance(ref["id"], str) or not re.fullmatch(r"[a-f0-9]{64}", ref["id"]) or ref["id"] in seen or
                not isinstance(ref["source_uuid"], str) or not ref["source_uuid"] or
                (not unknown_legacy and (not isinstance(ref["source_revision"], str) or not re.fullmatch(r"[a-f0-9]{64}", ref["source_revision"]))) or
                ref["evidence_kind"] not in {"source_span", "extracted_assertion", "legacy_assertion"} or
                (ref["kind"] == "capture" and ref["evidence_kind"] != "source_span") or
                (ref["kind"] == "passive" and ref["evidence_kind"] != "extracted_assertion") or
                not valid_day(ref["original_date"]) or
                type(ref["start"]) is not int or type(ref["end"]) is not int or
                ref["start"] < 0 or ref["end"] <= ref["start"] or
                not isinstance(ref["evidence"], str) or len(ref["evidence"]) != ref["end"] - ref["start"]):
            raise ValueError("Assertion support is unreadable.")
        if unknown_legacy and hashlib.sha1((ref["source_uuid"] + "|" + ref["original_date"] + "|" + ref["evidence"]).encode()).hexdigest()[:8] != ref["fingerprint"]:
            raise ValueError("Historical assertion fingerprint changed.")
        seen.add(ref["id"])


def render(body, person_uuid=None):
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    rows, ids, refs = [], set(), set()
    date = None
    section = False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section = line == HEADER
            date = None
        group = GROUP.match(line) if section else None
        if group:
            date = group[1]
        flat = FLAT.match(line) if section else None
        if line.lstrip().startswith(">"):
            continue
        match = MARKER.search(line)
        if not match:
            if "<!-- bio:v2:" in line:
                raise ValueError("Assertion marker is malformed.")
            continue
        if not section or len(MARKER.findall(line)) != 1 or not (flat or (date and line.startswith("  - "))):
            raise ValueError("Assertion layout is unreadable.")
        data = json.loads(match[1])
        valid_data(data)
        if person_uuid is not None and data["person_uuid"] != person_uuid:
            raise ValueError("Assertion belongs to a different Person.")
        if data["id"] in ids or any(ref["id"] in refs for ref in data["references"]):
            raise ValueError("Assertion ownership is duplicated.")
        ids.add(data["id"])
        refs.update(ref["id"] for ref in data["references"])
        text = flat[2] if flat else line[4:]
        actual_date = flat[1] if flat else date
        end = i + 1
        continuation = []
        while (end < len(lines) and (lines[end].startswith("    ") or lines[end].strip()) and
               not lines[end].startswith("## ") and not FLAT.match(lines[end]) and not GROUP.match(lines[end]) and
               not (not flat and lines[end].startswith("  - "))):
            continuation.append(lines[end][4:] if lines[end].startswith("    ") else lines[end])
            end += 1
        first = MARKER.sub("", text).rstrip()
        links = source_links(data)
        if links and first.endswith(links):
            first = first[:-len(links)]
        display = decode_literal(first + ("\n" + "\n".join(continuation) if continuation else ""))
        visible = assertion_text(display, data["baseline"]["text"])
        rows.append({"start": i, "end": end, "line": line, "data": data,
                     "visible": visible, "display": display, "date": actual_date})
    if sum(line.count("<!-- bio:v2:") for line in lines if not line.lstrip().startswith(">")) != len(rows):
        raise ValueError("Assertion marker is malformed.")
    return rows


def format_data(data, grouped=False, visible=None, log_date=None):
    text = encode_literal(visible if visible is not None else data["baseline"]["text"]).split("\n")
    links = source_links(data)
    prefix = "  - " if grouped else "- " + (log_date or data["baseline"]["log_date"]) + " — "
    return prefix + text[0] + links + " <!-- bio:v2:" + encoded(data) + " -->" + "".join("\n    " + line for line in text[1:])


def checked(row, assertion, edited=False):
    if row["data"]["baseline"] != assertion["baseline"]:
        raise ValueError("Assertion baseline changed.")
    if not edited and not row["data"]["protected"] and (row["visible"] != assertion["baseline"]["text"] or
            row["date"] != assertion["baseline"]["log_date"]):
        raise ValueError("The assertion was edited. Its content is retained.")


def replace_row(body, row, data):
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    replacement = ([format_data(data, row["line"].startswith("  - "), row.get("display", row["visible"]), row.get("date")).split("\n")[0]] +
                   lines[row["start"] + 1:row["end"]]) if data else []
    lines[row["start"]:row["end"]] = replacement
    if not replacement and row["start"] > 0 and GROUP.match(lines[row["start"] - 1]):
        if row["start"] == len(lines) or not lines[row["start"]].startswith("  - "):
            del lines[row["start"] - 1]
    return "\n".join(lines)


def attach(body, assertion, person_uuid, expected=False):
    rows = render(body, person_uuid)
    row = next((row for row in rows if row["data"]["id"] == assertion["id"]), None)
    if row:
        checked(row, assertion)
        if row["visible"] != row["data"]["baseline"]["text"] or row["date"] != row["data"]["baseline"]["log_date"]:
            raise ValueError("The compared assertion was edited. Its content is retained.")
        data = copy.deepcopy(row["data"])
        existing = [ref for ref in data["references"] if ref["id"] == assertion["reference"]["id"]]
        if existing:
            if existing != [assertion["reference"]]:
                raise ValueError("Assertion support changed.")
            return body
        if expected:
            raise ValueError("Assertion support was removed.")
        data["references"].append(assertion["reference"])
        return replace_row(body, row, data)
    if expected or assertion.get("existing"):
        raise ValueError("Assertion was removed.")
    data = {"id": assertion["id"], "person_uuid": person_uuid, "baseline": assertion["baseline"], "protected": False,
            "references": [assertion["reference"]]}
    valid_data(data)
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if HEADER not in lines:
        prefix = "\n".join(lines)
        while not prefix.endswith("\n\n"):
            prefix += "\n"
        lines = (prefix + HEADER + "\n").split("\n")
    start = lines.index(HEADER) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    position = next((i for i in range(start, end) if (FLAT.match(lines[i]) or GROUP.match(lines[i])) and
                     lines[i][2:12] < data["baseline"]["log_date"]), end)
    lines[position:position] = format_data(data).split("\n")
    return "\n".join(lines)


def verify(body, assertion, person_uuid):
    row = next((r for r in render(body, person_uuid) if r["data"]["id"] == assertion["id"]), None)
    if row is None:
        raise ValueError("Assertion is missing.")
    checked(row, assertion)
    if [r for r in row["data"]["references"] if r["id"] == assertion["reference"]["id"]] != [assertion["reference"]]:
        raise ValueError("Assertion support is missing or changed.")


def detach(body, assertion, person_uuid):
    row = next((r for r in render(body, person_uuid) if r["data"]["id"] == assertion["id"]), None)
    if row is None:
        raise ValueError("Assertion ownership is missing.")
    checked(row, assertion)
    data = copy.deepcopy(row["data"])
    if assertion["reference"] not in data["references"]:
        raise ValueError("Assertion support is missing or changed.")
    data["references"].remove(assertion["reference"])
    if not data["references"]:
        if data["baseline"]["origin"] == "capture" and not data["protected"]:
            return replace_row(body, row, None)
        data["protected"] = True
    return replace_row(body, row, data)


def protect(body, assertion, person_uuid):
    row = next((r for r in render(body, person_uuid) if r["data"]["id"] == assertion["id"]), None)
    if row is None:
        raise ValueError("Assertion ownership is missing.")
    checked(row, assertion, edited=True)
    data = copy.deepcopy(row["data"])
    data["protected"] = True
    return replace_row(body, row, data)


def legacy_rows(body, person_uuid, mention):
    rows = []
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    section, date = False, None
    for i, line in enumerate(lines):
        if line.startswith("## "):
            section, date = line == HEADER, None
        group = GROUP.match(line) if section else None
        if group:
            date = group[1]
            continue
        flat = FLAT.match(line) if section else None
        value = flat[2] if flat else line[4:] if section and date and line.startswith("  - ") else None
        day = flat[1] if flat else date
        continuation = (i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith("## ") and
                        not FLAT.match(lines[i + 1]) and not GROUP.match(lines[i + 1]) and
                        (flat or not lines[i + 1].startswith("  - ")))
        if value is None or MARKER.search(value) or continuation:
            continue
        src = re.search(r"\(\[source\]\(x-devonthink-item://([^)]+)\)\)", value)
        marker = re.search(r"<!-- fact:([a-f0-9]{8}) -->", value)
        text = clean(value)
        if not src or not marker or hashlib.sha1((src[1] + "|" + day + "|" + text).encode()).hexdigest()[:8] != marker[1]:
            continue
        ref = {"id": digest("legacy|" + person_uuid + "|" + src[1] + "|" + day + "|" + text),
               "kind": "legacy", "source_uuid": src[1], "source_revision": None, "fingerprint": marker[1],
               "original_date": day, "start": 0, "end": len(text), "evidence": text, "evidence_kind": "legacy_assertion"}
        data = {"id": digest("legacy-assertion|" + ref["id"]), "person_uuid": person_uuid, "baseline": {"text": relative(text, mention),
                "log_date": day, "temporal_context": "observed:" + day, "origin": "protected"},
                "protected": True, "references": [ref]}
        rows.append({"start": i, "end": i + 1, "line": line, "data": data, "visible": data["baseline"]["text"], "date": day, "legacy_line": line})
    return rows


def normalize_legacy(body, person_uuid, mention):
    for row in reversed(legacy_rows(body, person_uuid, mention)):
        body = replace_row(body, row, row["data"])
    return body


def prepare(source, source_date, text, result, processing_date, people, decisions=None, suggest=None, require_review=()):
    import entity_capture as capture
    manifest = capture.prepare(source, source_date, text, result, processing_date)
    manifest["version"] = 2
    questions = []
    decisions = decisions or {}
    for subject in manifest["subjects"]:
        person = next((p for p in people if p["uuid"] == subject["uuid"]), {})
        body = person.get("body", "")
        candidates = [row for row in render(body, subject["uuid"]) if row["visible"] == row["data"]["baseline"]["text"] and
                      row["date"] == row["data"]["baseline"]["log_date"]] + legacy_rows(
            body, subject["uuid"], [person.get("name", subject["evidence"]["mention"])] + person.get("aliases", "").split(","))
        assertions = []
        offset = text.index(subject["passage"])
        scope = subject["uuid"] or subject["contribution"]["id"]
        for start, end in spans(subject["passage"]):
            evidence = subject["passage"][start:end]
            value = relative(evidence.strip(), subject["evidence"]["mention"])
            ref_id = digest("capture-reference-v2|" + encoded([source["uuid"], manifest["revision"], scope, offset + start, offset + end]))
            baseline = {"text": value, "log_date": source_date, "temporal_context": "observed:" + source_date, "origin": "capture"}
            assertion = {"id": digest("assertion-v2|" + scope + "|" + ref_id), "baseline": baseline,
                         "reference": {"id": ref_id, "kind": "capture", "source_uuid": source["uuid"],
                            "source_revision": manifest["revision"], "original_date": source_date,
                            "start": offset + start, "end": offset + end, "evidence": evidence, "evidence_kind": "source_span"}}
            exact = [c for c in candidates if c["data"]["baseline"]["text"] == value]
            compatible = [c for c in exact if c["data"]["baseline"]["temporal_context"] == baseline["temporal_context"]]
            selected = decisions.get(ref_id)
            proposed = exact
            if suggest and not compatible and candidates:
                ids = suggest(value, source_date, [{"id": c["data"]["id"], **c["data"]["baseline"]} for c in candidates])
                if not isinstance(ids, list) or any(not isinstance(i, str) or i not in [c["data"]["id"] for c in candidates] for i in ids):
                    raise ValueError("Semantic suggestions are unreadable.")
                proposed += [c for c in candidates if c["data"]["id"] in ids and c not in proposed]
            if selected not in {None, "separate", "source"}:
                match = next((c for c in candidates if c["data"]["id"] == selected), None)
                if match is None:
                    raise ValueError("The selected assertion changed. Refresh before saving.")
            elif compatible and selected is None and ref_id not in require_review:
                match = compatible[0] if len(compatible) == 1 else None
            else:
                match = None
            if selected == "source":
                assertion["baseline"]["text"] = evidence.strip()
            if match:
                assertion.update(id=match["data"]["id"], baseline=match["data"]["baseline"], existing=True,
                                 equivalence="confirmed" if selected is not None else "exact")
                if match.get("legacy_line"):
                    assertion["legacy"] = {"line": match["legacy_line"], "data": match["data"]}
            elif proposed and selected is None:
                questions.append({"key": ref_id, "evidence": evidence, "assertion": value,
                    "observed_date": source_date, "candidates": [{"id": c["data"]["id"], **c["data"]["baseline"]} for c in proposed]})
            if selected is not None:
                manifest.setdefault("equivalence_answers", {})[ref_id] = selected
            assertions.append(assertion)
            if not any(candidate["data"]["id"] == assertion["id"] for candidate in candidates):
                candidates.append({"data": {"id": assertion["id"], "baseline": assertion["baseline"],
                                           "protected": False, "references": [assertion["reference"]]},
                                   "visible": assertion["baseline"]["text"]})
        subject["contribution"] = {"id": subject["contribution"]["id"], "scope": scope, "assertions": assertions}
    if questions:
        manifest.update(status="question", reason="assertion_equivalence", semantic_questions=questions,
                        extracted=[dict(s["evidence"], passage=s["passage"]) for s in manifest["subjects"]])
    return manifest


def decode(raw, source_uuid):
    import entity_capture as capture
    manifest = json.loads(raw)
    projected = copy.deepcopy(manifest)
    projected["version"] = 1
    for subject in projected.get("subjects", []):
        subject["contribution"] = capture.contribution(source_uuid, projected["revision"], subject, projected["source_date"])
    capture._decode_v1(capture.encode_manifest(projected), source_uuid)
    answers = manifest.get("equivalence_answers", {})
    if not isinstance(answers, dict):
        raise ValueError("Assertion decisions are unreadable.")
    refs = set()
    expected_applied = set()
    for subject in manifest["subjects"]:
        expected_contribution = capture.contribution(source_uuid, manifest["revision"], subject, manifest["source_date"])
        scope = subject["contribution"].get("scope")
        if subject["contribution"].get("id") != expected_contribution["id"] or scope not in {subject["uuid"], expected_contribution["id"]}:
            raise ValueError("Capture assertion scope is unreadable.")
        rows = subject["contribution"].get("assertions")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Capture assertions are unreadable.")
        cursor = manifest["text"].index(subject["passage"])
        for row in rows:
            valid_data({"id": row["id"], "person_uuid": subject["uuid"] or subject["contribution"]["scope"],
                        "baseline": row["baseline"], "protected": False, "references": [row["reference"]]})
            ref = row["reference"]
            if (ref["kind"] != "capture" or ref["source_uuid"] != source_uuid or
                    ref["source_revision"] != manifest["revision"] or ref["original_date"] != manifest["source_date"] or
                    ref["start"] != cursor or manifest["text"][ref["start"]:ref["end"]] != ref["evidence"] or
                    ref["id"] in refs):
                raise ValueError("Capture assertion coverage is incomplete.")
            expected_reference = digest("capture-reference-v2|" + encoded([source_uuid, manifest["revision"], scope, ref["start"], ref["end"]]))
            if ref["id"] != expected_reference:
                raise ValueError("Capture assertion support is not source-bound.")
            if row.get("existing"):
                if row.get("equivalence") == "confirmed":
                    if manifest.get("equivalence_answers", {}).get(ref["id"]) != row["id"]:
                        raise ValueError("Assertion equivalence was not explicitly confirmed.")
                elif (row.get("equivalence") != "exact" or
                      row["baseline"]["text"] != relative(ref["evidence"].strip(), subject["evidence"]["mention"]) or
                      row["baseline"]["temporal_context"] != "observed:" + manifest["source_date"]):
                    raise ValueError("Assertion equivalence is not exact.")
            elif row["id"] != digest("assertion-v2|" + scope + "|" + ref["id"]):
                raise ValueError("Capture assertion identity changed.")
            if not row.get("existing") and row["baseline"]["text"] not in {ref["evidence"].strip(), relative(ref["evidence"].strip(), subject["evidence"]["mention"])}:
                raise ValueError("Capture assertion omitted or invented information.")
            cursor = ref["end"]
            refs.add(ref["id"])
            expected_applied.add(subject["uuid"] + "|" + ref["id"])
        if cursor != manifest["text"].index(subject["passage"]) + len(subject["passage"]):
            raise ValueError("Capture assertion coverage is incomplete.")
    if any(key not in refs or not isinstance(value, str) or
           value not in {"separate", "source"} and not re.fullmatch(r"[a-f0-9]{64}", value)
           for key, value in answers.items()):
        raise ValueError("Assertion decisions are not source-bound.")
    applied = manifest.get("applied_references", [])
    if (not isinstance(applied, list) or len(applied) != len(set(applied)) or
            (manifest["status"] == "filed" and set(applied) != expected_applied)):
        raise ValueError("Capture assertion completion is unreadable.")
    return manifest


def write_body(bridge, manifest, previous, uuid, before, after, destinations=()):
    bridge([{"op": "biographical_write", "uuid": uuid, "expected_body": before, "body": after,
             "source_uuid": manifest["source_uuid"], "text": manifest.get("correction", {}).get("text", manifest["text"]),
             "expected_operation": previous, "destinations": list(destinations)}])
    if bridge([{"op": "get_text", "uuid": uuid}])[0]["text"] != after:
        raise ValueError("Assertion application could not be verified.")


def replay(bridge, manifest, previous="", persist_manifest=None, selves=(), preserve_contributions=()):
    import entity_capture as capture
    def persist():
        nonlocal previous
        if persist_manifest:
            persist_manifest(manifest)
            source = bridge([{"op": "get_source", "uuid": manifest["source_uuid"]}])[0]
            previous = source["capture_operation"]
        else:
            value = capture.encode_manifest(manifest)
            bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
                     "value": value, "text": manifest["text"]}])
            previous = value
    if manifest["status"] == "question":
        persist()
        return capture.outcome("unresolved", manifest["reason"])
    if manifest["status"] != "filed":
        capture.validate_frozen(bridge, manifest, selves)
    if previous != capture.encode_manifest(manifest):
        persist()
    for subject in manifest["subjects"]:
        result = bridge([{"op": "capture_person", "name": subject["name"], "uuid": subject["uuid"],
                          "evidence": subject["evidence"], "creation_id": subject["contribution"]["id"],
                          "initialize": not bool(subject["uuid"]), "source_uuid": manifest["source_uuid"], "text": manifest["text"]}])[0]
        if not result.get("initialized") or not result.get("uuid"):
            raise ValueError("Person initialization could not be verified.")
        subject["uuid"] = result["uuid"]
        persist()
        for assertion in subject["contribution"]["assertions"]:
            before = bridge([{"op": "get_text", "uuid": subject["uuid"]}])[0]["text"]
            body = before
            if assertion.get("legacy") and not any(r["data"]["id"] == assertion["id"] for r in render(body, subject["uuid"])):
                legacy = assertion["legacy"]
                if body.splitlines().count(legacy["line"]) != 1:
                    raise ValueError("Historical assertion changed.")
                body = body.replace(legacy["line"], format_data(legacy["data"], legacy["line"].startswith("  - ")))
            key = subject["uuid"] + "|" + assertion["reference"]["id"]
            if key in preserve_contributions:
                after = protect(body, assertion, subject["uuid"])
            else:
                after = attach(body, assertion, subject["uuid"], key in manifest.get("applied_references", []))
            if after != before:
                write_body(bridge, manifest, previous, subject["uuid"], before, after)
            verify(after, assertion, subject["uuid"])
            if key not in manifest.setdefault("applied_references", []):
                manifest["applied_references"].append(key)
            persist()
        subject["applied"] = True
        persist()
    if not manifest["receipt"]:
        day = manifest["processing_date"]
        daily = bridge([{"op": "get_or_create_daily", "date": day, "heading": day}])[0]
        manifest["receipt_uuid"] = daily["uuid"]
        persist()
        line = capture.receipt_line(manifest)
        bridge([{"op": "append_pinned", "uuid": daily["uuid"], "line": line}])
        if line not in bridge([{"op": "get_text", "uuid": daily["uuid"]}])[0]["text"]:
            raise ValueError("Capture receipt could not be verified.")
        manifest["receipt"] = True
        persist()
    bridge([{"op": "mark_filed", "uuid": manifest["source_uuid"]}])
    manifest["status"] = "filed"
    persist()
    if not persist_manifest:
        bridge([{"op": "capture_retire_source", "uuid": manifest["source_uuid"]}])
    return capture.outcome("filed", subjects=manifest["subjects"])
