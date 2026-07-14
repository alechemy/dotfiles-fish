/**
 * Picks the fact to show, from the corpus TRMNL just polled.
 *
 * TRMNL skips screen generation when merge variables are unchanged, so rotation
 * cannot live in Liquid against a static corpus — the screen would never
 * regenerate. Emitting one fact here is what makes the payload change.
 *
 * Index is derived from the clock rather than stored state (transforms are
 * stateless). `facts` is pre-interleaved at build time, so walking it in order
 * exhausts every fact before repeating any, and consecutive facts come from
 * different topics.
 */
function mix32(x) {
  x = Math.imul(x ^ 0x9e3779b9, 0x85ebca6b);
  x ^= x >>> 13;
  x = Math.imul(x, 0xc2b2ae35);
  x ^= x >>> 16;
  return x >>> 0;
}

/**
 * The corpus arrives either whole (one polling URL, `input.facts`) or split
 * across shards (several newline-separated URLs, which TRMNL delivers as
 * `IDX_0`, `IDX_1`, …). Shards exist because a single endpoint may not exceed
 * 100 kB; they are concatenated in index order, which is the order the build
 * wrote them, so the interleaving survives.
 *
 * Entries without a `facts` array are skipped rather than terminating the scan:
 * one polling URL is a cache-buster whose only job is to return something
 * different each poll, so TRMNL sees changed data and actually regenerates the
 * screen instead of deduping it away.
 */
function gather(input) {
  if (!input) return { facts: [], meta: {} };
  if (Array.isArray(input.facts)) return { facts: input.facts, meta: input.meta || {} };

  var facts = [];
  var meta = {};
  for (var i = 0; i < 64; i++) {
    var shard = input['IDX_' + i];
    if (!shard || !Array.isArray(shard.facts)) continue;
    if (!meta.rotationMinutes && shard.meta) meta = shard.meta;
    facts = facts.concat(shard.facts);
  }
  return { facts: facts, meta: meta };
}

/**
 * Minutes of "active" time elapsed since the epoch, where active means inside
 * the local daily window. Outside it the count is pinned, so the deck holds
 * instead of dealing facts to an empty room overnight.
 *
 * Local time comes from a fixed `utcOffsetMinutes`, not a timezone database —
 * the default transform runtime exposes no Intl. That costs an hour of drift
 * across a DST boundary, which a window this wide absorbs.
 */
function activeMinutes(nowMs, meta) {
  var startH = typeof meta.activeStartHour === 'number' ? meta.activeStartHour : 0;
  var endH = typeof meta.activeEndHour === 'number' ? meta.activeEndHour : 24;
  var perDay = (endH - startH) * 60;
  if (perDay <= 0 || perDay >= 1440) return Math.floor(nowMs / 60000);

  var localMs = nowMs + (meta.utcOffsetMinutes || 0) * 60000;
  var day = Math.floor(localMs / 86400000);
  var intoDay = Math.floor((localMs - day * 86400000) / 60000);

  var into = intoDay - startH * 60;
  if (into < 0) into = 0;
  if (into > perDay) into = perDay;

  return day * perDay + into;
}

function selectFact(input) {
  var gathered = gather(input);
  var facts = gathered.facts;
  if (!facts.length) return input;

  var meta = gathered.meta;
  var n = facts.length;
  var period = Math.max(1, meta.rotationMinutes || 60);

  var tick = Math.floor(activeMinutes(Date.now(), meta) / period);
  var cycle = Math.floor(tick / n);

  // The device only samples every s-th tick, where s is the display interval in
  // rotation periods. Advancing the deck linearly makes the sampled indices an
  // arithmetic progression, which collapses onto a fraction of the corpus
  // whenever s and n share structure — at n=261, s=10 reaches only 25% of the
  // facts, permanently. Rotating by a hashed offset each cycle breaks that
  // resonance (measured: 100% reachable for every s tried, at four corpus
  // sizes) while staying a bijection, so a cycle still shows each fact once.
  var offset = mix32(cycle) % n;
  var index = ((tick % n) + offset) % n;

  var f = facts[index] || facts[0];
  var code = typeof f.code === 'string' ? f.code : '';
  var lines = code ? code.split('\n') : [];
  var cols = 0;
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].length > cols) cols = lines[i].length;
  }

  return {
    id: f.id || '',
    topic: f.topic || '',
    title: f.title || '',
    fact: f.fact || '',
    code: code,
    lang: f.lang || '',
    level: f.level || '',
    version: f.version || '',
    has_code: code.length > 0,
    code_lines: lines.length,
    code_cols: cols,
    seq: index + 1,
    total: n,
    project: meta.project || '',
  };
}

function transform(input) {
  return selectFact(input);
}

function run(input) {
  return selectFact(input);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { transform: transform, run: run, selectFact: selectFact };
}
