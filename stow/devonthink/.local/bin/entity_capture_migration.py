"""Read-only capture migration preview and resumable, source-scoped application."""

import re
from datetime import date

import entity_candidates as ec
import entity_capture as capture
from entity_capture_reminders import save


def preview(sources, candidates, reviews, people):
    items = []
    for source in sources:
        if source.get("capture_operation"):
            disposition = "already_migrated"
        elif not source.get("ready", True):
            disposition = "upstream_pending"
        else:
            disposition = "process_source_only"
        links = []
        ignored_evidence = False
        for group in ("pending", "approved", "ignored"):
            for record in candidates.get(group, []):
                try:
                    data = ec.parse_candidate(record["text"])
                except ValueError:
                    continue
                if ec.dt_sighting_id(source["uuid"]) in data["sightings"]:
                    links.append(record["uuid"])
                    ignored_evidence = ignored_evidence or group == "ignored"
        if ignored_evidence and disposition == "process_source_only":
            disposition = "retain_ignored"
        old_filing = [row["uuid"] for row in capture.previous_filings(source["uuid"], people)]
        if old_filing and disposition == "process_source_only":
            disposition = "review_previous_filing"
        items.append({"source_uuid": source["uuid"], "disposition": disposition,
                      "candidates": links, "previous_people": old_filing,
                      "modified": source.get("modified", "")})
    counts = {}
    for item in items:
        counts[item["disposition"]] = counts.get(item["disposition"], 0) + 1
    return {"version": 1, "counts": counts, "items": items}


def journal_for_plan(plan):
    if not isinstance(plan, dict) or plan.get("version") != 1 or not isinstance(plan.get("items"), list):
        raise ValueError("The migration preview is unreadable.")
    scope, expected = [], {}
    for item in plan["items"]:
        if (not isinstance(item, dict) or not isinstance(item.get("source_uuid"), str) or not item["source_uuid"] or
                not isinstance(item.get("modified"), str) or item["source_uuid"] in expected):
            raise ValueError("The migration preview contains an invalid or repeated source.")
        scope.append(item["source_uuid"])
        expected[item["source_uuid"]] = item["modified"]
    identity = capture.revision(capture.encode_manifest(expected))
    return {"version": 1, "plan_id": identity, "items": {}, "scope": scope, "expected_modified": expected}


def detach_source(bridge, source_uuid, people):
    listing = bridge([{"op": "list_candidates"}])[0]
    sid = ec.dt_sighting_id(source_uuid)
    for group in ("pending", "approved"):
        for record in listing.get(group, []):
            try:
                data = ec.parse_candidate(record["text"])
            except ValueError:
                continue
            if sid not in data["sightings"]:
                continue
            del data["sightings"][sid]
            ec.recompute_derived(data)
            if data["sightings"]:
                bridge([{"op": "capture_retire_candidate", "uuid": record["uuid"],
                         "expected_text": record["text"], "text": ec.render_candidate(data, ec.near_matches(data["name"], people)),
                         "empty": False}])
            else:
                bridge([{"op": "capture_retire_candidate", "uuid": record["uuid"],
                         "expected_text": record["text"], "empty": True}])


def apply(bridge, extract, config, journal, path, selves=()):
    if journal.get("version") != 1 or not isinstance(journal.get("items"), dict):
        raise ValueError("Capture migration journal is unreadable.")
    journal["protocol"] = "questions-v2"
    save(path, journal)
    sources, candidates, reviews, people = bridge([
        {"op": "list_fact_captures"}, {"op": "list_candidates"}, {"op": "list_review"},
        {"op": "dump_people", "include_bodies": True}])
    report = preview(sources, candidates, reviews, people)
    by_source = {s["uuid"]: s for s in sources}
    for item in report["items"]:
        source_uuid = item["source_uuid"]
        if journal.get("scope") is not None and source_uuid not in journal["scope"]:
            continue
        progress = journal["items"].setdefault(source_uuid, {"status": "pending"})
        if progress["status"] == "done":
            continue
        people, candidates = bridge([{"op": "dump_people", "include_bodies": True}, {"op": "list_candidates"}])
        source = by_source[source_uuid]
        if progress["status"] == "pending" and journal.get("expected_modified", {}).get(source_uuid, source.get("modified", "")) != source.get("modified", ""):
            progress["disposition"] = "source_changed"
            save(path, journal)
            continue
        if item["disposition"] in {"retain_ignored", "upstream_pending"}:
            progress["disposition"] = item["disposition"]
            save(path, journal)
            continue
        text = capture.source_text(bridge([{"op": "get_text", "uuid": source_uuid}])[0]["text"])
        previous = source.get("capture_operation", "")
        manifest = capture.decode_manifest(previous, source_uuid) if previous else None
        if manifest and manifest["text"] != text:
            progress["disposition"] = "source_changed"
            save(path, journal)
            continue
        if manifest is None or manifest["status"] == "deferred":
            history = list(manifest.get("history", [])) if manifest else []
            if item["disposition"] == "review_previous_filing":
                extracted = []
                result = capture.outcome("unresolved", "legacy_filing")
            else:
                try:
                    extracted = extract(source, text, people)
                    result = capture.resolve(text, extracted, people, ec.CandidateIndex(candidates).ignored_names(), selves)
                except Exception:
                    extracted = []
                    result = capture.outcome("deferred", "model_unavailable")
            if result["status"] in {"new", "existing"}:
                manifest = capture.prepare(source, source.get("eventdate") or source.get("added") or date.today().isoformat(),
                                           text, result, date.today().isoformat())
                manifest["review_url"] = config.get("REVIEW_URL") or "http://localhost:8080/entities/"
            else:
                manifest = capture.pending(source, source.get("added") or date.today().isoformat(), text, extracted,
                                           result, date.today().isoformat())
                if result["reason"] != "model_unavailable":
                    manifest["status"] = "question"
            manifest["history"] = history
            bridge([{"op": "capture_store", "uuid": source_uuid, "expected": previous, "text": text,
                     "value": capture.encode_manifest(manifest)}])
            previous = capture.encode_manifest(manifest)
        progress["status"] = "prepared"
        save(path, journal)
        if manifest["status"] == "filing":
            capture.replay(bridge, manifest, previous, selves=selves)
        elif manifest["status"] == "correcting":
            manifest = capture.correct(bridge, manifest, previous, selves=selves)
        if manifest["status"] == "deferred":
            continue
        detach_source(bridge, source_uuid, people)
        review_rows = bridge([{"op": "list_review"}])[0]
        for group in ("pending", "approved"):
            for record in review_rows.get(group, []):
                text_body = record["text"]
                source_line = r"^Source: \[[^\n]*\]\(x-devonthink-item://" + re.escape(source_uuid) + r"\) \(\d{4}-\d{2}-\d{2}\)$"
                if not re.search(source_line, text_body, re.MULTILINE):
                    continue
                if re.search(r'"op"\s*:\s*"ensure_event"', text_body):
                    continue
                bridge([{"op": "capture_retire_review", "uuid": record["uuid"],
                         "expected_text": text_body}])
        if manifest["status"] in {"filed", "undone", "retained"}:
            bridge([{"op": "capture_retire_source", "uuid": source_uuid}])
        progress["status"] = "done"
        progress["disposition"] = manifest["status"]
        save(path, journal)
    return report


def retire_legacy_tasks(things, project_title, journal, path):
    decisions = journal.setdefault("legacy_tasks", {})
    token = things.auth_token()
    for project_uuid in things.find_projects(project_title):
        for row in things.read_project_tasks(project_uuid):
            if not re.search(r"(?:Proposal|Candidate): x-devonthink-item://[A-Za-z0-9-]+", row.get("notes") or ""):
                continue
            if row.get("status") != 0 or row.get("trashed"):
                decisions[row["uuid"]] = "terminal"
            elif things.update_todo(row["uuid"], token, {"canceled": "true"}, {"status": 2}):
                decisions[row["uuid"]] = "retired"
            else:
                decisions[row["uuid"]] = "waiting_for_token"
            save(path, journal)
    waiting = [uuid for uuid, status in decisions.items() if status == "waiting_for_token"]
    if waiting:
        current = things.read_tasks(waiting)
        for uuid in waiting:
            row = current.get(uuid)
            if row is None or row.get("status") != 0 or row.get("trashed"):
                decisions[uuid] = "terminal"
        save(path, journal)
    return not any(value == "waiting_for_token" for value in decisions.values())
