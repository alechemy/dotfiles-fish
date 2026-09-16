---
name: md-to-pdf
description: Convert Markdown documents into readable PDFs for the BOOX Note Max, with serif prose and a fixed margin for handwritten notes. Use when preparing agent-generated documents for e-reader reading, underlining, or annotation.
compatibility: Requires Python 3.9+, Pandoc 3.11+, Typst 0.15+, and locally installed fonts for the selected pairing. Mermaid diagrams also require Source Sans 3, Mise-managed Mermaid CLI, Node.js, and Chromium. Conversion runs locally on macOS or another Unix system.
---

# Markdown to reading PDF

Use the bundled `md_to_pdf.py` converter. Do not rewrite, summarize, or reformat the source document to perform the conversion.

## Convert a document

1. Identify the requested Markdown file. Ask only if the source is ambiguous.
2. Default to a fingerprinted reading edition so later revisions cannot replace a PDF containing annotations.
3. Run the command below, resolving the helper path relative to this skill directory. Quote source and output paths.
4. Return the resulting PDF path and any warnings. A conversion error is not a successful export.

```bash
python3 ~/.agents/skills/md-to-pdf/md_to_pdf.py "/path/to/document.md" --edition
```

The installed shell command is equivalent:

```bash
md-to-pdf "/path/to/document.md" --edition
```

The default profile is `boox-note-max`. It uses a 204 × 272 mm page, 10.5 pt Tiempos Text prose, and a 38 mm right margin. Use `--annotation-side left` only when requested. The margin stays on the same side on every page. PDFs omit page numbers so footer text does not interfere with BOOX automatic cropping.

The default pairing is `tiempos-berkeley`: Tiempos Text for prose, Tiempos Headline for titles and headings, and regular-width Berkeley Mono for code. The fonts must already be installed locally. Diagram labels stay in Source Sans 3. Use `--fonts source` for the original Source pairing. Font changes create distinct fingerprinted editions.

Code uses 8.5 pt Berkeley Mono. Prose uses 0.72 em leading and 1.1 em paragraph spacing. Headings have 1.8 em above and 1 em below, relative to the heading size. Code blocks have 1.8 prose em of space on both sides vertically, and block quotes have 2.4 em. Keep the annotation margin and font sizes unchanged unless requested.

Use `-o "/existing/directory/reading.pdf"` for a requested destination. With `--edition`, the converter adds the build fingerprint before `.pdf`. Without `--edition`, the output is a replaceable working build, but the converter still refuses to overwrite an untracked or externally modified PDF.

## Constraints

- Never delete a PDF or its build record to bypass overwrite protection. Use a new output path if an existing PDF has annotations or unknown provenance.
- Fenced Mermaid diagrams render locally to SVG. Browser page networking is blocked, HTML labels are disabled, and diagram configuration overrides are rejected. Dense diagrams can produce small-label warnings; include those when returning the PDF.
- Raw HTML, raw TeX/Typst, other diagram languages, and remote images fail with an explanation. Do not remove them to make conversion succeed. Discuss a native Markdown or local-image replacement if needed.
- Conversion does not fetch remote images, execute general-purpose code blocks, transfer files, or synchronize annotations. Get explicit approval before any remote write.
- Report missing dependencies. Do not install packages or substitute another renderer automatically.
- Table warnings identify layout risks, not confirmed overflow. Keep them in the handoff to the user.

Read [the workflow reference](references/workflow.md) for setup, output files, supported Markdown, and the sample document.
