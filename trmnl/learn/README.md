# Learning — TRMNL plugin

Ambient learning on the e-ink dashboard: one fact, code snippet, or gotcha about
a project's stack per rotation. The first corpus covers a React Native app's stack
(Expo SDK 57 / React Native 0.86 / NativeWind v5 / TanStack Query v5 / Valibot /
FlashList v2 / Reanimated v4 …), but the plugin is project-agnostic — a corpus is
data, not code. See [§Another project](#another-project).

Facts are pinned to the versions actually installed in the repo. A fact that is
true of FlashList v1 and false of v2 is worse than no fact at all, so every one
was sourced against the installed package's own types/changelog and then checked
by an independent pass that tried to refute it.

## How it works

```
dist/corpus.json  (static, hosted)          every fact, pre-interleaved
   └─ TRMNL polls it just before the plugin is displayed
        └─ src/transform.js   picks ONE fact from the clock
             └─ src/*.liquid  renders it
```

### Why polling + a transform, and not a webhook or plain Liquid

Three platform facts drive the whole design:

1. **TRMNL skips screen generation when the merge variables are unchanged.**
   So rotating *inside* Liquid against a static corpus — `{{ facts | sample }}`,
   or an index derived from `{{ 'now' }}` — does not work. The merge variables
   never change, no screen is generated, and the device keeps showing the old
   PNG forever. The rotation has to change the *payload*, which is what the
   transform does: it emits one fact, not the corpus.

2. **Polling is just-in-time.** Since May 2026 TRMNL refreshes a plugin
   immediately before it is shown, so the poll lands roughly *once per actual
   display*. That is as close to a "you are about to be on screen" signal as the
   platform offers, and it is why facts don't get burned unseen.

3. **Webhooks are capped at 12 pushes/hour and 2 kB**, and would need this Mac
   awake and pushing. Polling a static file needs neither.

### Rotation

`transform.js` derives the index from the clock rather than from stored state
(transforms are stateless — there is no counter, no render id, nothing durable):

```
tick   = floor(now / rotationMinutes)
cycle  = floor(tick / n)
index  = (tick mod n + mix32(cycle) mod n) mod n
```

`dist/corpus.json` is **pre-interleaved at build time** — round-robined across
topics so consecutive facts come from different topics. Walking it in order shows
every fact once before repeating any, which is the no-duplicates property, with
nothing to remember.

The hashed per-cycle rotation is not decoration. The device samples only every
`s`-th tick, where `s` is the display interval measured in rotation periods. Any
*linear* advance makes the sampled indices an arithmetic progression, which
collapses onto a fraction of the corpus whenever `s` and `n` share structure —
simulated at n=261, a plain `+1`-per-cycle offset reaches **25%** of the facts at
s=10 and never the rest. Rotating by `mix32(cycle)` breaks the resonance: 100%
of facts reachable at every stride tried, across four corpus sizes. It stays a
bijection, so a cycle still shows each fact exactly once.

The one cost: because the offset jumps at a cycle boundary, a fact can repeat
near the wrap — about once per full deck (~11 days), rather than never.

### Active hours

The deck advances only inside a local daily window, so it holds overnight rather
than dealing facts to an empty room. Without it, a 15-minute rotation burns ~60%
of the corpus while nobody is looking.

Local time comes from a fixed `utcOffsetMinutes`, not a timezone database — the
default transform runtime exposes no `Intl`. That costs an hour of drift across a
DST boundary, which a window this wide absorbs. Set `activeStartHour` /
`activeEndHour` to `0` / `24` to rotate around the clock.

### Tuning

Both live in `corpus/<project>.facts.json` under `meta`:

| key | |
|---|---|
| `rotationMinutes` | how long one fact stays up |
| `activeStartHour` / `activeEndHour` | local window the deck advances in |
| `utcOffsetMinutes` | e.g. `-420` for PDT |

A fact can only change when the **screen redraws**, so the real ceiling is the
device refresh rate — and the plugin is only *shown* once every
`playlist_size × refresh_rate`. Set `rotationMinutes` **at or above** that:

- rotation **shorter** than the display interval → facts advance between showings
  and you never see the skipped ones.
- rotation **longer** → you occasionally see the same fact twice, which is
  harmless (and not bad for retention).

To actually see facts quickly, shorten the *display* interval, not just the
rotation: drop the device refresh rate (15 min is the free-account floor, 5 min on
TRMNL+) and give the other playlist items custom schedules that hide them during
your desk hours — a plugin that is the only visible item shows on every refresh.
Current setup: 15 min refresh, solo across a 05:30–14:00 window → 34 facts/day,
full deck in 10.7 days. Hours may be fractional (`5.5` = 05:30).

## Code snippets

The TRMNL framework ships **no monospace face** — and its own bundled
`dogicapixel` is not actually fixed-pitch, despite the name. Code set in the
render container's fallback `monospace` gets anti-aliased and then hard-thresholded
to 1-bit, which is exactly the mush the pixel fonts exist to avoid.

So `shared.liquid` inlines **Departure Mono** (SIL OFL, `fonts/`) as base64. It is
genuinely fixed-pitch and lands on integer pixel advances at 11px multiples:

| size | advance | 52 cols |
|---|---|---|
| 11px (`code--sm`) | 7.00px | 364px |
| 22px (`code--lg`) | 14.00px | 728px |

Hence the corpus limit of **≤ 9 lines × ≤ 52 columns**: 9 × 26px = 234px tall and
728px wide is the worst case that still fits `view--full` alongside a title and a
line of prose. `full.liquid` uses 22px; the narrow mashup cells drop to 11px and
suppress snippets wider than they can hold.

Custom CSS is fine here, despite TRMNL's house style saying otherwise: `<style>`
in shared markup is documented and supported, and the only mechanical check
(`trmnlp lint`) applies to public Recipe submissions, not private plugins.

## Files

| Path | |
|---|---|
| `corpus/<project>.facts.json` | the master corpus — edit this |
| `topics/<project>.json` | topic manifest: what to source, pinned versions |
| `dist/corpus.json` | built artifact; the thing you host |
| `src/transform.js` | picks the fact (runs on TRMNL, before Liquid) |
| `src/full.liquid` | 800×480 — the one that matters |
| `src/half_vertical.liquid`, `half_horizontal.liquid`, `quadrant.liquid` | mashup cells |
| `src/shared.liquid` | **generated** — font + CSS + logo. Edit `templates/shared.liquid.in` |
| `src/settings.yml` | plugin config; holds the polling URL |
| `bin/build.js` | validate → interleave → `dist/corpus.json` + `src/shared.liquid` |
| `bin/vars.js` | run the real transform, print merge variables |
| `bin/preview.rb` | render + screenshot with headless Chromium |
| `bin/overflow-check.rb` | render *every* fact, fail on any that overflows |

## Build

```bash
bin/build.js
```

Validates every fact against the display limits (title ≤ 58; fact ≤ 230, or ≤ 150
when a snippet is present; code ≤ 9 lines × ≤ 52 cols) and **fails** on a
violation rather than shipping something that overflows the screen. Then
interleaves topics and writes `dist/corpus.json` + `src/shared.liquid`.

Fact ids are stable: order comes from a hash of the id, so adding facts later
does not reshuffle the deck.

## Preview

`trmnlp` needs Ruby ≥ 3.4 (system Ruby here is 2.6), so `bin/preview.rb` renders
the templates against the real transform output and screenshots them with headless
Chromium. Needs `gem install --user-install liquid -v 4.0.4`.

```bash
bin/vars.js --worst > /tmp/v.json     # the fact that stresses the layout hardest
bin/preview.rb /tmp/v.json out/       # -> out/full.png, half_vertical.png, ...
bin/vars.js --id zustand-04 > /tmp/v.json
```

Layout is accurate. The framework's clamp/overflow JS may not settle, and
`image-dither` is applied by TRMNL's server-side bitmap conversion, so neither
shows up locally.

To check the whole corpus rather than one fact — renders all of them and fails on
any that overflow the panel or clip a code line:

```bash
bin/overflow-check.rb
```

Worth running after any corpus or template change. It is what caught the 32 facts
whose prose contains `<Link>`, `<StrictMode>`, `<svg>` and friends: Liquid does not
auto-escape, so an unescaped `{{ title }}` turns those into live DOM elements. Every
interpolated field is `| escape`d for that reason — don't drop it.

The official tool, if you ever have a new enough Ruby or Docker:

```bash
trmnlp serve    # http://localhost:4567
docker run --pull always -p 4567:4567 -v "$PWD:/plugin" trmnl/trmnlp serve --bind 0.0.0.0
```

## Hosting

Live at **https://alechemy.github.io/trmnl-learn/corpus.json** — GitHub Pages off
the public repo `alechemy/trmnl-learn`, which holds nothing but the built corpus.

TRMNL's servers have to reach it, so the Tailscale-only Caddy can't serve it. Pages
serves `.json` as `application/json`; raw.githubusercontent serves `text/plain`,
which TRMNL may not parse. The corpus is facts drawn from public docs — nothing in
it is private.

To ship corpus changes (build → overflow-check → push):

```bash
bin/publish.sh          # expects the public repo at ~/Work/trmnl-learn
```

Pages caches for 10 minutes, so a new fact takes up to that long to go live.

## Deploying markup

Paste each `src/*.liquid` into the plugin's markup editor on trmnl.com, or
`trmnlp login && trmnlp push` from this directory. `src/transform.js` goes in the
plugin's transform editor — it defines both `transform(input)` (hosted *default*
runtime) and `run(input)` (hosted *serverless* runtime, and trmnlp), so it works
whichever runtime the plugin is set to.

## Another project

1. `topics/<project>.json` — list the topics and pin each to the version actually
   installed.
2. Source the facts against those pinned versions; write
   `corpus/<project>.facts.json` (`{meta: {project, rotationMinutes}, facts: [...]}`).
3. `bin/build.js` — it globs `corpus/*.facts.json`, so several projects can share
   one deck, or you can build a single project by passing its path.

Fact shape:

```json
{
  "id": "flashlist-01",
  "topic": "FlashList",
  "title": "FlashList v2 is estimate-free — no estimatedItemSize",
  "fact": "v2 removed estimatedItemSize, estimatedListSize and estimatedFirstItemOffset...",
  "code": "",
  "lang": "tsx",
  "level": "core",
  "source": "node_modules/.pnpm/@shopify+flash-list@2.0.2/.../FlashListProps.d.ts",
  "version": "@shopify/flash-list 2.0.2"
}
```

`source` is kept in the master corpus for auditing and stripped from `dist/`.

## The 100 kB polling cap

**A polling endpoint may not return more than 100 kB** — TRMNL rejects it outright
(`Large payload received (174198 bytes)`). The full corpus is ~166 kB, so `build.js`
also emits `dist/corpus-N.json` shards under `SHARD_BYTES` (60 kB), and
`settings.yml` points at all three, newline-separated. TRMNL delivers them as
`IDX_0`, `IDX_1`, `IDX_2`; `transform.js` concatenates them in order — which is the
order the build wrote them, so the topic interleaving survives — and then picks.

Shards keep every *endpoint* under the cap. The transform independently keeps the
*merge variables* under it, by emitting one fact instead of the deck. Both are
needed: TRMNL's docs can be read either as "oversized endpoints are rejected" or as
"the sandbox digests the big payload and only its result must fit", and this
satisfies both readings.

Adding facts eventually needs a fourth shard — `build.js` handles that on its own,
but the new URL has to be added to `polling_url` by hand.

## The cache-buster URL

**TRMNL dedups on the polled payload, before the transform runs** — not on the
merge variables it produces. Observed: the plugin synced every 15 min for hours
while the screen stayed on a fact the deck had not selected in three days. The
corpus is static, so TRMNL saw identical bytes, skipped screen generation, and the
transform never got to pick a new fact.

So `polling_url` carries a fourth URL that returns the current time. It is not data
— `transform.js` skips any polled entry with no `facts` array — its only job is to
make each poll's payload differ, so TRMNL regenerates the screen and the transform
runs.

If the fact ever freezes again, check that endpoint first. Swapping it is a
one-line settings change; the transform tolerates it being absent, in any position,
or replaced. (`worldtimeapi.org` was dead when this was picked — verify a
replacement actually returns changing bytes before trusting it.)
