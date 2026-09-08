# Markdown rendering validation

This checklist supplies the fixtures and evidence for [DT44-03](devonthink-4.4-plan.md#dt44-03-validate-markdown-rendering-and-custom-themes). Fixture preparation does not establish that native rendering, editing, or DTTG compatibility passes.

Status: The user deferred this GUI exercise. Keep the current styling and prose workaround. This checklist is a reference for a future rendering problem or style replacement, not a current setup task. The preprocessing fix is complete; visual compatibility remains unverified.

## Fixtures and scope

Use [`markdown-rendering.md`](../tests/fixtures/markdown-rendering.md) and its local [`SVG asset`](../tests/fixtures/markdown-rendering.svg). The note contains fictional timeline entries, tasks, entity-style fields, code, tables, math, links, and extension probes. There are no remote images, executable scripts, or live item links. The `example.invalid` URLs deliberately cannot resolve. Do not activate the unresolved WikiLink during the baseline.

Keep the tracked fixture unchanged. Work on a disposable copy, with the SVG beside it. Do not run Markdown formatting or auto-fixing against the tracked fixture; its raw tilde syntax, long lines, and extension probes are intentional inputs.

The fixture includes an Action Items section and machine-style timeline bullets. Importing it through the production pipeline could create Things tasks or daily-note entries. Before any GUI test, agree on a disposable destination that has no production smart-rule scope, sync, AI processing, or watcher activity. Do not use Lorebook, Global Inbox, the database inbox, or production note templates that route into those locations. A separate offline account or VM without pipeline setup avoids application-wide setting changes in the production session.

Creating the test database, importing fixtures, changing app settings, and editing records require the agreed GUI scope. No such action is part of fixture preparation. A browser or Marked preview can provide additional evidence, but neither substitutes for the actual DEVONthink renderer.

## Capture the baseline

Record only these non-secret details:

- Record the tested DEVONthink and macOS versions, display scaling, pane width, and editing/display mode.
- Record system appearance and whether the application follows it or has an override.
- Record the Markdown Color Theme, selected stylesheet, selected JavaScript, and syntax-highlighting setting.
- Record any Markdown smart quotes/dashes setting and relevant image-import behavior.
- Record the exact fixture revision or checksum and any explicitly approved setup changes.

Retain the prior settings so the test can restore them. Do not copy live preferences wholesale. If the fixture's relative SVG is unresolved, fix asset placement only within the approved test scope and record that separately from a rendering defect. Name the disposable record `DT44 rendering fixture` to match its H1.

## Comparison matrix

Run each desktop configuration in Preview and Side-by-Side, first with a wide pane and then a narrow pane. Target preview widths above and below the CSS's 640px breakpoint, and record actual widths when available. A narrow desktop pane is not a touch-device test; the mobile wrapping rule also requires a coarse pointer.

| Configuration | System appearance | Expected comparison | Result |
| --- | --- | --- | --- |
| Native styling, Automatic color theme, no custom JS. | Light. | Establish the native light baseline. | Pending. |
| Native styling, Automatic color theme, no custom JS. | Dark. | Establish the native dark baseline. | Pending. |
| Custom CSS, Automatic color theme, no custom JS. | Light. | Isolate the stylesheet's effect. | Pending. |
| Custom CSS, Automatic color theme, no custom JS. | Dark. | Check system-driven dark colors. | Pending. |
| Custom CSS and theme-toggle JS, Automatic color theme. | Light. | Toggle dark and back; check source/preview agreement. | Pending. |
| Custom CSS and theme-toggle JS, Automatic color theme. | Dark. | Toggle light and back; check source/preview agreement. | Pending. |
| One explicitly selected built-in color theme, native styling, no custom JS. | Record the appearance. | Establish the selected theme's native behavior. | Pending. |
| The same explicit color theme, custom CSS and JS. | Use the same appearance. | Detect conflicts between native theme and manual toggle. | Pending. |

The custom files are `stow/devonthink/Library/Application Support/DEVONthink/StyleSheets/Readable-Universal.css` and `theme-toggle.js`. Check a configuration without custom JS before attributing a palette difference to CSS.

## What to inspect

| Fixture section | Required observation |
| --- | --- |
| Prose and inline formatting | Check readability, emphasis, superscripts, subscript, emoji/shortcode rendering, selection contrast, and whether changing themes preserves the source. |
| Timeline and nested lists | Check that manual sub-lines stay under their meeting and second-level indentation remains distinct. Functional emoji must not disappear or become duplicate prefixes. |
| Action Items | Check checked/unchecked states, nested tasks, and continuation text alignment. A continuation belongs to its task rather than under the checkbox. |
| Tables | Check column alignment, escaped pipes, header contrast, row separation, and access to the final column in a narrow pane. |
| Callouts and quotations | Check all five callout types and the ordinary quotation control. Inspect source title styling with syntax highlighting enabled. |
| Code and long lines | Record whether lines wrap, scroll, or clip under each configuration. Copy a visually wrapped line and compare it with the single source line. A visual wrap must not insert stored newlines. |
| Tilde controls | Compare raw prose, escaped prose, inline code, backtick fences, and tilde fences. Record literal characters and syntax boundaries, not only appearance. |
| Math | Check inline and display equations, the matrix, and source preservation after opening an equation in WYSIWYG. |
| Links and footnotes | Check section navigation, reference-style link text, footnote reuse/backlinks, and preservation of the unresolved WikiLink. Do not navigate external URLs or create a WikiLink target. |
| Images | Check the local SVG's aspect ratio, borders, legibility, and fit within the pane. Separate asset resolution from image styling. |
| Extended syntax probes | Record support or literal fallback for definition lists, abbreviations, citations, and CriticMarkup. Compare their semantic HTML controls to distinguish parser support from CSS styling. Literal fallback alone is not a regression against a promised feature. |
| Entity-style fields | Check whether adjacent template fields remain readable and dated facts preserve their hierarchy. |
| Final source sentinel | Confirm the final sentence and reference definitions survive every saved edit and raw Markdown export. |

## Editing and source round-trips

Start each editing probe from a fresh disposable copy. Save/export raw Markdown before and after, then compare the source files. Do not use HTML, PDF, or LaTeX export for source comparisons.

1. Switch between source, preview, and Side-by-Side without editing. Compare the source afterward. Record any change rather than treating it as expected formatting.
2. In a live WYSIWYG table, change `Fixture alpha`'s quantity from `12` to `13`. Check that only the intended cell's value changes; record any table-format normalization separately.
3. Toggle only the first unchecked task. Confirm its continuation, nested task, and neighboring checked task retain their text, nesting, and independent states.
4. Edit the display equation's right-hand side from `14` to `15`, save, and reopen. Check its delimiters, the unchanged matrix, and surrounding prose.
5. Edit one manual sub-line under the fictional meeting, then save twice. The second save must not duplicate or reparent any bullet, add headings, or alter the final sentinel.
6. While scrolled near the bottom in Side-by-Side, save a small source edit. Record flicker, scroll jumps, and caret movement. Select nonadjacent ranges in WYSIWYG and check copied content and order.

Reopening a saved note must preserve the intended edits. Harmless serialization changes should be documented and checked against pipeline parsing expectations, not hidden by a whitespace-insensitive diff. Do not run task extraction or entity filing to validate this fixture.

## Isolate preprocessing from renderer behavior

The `lint-markdown-file` helper converts tabs and runs `markdownlint --fix` with the stowed `~/.config/dt-pipeline/markdownlint-rules.cjs` rule. The parser-backed rule escapes single prose tildes while preserving code, frontmatter, HTML blocks, and literal link destinations/reference identifiers. The smart rule calls the same helper. Render the raw fixture first; a preprocessed note cannot establish how 4.4 handles raw syntax.

For a separate offline comparison, run the following from the repository root. It prepares copies outside watched folders and modifies only the temporary copy:

```bash
fixture_dir=$(mktemp -d /tmp/dt44-markdown.XXXXXX)
cp devonthink/tests/fixtures/markdown-rendering.md "$fixture_dir/raw.md"
cp devonthink/tests/fixtures/markdown-rendering.svg "$fixture_dir/markdown-rendering.svg"
cp "$fixture_dir/raw.md" "$fixture_dir/processed.md"
PIPELINE_MANUAL=1 /bin/bash stow/devonthink/.local/bin/lint-markdown-file "$fixture_dir/processed.md"
diff -u "$fixture_dir/raw.md" "$fixture_dir/processed.md"
```

The helper tolerates unfixable lint violations but returns nonzero for missing rules or formatter execution errors. Stop the comparison if it fails. A nonzero `diff` status normally means the files differ. Retain both copies until the approved comparison is finished, then remove only that temporary fixture directory.

Confirm literal code paths and tilde fence delimiters remain intact. Inspect CriticMarkup substitutions separately; they still pass through prose escaping because the linter does not recognize that extension. Record preprocessing differences separately from the native renderer's raw-tilde behavior. Removing prose escaping requires the native comparison to establish the required policy.

## DTTG and shared-style decisions

DTTG remains a separate check on an explicitly approved fixture-only device/database arrangement. Do not enable production sync merely to distribute the fixture. Record its version, touch-device width/orientation, stylesheet/JS selection, code wrapping, table scrolling, and toggle behavior. If that environment is unavailable, retain shared CSS/JS and leave DTTG validation pending.

Native themes might remove the need for the desktop toggle, but that does not establish that the shared file is unnecessary. Record one of three decisions for each proposed change: retain the current behavior, make a tested correction, or defer with a reason. Restore the previous settings after testing unless a change is explicitly adopted.

## Evidence record

Fixture preparation and the preprocessing correction are complete. Native rendering, GUI editing, and DTTG checks are deferred by user decision.

Before the correction, an offline preprocessing check with `markdownlint` 0.49.1 and the tracked helper produced no diagnostics but changed literal inline code from `` `~/fixture/input.md` `` to `` `\~/fixture/input.md` ``. It also changed the tilde fence opener from `~~~text` to `~~\~text`, so the source no longer contains the original fence delimiter. This is preprocessing evidence, not a native-renderer observation. The tracked fixture remained unchanged. Its SHA-256 for this check was `76fc44618570b491eb225549c597980d50a236e12ffeb0513ed9e6dd2bfe3b76`.

The parser-backed correction preserves those code paths and fence delimiters. The 11 focused tests in `devonthink/tests/test_lint_markdown_file.py` also cover prose escaping, references, HTML blocks, frontmatter, repeat runs, line endings, and helper failures. The raw fixture remains unchanged. These results close the preprocessing defect, not the native rendering or editing checks.

For each observed issue, record the configuration, fixture section, exact reproduction, expected/actual behavior, raw-versus-processed input, source diff, and proposed disposition. Screenshots must contain only fixture content and no unrelated windows or sidebar records. Link the accepted findings from DT44-03 before changing shared styling.
