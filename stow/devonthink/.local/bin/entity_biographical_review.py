import json
import os
import re
import stat
from pathlib import Path

import entity_biographical_migration as migration
import entity_capture as capture

MAX_PLAN_BYTES = 32 * 1024 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate migration key.")
        result[key] = value
    return result


def read_private(path, maximum=MAX_PLAN_BYTES):
    path = Path(path)
    if path.parent.is_symlink():
        raise ValueError("Private registration directory required.")
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > maximum:
            raise ValueError("Private regular artifact required.")
        with os.fdopen(fd, "r") as stream:
            fd = None
            raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                raise ValueError("Private artifact is too large.")
            return json.loads(raw, object_pairs_hook=unique_object)
    finally:
        if fd is not None:
            os.close(fd)


def directory(state_dir):
    path = Path(state_dir) / "entity-biographical-plans"
    if path.is_symlink():
        raise ValueError("Private registration directory required.")
    return path


def plan_path(state_dir, plan_id):
    if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{64}", plan_id):
        raise ValueError("Invalid private plan ID.")
    return directory(state_dir) / (plan_id + ".json")


def registry(state_dir):
    path = directory(state_dir) / "registry.json"
    if path.is_symlink():
        raise ValueError("Private registry required.")
    if not path.exists():
        return {"version": 2, "plans": {}}
    value = read_private(path, 1024 * 1024)
    if not isinstance(value, dict) or value.get("version") != 2 or not isinstance(value.get("plans"), dict):
        raise ValueError("Migration registry is unreadable.")
    for plan_id, record in value["plans"].items():
        plan_path(state_dir, plan_id)
        if not isinstance(record, dict) or record.get("status") not in {"pending", "reviewed", "superseded", "applying", "applied", "rolled_back"}:
            raise ValueError("Migration registry status is unreadable.")
    return value


def save_registry(state_dir, before, after):
    if registry(state_dir) != before:
        raise ValueError("Migration registry changed.")
    migration.private_save(directory(state_dir) / "registry.json", after)


def register(state_dir, plan, prior=None):
    migration.validate(plan)
    current = registry(state_dir)
    updated = json.loads(json.dumps(current))
    if prior is not None:
        if current["plans"].get(prior, {}).get("status") not in {"pending", "reviewed"}:
            raise ValueError("Migration preview is stale or superseded.")
        updated["plans"][prior] = {"status": "superseded", "superseded_by": plan["plan_id"]}
    path = plan_path(state_dir, plan["plan_id"])
    if path.is_symlink() or current["plans"].get(plan["plan_id"], {}).get("status") in {"superseded", "applying", "applied", "rolled_back"}:
        raise ValueError("Migration registration is not replaceable.")
    if path.exists():
        if read_private(path) != plan:
            raise ValueError("Registered migration evidence changed.")
    else:
        migration.private_save(path, plan)
    status = "pending" if plan["counts"]["pending_questions"] else "reviewed"
    updated["plans"][plan["plan_id"]] = {"status": status}
    save_registry(state_dir, current, updated)
    return path


def load_current(state_dir, plan_id, writable=False, allow_terminal=False):
    plan_path(state_dir, plan_id)
    current = registry(state_dir)
    record = current["plans"].get(plan_id)
    if (not record or record["status"] == "superseded" or
            record["status"] in {"applied", "rolled_back"} and (writable or not allow_terminal)):
        raise ValueError("Migration preview is stale or superseded.")
    if writable and (record["status"] == "applying" or (Path(state_dir) / migration.FENCE_NAME).exists()):
        raise ValueError("Migration decisions are paused during recovery.")
    plan = read_private(plan_path(state_dir, plan_id))
    migration.validate(plan)
    if plan["plan_id"] != plan_id:
        raise ValueError("Migration registration changed.")
    return plan


def set_status(state_dir, plan_id, status):
    before = registry(state_dir)
    if plan_id not in before["plans"] or before["plans"][plan_id]["status"] == "superseded":
        raise ValueError("Migration preview is superseded.")
    after = json.loads(json.dumps(before))
    after["plans"][plan_id] = {"status": status}
    save_registry(state_dir, before, after)


def views(state_dir):
    out = []
    for plan_id, record in registry(state_dir)["plans"].items():
        if record["status"] not in {"pending", "applying"}:
            continue
        plan = load_current(state_dir, plan_id)
        for row in plan["questions"]:
            view = capture.view(row["manifest"], [])
            view.update(uuid="migration:" + plan_id + ":" + row["source_uuid"], source_uuid=row["source_uuid"],
                        migration_plan=plan_id, can_retain_legacy=False, conflict=False)
            out.append(view)
    return out


def decide(bridge, state_dir, plan_id, source_uuid, answers):
    plan = load_current(state_dir, plan_id, writable=True)
    next_plan = migration.resolve(bridge, plan, {"plan_id": plan_id, "answers": {source_uuid: answers}})
    register(state_dir, next_plan, prior=plan_id)
    return {"ok": True, "status": "migration_waiting" if next_plan["counts"]["pending_questions"] else "migration_reviewed",
            "plan_id": next_plan["plan_id"], "pending_questions": next_plan["counts"]["pending_questions"]}
