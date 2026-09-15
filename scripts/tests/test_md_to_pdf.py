#!/usr/bin/env python3
"""Exercise Markdown conversion, build identity, and annotation protection."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "stow/agents/.agents/skills/md-to-pdf"
SPEC = importlib.util.spec_from_file_location("md_to_pdf", SKILL / "md_to_pdf.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PROFILE = json.loads((SKILL / "assets/boox-note-max.json").read_bytes())
TEMPLATE = (SKILL / "assets/reader.typst").read_bytes()
FONTS = {prefix + "-Regular.ttf": prefix.encode() for prefix in MODULE.FONT_PREFIXES}
VERSIONS = {"pandoc": "pandoc 3.11", "typst": "typst 0.15.1"}


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "notes.md"
        self.source.write_text("# Reading notes\n\nA paragraph.\n")


class BuildTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.args = MODULE.parser().parse_args([str(self.source)])
        self.renders = 0
        for name, value in (("tool_versions", VERSIONS), ("font_snapshot", FONTS),
                            ("prepare_document", ({"blocks": []}, {}, []))):
            mock = patch.object(MODULE, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(MODULE, "compile_pdf", side_effect=self.fake_compile)
        mock.start()
        self.addCleanup(mock.stop)

    def fake_compile(self, document, images, fonts, profile, side, template, workspace):
        self.renders += 1
        path = workspace / "document.pdf"
        path.write_bytes(b"%PDF-1.7\nsynthetic-render-" + str(self.renders).encode())
        return path

    def test_unchanged_build_preserves_pdf_and_record_inode_and_mtime(self):
        output, status, _ = MODULE.convert(self.args)
        self.assertEqual(status, "created")
        record = output.with_suffix(".pdf.build.json")
        before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (output, record)]
        self.assertEqual(MODULE.convert(self.args)[1], "unchanged")
        self.assertEqual(self.renders, 1)
        self.assertEqual(before, [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (output, record)])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_source_change_updates_only_a_recognized_build(self):
        output, _, _ = MODULE.convert(self.args)
        before = output.read_bytes()
        self.source.write_text("A revised paragraph.\n")
        self.assertEqual(MODULE.convert(self.args)[1], "updated")
        self.assertNotEqual(output.read_bytes(), before)

    def test_changed_pdf_is_never_overwritten_even_with_unchanged_inputs(self):
        output, _, _ = MODULE.convert(self.args)
        output.write_bytes(b"%PDF-1.7\nhandwritten annotations")
        for changed_source in (False, True):
            if changed_source:
                self.source.write_text("Revised.\n")
            with self.assertRaisesRegex(MODULE.ConversionError, "annotation"):
                MODULE.convert(self.args)
            self.assertIn(b"handwritten annotations", output.read_bytes())
        self.assertEqual(self.renders, 1)

    def test_unknown_or_invalid_manifest_preserves_pdf(self):
        output = self.source.with_suffix(".boox.pdf")
        output.write_bytes(b"%PDF-private")
        record = output.with_suffix(".pdf.build.json")
        for data in (None, b"bad json", b"[]", b'{}'):
            if data is not None:
                record.write_bytes(data)
            with self.assertRaises(MODULE.ConversionError):
                MODULE.convert(self.args)
            self.assertEqual(output.read_bytes(), b"%PDF-private")

    def test_editions_change_names_and_leave_previous_edition_alone(self):
        self.args.edition = True
        first, _, _ = MODULE.convert(self.args)
        first.write_bytes(b"%PDF-annotated")
        self.source.write_text("A new edition.\n")
        second, _, _ = MODULE.convert(self.args)
        self.assertNotEqual(first, second)
        self.assertRegex(second.name, r"notes\.boox\.[0-9a-f]{12}\.pdf")
        self.assertEqual(first.read_bytes(), b"%PDF-annotated")
        self.assertEqual(MODULE.convert(self.args)[1], "unchanged")

    def test_failed_compilation_preserves_output_and_manifest(self):
        output, _, _ = MODULE.convert(self.args)
        record = output.with_suffix(".pdf.build.json")
        before = (output.read_bytes(), record.read_bytes())
        self.source.write_text("New content.\n")
        with patch.object(MODULE, "compile_pdf", side_effect=MODULE.ConversionError("failed")):
            with self.assertRaisesRegex(MODULE.ConversionError, "failed"):
                MODULE.convert(self.args)
        self.assertEqual((output.read_bytes(), record.read_bytes()), before)
        self.assertFalse(list(self.root.glob(".md-to-pdf-*")))

    def test_external_edit_during_compilation_is_preserved(self):
        output, _, _ = MODULE.convert(self.args)
        self.source.write_text("New content.\n")

        def concurrent_edit(*args):
            result = self.fake_compile(*args)
            output.write_bytes(b"%PDF-concurrent-annotation")
            return result

        with patch.object(MODULE, "compile_pdf", side_effect=concurrent_edit):
            with self.assertRaisesRegex(MODULE.ConversionError, "changed during"):
                MODULE.convert(self.args)
        self.assertEqual(output.read_bytes(), b"%PDF-concurrent-annotation")

    def test_symlink_outputs_records_and_locks_are_rejected(self):
        target = self.root / "keep"
        target.write_bytes(b"keep")
        output = self.source.with_suffix(".boox.pdf")
        for path in (output, output.with_suffix(".pdf.build.json"), output.with_suffix(".pdf.lock")):
            path.unlink(missing_ok=True)
            path.symlink_to(target)
            with self.assertRaises((MODULE.ConversionError, OSError)):
                MODULE.convert(self.args)
            self.assertEqual(target.read_bytes(), b"keep")
            path.unlink()

    def test_output_cannot_be_markdown_or_missing_directory(self):
        for path in (self.source, self.root / "missing" / "notes.pdf"):
            self.args.output = path
            with self.assertRaises(MODULE.ConversionError):
                MODULE.convert(self.args)
        self.assertTrue(self.source.read_text().startswith("# Reading"))

    def test_font_pairing_creates_a_distinct_reading_edition(self):
        self.args.edition = True
        self.assertEqual(self.args.fonts, "tiempos-berkeley")
        first, _, _ = MODULE.convert(self.args)
        self.args.fonts = "source"
        second, _, _ = MODULE.convert(self.args)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(MODULE.convert(self.args)[1], "unchanged")

    def test_profile_side_changes_trigger_a_new_build(self):
        MODULE.convert(self.args)
        self.args.annotation_side = "left"
        self.assertEqual(MODULE.convert(self.args)[1], "updated")


class FingerprintTests(unittest.TestCase):
    def test_every_rendering_dependency_changes_the_build_identity(self):
        values = [b"source", {"images/a.svg": b"image"}, FONTS, PROFILE, "right", TEMPLATE, VERSIONS]
        original = MODULE.fingerprint(*values)
        variants = [b"source changed", {"images/a.svg": b"new image"}, {**FONTS, "other.ttf": b"font"},
                    {**PROFILE, "body-size": 14}, "left", TEMPLATE + b"\n", {**VERSIONS, "typst": "0.16.0"}]
        for index, replacement in enumerate(variants):
            with self.subTest(index=index):
                changed = copy.deepcopy(values)
                changed[index] = replacement
                self.assertNotEqual(original, MODULE.fingerprint(*changed))
        reversed_fonts = dict(reversed(list(FONTS.items())))
        self.assertEqual(original, MODULE.fingerprint(values[0], values[1], reversed_fonts, *values[3:]))


class ResourceTests(TemporaryFiles):
    def test_font_snapshot_requires_each_family_and_hashes_contents(self):
        with self.assertRaisesRegex(MODULE.ConversionError, "font files are missing"):
            MODULE.font_snapshot([self.root])
        for name, data in FONTS.items():
            (self.root / name).write_bytes(data)
        (self.root / "Unrelated.ttf").write_bytes(b"unrelated")
        self.assertEqual(MODULE.font_snapshot([self.root]), FONTS)
        other = self.root / "other"
        other.mkdir()
        (other / next(iter(FONTS))).write_bytes(b"conflict")
        with self.assertRaisesRegex(MODULE.ConversionError, "Conflicting font"):
            MODULE.font_snapshot([self.root, other])

    def test_alternate_font_snapshot_selects_only_the_requested_faces(self):
        pairing = json.loads((SKILL / "assets/fonts-tiempos-berkeley.json").read_bytes())
        files = pairing["font-files"]
        expected = {name: name.encode() for name in files}
        for name, data in expected.items():
            (self.root / name).write_bytes(data)
        (self.root / "Berkeley Mono ExtraCondensed.otf").write_bytes(b"unwanted")
        (self.root / "Berkeley Mono Variable.ttf").write_bytes(b"unwanted")
        self.assertEqual(MODULE.font_snapshot([self.root], (), files), expected)
        source_sans = self.root / "SourceSans3-Regular.ttf"
        with self.assertRaisesRegex(MODULE.ConversionError, "SourceSans3"):
            MODULE.font_snapshot([self.root], ("SourceSans3",), files)
        source_sans.write_bytes(b"diagram-font")
        self.assertEqual(MODULE.font_snapshot([self.root], ("SourceSans3",), files),
                         {**expected, source_sans.name: b"diagram-font"})
        (self.root / files[0]).unlink()
        with self.assertRaisesRegex(MODULE.ConversionError, files[0]):
            MODULE.font_snapshot([self.root], (), files)

    def test_diagram_dimensions_preserve_margins_and_warn_about_small_labels(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 200"><text>Label</text></svg>'
        width, size = MODULE.diagram_size(svg, PROFILE)
        self.assertEqual(width, 148)
        self.assertLess(size, 9)
        tall = svg.replace(b"1600 200", b"200 1600")
        width, _ = MODULE.diagram_size(tall, PROFILE)
        self.assertAlmostEqual(width * 8, 216)

    def test_generated_svg_rejects_external_resources_and_missing_labels(self):
        for content in ('<foreignObject/>', '<script/>', '<image href="https://example.com/a.png"/>',
                        '<use href="file:///private/file"/>', '<style>x {fill: url(https://example.com/fill)}</style>'):
            with self.subTest(content=content):
                data = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">{content}</svg>'.encode()
                with self.assertRaises(MODULE.ConversionError):
                    MODULE.diagram_size(data, PROFILE)
        for dimensions in ("0 0 0 100", "0 0 inf 100", "bad"):
            data = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{dimensions}"/>'.encode()
            with self.assertRaises(MODULE.ConversionError):
                MODULE.diagram_size(data, PROFILE)

    def test_fifo_input_is_rejected_without_blocking(self):
        fifo = self.root / "image.png"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(MODULE.ConversionError, "regular file"):
            MODULE.read_regular(fifo)

    def test_missing_cli_and_engine_diagnostics_are_errors(self):
        with patch.object(MODULE.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(MODULE.ConversionError, "Required command is missing"):
                MODULE.run(["typst", "--version"])
        response = subprocess.CompletedProcess(["typst"], 0, b"", b"warning: missing font")
        with patch.object(MODULE.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(MODULE.ConversionError, "diagnostics"):
                MODULE.run(["typst"])

    def test_typst_environment_cannot_change_fonts_or_root(self):
        response = subprocess.CompletedProcess(["typst"], 0, b"ok", b"")
        with patch.dict(os.environ, {"TYPST_FONT_PATHS": "/other", "TYPST_ROOT": "/", "SOURCE_DATE_EPOCH": "123"}):
            with patch.object(MODULE.subprocess, "run", return_value=response) as mock:
                MODULE.run(["typst"])
        env = mock.call_args.kwargs["env"]
        self.assertFalse(any(key.startswith("TYPST_") for key in env))
        self.assertNotIn("SOURCE_DATE_EPOCH", env)

    def test_compiler_stages_assets_and_uses_isolated_fonts_and_root(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if command[1] == "fonts":
                return b"Source Serif 4\nSource Sans 3\nSource Code Pro\n"
            Path(command[-1]).write_bytes(b"%PDF-1.7\n")
            return b""

        with patch.object(MODULE, "run", side_effect=fake_run), patch.object(MODULE, "render_typst", return_value=b"content"):
            result = MODULE.compile_pdf({}, {"images/a.png": b"image"}, FONTS, PROFILE, "right", TEMPLATE, self.root)
        self.assertTrue(result.read_bytes().startswith(b"%PDF-"))
        self.assertEqual((self.root / "images/a.png").read_bytes(), b"image")
        self.assertIn("--ignore-system-fonts", calls[0])
        self.assertIn("--ignore-system-fonts", calls[1])
        self.assertEqual(calls[1][calls[1].index("--root") + 1], str(self.root))
        self.assertEqual(calls[1][calls[1].index("--creation-timestamp") + 1], "0")


class PandocTests(TemporaryFiles):
    @classmethod
    def setUpClass(cls):
        try:
            MODULE.run(["pandoc", "--version"])
        except MODULE.ConversionError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def prepare(self, text):
        return MODULE.prepare_document(self.source, text.encode())

    def test_standard_markdown_structures_survive(self):
        document, images, warnings = self.prepare(
            "# Heading\n\nA **bold** and *italic* paragraph with `code`, a [link](https://example.com), and a note.[^n]\n\n"
            "- [x] Complete\n- [ ] Pending\n\n> Quoted prose.\n\n"
            "| Name | Value |\n| --- | --- |\n| Alpha | Beta |\n\n"
            "```python\nprint('hello')\n```\n\n[^n]: A footnote.\n")
        kinds = {node["t"] for node in MODULE.walk(document)}
        self.assertTrue({"Header", "Strong", "Emph", "Code", "Link", "Note", "BulletList", "BlockQuote", "Table", "CodeBlock"} <= kinds)
        self.assertEqual(images, {})
        self.assertEqual(warnings, [])
        typst = MODULE.render_typst(document, PROFILE, "right", TEMPLATE, self.root).decode()
        for text in ("width: 204mm", "height: 272mm", "left: 18mm", "right: 38mm", "footnote", "table.header", "☒", "Source Serif 4"):
            self.assertIn(text, typst)
        self.assertNotIn("$body$", typst)

    def test_numbered_headings_match_github_style_fragment_links(self):
        document, _, _ = self.prepare("# 1. Overview\n\n[Overview](#1-overview)\n")
        heading = next(node for node in MODULE.walk(document) if node["t"] == "Header")
        self.assertEqual(heading["c"][1][0], "1-overview")

    def test_mermaid_survives_parsing_without_modifying_source(self):
        source = "flowchart LR\n  A[Read] --> B[Annotate]"
        document, images, warnings = self.prepare(f"```mermaid\n{source}\n```\n")
        self.assertEqual([node["c"][1] for node in MODULE.mermaid_nodes(document)], [source])
        self.assertEqual(images, {})
        self.assertEqual(warnings, [])

    def test_mermaid_cannot_override_rendering_configuration(self):
        for definition in ("%%{init: {securityLevel: 'loose'}}%%\nflowchart LR\n A-->B",
                           "---\nconfig:\n  htmlLabels: true\n---\nflowchart LR\n A-->B",
                           "a" * 50001):
            with self.subTest(definition=definition[:30]):
                with self.assertRaises(MODULE.ConversionError):
                    self.prepare(f"```mermaid\n{definition}\n```\n")

    def test_template_switches_fixed_annotation_side(self):
        document, _, _ = self.prepare("A paragraph.")
        typst = MODULE.render_typst(document, PROFILE, "left", TEMPLATE, self.root).decode()
        self.assertIn("left: 38mm", typst)
        self.assertIn("right: 18mm", typst)
        self.assertIn('line.clusters().join("\\u{200b}")', typst)
        self.assertIn("breakable: true", typst)

    def test_frontmatter_does_not_override_template_or_execute_includes(self):
        document, _, warnings = self.prepare(
            '---\ntitle: A title\nauthor: Example Author\nheader-includes: "#read(\\"private\\")"\n'
            'template: /private/template.typ\nmainfont: Wrong Font\n---\n\nA paragraph.\n')
        self.assertEqual(set(document["meta"]), {"title", "author"})
        self.assertTrue(warnings)
        typst = MODULE.render_typst(document, PROFILE, "right", TEMPLATE, self.root).decode()
        self.assertIn("A title", typst)
        self.assertNotIn("private", typst)
        self.assertNotIn("Wrong Font", typst)

    def test_raw_markup_and_diagrams_fail_without_silent_omission(self):
        for text in ('<details>Hidden text</details>', '<img src="https://example.com/image.png">',
                     '```{=typst}\n#read("private")\n```', '```plantuml\nA -> B\n```',
                     'Text with <br> a break.'):
            with self.subTest(text=text):
                with self.assertRaises(MODULE.ConversionError):
                    self.prepare(text)

    def test_remote_and_unsafe_image_paths_fail_before_fetching(self):
        for target in ("https://example.com/image.png", "//example.com/image.png", "file:///private/image.png",
                       "/private/image.png", "../image.png", "%2e%2e/image.png", "data:image/png;base64,abc"):
            with self.subTest(target=target):
                with self.assertRaises(MODULE.ConversionError):
                    self.prepare(f"![Image]({target})")

    def test_local_images_resolve_from_source_not_cwd_and_reset_sizes(self):
        folder = self.root / "assets"
        folder.mkdir()
        (folder / "a b.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        document, images, warnings = self.prepare('![Caption](assets/a%20b.svg){width=9999px}\n')
        self.assertEqual(len(images), 1)
        node = next(n for n in MODULE.walk(document) if n["t"] == "Image")
        self.assertIn(node["c"][2][0], images)
        self.assertEqual(node["c"][0][2], [])
        self.assertTrue(warnings)

    def test_symlink_image_cannot_escape_source_tree(self):
        with tempfile.TemporaryDirectory() as other:
            target = Path(other) / "image.png"
            target.write_bytes(b"private")
            (self.root / "image.png").symlink_to(target)
            with self.assertRaisesRegex(MODULE.ConversionError, "directory tree"):
                self.prepare("![Image](image.png)")

    def test_wide_table_and_relative_link_warnings(self):
        _, _, warnings = self.prepare(
            "| A | B | C | D | E |\n|---|---|---|---|---|\n|a|b|c|d|e|\n\n[Other](other.md)\n")
        self.assertTrue(any("5-column" in warning for warning in warnings))
        self.assertTrue(any("Relative" in warning for warning in warnings))

    def test_active_link_scheme_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ConversionError, "link scheme"):
            self.prepare("[Click](javascript:alert%281%29)")

    def test_sample_can_be_parsed_and_written_as_typst(self):
        source = SKILL / "examples/reading-sample.md"
        document, images, warnings = MODULE.prepare_document(source, source.read_bytes())
        typst = MODULE.render_typst(document, PROFILE, "right", TEMPLATE, self.root)
        self.assertTrue(images)
        self.assertIn(b"Reading and annotation sample", typst)
        self.assertEqual(warnings, [])


class InstalledRendererTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        try:
            MODULE.tool_versions()
            pairing = json.loads((SKILL / "assets/fonts-tiempos-berkeley.json").read_bytes())
            MODULE.font_snapshot([Path.home() / "Library/Fonts", Path("/Library/Fonts")],
                                 ("SourceSans3",), pairing["font-files"])
        except MODULE.ConversionError as exc:
            self.skipTest(str(exc))

    def extract_text(self, output):
        try:
            MODULE.run(["gs", "--version"])
        except MODULE.ConversionError as exc:
            self.skipTest(str(exc))
        return MODULE.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=txtwrite",
                           "-sOutputFile=-", str(output)]).decode().replace("\u200b", "")

    def test_inline_code_stays_in_the_surrounding_paragraph(self):
        self.source.write_text("Before `token` after.\n")
        args = MODULE.parser().parse_args([str(self.source)])
        output, _, _ = MODULE.convert(args)
        text = self.extract_text(output)
        self.assertIn("Before token after.", [line.strip() for line in text.splitlines()])

    def test_reading_type_scale_is_applied_to_all_text_roles(self):
        self.source.write_text('---\ntitle: Title\nsubtitle: Subtitle\n---\n\n'
                               '# First heading\n\nBody text with `inline code`.\n\n'
                               '## Second heading\n\n### Third heading\n\n'
                               '```text\nBlock code\n```\n\n'
                               '| Column | Value |\n| --- | --- |\n| Item | Data |\n')
        args = MODULE.parser().parse_args([str(self.source)])
        output, _, _ = MODULE.convert(args)
        self.extract_text(output)
        xml = MODULE.run(['gs', '-q', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-sDEVICE=txtwrite',
                          '-dTextFormat=0', '-sOutputFile=-', str(output)])
        spans = list(ET.fromstring(xml).iter('span'))
        for family, expected in (('TiemposText', {10.5, 12, 9.5}),
                                 ('TiemposHeadline', {20, 16, 13, 11.5}),
                                 ('BerkeleyMono', {8.5})):
            sizes = {round(float(span.attrib['size']), 2) for span in spans if family in span.attrib['font']}
            self.assertEqual(sizes, expected, family)

    def test_reading_spacing_separates_paragraphs_and_headings(self):
        self.source.write_text('Alpha first line.  \nAlpha second line.\n\nBeta paragraph.\n\n'
                               '## Sample heading\n\nGamma paragraph.\n')
        args = MODULE.parser().parse_args([str(self.source), '--fonts', 'tiempos-berkeley'])
        output, _, _ = MODULE.convert(args)
        self.extract_text(output)
        xml = MODULE.run(['gs', '-q', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-sDEVICE=txtwrite',
                          '-dTextFormat=0', '-sOutputFile=-', str(output)])
        lines = {}
        for char in ET.fromstring(xml).iter('char'):
            y = int(char.attrib['bbox'].split()[1])
            lines[y] = lines.get(y, '') + char.attrib['c']
        positions = {text: y for y, text in lines.items()}
        leading = positions['Alpha second line.'] - positions['Alpha first line.']
        paragraph = positions['Beta paragraph.'] - positions['Alpha second line.']
        heading = positions['Gamma paragraph.'] - positions['Sample heading']
        body_size = PROFILE['body-size']
        self.assertGreater(leading / body_size, 1.35)
        self.assertLess(leading / body_size, 1.55)
        self.assertGreaterEqual((paragraph - leading) / body_size, 0.25)
        self.assertGreaterEqual((heading - leading) / body_size, 0.2)

    def test_source_pairing_remains_available(self):
        try:
            MODULE.font_snapshot([Path.home() / 'Library/Fonts', Path('/Library/Fonts')])
        except MODULE.ConversionError as exc:
            self.skipTest(str(exc))
        self.source.write_text('# Heading\n\nProse and `code`.\n')
        args = MODULE.parser().parse_args([str(self.source), '--fonts', 'source'])
        output, _, _ = MODULE.convert(args)
        names = re.findall(rb'/BaseFont\s*/([^\s<>\[\]/]+)', output.read_bytes())
        for family in (b'SourceSerif4', b'SourceSans3', b'SourceCodePro'):
            self.assertTrue(any(family in name for name in names), family.decode())
        self.assertFalse(any(b'Tiempos' in name or b'Berkeley' in name for name in names))

    def test_code_and_tables_paginate_without_losing_content(self):
        lines = [f"record_{i:03} = {i}" for i in range(90)]
        rows = [f"| item_{i:03} | Value {i} |" for i in range(100)]
        self.source.write_text("# Code\n\n```python\n" + "\n".join(lines)
                               + "\n```\n\n# Table\n\n| Identifier | Value |\n| --- | --- |\n"
                               + "\n".join(rows) + "\n")
        args = MODULE.parser().parse_args([str(self.source)])
        output, _, warnings = MODULE.convert(args)
        self.assertEqual(warnings, [])
        text = self.extract_text(output)
        for line in lines:
            self.assertIn(line, text)
        for i in range(100):
            self.assertIn(f"item_{i:03}", text)
        self.assertGreater(text.count("Identifier"), 1)

    def require_mermaid(self):
        try:
            return MODULE.mermaid_environment(MODULE.parser().parse_args([str(self.source)]).browser)
        except (MODULE.ConversionError, OSError) as exc:
            self.skipTest(str(exc))

    def test_mermaid_renders_to_pdf_and_unchanged_run_skips_the_browser(self):
        self.require_mermaid()
        self.source.write_text('# 1. Diagrams\n\n[Return](#1-diagrams)\n\n```mermaid\nflowchart LR\n'
                               ' A[Read] --> B[Annotate]\n```\n\n```mermaid\nsequenceDiagram\n'
                               ' participant Reader\n participant Notebook\n'
                               ' Reader->>Notebook: Write a note\n```\n')
        original = self.source.read_bytes()
        args = MODULE.parser().parse_args([str(self.source), '--edition'])
        output, status, warnings = MODULE.convert(args)
        self.assertEqual(status, 'created')
        text = self.extract_text(output)
        for label in ('Read', 'Annotate', 'Reader', 'Notebook', 'Write a note'):
            self.assertIn(label, text)
        before = output.stat()
        with patch.object(MODULE, 'render_diagrams', side_effect=AssertionError('must not render')):
            self.assertEqual(MODULE.convert(args), (output, 'unchanged', warnings))
        self.assertEqual(output.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.source.read_bytes(), original)

    def test_mermaid_network_requests_never_reach_a_server(self):
        self.require_mermaid()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_port
            self.source.write_text('```mermaid\nflowchart LR\n'
                                   f' A@{{ img: "http://127.0.0.1:{port}/private-diagram", label: "Image" }}\n```\n')
            args = MODULE.parser().parse_args([str(self.source)])
            with self.assertRaisesRegex(MODULE.ConversionError, 'blocked request|browser diagnostics'):
                MODULE.convert(args)
            self.assertEqual(requests, [])
            self.assertFalse(self.source.with_suffix('.boox.pdf').exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_mermaid_parse_errors_do_not_expose_the_source(self):
        self.require_mermaid()
        self.source.write_text('```mermaid\nprivate-diagram-marker-invalid-language\n```\n')
        args = MODULE.parser().parse_args([str(self.source)])
        with self.assertRaises(MODULE.ConversionError) as error:
            MODULE.convert(args)
        self.assertNotIn('private-diagram-marker', str(error.exception))
        self.assertIn('Mermaid diagram 1', str(error.exception))

    def test_tiempos_and_berkeley_embed_with_mermaid_and_preserve_layout_settings(self):
        self.require_mermaid()
        pairing = json.loads((SKILL / "assets/fonts-tiempos-berkeley.json").read_bytes())
        try:
            MODULE.font_snapshot([Path.home() / "Library/Fonts", Path("/Library/Fonts")], (), pairing["font-files"])
        except MODULE.ConversionError as exc:
            self.skipTest(str(exc))
        self.source.write_text('# Heading\n\nProse with **bold**, *italic*, and `code`.\n\n'
                               '```text\n--runInBand\nattempt.current !== null\nhttps://example.com\n```\n\n'
                               '```mermaid\nflowchart LR\n A[Read] --> B[Annotate]\n```\n')
        args = MODULE.parser().parse_args([str(self.source), '--edition'])
        with patch.object(MODULE, 'render_typst', wraps=MODULE.render_typst) as render:
            output, _, _ = MODULE.convert(args)
        profile = render.call_args.args[1]
        for key in ('page-width', 'page-height', 'annotation-margin', 'body-size', 'code-size', 'leading'):
            self.assertEqual(profile[key], PROFILE[key])
        names = {name.decode() for name in re.findall(rb'/BaseFont\s*/([^\s<>\[\]/]+)', output.read_bytes())}
        for family in ('TiemposText', 'TiemposHeadline', 'BerkeleyMono', 'SourceSans3'):
            self.assertTrue(any(family in name for name in names), f'{family} missing from {sorted(names)}')
        self.assertFalse(any('SourceSerif4' in name or 'SourceCodePro' in name for name in names))
        text = self.extract_text(output)
        for code in ('--runInBand', 'attempt.current !== null', 'https://example.com'):
            self.assertIn(code, text)
        self.assertEqual(MODULE.convert(args)[1], 'unchanged')

    def test_sample_renders_with_installed_typst_and_fonts(self):
        args = MODULE.parser().parse_args([str(SKILL / "examples/reading-sample.md"),
                                          "-o", str(self.root / "sample.pdf"), "--edition"])
        output, status, warnings = MODULE.convert(args)
        self.assertEqual(status, "created")
        self.assertTrue(output.read_bytes().startswith(b"%PDF-"))
        self.assertEqual(warnings, [])
        self.assertEqual(MODULE.convert(args)[1], "unchanged")


if __name__ == "__main__":
    unittest.main()
