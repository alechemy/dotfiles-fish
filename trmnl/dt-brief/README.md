# DEVONthink Daily Brief — TRMNL plugin

Source for the TRMNL private plugin that mirrors the morning brief onto the
e-ink dashboard. The data producer is `dt-morning-brief.py` (snapshot) +
`trmnl-push-brief.py` (webhook POST); see `devonthink/docs/trmnl-brief.md`
for the full pipeline.

## Layout sources

- `src/full.liquid` — 800×480: meetings (left ⅔) + birthdays / reconnect /
  on-this-day / status lines (right ⅓)
- `src/half_vertical.liquid` — 400×480: stacked meetings, birthdays, reconnect
- `src/half_horizontal.liquid` — 800×240: meetings / birthdays / reconnect side by side
- `src/quadrant.liquid` — 400×240: next meetings only
- `src/shared.liquid` — captures the title_bar sunrise SVG (`svg_logo`), prepended to every size
- `src/settings.yml` — plugin settings (webhook strategy)

Framework gotchas baked into these templates (learned against v3.1.2):
`.layout` is a **centering flex row** — always add direction/alignment
(`layout--col`, `layout--top`); `.col--span-N` cells are `display:flex`
rows, so each cell's content lives in a single `stretch-x` wrapper div;
column layouts need a `w--full` wrapper or `align-items: center` shrinks
items; `data-group-header` produces side-labels for the overflow engine,
not section headings; `.item` carries no margin of its own — the inter-item
gap comes from a `.list` parent (`--list-gap-small`, 8px), so a bare run of
`.item`s stacks flush. `full.liquid`'s right column deliberately has no
`.list`: three sections plus the status lines already fill the height, and
8px gaps clip the last line at max data.

## Local preview

`bin/preview [outdir]` renders every size against the sample variables and
screenshots them with headless Chromium (needs `gem install --user-install
liquid -v 4.0.4`; system Ruby 2.6 is fine). Layout-accurate; the framework's
clamp/overflow JS may not settle, so confirm text truncation server-side.

The official tool, [trmnlp](https://github.com/usetrmnl/trmnlp), needs Ruby ≥ 3.4 or Docker:

```bash
cd trmnl/dt-brief
trmnlp serve          # http://localhost:4567, hot-reloads on save
# or, without Ruby:
docker run --pull always -p 4567:4567 -v "$PWD:/plugin" trmnl/trmnlp serve --bind 0.0.0.0
```

Sample merge variables live in `.trmnlp.yml` and are fictional — keep them
that way (repo rule: no real names from the People roster or Contacts).

## Deploying markup

Either paste each `src/*.liquid` into the plugin's markup editor on
trmnl.com, or `trmnlp login && trmnlp push` from this directory, or let an
agent session do it through TRMNL's MCP server (`MarkupsWriteTool` +
`MarkupsScreenshotTool`).
