# Markdown PDFs for the BOOX Note Max

The converter uses Pandoc to parse Markdown and write Typst, then runs the Typst CLI to produce a PDF. Python owns dependency snapshots, build identity, and output protection. No model participates in conversion.

## Setup

The dotfiles Brewfile declares Pandoc, Typst, and the open-source font alternative. The default Tiempos/Berkeley pairing also requires separately licensed fonts installed locally. To install the declared dependencies explicitly on a machine that needs them:

```bash
brew install pandoc typst
brew install --cask font-source-serif-4 font-source-sans-3 font-source-code-pro
```

Mermaid support uses the Mise-managed `@mermaid-js/mermaid-cli` package, pinned to `11.17.0` in the dotfiles Mise configuration. Install it explicitly when needed:

```bash
PUPPETEER_SKIP_DOWNLOAD=true mise install npm:@mermaid-js/mermaid-cli@11.17.0
```

The renderer uses the package's documented Node API and its Puppeteer dependency. It uses the existing Chromium app at `/Applications/Chromium.app/Contents/MacOS/Chromium`; `--browser /path/to/chromium` selects another installation. It never uses your normal browser profile. Node and Mise are needed only when a document contains Mermaid diagrams. The converter never installs dependencies itself.

The supported minimum versions are Python 3.9, Pandoc 3.11, and Typst 0.15. The default Tiempos/Berkeley pairing requires the locally installed commercial fonts listed in `assets/fonts-tiempos-berkeley.json`. The Source alternative uses upstream font filenames beginning with `SourceSerif4`, `SourceSans3`, and `SourceCodePro`. The repository stores filenames and settings, never those font binaries. The converter searches `~/Library/Fonts` and `/Library/Fonts`. Use repeatable `--font-dir` arguments to replace these locations on another Unix system.

After adding this skill and its command to a dotfiles checkout, link them with Stow:

```bash
cd ~/.dotfiles/stow
stow --restow --no-folding --ignore='.DS_Store' --ignore='__pycache__' --target="$HOME" agents bin claude
```

Pi discovers the shared skill under `~/.agents/skills/md-to-pdf`. Reload skills or start a new session if it was added after startup. Claude uses the compatibility link to the same directory.

## Usage

Create a reading edition:

```bash
md-to-pdf notes/design.md --edition
```

This produces a name such as `design.boox.7c82a1f294bc.pdf`. To choose a destination or move the writing margin:

```bash
md-to-pdf notes/design.md --edition \
  --annotation-side left -o "$HOME/Downloads/design.pdf"
```

The destination directory must already exist. Without `--edition`, the default output is `design.boox.pdf`. This is a working build that can be updated when its source changes.

The default pairing uses Tiempos Text for prose, Tiempos Headline for headings and titles, and Berkeley Mono for code. Berkeley Mono uses the regular-width static faces, excluding condensed and variable variants. Mermaid labels remain in Source Sans 3, which the default pairing only requires for documents with diagrams.

To use the original Source pairing instead:

```bash
md-to-pdf notes/design.md --edition --fonts source
```

Both pairings use the same page dimensions, font sizes, spacing settings, and annotation margin. Use `--fonts tiempos-berkeley` to select the default explicitly.

Each PDF has two adjacent files:

- The `.pdf.build.json` record stores the build fingerprint, original PDF hash, and conversion warnings. Keep it beside working builds so the converter can recognize them. It contains no document text or absolute source paths.
- The `.pdf.lock` file serializes converter processes targeting that output. Its presence does not mean a conversion is still running. Leave it in place; deleting an active lock can allow overlapping writers.

Transfer only the reading PDF to the BOOX. This workflow does not choose a transfer service or synchronize annotations.

## Layout

The layout profile lives in `assets/boox-note-max.json`, and the Pandoc template lives in `assets/reader.typst`, both relative to the skill directory. The profile contains the Source font roles; the default pairing replaces those roles with `assets/fonts-tiempos-berkeley.json`.

| Setting | Default |
| :--- | :--- |
| Page size | 204 × 272 mm. |
| Prose | Tiempos Text, 10.5 pt. |
| Headings | Tiempos Headline, 16 / 13 / 11.5 pt for levels 1 / 2 / 3. |
| Title and subtitle | 20 pt and 12 pt. |
| Code | Berkeley Mono, 8.5 pt. |
| Tables | 9.5 pt, without automatic shrinking. |
| Prose leading | 0.72 em of additional interline space. |
| Paragraph spacing | 1.1 em between line edges. |
| Spacing beneath headings | 0.8 em. |
| Annotation margin | 38 mm on the right, configurable to the left. |
| Opposite margin | 18 mm. |
| Top and bottom | 16 mm and 20 mm. |

Font sizes for prose, code, tables, headings, and title metadata are all controlled by the profile. When comparing a reference PDF, measure its font sizes and page dimensions together. Whole-page display scales different page sizes differently, and nominal point sizes do not account for differences in font x-height.

The template uses black text on white, page numbers, and native PDF heading navigation. Images retain their original colors. Code wraps using invisible break opportunities between characters, without adding visible hyphens. Standard ligatures and contextual alternates are disabled in code so operators remain separate characters. These break opportunities may appear in copied PDF text, so use the Markdown source when copying executable code. Tabs in fenced code expand to four-column tab stops.

Use whole-page display on the BOOX and disable automatic margin cropping. Otherwise, the reader can remove the space intended for handwriting.

## Supported input and limits

The input dialect is Pandoc Markdown with GitHub-style heading identifiers, task lists, bare URL detection, and lists immediately following prose. Numbered headings retain their numbers in fragment identifiers so GitHub-style tables of contents resolve. It includes headings, emphasis, links, block quotes, fenced code, footnotes, tables, and dollar-delimited mathematics. Citation processing is disabled, so citation-like text remains literal.

Title, subtitle, author, and date frontmatter are rendered. Other frontmatter is ignored with a warning and cannot select templates, load includes, or change the reading profile. Display frontmatter accepts text and lists of text rather than nested author mappings. The profile assumes English.

Local PNG, JPEG, SVG, and WebP images resolve relative to the Markdown document. They must remain within that directory tree, including after resolving symlinks. Percent-encoded filenames are supported. Image dimensions and custom attributes are reset to fit the text column, with a warning when attributes were present.

Fenced Mermaid definitions render as vector SVG images. The renderer uses a temporary Chromium profile with offline mode, request interception, and a restrictive content security policy. Only the installed renderer's local resources and embedded font data are permitted. Network-dependent diagrams fail, and errors do not echo diagram source. The staged Source Sans 3 font supplies browser label measurements and PDF text. HTML labels, Mermaid configuration directives, frontmatter, and definitions longer than 50,000 characters are rejected. The reading profile owns rendering settings in `assets/mermaid.json`.

Identical Mermaid definitions share one SVG. Diagrams fit inside the text column and page height without enlarging small originals. Dense diagrams produce estimated small-label warnings instead of consuming the annotation margin. The estimate assumes a 16 px label; custom styling can differ. Some sequence diagrams still require zooming on the tablet.

Remote images, absolute image paths, raw HTML, raw TeX/Typst, and Graphviz/PlantUML code fail rather than being fetched, executed, or silently dropped. There is no network-fetch option. Download an image explicitly or export an unsupported diagram before referencing it locally. Custom Div/Span styling produces a warning; its text remains. Relative document links may not work after transfer and also produce a warning.

Tables with more than four columns or unusually long tokens produce a layout warning. This is a heuristic, not a geometric overflow detector. Dense technical tables and unusual glyphs still need inspection. The converter does not claim to reproduce arbitrary HTML/CSS styling or make tiny text inside screenshots readable.

## Build identity and annotation safety

The fingerprint covers source bytes, referenced image bytes, selected font bytes, profile settings, annotation side, the template, converter source, Markdown dialect, and tool version strings. For documents with Mermaid, the fingerprint also covers the renderer helper and configuration, Mermaid CLI and dependency versions, the Mermaid bundle, Node, and Chromium versions. A cache hit does not launch Chromium. The converter snapshots assets and fonts before rendering. Typst sees only the selected font files and its own embedded fallback fonts; unrelated system font changes do not affect layout.

Unchanged inputs preserve the PDF and build record without changing their inode or modification time. Typst receives a fixed creation timestamp, and the template omits a document date unless supplied as display frontmatter. Byte-identical rebuilds across platforms or tool upgrades are not guaranteed.

A PDF whose hash differs from its build record is protected, even when the Markdown has not changed. Choose a new output path rather than deleting the record or overwriting annotations. Fingerprinted editions receive new filenames when any rendering dependency changes. Returning to an already annotated edition also triggers protection instead of rewriting it.

Each conversion uses a private temporary directory next to the output, then publishes the completed PDF. A failed render leaves the prior PDF and build record untouched. Converter processes share a per-output advisory lock. An external edit detected before publication aborts the replacement, but unrelated annotation applications do not participate in this lock. Do not annotate a replaceable working PDF while it is rebuilding; use editions.

The PDF and build record are separate atomic file updates. An interruption between them can leave a PDF without a matching record. The next run refuses replacement rather than guessing that it is safe. Use a new output path in that case. Symlink and non-regular outputs are rejected.

## Sample and verification

The bundled `examples/reading-sample.md` contains prose, emphasis, links, a footnote, code with a long line and identifier, a compact table, a checklist, and a local SVG. `examples/mermaid-sample.md` adds a flowchart and a sequence diagram.

```bash
md-to-pdf "$HOME/.agents/skills/md-to-pdf/examples/reading-sample.md" \
  --edition -o "$HOME/Downloads/boox-layout-sample.pdf"
```

For the first device check, read one page at whole-page size, underline a sentence, and write a short marginal note. Adjust the profile only if the font or gutter needs it. That is the bounded device check; repeated converter behavior belongs in automated tests.

From the dotfiles root:

```bash
python3 -m unittest discover -s scripts/tests -p test_md_to_pdf.py
```

Tests cover Pandoc parsing and template expansion, dependency fingerprints, safe publication, unchanged outputs, annotation protection, path restrictions, and isolated compiler arguments. The PDF-rendering tests require Typst and the relevant installed fonts. The default-pairing test verifies embedded font families and literal code operators, including SVG labels that could otherwise fall back to the prose font. A separate render checks the Source alternative. The spacing regression measures line positions in a rendered PDF to distinguish paragraph and heading gaps from ordinary line spacing. Mermaid tests also exercise the installed renderer, unchanged-build behavior, private error handling, and network blocking against a local test server. The inline-code layout regression also uses Ghostscript to extract rendered text and check that code stays in the surrounding paragraph. These tests skip explicitly when their prerequisites are absent. A skipped render test does not verify typography or pagination.

## Upstream references

- [Pandoc manual](https://pandoc.org/MANUAL.html) documents Markdown, the Typst writer, and templates.
- [Typst text](https://typst.app/docs/reference/text/text/) documents font selection and embedded fallback fonts.
- [Typst raw text](https://typst.app/docs/reference/text/raw/) documents code content and styling.
- [Typst CLI arguments](https://github.com/typst/typst/blob/v0.15.1/crates/typst-cli/src/args.rs) document isolated font paths, project roots, and creation timestamps.
- [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli) documents the Node rendering API and existing-Chromium configuration.
