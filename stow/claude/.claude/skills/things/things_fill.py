#!/usr/bin/env python3
"""
things_fill.py — reliable, idempotent bulk fill for Things 3.

Encodes the hard-won rules for automating Things via its URL scheme:
  * Things must be FRONTMOST or URL-scheme writes silently no-op  -> we focus it.
  * The MCP / `add` command CANNOT create headings; only the `json` command
    (with an auth token) can                                       -> ensure_headings().
  * Multi-item `json` imports TRUNCATE on long URLs (>~4 KB) into a modal
    "problem with JSON" sheet that BLOCKS all further writes        -> we add ONE
    to-do per `add` call (short URLs can't truncate) and auto-dismiss stray sheets.
  * Confirm every write by reading the Things SQLite DB; compute "missing" from
    the DB each step so retries never duplicate                     -> idempotent.

Usage:
    python3 things_fill.py SPEC.json [--dry-run]

SPEC.json:
{
  "project": "<project uuid OR exact title>",
  "headings": ["▶️ Now", "Build", ...],          # optional; created if missing (needs token)
  "todos": [
    {"title": "...", "heading": "Build",
     "notes": "...", "tags": ["❗Important"], "checklist": ["a","b"]}
  ]
}

Auth token (only needed to CREATE headings): set env THINGS_AUTH_TOKEN.
Get it from Things -> Settings -> General -> Enable Things URLs -> Manage.
Tags must already exist in Things (the `add` command won't create them).
"""
import sys, os, json, time, glob, sqlite3, subprocess, urllib.parse

def find_db():
    pats = os.path.expanduser("~/Library/Group Containers/*/ThingsData-*/Things Database.thingsdatabase/main.sqlite")
    hits = glob.glob(pats)
    if not hits:
        sys.exit("Things SQLite DB not found (is Things 3 installed?).")
    return hits[0]

DB = find_db()

def _con():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def resolve_project(p):
    """Accept a uuid or an exact title; return the project uuid."""
    con = _con(); cur = con.cursor()
    row = cur.execute("SELECT uuid FROM TMTask WHERE uuid=? AND type=1", (p,)).fetchone()
    if not row:
        rows = cur.execute("SELECT uuid FROM TMTask WHERE title=? AND type=1 AND trashed=0 AND status=0", (p,)).fetchall()
        if len(rows) == 1: row = rows[0]
        elif len(rows) == 0: con.close(); sys.exit(f"No active project named {p!r}.")
        else: con.close(); sys.exit(f"Multiple active projects named {p!r}; pass the uuid.")
    con.close(); return row[0]

def heading_ids(project):
    con = _con(); cur = con.cursor()
    d = {t: u for u, t in cur.execute("SELECT uuid,title FROM TMTask WHERE project=? AND type=2 AND trashed=0", (project,))}
    con.close(); return d

def existing_titles(project):
    con = _con(); cur = con.cursor()
    hs = [r[0] for r in cur.execute("SELECT uuid FROM TMTask WHERE project=? AND type=2", (project,))]
    ph = ",".join("?" * len(hs)) or "''"
    rows = cur.execute(
        f"SELECT title FROM TMTask WHERE type=0 AND trashed=0 AND status=0 AND (project=? OR heading IN ({ph}))",
        [project] + hs).fetchall()
    con.close(); return set(r[0] for r in rows if r[0])

def focus():
    subprocess.run(["open", "-a", "Things3"]); time.sleep(0.6)

OSA_DISMISS = '''tell application "System Events" to tell (first process whose name contains "Things")
  repeat 12 times
    set f to false
    repeat with w in windows
      repeat with s in sheets of w
        set f to true
        repeat with b in buttons of s
          if (name of b) is in {"OK","Done","Cancel","Close"} then click b
        end repeat
      end repeat
    end repeat
    if not f then exit repeat
    delay 0.2
  end repeat
end tell'''

def dismiss_sheets():
    subprocess.run(["osascript", "-e", OSA_DISMISS], capture_output=True)

def _open(url):
    subprocess.run(["open", url])

def ensure_headings(project, titles, token):
    have = set(heading_ids(project))
    missing = [t for t in titles if t not in have]
    if not missing:
        return
    if not token:
        sys.exit("Need THINGS_AUTH_TOKEN to create headings: " + ", ".join(missing))
    focus()
    # one json `update` per heading keeps the URL tiny and avoids truncation
    for t in missing:
        data = [{"type": "project", "operation": "update", "id": project,
                 "attributes": {"items": [{"type": "heading", "attributes": {"title": t}}]}}]
        # NOTE: update+items is unreliable on some Things builds; fall back to create-on-empty
        _open("things:///json?auth-token=" + token + "&data=" + urllib.parse.quote(json.dumps(data, ensure_ascii=False)))
        for _ in range(12):
            time.sleep(1.0)
            if t in heading_ids(project): break
    still = [t for t in titles if t not in set(heading_ids(project))]
    if still:
        sys.exit("Could not create headings via update; create the project fresh with "
                 "a json `project` create whose items[] list the headings, then re-run. Missing: " + ", ".join(still))

def add_todo(project, heading_id, item):
    p = {"title": item["title"], "list-id": project, "reveal": "false"}
    if heading_id: p["heading-id"] = heading_id
    if item.get("notes"): p["notes"] = item["notes"]
    if item.get("tags"): p["tags"] = ",".join(item["tags"])
    if item.get("checklist"): p["checklist-items"] = "\n".join(item["checklist"])
    url = "things:///add?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in p.items())
    _open(url)

def fill(project, todos, headings=None, token=None, dry_run=False):
    project = resolve_project(project)
    by = {t["title"]: t for t in todos}
    have = existing_titles(project)
    missing = [ti for ti in by if ti not in have]
    print(f"project {project}: {len(have)} present, {len(by)} desired, {len(missing)} missing")
    if dry_run:
        for m in missing: print("  MISSING:", m)
        return
    if headings:
        ensure_headings(project, headings, token)
    hids = heading_ids(project)
    focus()
    for i in range(len(by) * 2 + 5):
        have = existing_titles(project)
        missing = [ti for ti in by if ti not in have]
        if not missing: break
        ti = missing[0]; item = by[ti]
        hid = hids.get(item.get("heading", ""))
        if i % 8 == 0: focus()                  # keep Things frontmost
        add_todo(project, hid, item)
        ok = False
        for _ in range(10):
            time.sleep(0.6)
            if ti in existing_titles(project): ok = True; break
        if not ok: dismiss_sheets()             # clear any stray error sheet, then retry next loop
        print(f"{'OK  ' if ok else 'MISS'} {ti[:60]}")
    left = [ti for ti in by if ti not in existing_titles(project)]
    print("DONE" if not left else f"INCOMPLETE, missing {len(left)}: " + ", ".join(left[:5]))

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    spec = json.load(open(args[0]))
    fill(spec["project"], spec["todos"], spec.get("headings"),
         os.environ.get("THINGS_AUTH_TOKEN"), dry_run=dry)
