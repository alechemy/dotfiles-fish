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

Usage: normalize-devonthink-plist.py <plist>   # canonical XML on stdout
"""

import plistlib
import sys

# Per-entry keys DEVONthink owns at runtime; none of them describe the rule.
RUNTIME_KEYS = {"LastExecution"}


def strip(node):
    if isinstance(node, list):
        return [strip(item) for item in node]
    if isinstance(node, dict):
        return {k: strip(v) for k, v in node.items() if k not in RUNTIME_KEYS}
    return node


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], "rb") as f:
        data = plistlib.load(f)
    sys.stdout.buffer.write(plistlib.dumps(strip(data), fmt=plistlib.FMT_XML))


if __name__ == "__main__":
    main()
