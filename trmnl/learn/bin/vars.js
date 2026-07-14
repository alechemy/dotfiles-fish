#!/usr/bin/env node
'use strict';

/**
 * Runs the real src/transform.js against dist/corpus.json and prints the merge
 * variables TRMNL would hand to Liquid — so previews exercise the shipped
 * selection logic rather than a copy of it.
 *
 * Usage:
 *   bin/vars.js                 # whatever is showing right now
 *   bin/vars.js --id zustand-04
 *   bin/vars.js --index 12
 *   bin/vars.js --worst         # the fact that stresses the layout hardest
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const { selectFact } = require(path.join(ROOT, 'src', 'transform.js'));

const corpus = JSON.parse(fs.readFileSync(path.join(ROOT, 'dist', 'corpus.json'), 'utf8'));
const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(name);
  return i === -1 ? null : args[i + 1];
};

function emit(fact) {
  const one = { meta: corpus.meta, facts: [fact] };
  return selectFact(one);
}

if (args.includes('--worst')) {
  const score = (f) => {
    const lines = f.code ? f.code.split('\n') : [];
    const cols = lines.reduce((m, l) => Math.max(m, l.length), 0);
    return lines.length * 100 + cols + f.title.length + f.fact.length / 10;
  };
  const worst = corpus.facts.slice().sort((a, b) => score(b) - score(a))[0];
  console.log(JSON.stringify(emit(worst), null, 2));
} else if (flag('--id')) {
  const f = corpus.facts.find((x) => x.id === flag('--id'));
  if (!f) {
    console.error(`no fact with id ${flag('--id')}`);
    process.exit(1);
  }
  console.log(JSON.stringify(emit(f), null, 2));
} else if (flag('--index')) {
  console.log(JSON.stringify(emit(corpus.facts[Number(flag('--index'))]), null, 2));
} else {
  console.log(JSON.stringify(selectFact(corpus), null, 2));
}
