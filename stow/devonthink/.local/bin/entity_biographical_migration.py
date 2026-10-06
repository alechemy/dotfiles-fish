import copy
import json
import os
import tempfile
from pathlib import Path

import entity_biographical as bio
import entity_capture as capture

VERSION = "biographical-v2-3"
FENCE_NAME = "entity-biographical-fence.json"


def code_digest():
    root = Path(__file__).resolve().parent
    names = ("entity-biographical-migrate", "entity_biographical.py", "entity_biographical_correction.py", "entity_biographical_migration.py",
             "entity_capture.py", "entity-filing.py", "entity-dt-bridge.js", "entity_biographical_review.py", "entity-review-server.py")
    return bio.digest("".join((root / name).read_text() for name in names))


def private_save(path, value, replace=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".biographical-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        if replace:
            os.replace(temp, path)
        else:
            os.link(temp, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def controls(record):
    md = record.get("md", {})
    return {"entitytype": str(md.get("mdentitytype", "") or ""),
            "filingsuppressed": str(md.get("mdfilingsuppressed", "") or "")}


def filing_metadata(md):
    return {key: value for key, value in md.items() if key != "mdlastcontact"}


def coalesce(body, person_uuid):
    by_key = {}
    for row in reversed(bio.render(body, person_uuid)):
        data = row["data"]
        if row["visible"] != data["baseline"]["text"] or row["date"] != data["baseline"]["log_date"]:
            continue
        key = (data["baseline"]["text"], data["baseline"]["temporal_context"])
        if (key in by_key and data["baseline"]["origin"] == "protected" and
                all(r["kind"] != "capture" for r in data["references"] + by_key[key]["data"]["references"])):
            target = next(r for r in bio.render(body, person_uuid) if r["data"]["id"] == by_key[key]["data"]["id"])
            data["references"] += [r for r in target["data"]["references"] if r not in data["references"]]
            body = bio.replace_row(body, target, None)
            row = next(r for r in bio.render(body, person_uuid) if r["data"]["id"] == data["id"])
            body = bio.replace_row(body, row, data)
        by_key[key] = next(r for r in bio.render(body, person_uuid) if r["data"]["id"] == data["id"])
    return body


def preview(bridge, suggest=None, decisions=None, frozen_questions=None, selves=(), allowed_scope=None):
    people, registered, facts, candidates = bridge([
        {"op": "dump_people", "include_bodies": True}, {"op": "list_registered_captures"},
        {"op": "list_fact_captures"}, {"op": "list_candidates"}])
    sources = {r["uuid"]: r for r in registered}
    for row in facts:
        if row.get("capture_operation") and row["uuid"] not in sources:
            sources[row["uuid"]] = {"uuid": row["uuid"], "operation": row["capture_operation"]}
    if allowed_scope is not None:
        sources = {uuid: row for uuid, row in sources.items() if uuid in allowed_scope}
    records, states, steps, questions, question_scope = {}, {}, [], [], set()
    decisions = decisions or {}
    counts = {"normalized_people": 0, "upgraded_captures": 0, "skipped": 0, "conflicted": 0, "pending_questions": 0, "waiting_comparisons": 0}
    for person in people:
        records[person["uuid"]] = {"body": person.get("body", ""), "operation": "", "controls": controls(person),
                                   "name": person["name"], "aliases": person.get("aliases", ""), "md": filing_metadata(person.get("md", {}))}
    for uuid, source in sorted(sources.items()):
        body, fields = bridge([{"op": "get_text", "uuid": uuid},
                               {"op": "get_fields", "uuid": uuid, "fields": ["entitytype", "filingsuppressed"]}])
        records[uuid] = {"body": body["text"], "operation": source["operation"], "controls": fields["fields"],
                         "source_text": capture.source_text(body["text"])}
    states = {uuid: {"body": row["body"], "operation": row["operation"]} for uuid, row in records.items()}

    def mutate(uuid, field, value):
        if states[uuid][field] == value:
            return
        before = copy.deepcopy(states[uuid])
        states[uuid][field] = value
        steps.append({"uuid": uuid, "before": before, "after": copy.deepcopy(states[uuid]),
                      "controls": records[uuid]["controls"]})

    for person in people:
        if allowed_scope is not None and person["uuid"] not in allowed_scope:
            continue
        if capture.flag(person.get("md", {}).get("mdfilingsuppressed")):
            counts["skipped"] += 1
            continue
        try:
            body = coalesce(bio.normalize_legacy(person.get("body", ""), person["uuid"], [person["name"]] + person.get("aliases", "").split(",")), person["uuid"])
        except (ValueError, KeyError, TypeError):
            if allowed_scope is not None:
                raise ValueError("Migration preview changed during review.") from None
            counts["conflicted"] += 1
            continue
        if body != person.get("body", ""):
            mutate(person["uuid"], "body", body)
            counts["normalized_people"] += 1
    for uuid, source in sorted(sources.items()):
        checkpoint, frozen_states = len(steps), copy.deepcopy(states)
        try:
            original = capture.decode_manifest(source["operation"], uuid)
            if (original["version"] != 1 or original["status"] != "filed" or original.get("correction") or
                    original.get("replacement") or original.get("analysis_request")):
                counts["skipped"] += 1
                continue
            if records[uuid]["source_text"] != original["text"]:
                raise ValueError("Source changed.")
            capture.validate_frozen(bridge, original, selves)
            for subject in original["subjects"]:
                target = records[subject["uuid"]]
                if target["controls"]["entitytype"] != "Person" or capture.flag(target["controls"]["filingsuppressed"]):
                    raise ValueError("Target controls changed.")
                body = states[subject["uuid"]]["body"]
                block = subject["contribution"]["block"]
                if body.replace("\r\n", "\n").replace("\r", "\n").count(block) != 1:
                    raise ValueError("Legacy capture changed.")
                mutate(subject["uuid"], "body", body.replace("\r\n", "\n").replace("\r", "\n").replace(block, ""))
            current_people = [dict(p, body=states[p["uuid"]]["body"]) for p in people]
            resolved = capture.outcome("existing", subjects=original["subjects"])
            comparisons = suggest
            required = []
            if frozen_questions is not None:
                frozen = next((row["manifest"] for row in frozen_questions if row["source_uuid"] == uuid), None)
                required = [q["key"] for q in frozen.get("semantic_questions", [])] if frozen else []
                def comparisons(value, day, candidates):
                    ids = {c["id"] for c in candidates}
                    return list(dict.fromkeys(c["id"] for q in frozen.get("semantic_questions", [])
                        if q["assertion"] == value for c in q["candidates"] if c["id"] in ids)) if frozen else []
            upgraded = capture.prepare_unified({"uuid": uuid}, original["source_date"], original["text"], resolved,
                                               original["processing_date"], current_people, decisions=decisions.get(uuid), suggest=comparisons, require_review=required)
            if upgraded["status"] in {"question", "deferred"}:
                questions.append({"source_uuid": uuid, "manifest": upgraded})
                question_scope.update([uuid] + [s["uuid"] for s in original["subjects"]])
                counts["pending_questions"] += len(upgraded.get("semantic_questions", []))
                counts["waiting_comparisons"] += upgraded["status"] == "deferred"
                steps[checkpoint:] = []
                states = frozen_states
                continue
            for subject in upgraded["subjects"]:
                body = states[subject["uuid"]]["body"]
                for assertion in subject["contribution"]["assertions"]:
                    if assertion.get("legacy"):
                        legacy = assertion["legacy"]
                        body = body.replace(legacy["line"], bio.format_data(legacy["data"], legacy["line"].startswith("  - ")))
                    body = bio.attach(body, assertion, subject["uuid"])
                    upgraded.setdefault("applied_references", []).append(subject["uuid"] + "|" + assertion["reference"]["id"])
                subject["applied"] = True
                mutate(subject["uuid"], "body", body)
            for key in ("receipt", "receipt_uuid", "receipt_id", "review_url"):
                if key in original:
                    upgraded[key] = original[key]
            upgraded["status"] = "filed"
            upgraded["history"] = list(original.get("history", [])) + [{k: v for k, v in original.items() if k != "history"}]
            value = capture.encode_manifest(upgraded)
            capture.decode_manifest(value, uuid)
            mutate(uuid, "operation", value)
            counts["upgraded_captures"] += 1
        except (ValueError, KeyError, TypeError, RuntimeError):
            if allowed_scope is not None:
                raise ValueError("Migration preview changed during review.") from None
            steps[checkpoint:] = []
            states = frozen_states
            counts["conflicted"] += 1
    scope = sorted({step["uuid"] for step in steps} | question_scope)
    plan = {"version": 2, "code_version": VERSION, "code_digest": code_digest(), "records": {uuid: records[uuid] for uuid in scope},
            "scope": scope, "names": sorted({capture.norm(name) for uuid in scope if "name" in records[uuid]
                             for name in [records[uuid]["name"]] + records[uuid]["aliases"].split(",") if name.strip()}),
            "candidate_digest": bio.digest(bio.encoded(candidates)), "steps": steps, "counts": counts,
            "questions": questions, "decisions": decisions}
    plan["plan_id"] = bio.digest(bio.encoded(plan))
    return plan


def validate(plan):
    if (not isinstance(plan, dict) or plan.get("version") != 2 or plan.get("code_version") != VERSION or plan.get("code_digest") != code_digest() or
            plan.get("plan_id") != bio.digest(bio.encoded({k: v for k, v in plan.items() if k != "plan_id"})) or
            set(plan.get("scope", [])) != set(plan.get("records", {}))):
        raise ValueError("Migration plan is unreadable.")
    if not isinstance(plan.get("questions"), list) or not isinstance(plan.get("decisions"), dict):
        raise ValueError("Migration questions are unreadable.")
    question_count, waiting_count = 0, 0
    for question in plan["questions"]:
        manifest = capture.decode_manifest(capture.encode_manifest(question["manifest"]), question["source_uuid"])
        if (manifest["status"] not in {"question", "deferred"} or question["source_uuid"] not in plan["scope"] or
                manifest["status"] == "deferred" and (manifest.get("reason") != "comparison_unavailable" or manifest.get("semantic_questions"))):
            raise ValueError("Migration question scope is unreadable.")
        question_count += len(manifest.get("semantic_questions", []))
        waiting_count += manifest["status"] == "deferred"
    if question_count != plan["counts"].get("pending_questions", 0) or waiting_count != plan["counts"].get("waiting_comparisons", 0):
        raise ValueError("Migration question counts changed.")
    states = {uuid: {"body": record["body"], "operation": record["operation"]} for uuid, record in plan["records"].items()}
    for step in plan["steps"]:
        if step["uuid"] not in states or step["before"] != states[step["uuid"]] or sum(
                step["before"][key] != step["after"][key] for key in ("body", "operation")) != 1:
            raise ValueError("Migration chain is unreadable.")
        states[step["uuid"]] = step["after"]


def resolve(bridge, plan, answer_document):
    validate(plan)
    if plan["counts"].get("waiting_comparisons"):
        raise ValueError("Migration is waiting for local comparison. Create a fresh preview when it is available.")
    if (not isinstance(answer_document, dict) or set(answer_document) != {"plan_id", "answers"} or
            answer_document["plan_id"] != plan["plan_id"] or not isinstance(answer_document["answers"], dict) or
            not answer_document["answers"]):
        raise ValueError("Migration choices are unreadable.")
    pending = {row["source_uuid"]: row["manifest"]["semantic_questions"] for row in plan["questions"]}
    decisions = copy.deepcopy(plan.get("decisions", {}))
    for uuid, answers in answer_document["answers"].items():
        if uuid not in pending or not isinstance(answers, dict) or set(answers) != {q["key"] for q in pending[uuid]}:
            raise ValueError("Migration choices do not match the frozen questions.")
        for question in pending[uuid]:
            value = answers[question["key"]]
            if value not in ["separate", "source"] + [c["id"] for c in question["candidates"]]:
                raise ValueError("Migration choice was not in the frozen question.")
            if question["key"] in decisions.get(uuid, {}):
                raise ValueError("Migration choice was already recorded.")
        decisions.setdefault(uuid, {}).update(answers)
    if snapshot(bridge, plan) != expected_at(plan, 0):
        raise ValueError("Migration preview changed before review.")
    result = preview(bridge, decisions=decisions, frozen_questions=plan["questions"], allowed_scope=plan["scope"])
    if (result["candidate_digest"] != plan["candidate_digest"] or
            not set(result["scope"]).issubset(plan["scope"]) or
            any(result["records"][uuid] != plan["records"][uuid] for uuid in result["scope"])):
        raise ValueError("Migration preview changed during review.")
    result["reviewed_from"] = plan["plan_id"]
    result["plan_id"] = bio.digest(bio.encoded({k: v for k, v in result.items() if k != "plan_id"}))
    return result


def snapshot(bridge, plan):
    out = {}
    for uuid in plan["scope"]:
        body, fields = bridge([{"op": "get_text", "uuid": uuid}, {"op": "get_fields", "uuid": uuid,
            "fields": ["captureoperation", "entitytype", "filingsuppressed"]}])
        if {key: fields["fields"][key] for key in ("entitytype", "filingsuppressed")} != plan["records"][uuid]["controls"]:
            raise ValueError("Migration controls changed.")
        out[uuid] = {"body": body["text"], "operation": fields["fields"]["captureoperation"]}
    people, candidates = bridge([{"op": "dump_people", "include_bodies": False}, {"op": "list_candidates"}])
    if bio.digest(bio.encoded(candidates)) != plan["candidate_digest"]:
        raise ValueError("Migration identity controls changed.")
    for uuid, record in plan["records"].items():
        if "name" not in record:
            continue
        current = next((p for p in people if p["uuid"] == uuid), None)
        if (not current or any(current.get(key, "") != record[key] for key in ("name", "aliases")) or
                filing_metadata(current.get("md", {})) != record["md"]):
            raise ValueError("Migration Person identity changed.")
    return out


def expected_at(plan, position):
    states = {uuid: {"body": row["body"], "operation": row["operation"]} for uuid, row in plan["records"].items()}
    for step in plan["steps"][:position]:
        states[step["uuid"]] = step["after"]
    return states


def transact(bridge, plan, journal_path, fence_path, rollback=False):
    validate(plan)
    if plan["counts"].get("waiting_comparisons"):
        raise ValueError("Migration is waiting for local comparison.")
    if plan["counts"].get("pending_questions", 0):
        raise ValueError("Migration has unanswered questions.")
    journal_path, fence_path = Path(journal_path), Path(fence_path)
    fence = {"version": 2, "plan_id": plan["plan_id"], "scope": plan["scope"], "names": plan["names"]}
    if fence_path.exists() and json.loads(fence_path.read_text()) != fence:
        raise ValueError("Another migration is fenced.")
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
        if journal.get("plan_id") != plan["plan_id"] or journal.get("version") != 2:
            raise ValueError("Migration journal does not match.")
    else:
        journal = {"version": 2, "plan_id": plan["plan_id"], "position": 0, "direction": "apply", "status": "running"}
        if rollback:
            raise ValueError("No applied migration to roll back.")
    if not 0 <= journal["position"] <= len(plan["steps"]):
        raise ValueError("Migration checkpoint is unreadable.")
    position = journal["position"]
    actual = snapshot(bridge, plan)
    expected = expected_at(plan, position)
    direction = journal["direction"]
    if actual != expected:
        neighbor = position - 1 if direction == "rollback" else position + 1
        if not 0 <= neighbor <= len(plan["steps"]) or actual != expected_at(plan, neighbor):
            raise ValueError("Migration stopped on a changed record.")
        position = neighbor
        journal["position"] = position
    if rollback:
        journal["direction"] = "rollback"
    elif direction == "rollback":
        raise ValueError("Resume rollback explicitly.")
    private_save(journal_path, journal)
    private_save(fence_path, fence)
    journal["status"] = "running"
    private_save(journal_path, journal)
    while (position > 0 if rollback else position < len(plan["steps"])):
        if snapshot(bridge, plan) != expected_at(plan, position):
            raise ValueError("Migration stopped on a changed record.")
        index = position - 1 if rollback else position
        step = copy.deepcopy(plan["steps"][index])
        if rollback:
            step["before"], step["after"] = step["after"], step["before"]
        step.update(op="biographical_migration_write", migration_plan=plan["plan_id"])
        bridge([step])
        position += -1 if rollback else 1
        if snapshot(bridge, plan) != expected_at(plan, position):
            raise ValueError("Migration write could not be verified.")
        journal["position"] = position
        private_save(journal_path, journal)
    journal["status"] = "rolled_back" if rollback else "applied"
    private_save(journal_path, journal)
    fence_path.unlink()
    directory = os.open(str(fence_path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"status": journal["status"], "mutations": len(plan["steps"]), **plan["counts"]}
