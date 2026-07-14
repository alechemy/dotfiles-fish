#!/usr/bin/env node
'use strict';

/**
 * Builds the two artifacts the plugin needs:
 *
 *   dist/corpus.json   the hosted payload TRMNL polls
 *   src/shared.liquid  the markup, with the code font inlined
 *
 * Usage: bin/build.js [corpus/<name>.facts.json ...]
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const LIMITS = { title: 58, fact: 230, factWithCode: 150, codeLines: 9, codeCols: 52 };
// TRMNL rejects a polling endpoint that responds with more than 100 kB.
const SHARD_BYTES = 60000;

function fail(msg) {
  console.error(`build: ${msg}`);
  process.exit(1);
}

/** Stable per-fact sort key, so adding facts later does not reshuffle the deck. */
function hashKey(id) {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

function validate(facts) {
  const errors = [];
  const seen = new Set();

  for (const f of facts) {
    const at = f.id || f.title;
    if (!f.id) errors.push(`${at}: missing id`);
    if (seen.has(f.id)) errors.push(`${at}: duplicate id`);
    seen.add(f.id);

    if (f.title.length > LIMITS.title) errors.push(`${at}: title ${f.title.length} > ${LIMITS.title}`);
    if (/\.$/.test(f.title)) errors.push(`${at}: title ends with a period`);

    const cap = f.code ? LIMITS.factWithCode : LIMITS.fact;
    if (f.fact.length > cap) errors.push(`${at}: fact ${f.fact.length} > ${cap}`);

    if (f.code) {
      const lines = f.code.split('\n');
      if (lines.length > LIMITS.codeLines) errors.push(`${at}: code ${lines.length} lines > ${LIMITS.codeLines}`);
      for (const line of lines) {
        if (line.length > LIMITS.codeCols) errors.push(`${at}: code line ${line.length} cols > ${LIMITS.codeCols}: ${line}`);
      }
    }
  }
  return errors;
}

/**
 * Round-robins topics so consecutive facts come from different topics, drawing
 * from whichever topic is furthest behind its share. Walking the result in
 * order therefore exhausts every fact before repeating any.
 */
function interleave(facts) {
  const byTopic = new Map();
  for (const f of facts) {
    if (!byTopic.has(f.topic)) byTopic.set(f.topic, []);
    byTopic.get(f.topic).push(f);
  }

  const queues = [...byTopic.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([topic, items]) => ({
      topic,
      items: items.slice().sort((a, b) => hashKey(a.id) - hashKey(b.id)),
      taken: 0,
      total: items.length,
    }));

  const out = [];
  while (out.length < facts.length) {
    let best = null;
    for (const q of queues) {
      if (q.taken >= q.total) continue;
      if (q.topic === (out.length ? out[out.length - 1].topic : null) && queues.some((o) => o !== q && o.taken < o.total)) {
        continue;
      }
      const progress = (q.taken + 0.5) / q.total;
      if (!best || progress < best.progress) best = { q, progress };
    }
    if (!best) fail('interleave stalled');
    out.push(best.q.items[best.q.taken++]);
  }
  return out;
}

function shardPayload(payload, maxBytes) {
  const shards = [];
  let current = null;

  for (const fact of payload.facts) {
    const size = Buffer.byteLength(JSON.stringify(fact));
    if (!current || current.bytes + size > maxBytes) {
      current = { meta: payload.meta, facts: [], bytes: 200 };
      shards.push(current);
    }
    current.facts.push(fact);
    current.bytes += size + 1;
  }

  return shards.map((s) => ({ meta: s.meta, facts: s.facts }));
}

function buildSharedLiquid() {
  const tpl = fs.readFileSync(path.join(ROOT, 'templates', 'shared.liquid.in'), 'utf8');
  const font = fs.readFileSync(path.join(ROOT, 'fonts', 'DepartureMono-Regular.woff2'));
  const out = tpl.replace('__FONT_WOFF2_BASE64__', font.toString('base64'));
  fs.writeFileSync(path.join(ROOT, 'src', 'shared.liquid'), out);
  return out.length;
}

function main() {
  const args = process.argv.slice(2);
  const sources = args.length
    ? args
    : fs.readdirSync(path.join(ROOT, 'corpus'))
        .filter((f) => f.endsWith('.facts.json'))
        .map((f) => path.join(ROOT, 'corpus', f));

  if (!sources.length) fail('no corpus/*.facts.json found');

  const facts = [];
  const meta = { project: '', rotationMinutes: 60 };

  for (const src of sources) {
    const doc = JSON.parse(fs.readFileSync(src, 'utf8'));
    if (!Array.isArray(doc.facts)) fail(`${src}: no facts array`);
    if (doc.meta) Object.assign(meta, doc.meta);
    facts.push(...doc.facts);
  }

  const errors = validate(facts);
  if (errors.length) {
    console.error(`build: ${errors.length} validation error(s):`);
    for (const e of errors.slice(0, 25)) console.error(`  - ${e}`);
    process.exit(1);
  }

  const ordered = interleave(facts);

  const payload = {
    meta: { ...meta, count: ordered.length },
    facts: ordered.map((f) => ({
      id: f.id,
      topic: f.topic,
      title: f.title,
      fact: f.fact,
      code: f.code || '',
      lang: f.lang || '',
      level: f.level,
      version: f.version,
    })),
  };

  const distPath = path.join(ROOT, 'dist', 'corpus.json');
  fs.writeFileSync(distPath, JSON.stringify(payload));

  // A single polling endpoint may not exceed 100 kB, so also emit shards small
  // enough to stay clear of it. Order is preserved across shards, which is what
  // keeps the topic interleaving intact once the transform concatenates them.
  const shards = shardPayload(payload, SHARD_BYTES);
  for (const f of fs.readdirSync(path.join(ROOT, 'dist'))) {
    if (/^corpus-\d+\.json$/.test(f)) fs.unlinkSync(path.join(ROOT, 'dist', f));
  }
  shards.forEach((shard, i) => {
    fs.writeFileSync(path.join(ROOT, 'dist', `corpus-${i}.json`), JSON.stringify(shard));
  });

  const sharedBytes = buildSharedLiquid();

  const topics = new Set(ordered.map((f) => f.topic));
  const bytes = fs.statSync(distPath).size;
  const withCode = ordered.filter((f) => f.code).length;

  console.log(`dist/corpus.json   ${ordered.length} facts · ${topics.size} topics · ${withCode} with code · ${(bytes / 1024).toFixed(1)} kB`);
  const shardSizes = shards.map((_, i) => fs.statSync(path.join(ROOT, 'dist', `corpus-${i}.json`)).size);
  console.log(`dist/corpus-N.json ${shards.length} shards · ${shardSizes.map((b) => (b / 1024).toFixed(0) + ' kB').join(' · ')} (each < 100 kB cap)`);
  console.log(`src/shared.liquid  ${(sharedBytes / 1024).toFixed(1)} kB (font inlined)`);

  let repeats = 0;
  for (let i = 1; i < ordered.length; i++) {
    if (ordered[i].topic === ordered[i - 1].topic) repeats++;
  }
  const startH = meta.activeStartHour ?? 0;
  const endH = meta.activeEndHour ?? 24;
  const activeHours = endH - startH > 0 && endH - startH < 24 ? endH - startH : 24;
  const perDay = Math.floor((activeHours * 60) / meta.rotationMinutes);
  const deckDays = ordered.length / perDay;
  const window = activeHours === 24 ? '24/7' : `${String(startH).padStart(2, '0')}:00–${String(endH).padStart(2, '0')}:00 local`;

  console.log(`rotation           ${meta.rotationMinutes} min · active ${window} · ${perDay} facts/day · full deck in ${deckDays.toFixed(1)} days`);
  console.log(`interleave         ${repeats} back-to-back same-topic`);
}

main();
