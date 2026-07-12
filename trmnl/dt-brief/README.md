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
- `src/settings.yml` — plugin settings (webhook strategy)

## Local preview

Needs the [trmnlp](https://github.com/usetrmnl/trmnlp) gem (Ruby ≥ 3.4) or Docker:

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
