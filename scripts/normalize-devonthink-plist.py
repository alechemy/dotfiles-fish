#!/usr/bin/env python3
"""Print a DEVONthink config plist as XML with runtime state stripped.

DEVONthink stores per-rule bookkeeping alongside the rule itself, so a plist
that is byte-different is not necessarily *configured* differently: every smart
rule that fires rewrites its own LastExecution timestamp. Tracking those makes
the seed report drift forever, re-drift the moment a rule next runs, and carry
one machine's execution history into the repo.

Both the reconciler (which compares) and the dump (which writes the seed) route
through this, so they agree on what a seed is: portable configuration, never
machine state.

CustomMetaData.plist is a schema (a list of field definitions), not a rule, and
seed-devonthink-config.sh deliberately merges it by identifier rather than
copying it whole — a byte/XML compare would report drift forever, since the
merge itself reassigns `index` on the fields it adds. The --custom-metadata-*
modes give the reconciler the same identifier-aware compare and the same merge
algorithm, instead of a whole-file copy that would drop machine-local fields.

Usage:
    normalize-devonthink-plist.py <plist>                     canonical XML on stdout
    normalize-devonthink-plist.py --custom-metadata-status <seed> <live>
        Print missing|same|differs: same identifier-set semantics as the merge
        below — "differs" means at least one seed field is absent from live.
    normalize-devonthink-plist.py --custom-metadata-merge <seed> <live>
        Merge seed fields into live by identifier (live is created verbatim if
        absent) and print the identifiers added, one per line ("none" if none).
"""

import os
import plistlib
import sys
import tempfile

# Per-entry keys DEVONthink owns at runtime; none of them describe the rule.
RUNTIME_KEYS = {"LastExecution"}


def strip(node):
    if isinstance(node, list):
        return [strip(item) for item in node]
    if isinstance(node, dict):
        return {k: strip(v) for k, v in node.items() if k not in RUNTIME_KEYS}
    return node


def load_plist(path):
    with open(path, "rb") as f:
        return plistlib.load(f)


def custom_metadata_status(seed_path, live_path):
    """missing | same | differs. Ignores `index` and RUNTIME_KEYS: "differs"
    means at least one seed identifier is absent from live, matching exactly
    what custom_metadata_merge would add — a merge-caused index shuffle is
    never mistaken for configuration drift."""
    if not os.path.exists(live_path):
        return "missing"
    try:
        seed, live = load_plist(seed_path), load_plist(live_path)
    except Exception:
        return "differs"
    if not isinstance(seed, list) or not isinstance(live, list):
        return "differs"
    have = {f.get("identifier") for f in live}
    return "differs" if any(f.get("identifier") not in have for f in seed) \
        else "same"


def custom_metadata_merge(seed_path, live_path):
    """Merge seed field definitions into live by identifier: a definition
    missing from live is appended with a reassigned index, an existing one is
    never touched, and a live file that doesn't exist yet is seeded verbatim
    (no index reassignment, so the seed's own field order is preserved) — the
    same algorithm seed-devonthink-config.sh uses. Writes live_path atomically
    and returns the identifiers added."""
    seed = load_plist(seed_path)
    if not os.path.exists(live_path):
        live = seed
        added = [f.get("identifier") for f in seed]
    else:
        live = load_plist(live_path)
        have = {f.get("identifier") for f in live}
        nxt = max((f.get("index", 0) for f in live), default=0) + 1
        added = []
        for field in seed:
            if field.get("identifier") in have:
                continue
            field = dict(field)
            field["index"] = nxt
            nxt += 1
            live.append(field)
            added.append(field.get("identifier"))
    if added:
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(live_path) or ".", suffix=".plist")
        try:
            with os.fdopen(fd, "wb") as f:
                plistlib.dump(live, f)
            os.replace(tmp, live_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return added


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--custom-metadata-status":
        print(custom_metadata_status(sys.argv[2], sys.argv[3]))
        return
    if len(sys.argv) == 4 and sys.argv[1] == "--custom-metadata-merge":
        added = custom_metadata_merge(sys.argv[2], sys.argv[3])
        print("\n".join(added) if added else "none")
        return
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], "rb") as f:
        data = plistlib.load(f)
    sys.stdout.buffer.write(plistlib.dumps(strip(data), fmt=plistlib.FMT_XML))


if __name__ == "__main__":
    main()
