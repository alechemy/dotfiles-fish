"""Things 3 primitives for launchd pipelines.

Writes go through the URL scheme fired with `open -g` — backgrounded, no focus
steal, and no per-app Automation grant a headless launchd prompt could fumble.
Reads go straight to Things' SQLite store, opened read-only; the file lives
under ~/Library/Group Containers, which is Full Disk Access-gated like
~/Library/Messages (the existing FDA grant on /usr/bin/python3 covers both).
Every write is confirmed by reading the DB afterwards — a fired `open` is not
evidence the write landed (see the `things` skill).

Import pattern (scripts live alongside this file in ~/.local/bin):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.home() / ".local" / "bin"))
    import things_bridge

All failures raise ThingsError with a human-readable message; callers are
expected to catch it per phase, log one WARNING, and continue without Things.
TMTask timestamps (stopDate, userModificationDate, creationDate) are Unix
seconds, and status is 0=open / 2=canceled / 3=done.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse

DB_GLOB = ("~/Library/Group Containers/*/ThingsData-*/"
           "Things Database.thingsdatabase/main.sqlite")
ZSHENV = os.path.expanduser("~/.zshenv")
TASK_COLUMNS = ("uuid", "title", "notes", "status", "trashed", "project",
                "heading", "stopDate", "userModificationDate")


class ThingsError(RuntimeError):
    """Things is unusable for this run (DB missing/unreadable, write
    unconfirmed, ambiguous project). Callers degrade, never crash."""


def find_db():
    hits = glob.glob(os.path.expanduser(DB_GLOB))
    if not hits:
        raise ThingsError("Things database not found (is Things 3 installed?)")
    return hits[0]


def _query(sql, params=()):
    try:
        con = sqlite3.connect(f"file:{find_db()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise ThingsError(
            f"cannot open Things database ({exc}); if this is a launchd run, "
            "check Full Disk Access for /usr/bin/python3") from exc
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise ThingsError(f"Things database query failed: {exc}") from exc
    finally:
        con.close()


def _row_dicts(rows):
    return [dict(zip(TASK_COLUMNS, r)) for r in rows]


def read_tasks(uuids):
    """Current TMTask rows for the given task uuids, keyed by uuid.

    A task the user hard-deleted (emptied trash) is simply absent from the
    result — distinguish that from trashed=1, which is the normal delete.
    """
    uuids = list(uuids)
    if not uuids:
        return {}
    ph = ",".join("?" * len(uuids))
    rows = _query(
        f"SELECT {','.join(TASK_COLUMNS)} FROM TMTask "
        f"WHERE type=0 AND uuid IN ({ph})", uuids)
    return {r["uuid"]: r for r in _row_dicts(rows)}


def read_project_tasks(project_uuid):
    """Every to-do in a project — all statuses, trashed rows included, and
    tasks filed under headings (whose `project` column is NULL)."""
    rows = _query(
        f"SELECT {','.join(TASK_COLUMNS)} FROM TMTask "
        "WHERE type=0 AND (project=? OR heading IN "
        "(SELECT uuid FROM TMTask WHERE type=2 AND project=?))",
        (project_uuid, project_uuid))
    return _row_dicts(rows)


def project_alive(project_uuid):
    return bool(_query(
        "SELECT 1 FROM TMTask WHERE uuid=? AND type=1 AND trashed=0 AND status=0",
        (project_uuid,)))


def build_url(command, params):
    query = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in params.items())
    return f"things:///{command}?{query}"


def _fire(url):
    subprocess.run(["/usr/bin/open", "-g", url], check=True)


def prewarm():
    """Launch Things hidden in the background if needed. The local DB only
    receives Things Cloud pushes while the app runs, so a closed app would
    silently stall the whole review loop."""
    subprocess.run(["/usr/bin/open", "-g", "-j", "-a", "Things3"], check=False)
    time.sleep(0.4)


def _wait(pred, tries=24, delay=0.4):
    for _ in range(tries):
        time.sleep(delay)
        result = pred()
        if result:
            return result
    return None


def ensure_project(title):
    """UUID of the active project with this title, creating it if absent.
    More than one active match raises — never guess between duplicates."""
    def lookup():
        return [r[0] for r in _query(
            "SELECT uuid FROM TMTask WHERE title=? AND type=1 AND trashed=0 "
            "AND status=0", (title,))]

    hits = lookup()
    if len(hits) > 1:
        raise ThingsError(f"multiple active Things projects named {title!r}; "
                          "rename or delete the extras")
    if hits:
        return hits[0]
    prewarm()
    _fire(build_url("add-project", {"title": title, "reveal": "false"}))
    hits = _wait(lookup)
    if not hits:
        raise ThingsError(f"creating Things project {title!r} was not "
                          "confirmed by the database")
    if len(hits) > 1:
        raise ThingsError(f"multiple active Things projects named {title!r} "
                          "appeared; rename or delete the extras")
    return hits[0]


def add_todo_params(project_uuid, title, notes, when=None):
    """URL parameters for `add`, so a caller that sizes the URL before firing
    measures the URL `add_todo` actually fires. `when` is a Things schedule
    keyword (today/tomorrow/evening/anytime/someday) or a date."""
    params = {"title": title, "notes": notes, "list-id": project_uuid or "",
              "reveal": "false"}
    if when:
        params["when"] = when
    return params


def add_todo(project_uuid, title, notes, marker, when=None):
    """Create a to-do and return its uuid, confirmed via the DB.

    Identification is a before/after uuid-set diff constrained to rows whose
    notes contain `marker` — title matching would collide with older tasks.
    """
    before = {t["uuid"] for t in read_project_tasks(project_uuid)}
    prewarm()
    _fire(build_url("add", add_todo_params(project_uuid, title, notes, when)))

    def created():
        return [t["uuid"] for t in read_project_tasks(project_uuid)
                if t["uuid"] not in before and marker in (t["notes"] or "")]

    hits = _wait(created)
    if not hits:
        raise ThingsError(f"adding to-do {title!r} was not confirmed by the "
                          "database (URL may have been rejected)")
    return hits[0]


def update_todo(task_uuid, auth_token, attrs, expect):
    """Fire an `update` and confirm the expected post-state in the DB.

    `attrs` are URL parameters (e.g. {"completed": "false", "notes": ...});
    `expect` maps TMTask columns to required values. Returns True only when
    the DB reflects every expectation — callers must not advance their own
    state on False. An already-satisfied expectation returns True without
    firing (and without needing the token).
    """
    def confirmed():
        row = read_tasks([task_uuid]).get(task_uuid)
        return row is not None and all(row.get(k) == v for k, v in expect.items())

    if confirmed():
        return True
    if not auth_token:
        return False
    params = {"auth-token": auth_token, "id": task_uuid}
    params.update(attrs)
    prewarm()
    _fire(build_url("update", params))
    return bool(_wait(confirmed))


def auth_token():
    """Things URL auth token, from the environment or the managed block
    build-things-config.sh writes into ~/.zshenv (launchd sources neither
    shell profile, so the poller reads the file itself). None when absent."""
    token = os.environ.get("THINGS_AUTH_TOKEN")
    if token:
        return token
    try:
        with open(ZSHENV) as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^export THINGS_AUTH_TOKEN='([^']+)'$", text, re.MULTILINE)
    return m.group(1) if m else None
