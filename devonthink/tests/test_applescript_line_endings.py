"""Guards the AppleScript → shell → record round-trip against CR corruption.

`do shell script` coerces a helper's LF output to CR, and AppleScript's `return`
constant IS a CR. Either one, written back into a record, stores the whole body
as one CR-delimited line. Consumers that split on '\\n' — entity-dt-bridge's
upsert_section, sync-markdown-h1 — then see a body with no lines and no
headers, and respectively duplicate every generated section or emit an H1 in
place of the document.

The scan follows a tainted value to a record sink, so it catches the
indirections a flat grep misses: a shell result assigned through an
intermediate variable, a CR-built content block handed to a handler, and
`create record with {plain text:…}` as well as `set plain text of`.
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "tests"}
# AppleScript lives in .applescript files and embedded in Python/shell strings.
SOURCE_SUFFIXES = {".applescript", ".scpt", ".py", ".sh", ".js"}
MARKERS = ("do shell script", 'tell application id "DNtp"', "set plain text of")

CONTINUATION = re.compile(r"¬\s*\n\s*")
ASSIGN = re.compile(r"^\s*set\s+(\w+)\s+to\s+(.*)$")
BODY_SINK = re.compile(
    r"set\s+(?:plain text|rich text|comment)\s+of\s+.+?\s+to\s+(.+)$")
CREATE_SINK = re.compile(r"create record with\s*\{([^}]*)\}")
HANDLER_DEF = re.compile(r"^\s*on\s+(\w+)\s*\(")
HANDLER_CALL = re.compile(r"\bmy\s+(\w+)\s*\((.*)\)")
CR_BUILT = re.compile(r"&\s*return\b|\breturn\s*&")
MODIFIER = "without altering line endings"
# Commands whose output is one line by construction, not by luck. `echo`/`printf`
# are deliberately absent: `echo <body> | helper` is a pipeline that can emit
# anything, and exempting it on its first word would hide a real body round-trip.
ONE_LINER = re.compile(r'do shell script\s+(?:¬\s*)?"\s*'
                       r"(date|mktemp|rm|shasum)\b")
# `paragraphs of` is AppleScript's CR-tolerant split: it consumes the coercion.
UNTAINT = re.compile(r"\bparagraphs of\b")


def sources():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if SKIP_DIRS & set(path.parts):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if any(m in text for m in MARKERS):
            yield path, CONTINUATION.sub(" ", text)


def writing_handlers(text):
    """Names of `on NAME(…)` handlers whose own body writes to a record, so a
    call to one is treated as a sink. Keyed off what the handler does, not its
    name — a logging handler taking a tainted argument is not a sink."""
    names, current, body = set(), None, []
    for line in text.split("\n"):
        start = HANDLER_DEF.match(line)
        if start:
            current, body = start.group(1), []
            continue
        if current is None:
            continue
        if re.match(rf"^\s*end\s+{re.escape(current)}\b", line):
            if any(BODY_SINK.search(b) or CREATE_SINK.search(b) for b in body):
                names.add(current)
            current = None
            continue
        body.append(line)
    return names


def scan(text):
    """(offenders, sink_count) for one AppleScript source.

    Tracks two taints per variable: a `do shell script` result that lacks the
    modifier, and a value built with the `return` constant. Either reaching a
    record body is the bug.
    """
    shell, cr, offenders, sinks = set(), set(), [], 0
    writers = writing_handlers(text)

    def tainted(expr, names):
        return [n for n in names if re.search(rf"\b{re.escape(n)}\b", expr)]

    for lineno, line in enumerate(text.split("\n"), 1):
        assign = ASSIGN.match(line)
        if assign:
            var, expr = assign.group(1), assign.group(2)
            if "do shell script" in expr:
                # The call's own result is what lands in the variable; a tainted
                # argument (a tmpPath from mktemp) says nothing about its output.
                dirty = MODIFIER not in expr and not ONE_LINER.search(expr)
                shell.add(var) if dirty else shell.discard(var)
                # The modifier says nothing about a `return` concatenated on after.
                cr.add(var) if CR_BUILT.search(expr) else cr.discard(var)
                continue
            if UNTAINT.search(expr):
                shell.discard(var)
                cr.discard(var)
                continue
            shell.add(var) if tainted(expr, shell) else shell.discard(var)
            if CR_BUILT.search(expr) or tainted(expr, cr):
                cr.add(var)
            else:
                cr.discard(var)

        handler = HANDLER_CALL.search(line)
        sink_hits = []
        for sink_re, kind in ((BODY_SINK, "body"), (CREATE_SINK, "create")):
            hit = sink_re.search(line)
            if hit and not (kind == "create" and "plain text:" not in hit.group(1)):
                if not (kind == "body" and ASSIGN.match(line)):
                    sink_hits.append(hit.group(1))
        # A handler that writes a body is a sink too — the caller's argument is
        # what lands in the record.
        if handler and handler.group(1) in writers:
            sink_hits.append(handler.group(2))

        for expr in sink_hits:
            sinks += 1
            if "do shell script" in expr and MODIFIER not in expr:
                offenders.append(f"{lineno}: inline unflagged shell output")
            for var in tainted(expr, shell):
                offenders.append(f"{lineno}: {var} (shell output, no modifier)")
            for var in tainted(expr, cr):
                offenders.append(f"{lineno}: {var} (built with `return`)")
            if CR_BUILT.search(expr):
                offenders.append(f"{lineno}: inline `return` in record text")
    return offenders, sinks


class DoShellScriptCoercesLineEndings(unittest.TestCase):
    """The platform behavior the modifier exists to defeat."""

    def char_id(self, extra=""):
        out = subprocess.run(
            ["/usr/bin/osascript",
             "-e", 'set t to do shell script "printf \'a\nb\'"' + extra,
             "-e", "return (id of character 2 of t) as text"],
            capture_output=True, text=True, check=True,
        )
        return int(out.stdout.strip())

    def test_bare_call_returns_cr(self):
        self.assertEqual(self.char_id(), 13)

    def test_modifier_preserves_lf(self):
        self.assertEqual(self.char_id(" " + MODIFIER), 10)


class RecordWritesKeepLinefeeds(unittest.TestCase):
    def test_no_cr_source_reaches_a_record_body(self):
        offenders = []
        for path, text in sources():
            found, _ = scan(text)
            offenders += [f"{path.relative_to(REPO)}:{o}" for o in found]
        self.assertEqual(offenders, [])

    def test_scan_is_not_vacuous(self):
        """A regex that silently stops matching would pass the test above."""
        files = list(sources())
        total = sum(scan(text)[1] for _, text in files)
        self.assertGreaterEqual(len(files), 12, "source discovery collapsed")
        self.assertGreaterEqual(total, 8, "record sinks no longer detected")

    def test_scan_catches_the_known_shapes(self):
        cases = {
            "direct": 'set x to do shell script "helper"\n'
                      "set plain text of r to x",
            "indirect": 'set raw to do shell script "helper"\n'
                        "set x to raw & linefeed\n"
                        "set plain text of r to x",
            "inline": 'set plain text of r to (do shell script "helper")',
            "create": 'set x to do shell script "helper"\n'
                      "create record with {name:n, plain text:x} in g",
            "cr_content": "on appendToSection(n, h, blk)\n"
                          "set plain text of n to blk\n"
                          "end appendToSection\n"
                          'set block to "### h" & return & return\n'
                          "my appendToSection(note, header, block)",
            "cr_skeleton": 'set noteContent to "# " & d & return\n'
                           "set plain text of newNote to noteContent",
            "echo_pipeline": 'set x to do shell script "echo " & quoted form of '
                             'body & " | /usr/bin/python3 helper.py"\n'
                             "set plain text of r to x",
            "cr_after_modifier": 'set x to (do shell script "helper" '
                                 f"{MODIFIER}) & return\n"
                                 "set plain text of r to x",
        }
        for name, src in cases.items():
            with self.subTest(shape=name):
                self.assertTrue(scan(src)[0], f"{name} slipped through")

    def test_modifier_and_linefeed_clear_the_scan(self):
        clean = (f'set x to do shell script "helper" {MODIFIER}\n'
                 'set noteContent to "# " & d & linefeed\n'
                 "set plain text of r to x\n"
                 "create record with {name:n, plain text:noteContent} in g")
        self.assertEqual(scan(clean)[0], [])


if __name__ == "__main__":
    unittest.main()
