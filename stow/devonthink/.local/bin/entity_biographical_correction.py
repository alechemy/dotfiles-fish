import copy
from datetime import datetime

import entity_biographical as bio
import entity_capture as capture


def contributions(manifest):
    for subject in manifest["subjects"]:
        if manifest["version"] == 2:
            for assertion in subject["contribution"]["assertions"]:
                yield subject, assertion, subject["uuid"] + "|" + assertion["reference"]["id"]
        else:
            yield subject, subject["contribution"], subject["uuid"] + "|" + subject["contribution"]["id"]


def correct(bridge, manifest, previous, selves=()):
    correction = manifest["correction"]
    if correction.get("source_edit"):
        edit = correction["source_edit"]
        actual = capture.source_text(bridge([{"op": "get_text", "uuid": manifest["source_uuid"]}])[0]["text"])
        if actual not in {edit["from"], edit["to"]}:
            raise ValueError("The source changed during correction. Its text is retained.")
        value = capture.encode_manifest(manifest)
        if previous != value:
            bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
                     "value": value, "text": actual}])
            previous = value
        capture.repair_source_edit(bridge, manifest["source_uuid"], edit)

    def persist(next_manifest=None):
        nonlocal previous
        if next_manifest is not None:
            correction["next"] = next_manifest
        value = capture.encode_manifest(manifest)
        bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
                 "value": value, "text": correction["text"]}])
        previous = value

    def conflict(reason, token):
        manifest["reason"] = reason
        correction["conflict_token"] = token
        persist()
        raise ValueError(capture.QUESTIONS[reason])

    persist()
    destination = correction.get("next")
    destination_keys = set()
    preserved = []
    for subject, row, token in contributions(destination) if destination else []:
        destination_keys.add(token)
        body = bridge([{"op": "get_text", "uuid": subject["uuid"]}])[0]["text"] if subject["uuid"] else ""
        keep = "destination|" + token in correction.get("keep_edited", [])
        if destination["version"] == 2:
            started = any(r["reference"]["id"] == row["reference"]["id"] for item in bio.render(body, subject["uuid"]) for r in
                          [{"reference": ref} for ref in item["data"]["references"]])
            if keep:
                after = bio.protect(body, row, subject["uuid"])
                if after != body:
                    bio.write_body(bridge, manifest, previous, subject["uuid"], body, after)
                preserved.append(token)
            elif started or token in destination.get("applied_references", []):
                try:
                    bio.verify(body, row, subject["uuid"])
                except ValueError:
                    conflict("destination_edited", "destination|" + token)
        elif subject["applied"] or row["id"] in body:
            if not keep and body.count(row["block"]) != 1:
                conflict("destination_edited", "destination|" + token)
            if keep:
                preserved.append(row["id"])
    if destination and destination["status"] != "filed":
        capture.replay(bridge, destination, persist_manifest=persist, selves=selves,
                       preserve_contributions=preserved)
    destinations = []
    for subject, row, token in contributions(destination) if destination else []:
        body = bridge([{"op": "get_text", "uuid": subject["uuid"]}])[0]["text"]
        if destination["version"] == 2:
            try:
                bio.verify(body, row, subject["uuid"])
            except ValueError:
                conflict("destination_edited", "destination|" + token)
        elif "destination|" + token not in correction.get("keep_edited", []) and body.count(row["block"]) != 1:
            conflict("destination_edited", "destination|" + token)
        if not any(d["uuid"] == subject["uuid"] for d in destinations):
            destinations.append({"uuid": subject["uuid"], "body": body})
    for subject, row, token in contributions(manifest):
        if not subject["uuid"] or token in correction["removed"] or token in destination_keys:
            continue
        body = bridge([{"op": "get_text", "uuid": subject["uuid"]}])[0]["text"]
        intent = correction.setdefault("removal_intents", {}).get(token)
        if intent and capture.revision(body) == intent["after"]:
            correction["removed"].append(token)
            persist()
            continue
        if intent and capture.revision(body) != intent["before"]:
            conflict("edited_contribution", token)
        try:
            if manifest["version"] == 2:
                protected = bio.protect(body, row, subject["uuid"]) if token in correction.get("keep_edited", []) else body
                after = bio.detach(protected, row, subject["uuid"])
            elif token in correction.get("keep_edited", []):
                after = body
            elif body.replace("\r\n", "\n").replace("\r", "\n").count(row["block"]) == 1:
                after = body.replace("\r\n", "\n").replace("\r", "\n").replace(row["block"], "")
            else:
                raise ValueError("Legacy contribution changed.")
        except ValueError:
            conflict("edited_contribution", token)
        frozen_intent = {"before": capture.revision(body), "after": capture.revision(after)}
        if intent and intent != frozen_intent:
            conflict("edited_contribution", token)
        correction["removal_intents"][token] = frozen_intent
        persist()
        if after != body:
            bio.write_body(bridge, manifest, previous, subject["uuid"], body, after, destinations)
        for target in destinations:
            if target["uuid"] == subject["uuid"]:
                target["body"] = after
        correction["removed"].append(token)
        persist()
    day = correction.setdefault("receipt_date", datetime.now().strftime("%Y-%m-%d"))
    if not correction.get("receipt_uuid"):
        correction["receipt_uuid"] = bridge([{"op": "get_or_create_daily", "date": day, "heading": day}])[0]["uuid"]
        persist()
    word = "Undid" if correction["action"] == "undo" else "Corrected"
    line = ("- 📝 " + word + " this capture's filing. [Source](x-devonthink-item://" + manifest["source_uuid"] +
            ") <!-- capture-receipt:" + capture.revision(correction["id"] + "|status") + " -->")
    bridge([{"op": "append_pinned", "uuid": correction["receipt_uuid"], "line": line}])
    if line not in bridge([{"op": "get_text", "uuid": correction["receipt_uuid"]}])[0]["text"]:
        raise ValueError("Correction receipt could not be verified.")
    snapshot = {k: copy.deepcopy(v) for k, v in manifest.items() if k not in {"history", "correction"}}
    snapshot["status"] = "undone"
    snapshot["preserved_manual"] = correction.get("keep_edited", [])
    history = list(manifest.get("history", [])) + [snapshot]
    edit = correction.get("source_edit")
    if edit and edit["from"] != manifest["text"]:
        history.append(capture.pending({"uuid": manifest["source_uuid"]}, manifest["source_date"], edit["from"], [],
            capture.outcome("unresolved", "source_changed"), day))
    if destination:
        final = dict(destination, history=history)
    else:
        final = dict(snapshot, status="undone", subjects=[], history=history, text=correction["text"],
                     revision=capture.revision(correction["text"]))
        final.pop("changed_text", None)
        final.pop("reason", None)
    bridge([{"op": "capture_store", "uuid": manifest["source_uuid"], "expected": previous,
             "value": capture.encode_manifest(final), "text": correction["text"]}])
    bridge([{"op": "capture_retire_source", "uuid": manifest["source_uuid"]}])
    return final
