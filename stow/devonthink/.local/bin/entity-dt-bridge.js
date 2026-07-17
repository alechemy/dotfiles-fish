#!/usr/bin/osascript -l JavaScript
// entity-dt-bridge.js — single DEVONthink I/O gateway for the entity layer.
//
// Executes a batch of operations described in a JSON file and prints a JSON
// result to stdout. All entity-layer AppleEvents to DEVONthink flow through
// this script so the Python orchestrators (dt-morning-brief.py,
// entity-filing.py) stay stdlib-only and JSON never has to round-trip
// through AppleScript records.
//
// Wire format:
//   argv[0]  path to a JSON file: {"ops": [{"op": "<name>", ...}, ...]}
//   stdout   {"ok": true, "results": [...]}                on success
//            {"ok": false, "error": "...", "failed_op": i,
//             "results": [...partial...]}                  on first failure
//            {"ok": false, "unavailable": true, "error": "..."}
//                                                          DT/db not reachable
//
// Ops:
//   dump_people        {include_bodies?}                -> [{uuid,name,aliases,md,body?}]
//   list_sources       {}                               -> [{uuid,name,kind,eventdate,
//                                                            modified,ready,...}]
//   get_source         {uuid}                            -> {uuid,name,kind,eventdate,...}
//   list_group         {path}                           -> [{uuid,name}]
//   search             {query,limit?}                   -> [{uuid,name,eventdate,documenttype}]
//   get_at_path        {path}                           -> {uuid,name} | null
//   get_text           {uuid}                           -> {uuid,text}
//   set_text           {uuid,text}                      -> {uuid}
//   ensure_group       {path,exclude_chat?}             -> {uuid,created,chat_excluded}
//   get_fields         {uuid,fields}                    -> {uuid,fields:{k:v}}
//   set_fields         {uuid,fields}                    -> {uuid}
//   set_comment        {uuid,comment}                   -> {uuid}
//   set_name           {uuid,name}                      -> {uuid}
//   set_tags           {uuid,tags}                      -> {uuid}
//   find_by_field      {field,value}                    -> [{uuid,name,path,location}]
//   import_record      {path,group}                     -> {uuid,name}
//   replace_file       {uuid,path}                      -> {uuid}
//   move_to            {uuid,group}                     -> {uuid}
//   list_tags          {}                               -> [name,...]
//   ensure_person      {name,aliases?,fields?,log_lines?} -> {uuid,created,
//                       lastcontact_changed?,lastcontact_invalid?}
//   ensure_event       {name,date,location?,attendees?,summary?,source_uuid,
//                       log_line?}                      -> {uuid,created,merged?}
//   append_log         {uuid,lines}                     -> {uuid,appended,skipped}
//   set_field          {uuid,field,value,effective_date?,
//                       expected_previous?,transition_line?}
//                                                       -> {uuid,changed,previous,stale?}
//   bump_lastcontact   {uuid,date}                      -> {uuid,changed,invalid?}
//   mark_filed         {uuid}                           -> {uuid}
//   create_record      {name,path,text,fields?,tags?}   -> {uuid}
//   get_or_create_daily {date,heading}                  -> {uuid,text,created}
//   upsert_section     {uuid,header,content}            -> {uuid,changed,replaced}
//   insert_under_section {uuid,header,line}             -> {uuid,changed}
//   relink_entities    {}                               -> {records,changed}
//   sort_logs          {dry_run?}                       -> {records,changed,
//                                                           records_changed}
//   trash              {uuid}                           -> {uuid}
//   add_aliases        {uuid,aliases}                   -> {uuid,aliases}
//   list_candidates    {}                               -> {pending:[{uuid,name,md,text}],
//                                                           approved:[...],ignored:[...]}

ObjC.import('Foundation')

const DB_NAME = 'Lorebook'
const ENTITIES_PATH = '/20_ENTITIES'
const PEOPLE_PATH = ENTITIES_PATH + '/People'
const PLACES_PATH = ENTITIES_PATH + '/Places'
const EVENTS_PATH = ENTITIES_PATH + '/Events'
const DAILY_PATH = '/10_DAILY'
const JOURNAL_PATH = '/15_JOURNAL'
const FACTS_PATH = ENTITIES_PATH + '/_Facts'
const CANDIDATES_PATH = ENTITIES_PATH + '/_Candidates'
const CANDIDATES_APPROVED_PATH = CANDIDATES_PATH + '/Approved'
const CANDIDATES_IGNORED_PATH = CANDIDATES_PATH + '/Ignored'
const NOTES_SECTION = "## Today's Notes"
const LOG_SECTION = '## Biographical Log'
const EVENT_LOG_SECTION = '## Log'
const TEMPLATE_DIR =
  $.NSHomeDirectory().js +
  '/Library/Application Support/DEVONthink/Templates.noindex/Entities/'
const PERSON_TEMPLATE = TEMPLATE_DIR + 'Person.md'
const EVENT_TEMPLATE = TEMPLATE_DIR + 'Event.md'

function readFile(path) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(
    path, $.NSUTF8StringEncoding, null)
  return s.isNil() ? null : s.js
}

// Pure over an already-fetched customMetaData dict, so a bulk-fetched array
// of them (one AppleEvent for a whole group) can be read the same way a
// single record's live dict is.
function mdField(md, key) {
  const v = (md || {})['md' + key]
  return v === undefined || v === null ? '' : String(v)
}

function mdValue(rec, key) {
  return mdField(rec.customMetaData(), key)
}

// A flag set by script reads back as '1'; the same flag ticked in the GUI
// reads back as 'true' (see CLAUDE.md on DT's boolean representation).
function flagSet(v) {
  return v === '1' || v === 'true'
}

function isoStamp(d) {
  return d
    ? new Date(d.getTime() - d.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 19)
    : ''
}

function normName(s) {
  return String(s || '').normalize('NFKD').replace(/\p{M}/gu, '')
    .replace(/ß/g, 'ss').toLowerCase().replace(/\s+/g, ' ').trim()
}

// Raise-only guard for LastContact, shared by bump_lastcontact and
// ensure_person: a hand-typed non-ISO current value sorts above every ISO
// date and would freeze the field, so it is treated as absent; an incoming
// value that isn't a bare YYYY-MM-DD is rejected outright rather than
// written, so a malformed hand-edited proposal can't smudge the field.
function lastContactGuard(current, incoming) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(incoming || ''))) {
    return { changed: false, invalid: true }
  }
  const comparable = /^\d{4}-\d{2}-\d{2}$/.test(current) ? current : ''
  return { changed: !comparable || incoming > comparable, invalid: false }
}

// Comparison/storage normalization for the Email field: trims, lowercases,
// and strips a leading mailto: so that variant never reads as a change.
function normalizeEmail(v) {
  return String(v || '').trim().replace(/^mailto:/i, '').toLowerCase()
}

// Classifies one source record's kind for transport/privacy policy, checked
// in order of privacy restriction — every local-only kind (daily, journal,
// fact, handwritten) before the cloud-eligible meeting kind — so a record
// carrying more than one marker lands on the more restrictive kind. Pure
// over already-extracted values so list_sources and get_source agree.
function classify(v) {
  if (v.location.indexOf(DAILY_PATH) === 0) return 'daily'
  if (v.location.indexOf(JOURNAL_PATH) === 0) return 'journal'
  if (v.location.indexOf(FACTS_PATH) === 0) return 'fact'
  if (v.handwritten) return 'handwritten'
  if (v.documenttype.indexOf('Meeting') !== -1) return 'meeting'
  return 'other'
}

// A body can come back CR-delimited: AppleScript's `do shell script` coerces
// LF to CR, so a smart rule that pipes a body through a helper and writes the
// result back stores CRs. Splitting on \n alone would then see the whole note
// as one line, no `##` header would match, and upsert_section would append a
// duplicate section instead of replacing it. Writes always emit \n, so a body
// self-heals on the next edit.
function bodyLines(rec) {
  return rec.plainText().split(/\r\n|\r|\n/)
}

function personBriefFrom(uuid, name, aliases, md, body) {
  const out = {
    uuid: uuid,
    name: name,
    aliases: String(aliases || ''),
    md: md || {},
  }
  if (body !== undefined) out.body = body
  return out
}

// Person lookup key set: record name plus each comma-separated alias.
function personKeys(rec) {
  const keys = [normName(rec.name())]
  String(rec.aliases() || '').split(',').forEach(a => {
    const k = normName(a)
    if (k) keys.push(k)
  })
  return keys
}

// Union two alias lists (comma-separated string or array), preserving the
// existing order and appending new entries deduped case-insensitively.
function unionAliases(existing, incoming) {
  const split = v => (Array.isArray(v) ? v : String(v || '').split(','))
    .map(s => String(s).trim()).filter(Boolean)
  const current = split(existing)
  const seen = Object.create(null)
  for (const a of current) seen[normName(a)] = true
  const out = current.slice()
  for (const a of split(incoming)) {
    const k = normName(a)
    if (!seen[k]) { seen[k] = true; out.push(a) }
  }
  return out.join(', ')
}

// The line index a section's span ends at: the next heading of level 1 or 2
// (trimmed), or the body's length. Shared by every scanner that owns a `##`
// section's span, so a user-authored `# H1` block never lands inside one.
function sectionSpan(body, headerIdx) {
  let end = body.length
  for (let i = headerIdx + 1; i < body.length; i++) {
    if (/^#{1,2}\s/.test(body[i].trim())) { end = i; break }
  }
  return end
}

// The body span a `##` section owns: the lines after its header, up to the
// next heading. Null when the record has no such header.
function sectionBounds(body, header) {
  let headerIdx = -1
  for (let i = 0; i < body.length; i++) {
    if (body[i].trim() === header) { headerIdx = i; break }
  }
  if (headerIdx === -1) return null
  return { header: headerIdx, start: headerIdx + 1, end: sectionSpan(body, headerIdx) }
}

function insertUnderSection(body, header, lines) {
  const out = body.slice()
  const b = sectionBounds(out, header)
  if (b === null) {
    while (out.length && out[out.length - 1].trim() === '') out.pop()
    return out.concat(['', header, ''], lines, [''])
  }
  let insertAt = b.end
  while (insertAt > b.start && out[insertAt - 1].trim() === '') insertAt--
  const block = insertAt === b.start ? [''].concat(lines) : lines
  out.splice(insertAt, 0, ...block)
  return out
}

// Idempotent single-line form: a retry of the record's latest body must not
// duplicate the line, the same guarantee upsert_section gives a full section.
function insertUnderSectionOnce(body, header, line) {
  const b = sectionBounds(body, header)
  if (b !== null) {
    const want = String(line).trim()
    if (body.slice(b.start, b.end).some(l => l.trim() === want)) return body
  }
  return insertUnderSection(body, header, [line])
}

// Pure core of the upsert_section op: the new full body text to write (or
// null when nothing changed) for replacing/creating/removing one section.
function sectionUpsert(lines, header, content) {
  let start = -1
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].trim() === header) { start = i; break }
  }
  if (start === -1) {
    if (!content.trim()) return { text: null, changed: false, replaced: false }
    let out = lines.slice()
    // Jots are inserted relative to the last bullet BEFORE this header
    // (see insert-jot-into-daily-note.py); generated sections must sit
    // after it, so guarantee it exists.
    if (out.map(l => l.trim()).indexOf(NOTES_SECTION) === -1) {
      while (out.length && out[out.length - 1].trim() === '') out.pop()
      out = out.concat(['', NOTES_SECTION])
    }
    while (out.length && out[out.length - 1].trim() === '') out.pop()
    const text =
      out.concat(['', header, ''], content.split('\n')).join('\n') + '\n'
    return { text: text, changed: true, replaced: false }
  }
  const end = sectionSpan(lines, start)
  if (!content.trim()) {
    const out = lines.slice(0, start).concat(lines.slice(end))
    while (out.length && out[out.length - 1].trim() === '') out.pop()
    return { text: out.join('\n') + '\n', changed: true, replaced: true, removed: true }
  }
  let spanEnd = end
  while (spanEnd > start && lines[spanEnd - 1].trim() === '') spanEnd--
  const section = [header, ''].concat(content.split('\n'))
  if (lines.slice(start, spanEnd).join('\n') === section.join('\n')) {
    return { text: null, changed: false, replaced: true }
  }
  const out = lines.slice(0, start).concat(section, [''], lines.slice(end))
  return { text: out.join('\n'), changed: true, replaced: true }
}

const LOG_ENTRY_RE = /^-\s+(\d{4}-\d{2}-\d{2})(?![\d-])/

// Order a dated log section newest-first. Facts arrive in filing order, not
// fact order — a backlog drain, a corrected re-extraction, or a note about
// something that happened last year all append below entries dated after
// them — so without this the log reads in no order at all.
//
// An entry is a column-zero `- YYYY-MM-DD` bullet plus any non-blank lines
// under it, and only entries move: blank lines and prose keep the positions
// they hold in the section, each entry slot refilled from the sorted run.
// Undated bullets are prose by that rule, and an *indented* dated bullet is a
// continuation of the entry above it — both travel with their parent rather
// than sorting on their own date, so nested details never detach.
// Same-date entries keep their existing order, which leaves an append after
// its same-day neighbours and makes the sort idempotent.
function sortLogSection(body, header) {
  const b = sectionBounds(body, header)
  if (b === null) return body
  const slots = []
  const entries = []
  for (let i = b.start; i < b.end; i++) {
    const m = body[i].match(LOG_ENTRY_RE)
    if (!m) { slots.push(body[i]); continue }
    const block = [body[i]]
    while (i + 1 < b.end && body[i + 1].trim() !== '' &&
           !LOG_ENTRY_RE.test(body[i + 1])) {
      block.push(body[++i])
    }
    entries.push({ date: m[1], order: entries.length, block: block })
    slots.push(null)
  }
  const sorted = entries.slice().sort((x, y) =>
    x.date === y.date ? x.order - y.order : (x.date < y.date ? 1 : -1))
  const out = body.slice(0, b.start)
  let next = 0
  for (const slot of slots) {
    if (slot === null) out.push(...sorted[next++].block)
    else out.push(slot)
  }
  return out.concat(body.slice(b.end))
}

// A candidate event record's body Date value, or '' when missing/bare — the
// fallback ensure_event scores against when mdeventdate is unset.
function bodyDateValue(lines) {
  for (const line of lines) {
    if (/^\*\*Date:\*\*/.test(line)) {
      const v = line.replace(/^\*\*Date:\*\*\s*/, '').trim()
      return v === '' || v === '—' ? '' : v
    }
  }
  return ''
}

// ensure_event's match-window gap in days for one candidate: falls back to
// the body Date when eventdate metadata is blank, so an undated record is
// only ever the weakest (date-agnostic) match once — a real date on either
// side wins it a real gap instead of matching forever.
function eventMatchGap(opDate, mdEventDate, bodyDate, matchDays) {
  const d = mdEventDate || bodyDate
  if (!opDate || !d) return matchDays
  return Math.abs(new Date(opDate) - new Date(d)) / 86400000
}

// Set by run(); lets module-level helpers reach the DT session.
let bridgeCtx = null
let entityIndex = null
let peopleIndex = null

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// One group's markdown children as {uuid,name,aliases} arrays fetched in a
// handful of AppleEvents total (one per property, on the unresolved
// `children` element specifier) instead of two AppleEvents per record.
function groupChildFields(group) {
  const c = group.children
  return {
    uuid: c.uuid(), name: c.name(), aliases: c.aliases(),
    recordType: c.recordType(),
  }
}

// Names/aliases of every existing entity record, longest first so
// "Maya Chen" wins over "Maya". Rebuilt after any create.
function buildEntityIndex() {
  const entries = []
  for (const path of [PEOPLE_PATH, PLACES_PATH, EVENTS_PATH]) {
    let group
    try { group = bridgeCtx.groupAt(path) } catch (e) { continue }
    const f = groupChildFields(group)
    for (let i = 0; i < f.uuid.length; i++) {
      if (f.recordType[i] !== 'markdown') continue
      const uuid = f.uuid[i]
      const names = [f.name[i]].concat(String(f.aliases[i] || '').split(','))
      for (const n of names) {
        const t = n.trim()
        if (t.length >= 3 && t.indexOf('[') === -1 && t.indexOf(']') === -1) {
          entries.push({ name: t, uuid: uuid })
        }
      }
    }
  }
  entries.sort((a, b) => b.name.length - a.name.length)
  return entries
}

// Markdown-link spans land at odd indices after a capture-group split.
const LINK_SPLIT_RE = /(\[[^\]]*\]\([^)]*\))/

// Whitespace-delimited token ranges that look like a URL (a `://` scheme or
// a bare `www.` host), so linkEntities never wraps a substring inside one —
// an entity name inside a bare URL would break the link, not annotate it.
function urlSpans(text) {
  const spans = []
  const re = /\S+/g
  let m
  while ((m = re.exec(text))) {
    if (m[0].indexOf('://') !== -1 || /^www\./i.test(m[0])) {
      spans.push([m.index, m.index + m[0].length])
    }
  }
  return spans
}

function withinSpan(start, end, spans) {
  return spans.some(s => start >= s[0] && end <= s[1])
}

// Wrap the first mention of each known entity in an item link so hand-
// authored Places/Events (and other People) accrue backlinks from every
// filed fact. Never links inside an existing link or a bare URL, never
// links the target record to itself, skips entities the line already
// links to.
function linkEntities(line, excludeUuid) {
  if (entityIndex === null) entityIndex = buildEntityIndex()
  let out = line
  for (const e of entityIndex) {
    if (e.uuid === excludeUuid || out.indexOf(e.uuid) !== -1) continue
    const re = new RegExp(
      '(^|[^\\w\\[])(' + escapeRe(e.name) + ')(?![\\w\\]])', 'ig')
    const parts = out.split(LINK_SPLIT_RE)
    for (let i = 0; i < parts.length; i += 2) {
      const spans = urlSpans(parts[i])
      re.lastIndex = 0
      let m, hit = null
      while ((m = re.exec(parts[i]))) {
        const start = m.index + m[1].length
        if (!withinSpan(start, start + m[2].length, spans)) { hit = m; break }
      }
      if (hit) {
        parts[i] = parts[i].slice(0, hit.index) + hit[1] +
          '[' + hit[2] + '](x-devonthink-item://' + e.uuid + ')' +
          parts[i].slice(hit.index + hit[0].length)
        out = parts.join('')
        break
      }
    }
  }
  return out
}

// A filed fact's dedup identity: its source-note UUID plus the line's text
// with every item-link flattened to its label and the fact: provenance
// marker removed. Keying on content rather than just the source link keeps
// re-applies idempotent while letting a corrected re-extraction of the same
// note file the genuinely new facts it surfaces; flattening links and
// dropping the marker means neither auto-linking nor a hand-deleted marker
// ever changes the identity.
function factSignature(line) {
  const src = (line.match(
    /\[source\]\(x-devonthink-item:\/\/([0-9A-Fa-f-]+)\)/) || [])[1] || ''
  const text = line
    .replace(/<!--\s*fact:[0-9a-f]+\s*-->/g, '')
    .replace(/\[([^\]]*)\]\(x-devonthink-item:\/\/[0-9A-Fa-f-]+\)/g, '$1')
    .replace(/\s+/g, ' ').trim()
  return src + '|' + text
}

// Skip lines already filed (by fact signature), link known entities in the
// remainder, insert under the given section header, and re-sort the section
// newest-first. The sort runs even when nothing was appended, so a record the
// filer touches at all leaves in order.
function appendLogLines(rec, lines, section) {
  const header = section || LOG_SECTION
  const body = bodyLines(rec)
  const uuid = rec.uuid()
  const seen = Object.create(null)
  for (const bl of body) seen[factSignature(bl)] = true
  const fresh = []
  let skipped = 0
  for (const line of lines || []) {
    const sig = factSignature(line)
    if (seen[sig]) { skipped++; continue }
    seen[sig] = true
    fresh.push(linkEntities(line, uuid))
  }
  const next = sortLogSection(
    fresh.length ? insertUnderSection(body, header, fresh) : body, header)
  const text = next.join('\n')
  if (text !== body.join('\n')) rec.plainText = text
  return { appended: fresh.length, skipped: skipped }
}

function run(argv) {
  const opsRaw = readFile(argv[0])
  if (opsRaw === null) {
    return JSON.stringify({ ok: false, error: 'cannot read ops file: ' + argv[0] })
  }
  const ops = JSON.parse(opsRaw).ops || []

  const dt = Application('com.devon-technologies.think')
  const std = Application.currentApplication()
  std.includeStandardAdditions = true

  // A byName() specifier resolves lazily, so a closed or still-loading
  // database would surface as a bare "Can't get object." from whichever op
  // touches it first. Enumerate, and report unavailability as its own answer.
  let db = null
  try {
    const dbs = dt.databases()
    for (let i = 0; i < dbs.length; i++) {
      if (String(dbs[i].name()) === DB_NAME) { db = dbs[i]; break }
    }
  } catch (e) {
    return JSON.stringify({
      ok: false,
      unavailable: true,
      error: 'DEVONthink is not answering: ' + String(e.message || e),
    })
  }
  if (db === null) {
    return JSON.stringify({
      ok: false,
      unavailable: true,
      error: 'database not open: ' + DB_NAME,
    })
  }

  function groupAt(path) {
    const g = dt.getRecordAt(path, { in: db })
    if (!g) throw new Error('group not found: ' + path)
    return g
  }

  function byUuid(uuid) {
    const r = dt.getRecordWithUuid(uuid)
    if (!r) throw new Error('record not found: ' + uuid)
    return r
  }

  bridgeCtx = { groupAt: groupAt }

  // People lookups (findByNameOrAlias/personLink/ensure_person) are the
  // hottest path — every attendee of every meeting resolves through here —
  // so the group's children are read once per bridge invocation and reused,
  // instead of a fresh full-group enumeration per name. Invalidated (not
  // incrementally updated) wherever a Person is created, so a later lookup
  // in the same batch reloads and sees it.
  function loadPeopleIndex() {
    const group = groupAt(PEOPLE_PATH)
    const recs = group.children()
    const names = group.children.name()
    const aliases = group.children.aliases()
    peopleIndex = recs.map((rec, i) => ({
      rec: rec,
      keys: [normName(names[i])].concat(
        String(aliases[i] || '').split(',').map(normName).filter(Boolean)),
    }))
  }

  function findByNameOrAlias(path, name) {
    const want = normName(name)
    const hits = []
    if (path === PEOPLE_PATH) {
      if (peopleIndex === null) loadPeopleIndex()
      for (const entry of peopleIndex) {
        if (entry.keys.indexOf(want) !== -1) hits.push(entry.rec)
      }
      return hits
    }
    for (const rec of groupAt(path).children()) {
      if (personKeys(rec).indexOf(want) !== -1) hits.push(rec)
    }
    return hits
  }

  function findPerson(name) {
    return findByNameOrAlias(PEOPLE_PATH, name)
  }

  function personLink(name) {
    const hits = findPerson(name)
    if (hits.length === 1) {
      return '[' + hits[0].name() + '](x-devonthink-item://' + hits[0].uuid() + ')'
    }
    return name
  }

  function personSkeleton(name) {
    let tpl = readFile(PERSON_TEMPLATE)
    if (tpl === null) {
      tpl = '# Person Name\n\n**Role:** —\n**City:** —\n**Partner:** —\n' +
        '**Kids:** —\n**How we met:** —\n\n' + LOG_SECTION + '\n'
    }
    const lines = tpl.split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (/^#\s/.test(lines[i])) { lines[i] = '# ' + name; break }
    }
    return lines.join('\n')
  }

  const handlers = {
    dump_people(op) {
      const includeBodies = op.include_bodies !== false
      const c = groupAt(PEOPLE_PATH).children
      const types = c.recordType()
      const uuids = c.uuid()
      const names = c.name()
      const aliases = c.aliases()
      const mds = c.customMetaData()
      const bodies = includeBodies ? c.plainText() : null
      const out = []
      for (let i = 0; i < uuids.length; i++) {
        if (types[i] !== 'markdown') continue
        out.push(personBriefFrom(
          uuids[i], names[i], aliases[i], mds[i],
          includeBodies ? bodies[i] : undefined))
      }
      return out
    },

    list_sources() {
      const out = []
      const seen = {}
      const addResolved = (uuid, name, location, md, added, modified) => {
        if (seen[uuid]) return
        seen[uuid] = true
        const kind = classify({
          location: String(location || ''),
          handwritten: flagSet(mdField(md, 'handwritten')),
          documenttype: mdField(md, 'documenttype'),
        })
        out.push({
          uuid: uuid,
          name: name,
          kind: kind,
          eventdate: mdField(md, 'eventdate'),
          added: added ? isoStamp(added).slice(0, 10) : '',
          modified: isoStamp(modified),
          entityfiled: flagSet(mdField(md, 'entityfiled')),
          // NeedsProcessing=1 means the smart-rule pipeline (OCR, comment
          // formatting, enrichment) hasn't finished — the record's text may
          // not exist yet. Daily notes never carry the flag.
          ready: kind === 'daily'
            ? true
            : !flagSet(mdField(md, 'needsprocessing')),
        })
      }
      // A dt.search() result is a resolved list of records, not a lazy
      // element specifier, so it has no bulk per-property fetch — but one
      // properties() call per record still collapses what add() used to
      // read as ~5 property AppleEvents plus a customMetaData() per field
      // into a single AppleEvent.
      const addFromRecord = (r) => {
        const p = r.properties()
        addResolved(p.uuid, p.name, p.location, p.customMetaData,
          p.additionDate, p.modificationDate)
      }
      // A group's `children` element specifier (unparenthesized) fetches one
      // property for every child in a single AppleEvent — ~140x faster than
      // reading it off each child record in turn — so every field add()
      // needs is pulled as its own array and joined by index.
      const addFromGroup = (group, filter) => {
        const c = group.children
        const uuids = c.uuid()
        const names = c.name()
        const locations = c.location()
        const mds = c.customMetaData()
        const addeds = c.additionDate()
        const modifieds = c.modificationDate()
        for (let i = 0; i < uuids.length; i++) {
          if (filter && !filter(names[i])) continue
          addResolved(uuids[i], names[i], locations[i], mds[i], addeds[i], modifieds[i])
        }
      }
      // Quoted-phrase equality (mddocumenttype=="Meeting Notes") matches
      // nothing in DT's query parser; substring match is the reliable form.
      dt.search('mddocumenttype:~Meeting', { in: db.root() })
        .forEach(addFromRecord)
      dt.search('mdhandwritten==1', { in: db.root() })
        .forEach(addFromRecord)
      const today = new Date()
      const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
        .toISOString().slice(0, 10)
      addFromGroup(groupAt(DAILY_PATH),
        name => /^\d{4}-\d{2}-\d{2}$/.test(name) && name < localToday)
      // Journal entries live under year subgroups; today's entry is
      // skipped like today's daily note — it may still gain content.
      let journalGroup = null
      try { journalGroup = groupAt(JOURNAL_PATH) } catch (e) {}
      if (journalGroup) {
        for (const yearGroup of journalGroup.children()) {
          if (String(yearGroup.type()) !== 'group') continue
          addFromGroup(yearGroup,
            name => /^\d{4}-\d{2}-\d{2} Journal$/.test(name) &&
              name.slice(0, 10) < localToday)
        }
      }
      // Person-fact captures from the Drafts action. Not date-gated: a fact
      // capture is a complete thought at write time, unlike a daily note.
      let factsGroup = null
      try { factsGroup = groupAt(FACTS_PATH) } catch (e) {}
      if (factsGroup) {
        const c = factsGroup.children
        const types = c.recordType()
        const uuids = c.uuid()
        const names = c.name()
        const locations = c.location()
        const mds = c.customMetaData()
        const addeds = c.additionDate()
        const modifieds = c.modificationDate()
        for (let i = 0; i < uuids.length; i++) {
          if (types[i] === 'group') continue
          addResolved(uuids[i], names[i], locations[i], mds[i], addeds[i], modifieds[i])
        }
      }
      return out
    },

    // classify() with the same precedence list_sources uses, so --force
    // can target a record the database sweep never surfaces.
    get_source(op) {
      const r = byUuid(op.uuid)
      const kind = classify({
        location: String(r.location() || ''),
        handwritten: flagSet(mdValue(r, 'handwritten')),
        documenttype: mdValue(r, 'documenttype'),
      })
      const added = r.additionDate()
      return {
        uuid: r.uuid(),
        name: r.name(),
        kind: kind,
        eventdate: mdValue(r, 'eventdate'),
        added: added ? isoStamp(added).slice(0, 10) : '',
        modified: isoStamp(r.modificationDate()),
        entityfiled: flagSet(mdValue(r, 'entityfiled')),
        ready: kind === 'daily'
          ? true
          : !flagSet(mdValue(r, 'needsprocessing')),
      }
    },

    list_group(op) {
      return groupAt(op.path).children().map(r => ({ uuid: r.uuid(), name: r.name() }))
    },

    add_aliases(op) {
      const rec = byUuid(op.uuid)
      rec.aliases = unionAliases(rec.aliases(), op.aliases)
      entityIndex = null
      peopleIndex = null
      return { uuid: op.uuid, aliases: String(rec.aliases() || '') }
    },

    list_candidates() {
      // Missing groups (bootstrap not yet run) read as empty, not an error,
      // so filing degrades to today's behavior instead of failing the batch.
      const listOne = (path) => {
        const g = dt.getRecordAt(path, { in: db })
        if (!g) return []
        const c = g.children
        const types = c.recordType()
        const uuids = c.uuid()
        const names = c.name()
        const mds = c.customMetaData()
        const bodies = c.plainText()
        const out = []
        for (let i = 0; i < uuids.length; i++) {
          if (types[i] !== 'markdown') continue
          out.push({ uuid: uuids[i], name: names[i],
                     md: mds[i] || {}, text: String(bodies[i] || '') })
        }
        return out
      }
      return {
        pending: listOne(CANDIDATES_PATH),
        approved: listOne(CANDIDATES_APPROVED_PATH),
        ignored: listOne(CANDIDATES_IGNORED_PATH),
      }
    },

    search(op) {
      const cap = op.limit || 50
      return dt.search(String(op.query || ''), { in: db.root() })
        .slice(0, cap)
        .map(r => ({
          uuid: r.uuid(),
          name: r.name(),
          eventdate: mdValue(r, 'eventdate'),
          documenttype: mdValue(r, 'documenttype'),
        }))
    },

    get_at_path(op) {
      const r = dt.getRecordAt(op.path, { in: db })
      if (!r) return null
      return { uuid: r.uuid(), name: r.name() }
    },

    get_text(op) {
      const r = byUuid(op.uuid)
      // Handwritten records keep their AI-readable text in the Finder
      // comment (the formatted transcription); their image plain text is
      // a legacy OCR layer that goes stale on re-export.
      if (flagSet(mdValue(r, 'handwritten'))) {
        const c = r.comment()
        if (c) return { uuid: op.uuid, text: c }
      }
      return { uuid: op.uuid, text: r.plainText() }
    },

    ensure_group(op) {
      let g = dt.getRecordAt(op.path, { in: db })
      const created = !g
      if (!g) g = dt.createLocation(op.path, { in: db })
      let chatExcluded = false
      if (op.exclude_chat) {
        // Privacy boundary, same as /10_DAILY: journal content must never
        // be readable by DT chat, which may be a cloud provider.
        try {
          g.excludeFromChat = true
          chatExcluded = flagSet(String(g.excludeFromChat()))
        } catch (e) {
          chatExcluded = false
        }
      }
      return { uuid: g.uuid(), created: created, chat_excluded: chatExcluded }
    },

    get_fields(op) {
      const out = {}
      const r = byUuid(op.uuid)
      for (const field of op.fields || []) {
        out[field] = mdValue(r, field.toLowerCase().replace(/\s/g, ''))
      }
      return { uuid: op.uuid, fields: out }
    },

    set_fields(op) {
      const r = byUuid(op.uuid)
      for (const [field, value] of Object.entries(op.fields || {})) {
        dt.addCustomMetaData(value, { for: field, to: r })
      }
      return { uuid: op.uuid }
    },

    set_comment(op) {
      const r = byUuid(op.uuid)
      r.comment = op.comment
      return { uuid: op.uuid }
    },

    set_name(op) {
      const r = byUuid(op.uuid)
      r.name = op.name
      return { uuid: op.uuid }
    },

    set_tags(op) {
      const r = byUuid(op.uuid)
      r.tags = op.tags
      return { uuid: op.uuid }
    },

    // Unquoted value is deliberate: DT's md<field>== matches multi-word
    // values this way but not when quoted; the exact-compare filter makes
    // the broad hit set harmless (same trick as boox-import's dedup).
    find_by_field(op) {
      const key = op.field.toLowerCase().replace(/\s/g, '')
      const out = []
      for (const hit of dt.search('md' + key + '==' + op.value,
                                  { in: db.root() })) {
        if (mdValue(hit, key) === String(op.value)) {
          out.push({ uuid: hit.uuid(), name: hit.name(), path: hit.path(),
                     location: hit.location() })
        }
      }
      return out
    },

    import_record(op) {
      const rec = dt.importPath(op.path, { to: groupAt(op.group) })
      return { uuid: rec.uuid(), name: rec.name() }
    },

    // Stage + atomic same-volume mv so the record's backing file is always
    // the old or the new content — a plain copy truncates in place and a
    // mid-write failure would corrupt the record with no undo.
    replace_file(op) {
      const r = byUuid(op.uuid)
      const dest = r.path()
      const stage = dest + '.dt-replace-tmp'
      const shq = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
      try {
        std.doShellScript('cp ' + shq(op.path) + ' ' + shq(stage) +
                          ' && /bin/mv -f ' + shq(stage) + ' ' + shq(dest))
      } catch (e) {
        try { std.doShellScript('rm -f ' + shq(stage)) } catch (e2) {}
        throw new Error('replace failed: ' + e.message)
      }
      dt.synchronize({ record: r })
      return { uuid: op.uuid }
    },

    move_to(op) {
      const r = byUuid(op.uuid)
      dt.move({ record: r, to: groupAt(op.group) })
      return { uuid: op.uuid }
    },

    list_tags() {
      return db.tagGroups().map(t => t.name())
    },

    set_text(op) {
      const r = byUuid(op.uuid)
      r.plainText = op.text
      return { uuid: op.uuid }
    },

    ensure_person(op) {
      const hits = findPerson(op.name)
      if (hits.length > 1) {
        throw new Error('ambiguous person: ' + op.name + ' (' + hits.length + ' matches)')
      }
      let rec
      let created = false
      if (hits.length === 1) {
        rec = hits[0]
      } else {
        rec = dt.createRecordWith(
          { name: op.name, type: 'markdown' }, { in: groupAt(PEOPLE_PATH) })
        rec.plainText = personSkeleton(op.name)
        created = true
        entityIndex = null
        peopleIndex = null
        dt.addCustomMetaData('Person', { for: 'entitytype', to: rec })
        dt.addCustomMetaData('active', { for: 'entitystatus', to: rec })
      }
      if (op.aliases) rec.aliases = unionAliases(rec.aliases(), op.aliases)
      let lastcontact
      for (const [field, value] of Object.entries(op.fields || {})) {
        if (!value) continue
        // ensure_person can resolve to an existing record (a re-run proposal),
        // so LastContact keeps the bump op's monotonicity instead of being
        // overwritten backwards.
        if (field === 'lastcontact') {
          const g = lastContactGuard(mdValue(rec, 'lastcontact'), value)
          if (g.changed) dt.addCustomMetaData(value, { for: field, to: rec })
          lastcontact = g
          continue
        }
        dt.addCustomMetaData(value, { for: field, to: rec })
      }
      if ((op.log_lines || []).length) {
        appendLogLines(rec, op.log_lines)
      }
      const result = { uuid: rec.uuid(), created: created }
      if (lastcontact) {
        result.lastcontact_changed = lastcontact.changed
        if (lastcontact.invalid) result.lastcontact_invalid = true
      }
      return result
    },

    // Event identity is normalized name PLUS date proximity: same-name
    // events dated within EVENT_MATCH_DAYS merge (closest date wins);
    // outside the window a new record is created, so "Christmas Party"
    // 2025 and 2026 stay distinct. A matched event merges its structured
    // fields — attendee union, missing Date/Where filled — instead of
    // freezing whatever the first note happened to mention.
    ensure_event(op) {
      const EVENT_MATCH_DAYS = 45
      const hits = findByNameOrAlias(EVENTS_PATH, op.name)
      const opDate = String(op.date || '')
      // log_line arrives pre-built (with its fact: provenance marker) from
      // ops_for_plan; the composed form covers proposals frozen before it.
      const logLine = op.log_line ||
        (op.summary && op.source_uuid
          ? '- ' + op.date + ' — ' + op.summary +
            ' ([source](x-devonthink-item://' + op.source_uuid + '))'
          : null)
      let rec = null
      let bestGap = Infinity
      for (const h of hits) {
        const d = mdValue(h, 'eventdate').slice(0, 10)
        const bd = d ? '' : bodyDateValue(bodyLines(h))
        const gap = eventMatchGap(opDate, d, bd, EVENT_MATCH_DAYS)
        if (gap <= EVENT_MATCH_DAYS && gap < bestGap) {
          rec = h
          bestGap = gap
        }
      }
      if (rec !== null) {
        const uuid = rec.uuid()
        const lines = bodyLines(rec)
        let changed = false
        for (let i = 0; i < lines.length; i++) {
          const bare = v => v === '' || v === '—'
          if (/^\*\*Date:\*\*/.test(lines[i])) {
            const v = lines[i].replace(/^\*\*Date:\*\*\s*/, '').trim()
            if (bare(v) && opDate) {
              lines[i] = '**Date:** ' + opDate
              changed = true
            }
          } else if (/^\*\*Where:\*\*/.test(lines[i])) {
            const v = lines[i].replace(/^\*\*Where:\*\*\s*/, '').trim()
            if (bare(v) && op.location) {
              lines[i] = '**Where:** ' + linkEntities(op.location, uuid)
              changed = true
            }
          } else if (/^\*\*Who:\*\*/.test(lines[i])) {
            const raw = lines[i].replace(/^\*\*Who:\*\*\s*/, '').trim()
            const existing = (raw === '' || raw === '—')
              ? [] : raw.split(',').map(s => s.trim()).filter(Boolean)
            const seen = Object.create(null)
            for (const e of existing) {
              seen[normName(e.replace(/^\[([^\]]*)\].*$/, '$1'))] = true
            }
            const additions = []
            for (const a of (op.attendees || [])) {
              const k = normName(a)
              if (k && !seen[k]) {
                seen[k] = true
                additions.push(personLink(a))
              }
            }
            if (additions.length) {
              lines[i] = '**Who:** ' + existing.concat(additions).join(', ')
              changed = true
            }
          }
        }
        if (!mdValue(rec, 'eventdate')) {
          const backfill = bodyDateValue(lines) || opDate
          if (backfill) dt.addCustomMetaData(backfill, { for: 'eventdate', to: rec })
        }
        if (changed) rec.plainText = lines.join('\n')
        if (logLine) appendLogLines(rec, [logLine], EVENT_LOG_SECTION)
        return { uuid: uuid, created: false, merged: changed }
      }
      let tpl = readFile(EVENT_TEMPLATE)
      if (tpl === null) {
        tpl = '# Event Name\n\n**Date:** —\n**Where:** —\n**Who:** —\n\n' +
          EVENT_LOG_SECTION + '\n'
      }
      const attendees = (op.attendees || []).map(personLink)
      const lines = tpl.split('\n')
      for (let i = 0; i < lines.length; i++) {
        if (/^#\s/.test(lines[i])) lines[i] = '# ' + op.name
        else if (/^\*\*Date:\*\*/.test(lines[i])) lines[i] = '**Date:** ' + op.date
        else if (/^\*\*Where:\*\*/.test(lines[i]) && op.location) {
          lines[i] = '**Where:** ' + linkEntities(op.location, null)
        } else if (/^\*\*Who:\*\*/.test(lines[i]) && attendees.length) {
          lines[i] = '**Who:** ' + attendees.join(', ')
        }
      }
      rec = dt.createRecordWith(
        { name: op.name, type: 'markdown' }, { in: groupAt(EVENTS_PATH) })
      rec.plainText = lines.join('\n')
      entityIndex = null
      dt.addCustomMetaData('Event', { for: 'entitytype', to: rec })
      dt.addCustomMetaData(op.date, { for: 'eventdate', to: rec })
      if (logLine) appendLogLines(rec, [logLine], EVENT_LOG_SECTION)
      return { uuid: rec.uuid(), created: true }
    },

    append_log(op) {
      const rec = byUuid(op.uuid)
      const counts = appendLogLines(rec, op.lines)
      return { uuid: op.uuid, appended: counts.appended, skipped: counts.skipped }
    },

    // Temporal conflict protection: each applied update records the source's
    // date in the FieldAsOf JSON blob, and an update dated before a field's
    // recorded date is refused (an older source processed later, or an
    // approved proposal overtaken by a newer write, must not clobber current
    // state). expected_previous is the fallback for fields with no recorded
    // date yet: a mismatch means the value changed after the ops were built.
    set_field(op) {
      const rec = byUuid(op.uuid)
      const previous = mdValue(rec, op.field)
      const isEmail = op.field === 'email'
      const incomingValue = isEmail ? normalizeEmail(op.value) : op.value
      const sameValue = isEmail
        ? normalizeEmail(previous) === incomingValue
        : previous === String(op.value)
      let dates = {}
      try { dates = JSON.parse(mdValue(rec, 'fieldasof') || '{}') || {} }
      catch (e) { dates = {} }
      const asof = typeof dates[op.field] === 'string' ? dates[op.field] : ''
      const stampAsOf = () => {
        if (op.effective_date && op.effective_date > asof) {
          dates[op.field] = op.effective_date
          dt.addCustomMetaData(JSON.stringify(dates), { for: 'fieldasof', to: rec })
        }
      }
      if (sameValue) {
        stampAsOf()
        return { uuid: op.uuid, changed: false, previous: previous }
      }
      if (op.effective_date && asof) {
        if (op.effective_date < asof) {
          return { uuid: op.uuid, changed: false, stale: true,
                   previous: previous, asof: asof }
        }
      } else if (previous && op.expected_previous !== undefined &&
                 op.expected_previous !== null &&
                 String(op.expected_previous) !== previous) {
        return { uuid: op.uuid, changed: false, stale: true, previous: previous }
      }
      dt.addCustomMetaData(incomingValue, { for: op.field, to: rec })
      stampAsOf()
      if (op.transition_line) appendLogLines(rec, [op.transition_line])
      return { uuid: op.uuid, changed: true, previous: previous }
    },

    bump_lastcontact(op) {
      const rec = byUuid(op.uuid)
      const g = lastContactGuard(mdValue(rec, 'lastcontact'), op.date)
      if (g.invalid) return { uuid: op.uuid, changed: false, invalid: true }
      if (g.changed) dt.addCustomMetaData(op.date, { for: 'lastcontact', to: rec })
      return { uuid: op.uuid, changed: g.changed }
    },

    mark_filed(op) {
      dt.addCustomMetaData(true, { for: 'entityfiled', to: byUuid(op.uuid) })
      return { uuid: op.uuid }
    },

    create_record(op) {
      const rec = dt.createRecordWith(
        { name: op.name, type: 'markdown' }, { in: groupAt(op.path) })
      rec.plainText = op.text
      for (const [field, value] of Object.entries(op.fields || {})) {
        dt.addCustomMetaData(value, { for: field, to: rec })
      }
      if (op.tags) rec.tags = op.tags
      return { uuid: rec.uuid() }
    },

    get_or_create_daily(op) {
      const group = groupAt(DAILY_PATH)
      // getRecordAt is O(1) but resolves by path, not by the scan's identity
      // rule (record name === op.date); a name mismatch (or no record at
      // that path) falls through to the linear scan unchanged.
      const fast = dt.getRecordAt(DAILY_PATH + '/' + op.date, { in: db })
      if (fast && fast.name() === op.date) {
        return { uuid: fast.uuid(), text: fast.plainText(), created: false }
      }
      for (const rec of group.children()) {
        if (rec.name() === op.date) {
          return { uuid: rec.uuid(), text: rec.plainText(), created: false }
        }
      }
      const rec = dt.createRecordWith(
        { name: op.date, type: 'markdown' }, { in: group })
      // Match create-daily-note.sh's skeleton exactly, trailing-space bullet included.
      const text = '# ' + op.heading + '\n\n- \n'
      rec.plainText = text
      rec.tags = ['Daily Note']
      return { uuid: rec.uuid(), text: text, created: true }
    },

    // Repair pass for logs that predate the sort (or were hand-edited out of
    // order): re-sorts every entity record's log section newest-first. The
    // filer keeps records it touches in order on its own, so this is only for
    // the ones it has no reason to touch. Invoke manually:
    //   echo '{"ops":[{"op":"sort_logs"}]}' > /tmp/ops.json &&
    //   osascript -l JavaScript entity-dt-bridge.js /tmp/ops.json
    sort_logs(op) {
      let records = 0
      let changedRecords = 0
      const changed = []
      for (const path of [PEOPLE_PATH, PLACES_PATH, EVENTS_PATH]) {
        let group
        try { group = groupAt(path) } catch (e) { continue }
        for (const rec of group.children()) {
          if (String(rec.type()) !== 'markdown') continue
          records++
          const body = bodyLines(rec)
          let next = body
          for (const header of [LOG_SECTION, EVENT_LOG_SECTION]) {
            next = sortLogSection(next, header)
          }
          const text = next.join('\n')
          if (text === body.join('\n')) continue
          if (!op.dry_run) rec.plainText = text
          changedRecords++
          changed.push({ uuid: rec.uuid(), name: rec.name() })
        }
      }
      return { records: records, changed: changedRecords, records_changed: changed }
    },

    // On-demand backfill: creating a Place or Event only affects facts
    // filed afterwards, so this re-runs entity auto-linking over every
    // existing entity record's bullet lines. Fact identity is unchanged —
    // factSignature flattens links. Invoke manually:
    //   echo '{"ops":[{"op":"relink_entities"}]}' > /tmp/ops.json &&
    //   osascript -l JavaScript entity-dt-bridge.js /tmp/ops.json
    relink_entities() {
      let records = 0
      let changedRecords = 0
      for (const path of [PEOPLE_PATH, PLACES_PATH, EVENTS_PATH]) {
        let group
        try { group = groupAt(path) } catch (e) { continue }
        for (const rec of group.children()) {
          if (String(rec.type()) !== 'markdown') continue
          records++
          const uuid = rec.uuid()
          const lines = bodyLines(rec)
          let changed = false
          for (let i = 0; i < lines.length; i++) {
            if (!/^- /.test(lines[i])) continue
            const linked = linkEntities(lines[i], uuid)
            if (linked !== lines[i]) {
              lines[i] = linked
              changed = true
            }
          }
          if (changed) {
            rec.plainText = lines.join('\n')
            changedRecords++
          }
        }
      }
      return { records: records, changed: changedRecords }
    },

    // Replace (or append, or remove on empty content) one generated `##`
    // section — the header line through the next `##` header — against the
    // record's LATEST body, read and written inside this single bridge
    // invocation. The old flow read the whole body, edited it in Python,
    // and wrote it back seconds later; a jot inserted in between was lost.
    // The write is skipped when the section is already byte-identical, so
    // refresh retries don't churn sync.
    upsert_section(op) {
      const rec = byUuid(op.uuid)
      const lines = bodyLines(rec)
      const r = sectionUpsert(lines, op.header, String(op.content || ''))
      if (r.text !== null) rec.plainText = r.text
      const out = { uuid: op.uuid, changed: r.changed, replaced: r.replaced }
      if (r.removed) out.removed = true
      return out
    },

    // Insert one line under a `##` section header against the record's
    // LATEST body, read and written inside this single bridge invocation —
    // the read-modify-write upsert_section closes for a full section,
    // scoped to a single line. A line already present under the section is
    // a no-op.
    insert_under_section(op) {
      const rec = byUuid(op.uuid)
      const body = bodyLines(rec)
      const next = insertUnderSectionOnce(body, op.header, op.line)
      if (next === body) return { uuid: op.uuid, changed: false }
      rec.plainText = next.join('\n')
      return { uuid: op.uuid, changed: true }
    },

    trash(op) {
      dt.move({ record: byUuid(op.uuid), to: db.trashGroup() })
      return { uuid: op.uuid }
    },
  }

  const results = []
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i]
    try {
      const handler = handlers[op.op]
      if (!handler) throw new Error('unknown op: ' + op.op)
      results.push(handler(op))
    } catch (e) {
      return JSON.stringify({
        ok: false,
        error: String(e.message || e),
        failed_op: i,
        results: results,
      })
    }
  }
  return JSON.stringify({ ok: true, results: results })
}
