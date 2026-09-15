---
title: Reading and annotation sample
subtitle: BOOX Note Max
---

## Reading a technical proposal

A useful proposal makes its assumptions visible. The reader should be able to distinguish a requirement from a preference, find the evidence behind a claim, and write a question beside the paragraph that prompted it. This sample leaves a fixed annotation margin on the right of every page.

The body uses a serif font, while headings and code use separate families. **Bold text should remain distinct**, and *italic text should remain legible* without zooming. Underline a sentence here and write a short note in the adjacent margin. The line spacing should keep your pen marks clear of the next line.

Paragraphs have a ragged right edge. This avoids stretching spaces to fill each line. There is no background tint behind prose or code, and links remain black. The [Pandoc manual](https://pandoc.org/MANUAL.html) is an example of an external link.[^links]

### A decision worth annotating

The converter reads the source document without asking an agent to rewrite it. A template determines the page layout. If the inputs have not changed, the converter leaves the existing PDF untouched. A reading edition gets a content-derived filename so that a later revision does not replace the copy containing handwritten notes.

> An annotation belongs to a particular arrangement of text on a page. Changing that arrangement can detach a note from the sentence it refers to.

The build record also stores a hash of the PDF. If another application changes the file, the converter refuses to overwrite it. This protects an annotated copy even when someone accidentally uses the ordinary build command instead of creating an edition.

## Code and structured information

The following block deliberately includes a long line. It should wrap inside the text column, without a hyphen that could be mistaken for part of the code.

```python
from pathlib import Path

source = Path("notes/design.md")
reading_profile = {"device": "boox-note-max", "annotation_side": "right", "keep_existing_annotations": True, "output_directory": "reading-editions"}

for document in sorted(source.parent.glob("*.md")):
    print(document.name)
```

Inline code such as `document.with_suffix(".pdf")` should fit naturally into a paragraph. The next identifier tests wrapping of a long token without spaces: `a_deliberately_long_identifier_that_should_wrap_without_extending_into_the_handwritten_annotation_margin`.

### A compact table

| Input | Treatment | Reason |
| :--- | :--- | :--- |
| Prose | Serif text | Comfortable continuous reading. |
| Code | Monospaced text | Visible indentation and punctuation. |
| Images | Fit the column | Preserve the annotation margin. |
| Tables | Keep readable text | Avoid fitting by shrinking everything. |

### A short checklist

- [x] Preserve headings and emphasis.
- [x] Resolve a local image relative to the Markdown file.
- [ ] Confirm that the annotation margin suits your handwriting.

1. Read a paragraph at whole-page size.
2. Underline one sentence.
3. Write a short marginal note beside it.

## An image and its caption

![A local vector illustration of three connected stages.](pipeline.svg)

The illustration should remain inside the text column. A diagram exported as SVG keeps its lines sharp when zoomed. Embedded screenshots retain their original colors; the converter does not recolor source images or improve text that was already too small in the screenshot.

## Keeping a reading edition

A working PDF can change whenever its source changes. A reading edition should stay fixed while you annotate it. Copy the fingerprinted edition to the tablet, then keep that filename when exporting the annotated copy. The conversion workflow does not transfer files or synchronize annotations.

The final page should have the same right-hand writing space as the first. Page numbers belong below the text rather than inside the annotation margin. Footnotes should remain selectable text, not part of a page-sized image.

[^links]: PDF links can remain clickable without using colored text. Opening an external link on the tablet may still require network access.
