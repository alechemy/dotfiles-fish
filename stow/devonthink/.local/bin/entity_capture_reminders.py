"""Optional question reminders. Task housekeeping never decides entity data."""

import json
import os
import tempfile
from pathlib import Path

import entity_capture as capture

MARKER = "Entity question v2: x-devonthink-item://"


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".capture-reminders.")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync(bridge, things, config, path, dry_run=False):
    if config.get("THINGS_SYNC") != "on":
        return
    if os.path.exists(path):
        with open(path) as stream:
            state = json.load(stream)
        if not isinstance(state, dict) or state.get("version") != 2 or not isinstance(state.get("tasks"), dict):
            raise RuntimeError("Question reminder state is unreadable; reminders are paused.")
    else:
        state = {"version": 2, "tasks": {}}
    obsolete = state.setdefault("obsolete", {})
    records = {row["uuid"]: row for row in bridge([{"op": "list_captures"}])[0]}
    uncertain = set()
    for source_uuid in set(state["tasks"]) - set(records):
        try:
            source, body = bridge([{"op": "get_source", "uuid": source_uuid}, {"op": "get_text", "uuid": source_uuid}])
            records[source_uuid] = {"uuid": source_uuid, "operation": source.get("capture_operation", ""),
                                    "text": capture.source_text(body["text"])}
        except Exception:
            uncertain.add(source_uuid)
    pending = {}
    for source_uuid, row in records.items():
        if not row.get("operation"):
            uncertain.add(source_uuid)
            continue
        try:
            manifest = capture.decode_manifest(row["operation"], source_uuid)
        except (ValueError, TypeError, KeyError):
            uncertain.add(source_uuid)
            continue
        live = row.get("text", manifest.get("changed_text", manifest["text"]))
        if manifest["status"] == "filed" and live != manifest["text"]:
            manifest.update(status="revision_question", reason="source_changed", changed_text=live)
        if manifest["status"] in {"question", "revision_question"} and not manifest.get("deferred"):
            pending[source_uuid] = (manifest, row["operation"], capture.revision(live), live)
    if dry_run or (not pending and not state["tasks"] and not obsolete):
        return
    project = things.ensure_project(config.get("THINGS_PROJECT", "Entity Filing"))
    rows = things.read_project_tasks(project)
    token = things.auth_token()
    for source_uuid, (manifest, raw, question_revision, live) in pending.items():
        marker = MARKER + source_uuid + "#" + question_revision
        entry = state["tasks"].get(source_uuid)
        if entry and entry.get("revision") != question_revision:
            obsolete[entry["task_uuid"]] = dict(entry, source_uuid=source_uuid)
            del state["tasks"][source_uuid]
            entry = None
            save(path, state)
        matches = [row for row in rows if marker in (row.get("notes") or "")]
        if matches:
            if len(matches) > 1:
                continue
            row = matches[0]
            entry = {"task_uuid": row["uuid"], "revision": question_revision}
            state["tasks"][source_uuid] = entry
            if row.get("status") != 0 or row.get("trashed"):
                entry["dismissed"] = True
        elif entry:
            entry["dismissed"] = True
        elif manifest.get("reminder_revision") != question_revision and not (
                manifest.get("reminder_offered") and not manifest.get("reminder_revision") and manifest["revision"] == question_revision):
            url = config.get("REVIEW_URL") or "http://localhost:8080/entities/"
            notes = marker + "\nAnswer in the review app: " + url + "#" + source_uuid
            params = things.add_todo_params(project, "A saved note needs an answer", notes)
            if len(things.build_url("add", params)) > 3500:
                continue
            manifest["reminder_offered"] = True
            manifest["reminder_revision"] = question_revision
            bridge([{"op": "capture_store", "uuid": source_uuid, "expected": raw,
                     "value": capture.encode_manifest(manifest), "text": live}])
            task_uuid = things.add_todo(project, "A saved note needs an answer", notes, marker)
            state["tasks"][source_uuid] = {"task_uuid": task_uuid, "revision": question_revision}
        save(path, state)
    retire = dict(obsolete)
    retire.update({entry["task_uuid"]: dict(entry, source_uuid=source_uuid)
                   for source_uuid, entry in state["tasks"].items() if source_uuid not in pending and source_uuid not in uncertain})
    current = things.read_tasks(list(retire)) if retire else {}
    for task_uuid, entry in retire.items():
        row = current.get(task_uuid)
        marker = MARKER + entry["source_uuid"] + "#" + entry["revision"]
        terminal = row is None or row.get("status") != 0 or row.get("trashed") or marker not in (row.get("notes") or "")
        if not terminal:
            terminal = things.update_todo(task_uuid, token, {"canceled": "true"}, {"status": 2})
        if terminal:
            obsolete.pop(task_uuid, None)
            source_uuid = entry["source_uuid"]
            if source_uuid not in pending and state["tasks"].get(source_uuid, {}).get("task_uuid") == task_uuid:
                state["tasks"].pop(source_uuid, None)
    save(path, state)
